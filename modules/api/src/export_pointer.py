"""
Read-side validation of the publish export generation pointer
(``current.json``), in parity with the writer
(``modules/publish/src/generation_store.py``) and the site reader
(``modules/site/src/utils/export_root.js``): a strict Windows-safe
generation id (validated before any path join, so no arbitrary string
becomes a path component), calendar-valid ISO-8601 UTC second-precision
timestamps, a non-empty language list, and the versioned
content-fingerprint format (DATA_DEPENDENCIES.md §4).

Any violation is fail-stop: the api maps ``PointerError`` to ``503`` with
``Retry-After`` (API_CONTRACT.md §§4–5). There is no fallback to stale
generations, flat layouts, or directory scanning (DATA_DEPENDENCIES.md
§3).
"""

import datetime
import json
import pathlib
import re
from dataclasses import dataclass
from typing import Any, Optional, Tuple

POINTER_FILE_NAME = "current.json"
GENERATIONS_DIR_NAME = "generations"

GENERATION_ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z(-r\d+)?$")
ISO_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
FINGERPRINT_RE = re.compile(r"^sha256-exportstate-v1:[0-9a-f]{64}$")
# Language codes double as path components (`generations/<id>/<lang>/`), so
# they are restricted to a Windows-safe lowercase form — no separators,
# dots, or drive letters can ever reach the join (DATA_DEPENDENCIES.md §4).
LANGUAGE_CODE_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


class PointerError(Exception):
    """current.json is missing, unreadable, or invalid → 503."""


def is_valid_generation_id(value: Any) -> bool:
    return isinstance(value, str) and GENERATION_ID_RE.match(value) is not None


def is_valid_iso_timestamp(value: Any) -> bool:
    """Format plus calendar validity: the regex pins the shape, strptime
    rejects impossible dates (e.g. February 30) that the shape admits."""
    return parse_iso_timestamp(value) is not None


def parse_iso_timestamp(value: Any) -> Optional[datetime.datetime]:
    """Strict ``YYYY-MM-DDTHH:MM:SSZ`` parse; returns an aware UTC datetime
    or None for anything off-format or calendar-impossible."""
    if not isinstance(value, str) or ISO_TIMESTAMP_RE.match(value) is None:
        return None
    try:
        parsed = datetime.datetime.strptime(value, _TS_FORMAT)
    except ValueError:
        return None
    return parsed.replace(tzinfo=datetime.timezone.utc)


@dataclass(frozen=True)
class Pointer:
    """A validated current.json payload. ``languages`` is the authoritative
    supported-language set (DATA_DEPENDENCIES.md §3)."""

    generation: str
    export_completed_at: str
    last_successful_run_at: str
    languages: Tuple[str, ...]
    content_fingerprint: str

    @property
    def last_successful_run_datetime(self) -> datetime.datetime:
        parsed = parse_iso_timestamp(self.last_successful_run_at)
        assert parsed is not None  # validated at construction
        return parsed


def validate_pointer(pointer: Any) -> Pointer:
    """Fail-stop field validation of a parsed current.json (shape rules
    only; generation-directory existence is checked at resolution time so a
    mid-read sweep enters the retry scope of API_CONTRACT.md §5)."""
    if not isinstance(pointer, dict):
        raise PointerError("current.json is invalid: top-level value must be an object")
    generation = pointer.get("generation")
    if not is_valid_generation_id(generation):
        raise PointerError(
            f"current.json is invalid: 'generation' must match "
            f"{GENERATION_ID_RE.pattern}, got {generation!r}"
        )
    for field in ("export_completed_at", "last_successful_run_at"):
        if not is_valid_iso_timestamp(pointer.get(field)):
            raise PointerError(
                f"current.json is invalid: '{field}' must be a calendar-valid "
                f"ISO-8601 UTC timestamp, got {pointer.get(field)!r}"
            )
    languages = pointer.get("languages")
    if (
        not isinstance(languages, list)
        or not languages
        or not all(isinstance(lang, str) for lang in languages)
    ):
        raise PointerError(
            "current.json is invalid: 'languages' must be a non-empty list of strings"
        )
    unsafe = [lang for lang in languages if LANGUAGE_CODE_RE.match(lang) is None]
    if unsafe:
        raise PointerError(
            "current.json is invalid: 'languages' entries must be safe path "
            f"components matching {LANGUAGE_CODE_RE.pattern}, got {unsafe!r}"
        )
    fingerprint = pointer.get("content_fingerprint")
    if not isinstance(fingerprint, str) or FINGERPRINT_RE.match(fingerprint) is None:
        raise PointerError(
            "current.json is invalid: 'content_fingerprint' must be a "
            "sha256-exportstate-v1 digest string"
        )
    return Pointer(
        generation=generation,
        export_completed_at=pointer["export_completed_at"],
        last_successful_run_at=pointer["last_successful_run_at"],
        languages=tuple(languages),
        content_fingerprint=fingerprint,
    )


def read_pointer(export_dir: pathlib.Path) -> Pointer:
    """Read and validate ``<export_dir>/current.json``. A missing file is
    the bootstrap state (the export has never completed); unreadable or
    invalid content is the same ``503`` category per API_CONTRACT.md §5."""
    pointer_path = export_dir / POINTER_FILE_NAME
    if not pointer_path.is_file():
        raise PointerError(
            f"Publish export pointer {pointer_path} does not exist; the "
            "export has not completed yet"
        )
    try:
        raw = pointer_path.read_text(encoding="utf-8")
        payload = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise PointerError(f"current.json is unreadable or corrupt: {e}") from e
    return validate_pointer(payload)


def resolve_generation_root(export_dir: pathlib.Path, pointer: Pointer) -> pathlib.Path:
    """Resolve the pointer's generation directory. The id was validated
    against the strict format before this join, so no arbitrary string
    reaches the filesystem. A missing directory means the generation was
    swept by retention (or the export state is broken); callers treat the
    FileNotFoundError as the retriable sweep signal of API_CONTRACT.md §5."""
    root = export_dir / GENERATIONS_DIR_NAME / pointer.generation
    if not root.is_dir():
        raise FileNotFoundError(
            f"current.json points at generation '{pointer.generation}', but "
            f"{root} does not exist or is not a directory"
        )
    return root
