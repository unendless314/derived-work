"""
Query adapter: the pure read logic behind ``GET /v1/articles``
(DATA_DEPENDENCIES.md §2). Reads exclusively the current generation
resolved through ``current.json``: ``<lang>/index.json`` (already in
response order per the publish index contract: ``source_published_at``
DESC, ``slug`` ASC) plus per-slug joins into ``<lang>/items/`` for the
full-record fields. It never scans ``items/`` wholesale, never stitches
``archives/``, and never touches canonical storage.

The entire read flow is wrapped in the single retry scope of
API_CONTRACT.md §5: if the resolved generation vanishes at any point
mid-read (retention sweep), the pointer is re-read and the whole flow
re-run exactly once; a second failure is ``503``. A JSON parse error or
schema violation inside a resolved generation is not retried — the
generation is immutable, so re-reading cannot help; it maps to ``500``
(EXECUTION_POLICY.md §8).
"""

import datetime
import json
import logging
import pathlib
import re
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from .cursor import (
    CursorError,
    DecodedCursor,
    check_cursor,
    decode_cursor,
    encode_cursor,
)
from .export_pointer import (
    Pointer,
    PointerError,
    parse_iso_timestamp,
    read_pointer,
    resolve_generation_root,
)

logger = logging.getLogger("api.adapter")

ALLOWED_INCLUDE: FrozenSet[str] = frozenset({"bullets"})
ALLOWED_DOWNSTREAM_ACTIONS: FrozenSet[str] = frozenset({"publish_summary", "publish_link"})
BULLETS_KEYS = frozenset({"key_claim", "evidence_level", "objective_impact"})
# Index slugs double as the items/ filename key, so they are validated
# against the slugify charset (modules/publish/src/validation.py) before
# any path join — no separator, dot, or drive letter can reach the join.
SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


class ExportUnavailableError(Exception):
    """The export is not serveable: pointer missing/invalid, or the
    generation still unresolvable after one full-flow retry → 503."""


class MalformedExportError(Exception):
    """Unreadable or malformed export data within a resolved generation
    → 500."""


class UnsupportedLanguageError(Exception):
    """The requested language is not in the pointer's authoritative
    ``languages`` list → 400 with the supported list."""

    def __init__(self, language: str, supported: Tuple[str, ...]):
        super().__init__(
            f"language {language!r} is not supported by the current export"
        )
        self.language = language
        self.supported = list(supported)


def _load_json_file(path: pathlib.Path, what: str) -> Any:
    """Read and parse a JSON artifact. A missing file propagates as
    FileNotFoundError (retriable sweep signal); a parse or read failure is
    malformed generation data (never retried)."""
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        raise
    except UnicodeDecodeError as e:
        # Not an OSError subclass: undecodable bytes are malformed
        # generation data, mapping to 500 malformed_export like any parse
        # failure — never the unhandled internal_error.
        raise MalformedExportError(f"{what} is not valid UTF-8: {e}") from e
    except OSError as e:
        raise MalformedExportError(f"{what} is unreadable: {e}") from e
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise MalformedExportError(f"{what} is not valid JSON: {e}") from e


