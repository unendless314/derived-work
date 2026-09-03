"""
Shared test support for the api module test suite.

This module is api-owned and imported explicitly by tests under
``modules/api/tests/`` (no ``conftest.py`` implicit fixtures). It provides:

- ``copy_fixture_export``: copies the static two-generation fixture tree
  (``fixtures/publish_export/``, mirroring the site fixture pattern) into a
  tmp_path so tests may mutate it (pointer switches, mid-read sweeps).
- ``write_pointer`` / ``write_generation`` / ``make_item_record``: builders
  for custom export trees covering edge cases the static fixture does not
  carry (unparseable event times, malformed bullets, malformed payloads).
- Frozen-time constants so freshness assertions never depend on wall time.

Test-only helpers must never be imported from production modules, and this
module must not become a runtime dependency.
"""

import datetime
import json
import pathlib
import shutil
import zlib
from typing import Any, Dict, Iterable, List, Mapping, Optional

FIXTURE_EXPORT_ROOT = (
    pathlib.Path(__file__).resolve().parent / "fixtures" / "publish_export"
)

GENERATION_CURRENT = "2026-08-05T15-23-51Z"
GENERATION_PREVIOUS = "2026-08-04T10-00-00Z"
ZERO_FINGERPRINT = "sha256-exportstate-v1:" + "0" * 64

# 2026-08-05T18:00:00Z is ~2.6h after the fixture pointer's
# last_successful_run_at (2026-08-05T15:23:51Z): fresh under the 6h SLA.
FROZEN_NOW = datetime.datetime(2026, 8, 5, 18, 0, 0, tzinfo=datetime.timezone.utc)
# ~6.1h after last_successful_run_at: stale under the 6h SLA.
FROZEN_NOW_STALE = datetime.datetime(2026, 8, 5, 21, 30, 0, tzinfo=datetime.timezone.utc)

INDEX_ENTRY_KEYS = (
    "slug",
    "display_title",
    "summary_short",
    "canonical_url",
    "source_published_at",
    "approved_at",
    "published_at",
)

TEST_TOKEN = "test-token-0123456789"
# Stand-in cursor-signing secret for adapter/codec tests (the HTTP layer
# derives the real one from the Bearer token via derive_cursor_secret).
TEST_CURSOR_SECRET = "test-cursor-secret-0123456789"


def make_resolved_config(export_dir: pathlib.Path, token: str = TEST_TOKEN):
    """A ResolvedConfig pointing at the given export root, for HTTP-layer
    tests that build the app against a fixture copy."""
    from modules.api.src.config import ApiSettings, ResolvedConfig

    return ResolvedConfig(
        settings=ApiSettings(
            port=8010,
            export_dir=str(export_dir),
            freshness_sla_hours=6,
            token_env_var="EXOPOLITICS_API_TOKEN",
        ),
        export_dir=export_dir,
        token=token,
    )


def copy_fixture_export(tmp_path: pathlib.Path) -> pathlib.Path:
    target = tmp_path / "publish_export"
    shutil.copytree(FIXTURE_EXPORT_ROOT, target)
    return target


def write_json(path: pathlib.Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_pointer(
    export_dir: pathlib.Path,
    *,
    generation: Any = GENERATION_CURRENT,
    languages: Any = ("zh", "en"),
    export_completed_at: Any = "2026-08-05T15:23:51Z",
    last_successful_run_at: Any = "2026-08-05T15:23:51Z",
    content_fingerprint: Any = ZERO_FINGERPRINT,
) -> None:
    write_json(
        export_dir / "current.json",
        {
            "generation": generation,
            "export_completed_at": export_completed_at,
            "last_successful_run_at": last_successful_run_at,
            "languages": list(languages) if isinstance(languages, (list, tuple)) else languages,
            "content_fingerprint": content_fingerprint,
        },
    )


def make_item_record(
    slug: str,
    source_published_at: Any,
    *,
    language: str = "zh",
    source_item_id: Optional[int] = None,
    downstream_action: str = "publish_summary",
    bullets: Any = ...,  # ellipsis = derive from downstream_action
    display_title: Optional[str] = None,
    summary_short: Optional[str] = None,
    canonical_url: Optional[str] = None,
    approved_at: str = "2026-08-05T10:00:00Z",
    published_at: str = "2026-08-05T15:23:51Z",
    extra: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """A full item record as publish emits it under ``items/<slug>.json``.
    ``bullets=...`` (default) derives the publish-verbatim shape: the fixed
    three-key object on publish_summary items, None on publish_link items.
    Pass an explicit value to craft malformed shapes."""
    if source_item_id is None:
        source_item_id = zlib.crc32(slug.encode("utf-8")) % 100000
    if bullets is ...:
        bullets = (
            {
                "key_claim": f"Key claim for {slug}.",
                "evidence_level": f"Evidence level for {slug}.",
                "objective_impact": f"Objective impact for {slug}.",
            }
            if downstream_action == "publish_summary"
            else None
        )
    record: Dict[str, Any] = {
        "source_item_id": source_item_id,
        "language_code": language,
        "slug": slug,
        "display_title": display_title if display_title is not None else f"Title for {slug}",
        "summary_short": summary_short if summary_short is not None else f"Summary for {slug}.",
        "bullets": bullets,
        "canonical_url": canonical_url or f"https://example.com/{slug}",
        "source_published_at": source_published_at,
        "approved_at": approved_at,
        "published_at": published_at,
        "downstream_action": downstream_action,
        "disclosure_note": "This item is AI-assisted and human-curated.",
        "author_metadata": {"source_module": "edit", "writer_type": "human", "editor": "john_doe"},
    }
    if extra:
        record.update(extra)
    return record


def write_generation(
    export_dir: pathlib.Path,
    generation: str,
    items_by_lang: Mapping[str, List[Dict[str, Any]]],
    *,
    built_at: str = "2026-08-05T15:23:51Z",
) -> pathlib.Path:
    """Write a generation directory from full item records. The per-language
    ``index.json`` is derived from the item records (index entry keys only,
    in the given list order — callers own the ordering they want to pin)."""
    root = export_dir / "generations" / generation
    for lang, items in items_by_lang.items():
        index = [
            {key: item[key] for key in INDEX_ENTRY_KEYS if key in item} for item in items
        ]
        write_json(root / lang / "index.json", index)
        for item in items:
            write_json(root / lang / "items" / f"{item['slug']}.json", item)
    write_json(root / "stats.json", {"last_export_run_timestamp": built_at})
    write_json(
        root / "meta.json",
        {
            "generation": generation,
            "created_at": built_at,
            "content_fingerprint": ZERO_FINGERPRINT,
        },
    )
    return root


def utc_now(*args: Any, **kwargs: Any) -> datetime.datetime:
    """A frozen ``datetime.datetime.now`` replacement returning FROZEN_NOW."""
    return FROZEN_NOW