def _load_index(index_path: pathlib.Path, language: str) -> List[Dict[str, Any]]:
    """Read the language index and verify it fail-stop: shape, field values,
    and the contract ordering the pagination math relies on."""
    index = _load_json_file(index_path, f"index.json for language '{language}'")
    if not isinstance(index, list):
        raise MalformedExportError(
            f"index.json for language '{language}' is invalid: top-level "
            "value must be an array"
        )
    previous = None  # (ts, slug) of the previous event-time-parseable entry
    seen_slugs = set()  # every join key must be unique across the index
    for position, entry in enumerate(index):
        if not isinstance(entry, dict):
            raise MalformedExportError(
                f"index.json for language '{language}' entry #{position} is "
                "not an object"
            )
        slug = entry.get("slug")
        if not isinstance(slug, str) or SLUG_RE.match(slug) is None:
            raise MalformedExportError(
                f"index.json for language '{language}' entry #{position} "
                "carries no valid slug (the join key): slugs must match "
                f"{SLUG_RE.pattern}, so no unsafe value ever reaches the "
                "items/ path join"
            )
        # A duplicate join key can pass the strict-ordering check (differing
        # timestamps) and would then join the same item file into multiple
        # articles — malformed generation data, fail closed.
        if slug in seen_slugs:
            raise MalformedExportError(
                f"index.json for language '{language}' carries slug '{slug}' "
                "more than once — a duplicate join key would join the same "
                "item record into multiple articles"
            )
        seen_slugs.add(slug)
        # Value checks, in parity with the publish writer's rules — a
        # present-but-wrong-typed field is malformed generation data, not a
        # 200 response carrying null.
        for key in ("display_title", "summary_short", "canonical_url"):
            value = entry.get(key)
            if not isinstance(value, str) or not value.strip():
                raise MalformedExportError(
                    f"index.json for language '{language}' entry '{slug}' "
                    f"carries no valid {key} (a non-empty string is required)"
                )
        for key in ("approved_at", "published_at"):
            if parse_iso_timestamp(entry.get(key)) is None:
                raise MalformedExportError(
                    f"index.json for language '{language}' entry '{slug}' "
                    f"carries no valid {key} (a calendar-valid ISO-8601 UTC "
                    "timestamp is required)"
                )
        # The index must already be in response order (source_published_at
        # DESC, slug ASC — the publish index contract); cursor pagination
        # relies on it, so a misordered index fails closed instead of
        # silently skipping entries. Entries with a missing or unparseable
        # event time are never served and sit outside the ordering check.
        ts = parse_iso_timestamp(entry.get("source_published_at"))
        if ts is not None:
            if previous is not None:
                prev_ts, prev_slug = previous
                if not (prev_ts > ts or (prev_ts == ts and prev_slug < slug)):
                    raise MalformedExportError(
                        f"index.json for language '{language}' violates the "
                        f"contract order (source_published_at DESC, slug ASC) "
                        f"at entry '{slug}'"
                    )
            previous = (ts, slug)
    return index


def _load_item(items_path: pathlib.Path, slug: str, language: str) -> Dict[str, Any]:
    item = _load_json_file(items_path / f"{slug}.json", f"item record '{slug}'")
    if not isinstance(item, dict):
        raise MalformedExportError(f"item record '{slug}' is not an object")
    # Join identity: the record in the file must be the one the index entry
    # points at — a file carrying another slug's or language's record must
    # never be mixed into this entry's response fields.
    if item.get("slug") != slug or item.get("language_code") != language:
        raise MalformedExportError(
            f"item record '{slug}' (language '{language}') carries "
            f"slug={item.get('slug')!r} and language_code="
            f"{item.get('language_code')!r} — the join would mix records"
        )
    source_item_id = item.get("source_item_id")
    if isinstance(source_item_id, bool) or not isinstance(source_item_id, int):
        raise MalformedExportError(
            f"item record '{slug}' carries no valid source_item_id"
        )
    action = item.get("downstream_action")
    if not isinstance(action, str) or action not in ALLOWED_DOWNSTREAM_ACTIONS:
        raise MalformedExportError(
            f"item record '{slug}' carries an unknown downstream_action: "
            f"{action!r} (publish emits exactly publish_summary or publish_link)"
        )
    return item


def _pass_through_bullets(item: Dict[str, Any], slug: str) -> Any:
    """The publish-verbatim ``bullets`` value, never omitted, never
    re-derived. The shape is bound to ``downstream_action`` in parity with
    the publish writer's validation (``modules/publish/src/validation.py``):
    ``null`` on publish_link items, the fixed three-key object with
    non-empty string values on publish_summary items. Anything else is
    malformed generation data → 500 (DATA_DEPENDENCIES.md §2)."""
    action = item["downstream_action"]  # one of two values; _load_item validated it
    if "bullets" not in item:
        raise MalformedExportError(f"item record '{slug}' has no 'bullets' key")
    bullets = item["bullets"]
    if action == "publish_link":
        if bullets is not None:
            raise MalformedExportError(
                f"item record '{slug}' is a publish_link item, so 'bullets' "
                "must be null"
            )
        return None
    if (
        not isinstance(bullets, dict)
        or set(bullets) != BULLETS_KEYS
        or not all(
            isinstance(bullets[key], str) and bullets[key].strip()
            for key in BULLETS_KEYS
        )
    ):
        raise MalformedExportError(
            f"item record '{slug}' is a publish_summary item, so 'bullets' "
            "must be the fixed three-key object {key_claim, evidence_level, "
            "objective_impact} with non-empty string values"
        )
    return bullets


def _is_after_cursor(
    ts: datetime.datetime, slug: str, cursor: DecodedCursor
) -> bool:
    """Total-order comparison matching the response order
    (``source_published_at`` DESC, ``slug`` ASC): an entry follows the
    cursor position when its timestamp is earlier, or equal with a
    lexicographically greater slug."""
    cursor_ts = parse_iso_timestamp(cursor.last_source_published_at)
    assert cursor_ts is not None  # validated at decode time
    return ts < cursor_ts or (ts == cursor_ts and slug > cursor.last_slug)


def _read_page(
    export_dir: pathlib.Path,
    pointer: Pointer,
    *,
    event_from: datetime.date,
    event_to: datetime.date,
    language: str,
    limit: int,
    cursor: Optional[DecodedCursor],
    include: FrozenSet[str],
    freshness_sla_hours: int,
    now: datetime.datetime,
    cursor_secret: str,
) -> Dict[str, Any]:
    if language not in pointer.languages:
        raise UnsupportedLanguageError(language, pointer.languages)
    if cursor is not None:
        check_cursor(
            cursor,
            generation=pointer.generation,
            event_from=event_from,
            event_to=event_to,
            language=language,
            include=include,
        )

    generation_root = resolve_generation_root(export_dir, pointer)
    index = _load_index(generation_root / language / "index.json", language)

    # Event-time pass: entries with a missing or unparseable
    # source_published_at cannot join any range — they are counted and
    # skipped (feed metadata quality varies by source; API_CONTRACT.md §4).
    parseable: List[Tuple[datetime.datetime, Dict[str, Any]]] = []
    items_without_event_time = 0
    for entry in index:
        ts = parse_iso_timestamp(entry.get("source_published_at"))
        if ts is None:
            items_without_event_time += 1
            continue
        parseable.append((ts, entry))

    window_from = min((ts for ts, _ in parseable), default=None)
    window_to = max((ts for ts, _ in parseable), default=None)

    # Inclusive date-range filtering on event time; the index is already in
    # response order, so filtering preserves it without a re-sort.
    matched = [
        (ts, entry)
        for ts, entry in parseable
        if event_from <= ts.date() <= event_to
    ]
    total_count = len(matched)

    if cursor is not None:
        remaining = [
            (ts, entry)
            for ts, entry in matched
            if _is_after_cursor(ts, entry["slug"], cursor)
        ]
    else:
        remaining = matched

    page = remaining[:limit]
    has_more = len(remaining) > limit

    articles: List[Dict[str, Any]] = []
    items_path = generation_root / language / "items"
    for ts, entry in page:
        item = _load_item(items_path, entry["slug"], language)
        article = {
            "source_item_id": item["source_item_id"],
            "slug": entry["slug"],
            "display_title": entry["display_title"],
            "summary_short": entry["summary_short"],
            "canonical_url": entry["canonical_url"],
            "source_published_at": entry["source_published_at"],
            "approved_at": entry["approved_at"],
            "published_at": entry["published_at"],
            "downstream_action": item["downstream_action"],
        }
        if "bullets" in include:
            article["bullets"] = _pass_through_bullets(item, entry["slug"])
        articles.append(article)

    next_cursor = None
    if has_more:
        last_ts, last_entry = page[-1]
        next_cursor = encode_cursor(
            secret=cursor_secret,
            generation=pointer.generation,
            event_from=event_from,
            event_to=event_to,
            language=language,
            include=include,
            last_source_published_at=last_entry["source_published_at"],
            last_slug=last_entry["slug"],
        )

    # The request range [event_from, event_to] interpreted as whole UTC
    # days extends beyond the window when the exclusive end-of-range
    # (start of the day after event_to) is past the window's newest event.
    if event_to == datetime.date.max:
        # date.max + 1 day overflows; an unbounded range end exceeds any
        # real data window by definition.
        range_end_exclusive = datetime.datetime.max.replace(
            tzinfo=datetime.timezone.utc
        )
    else:
        range_end_exclusive = datetime.datetime.combine(
            event_to + datetime.timedelta(days=1),
            datetime.time.min,
            tzinfo=datetime.timezone.utc,
        )
    request_exceeds_window_to = window_to is None or range_end_exclusive > window_to

    data_may_be_stale = (now - pointer.last_successful_run_datetime) > datetime.timedelta(
        hours=freshness_sla_hours
    )

    return {
        "range": {
            "event_from": event_from.isoformat(),
            "event_to": event_to.isoformat(),
        },
        "language": language,
        "coverage": {
            "window_from": window_from.strftime(_TS_FORMAT) if window_from else None,
            "window_to": window_to.strftime(_TS_FORMAT) if window_to else None,
            "basis": "index",
            "generation": pointer.generation,
            "export_completed_at": pointer.export_completed_at,
            "last_successful_run_at": pointer.last_successful_run_at,
            "request_exceeds_window_to": request_exceeds_window_to,
            "data_may_be_stale": data_may_be_stale,
            "items_without_event_time": items_without_event_time,
        },
        "total_count": total_count,
        "returned_count": len(articles),
        "next_cursor": next_cursor,
        "articles": articles,
    }


def query_articles(
    export_dir: pathlib.Path,
    *,
    event_from: datetime.date,
    event_to: datetime.date,
    language: str,
    limit: int,
    cursor_token: Optional[str] = None,
    include: FrozenSet[str] = frozenset(),
    freshness_sla_hours: int,
    now: Optional[datetime.datetime] = None,
    cursor_secret: str,
) -> Dict[str, Any]:
    """Run one ``GET /v1/articles`` query against the current generation,
    wrapped in the single full-flow retry scope of API_CONTRACT.md §5.
    ``cursor_secret`` is the HMAC key cursors are signed and verified with
    (derived from the Bearer token by the HTTP layer)."""
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    cursor = (
        decode_cursor(cursor_token, secret=cursor_secret)
        if cursor_token is not None
        else None
    )

    attempt = 0
    while True:
        attempt += 1
        try:
            pointer = read_pointer(export_dir)
        except PointerError as e:
            raise ExportUnavailableError(str(e)) from e
        try:
            return _read_page(
                export_dir,
                pointer,
                event_from=event_from,
                event_to=event_to,
                language=language,
                limit=limit,
                cursor=cursor,
                include=include,
                freshness_sla_hours=freshness_sla_hours,
                now=now,
                cursor_secret=cursor_secret,
            )
        except (FileNotFoundError, NotADirectoryError) as e:
            if attempt >= 2:
                raise ExportUnavailableError(
                    f"generation '{pointer.generation}' vanished mid-read and "
                    f"the retry did not resolve a serveable generation: {e}"
                ) from e
            logger.info(
                "Generation %s vanished mid-read (%s); re-reading the pointer "
                "and retrying once",
                pointer.generation,
                e,
            )
