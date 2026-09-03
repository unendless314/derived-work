"""
Adapter tests (IMPLEMENTATION_PLAN.md §4): query semantics, ordering,
pagination, coverage/freshness, projection, and the consistency/failure
model — all against the fixture export tree (pure logic, no web layer).
"""

import base64
import datetime
import json
import shutil

import pytest

from modules.api.src import adapter
from modules.api.src.adapter import (
    ExportUnavailableError,
    MalformedExportError,
    UnsupportedLanguageError,
    query_articles,
)
from modules.api.src.cursor import (
    CursorExpiredError,
    CursorInvalidError,
    CursorQueryMismatchError,
)
from modules.api.tests import support

FRESHNESS_SLA_HOURS = 6
WIDE_RANGE = {"event_from": datetime.date(2020, 1, 1), "event_to": datetime.date(2030, 1, 1)}


def _query(export_dir, **overrides):
    kwargs = {
        "event_from": WIDE_RANGE["event_from"],
        "event_to": WIDE_RANGE["event_to"],
        "language": "zh",
        "limit": 100,
        "freshness_sla_hours": FRESHNESS_SLA_HOURS,
        "now": support.FROZEN_NOW,
        "cursor_secret": support.TEST_CURSOR_SECRET,
    }
    kwargs.update(overrides)
    return query_articles(export_dir, **kwargs)


# --- query semantics -----------------------------------------------------


def test_full_range_returns_all_items_in_response_order(tmp_path):
    export_dir = support.copy_fixture_export(tmp_path)
    result = _query(export_dir)
    slugs = [a["slug"] for a in result["articles"]]
    # source_published_at DESC, slug ASC on ties (publish index contract):
    # alpha and bravo share 2026-08-04T09:00:00Z and sort ascending.
    assert slugs == [
        "echo-evening-briefing",
        "alpha-tie-break",
        "bravo-tie-break",
        "delta-late-approval",
        "foxtrot-window-floor",
        "charlie-old-event",
    ]
    assert result["total_count"] == 6
    assert result["returned_count"] == 6
    assert result["next_cursor"] is None


def test_event_time_semantics_old_approved_item_excluded(tmp_path):
    # charlie-old-event (event 2025-11-10, approved 2026-08-04) is not this
    # week's news; delta-late-approval (event 2026-08-03, approved
    # 2026-08-05) is captured by event-time queries (API_CONTRACT.md §3).
    export_dir = support.copy_fixture_export(tmp_path)
    result = _query(
        export_dir,
        event_from=datetime.date(2026, 8, 3),
        event_to=datetime.date(2026, 8, 5),
    )
    slugs = [a["slug"] for a in result["articles"]]
    assert slugs == [
        "echo-evening-briefing",
        "alpha-tie-break",
        "bravo-tie-break",
        "delta-late-approval",
    ]


def test_inclusive_bounds_single_day(tmp_path):
    export_dir = support.copy_fixture_export(tmp_path)
    result = _query(
        export_dir,
        event_from=datetime.date(2026, 8, 4),
        event_to=datetime.date(2026, 8, 4),
    )
    assert [a["slug"] for a in result["articles"]] == ["alpha-tie-break", "bravo-tie-break"]


def test_range_older_than_window_returns_in_window_matches(tmp_path):
    export_dir = support.copy_fixture_export(tmp_path)
    result = _query(
        export_dir,
        event_from=datetime.date(2025, 1, 1),
        event_to=datetime.date(2025, 12, 31),
    )
    assert [a["slug"] for a in result["articles"]] == ["charlie-old-event"]
    assert result["total_count"] == 1


def test_range_entirely_outside_window_is_empty_not_error(tmp_path):
    export_dir = support.copy_fixture_export(tmp_path)
    result = _query(
        export_dir,
        event_from=datetime.date(2020, 1, 1),
        event_to=datetime.date(2020, 12, 31),
    )
    assert result["articles"] == []
    assert result["total_count"] == 0


def test_response_field_pass_through(tmp_path):
    export_dir = support.copy_fixture_export(tmp_path)
    result = _query(export_dir, limit=1)
    article = result["articles"][0]
    assert list(article.keys()) == [
        "source_item_id",
        "slug",
        "display_title",
        "summary_short",
        "canonical_url",
        "source_published_at",
        "approved_at",
        "published_at",
        "downstream_action",
    ]
    assert article["source_item_id"] == 105
    assert article["downstream_action"] == "publish_summary"
    assert article["canonical_url"] == "https://example.com/echo-evening-briefing"


def test_language_selection(tmp_path):
    export_dir = support.copy_fixture_export(tmp_path)
    result = _query(export_dir, language="en")
    assert [a["slug"] for a in result["articles"]] == [
        "echo-evening-briefing",
        "alpha-tie-break",
    ]
    assert result["language"] == "en"


def test_unsupported_language_lists_supported_codes(tmp_path):
    export_dir = support.copy_fixture_export(tmp_path)
    with pytest.raises(UnsupportedLanguageError) as excinfo:
        _query(export_dir, language="ja")
    assert excinfo.value.supported == ["zh", "en"]


# --- pagination ----------------------------------------------------------


def _paginate_all(export_dir, limits, **overrides):
    """Follow next_cursor with a per-page limit schedule; returns pages."""
    pages = []
    cursor = None
    for limit in limits:
        result = _query(export_dir, limit=limit, cursor_token=cursor, **overrides)
        pages.append(result)
        cursor = result["next_cursor"]
        if cursor is None:
            break
    return pages


def test_pagination_gap_free_duplicate_free(tmp_path):
    export_dir = support.copy_fixture_export(tmp_path)
    pages = _paginate_all(export_dir, [2, 2, 2, 2])
    assert [p["returned_count"] for p in pages] == [2, 2, 2]
    slugs = [a["slug"] for p in pages for a in p["articles"]]
    assert slugs == [
        "echo-evening-briefing",
        "alpha-tie-break",
        "bravo-tie-break",
        "delta-late-approval",
        "foxtrot-window-floor",
        "charlie-old-event",
    ]
    assert all(p["total_count"] == 6 for p in pages)
    assert pages[-1]["next_cursor"] is None


def test_include_bullets_paginates_cleanly(tmp_path):
    export_dir = support.copy_fixture_export(tmp_path)
    pages = _paginate_all(export_dir, [2, 2, 2], include=frozenset({"bullets"}))
    slugs = [a["slug"] for p in pages for a in p["articles"]]
    assert len(slugs) == len(set(slugs)) == 6
    assert all("bullets" in a for p in pages for a in p["articles"])


def test_changing_include_between_pages_mismatches_cursor(tmp_path):
    export_dir = support.copy_fixture_export(tmp_path)
    first = _query(export_dir, limit=2, include=frozenset({"bullets"}))
    with pytest.raises(CursorQueryMismatchError):
        _query(export_dir, limit=2, cursor_token=first["next_cursor"])


def test_changing_only_limit_with_projection_remains_valid(tmp_path):
    export_dir = support.copy_fixture_export(tmp_path)
    pages = _paginate_all(export_dir, [5, 1], include=frozenset({"bullets"}))
    slugs = [a["slug"] for p in pages for a in p["articles"]]
    assert len(slugs) == len(set(slugs)) == 6


def test_changing_only_limit_between_pages_remains_valid(tmp_path):
    # limit is the one parameter deliberately not cursor-bound.
    export_dir = support.copy_fixture_export(tmp_path)
    pages = _paginate_all(export_dir, [1, 4, 100])
    slugs = [a["slug"] for p in pages for a in p["articles"]]
    assert slugs == [
        "echo-evening-briefing",
        "alpha-tie-break",
        "bravo-tie-break",
        "delta-late-approval",
        "foxtrot-window-floor",
        "charlie-old-event",
    ]
    assert [p["returned_count"] for p in pages] == [1, 4, 1]


def test_pagination_across_tie_break_boundary(tmp_path):
    # Page 1 ends between the two entries sharing 2026-08-04T09:00:00Z; the
    # cursor comparison direction must match the external order (ts DESC,
    # slug ASC) so bravo is neither skipped nor repeated.
    export_dir = support.copy_fixture_export(tmp_path)
    pages = _paginate_all(export_dir, [2, 100])
    slugs = [a["slug"] for p in pages for a in p["articles"]]
    assert slugs[:3] == ["echo-evening-briefing", "alpha-tie-break", "bravo-tie-break"]
    assert len(slugs) == 6


def test_generation_switch_between_pages_expires_cursor(tmp_path):
    export_dir = support.copy_fixture_export(tmp_path)
    first = _query(export_dir, limit=2)
    support.write_pointer(
        export_dir,
        generation=support.GENERATION_PREVIOUS,
        export_completed_at="2026-08-04T10:00:00Z",
        last_successful_run_at="2026-08-04T10:00:00Z",
    )
    with pytest.raises(CursorExpiredError):
        _query(export_dir, limit=2, cursor_token=first["next_cursor"])


def test_query_change_between_pages_mismatches_cursor(tmp_path):
    export_dir = support.copy_fixture_export(tmp_path)
    first = _query(export_dir, limit=2)
    with pytest.raises(CursorQueryMismatchError):
        _query(export_dir, limit=2, cursor_token=first["next_cursor"], language="en")


def test_tampered_cursor_is_invalid(tmp_path):
    export_dir = support.copy_fixture_export(tmp_path)
    with pytest.raises(CursorInvalidError):
        _query(export_dir, cursor_token="forged-cursor")


def test_reencoded_tampered_cursor_is_invalid(tmp_path):
    # The review scenario end-to-end: decode a genuine next_cursor, alter
    # the position slug, re-encode the payload, and attach the original
    # signature — the HMAC no longer covers the payload, so the read fails
    # closed instead of silently skipping or duplicating records.
    export_dir = support.copy_fixture_export(tmp_path)
    first = _query(export_dir, limit=2)
    payload_b64, _, sig = first["next_cursor"].rpartition(".")
    payload = json.loads(base64.urlsafe_b64decode(payload_b64 + "=="))
    payload["last_slug"] = "zzzzz-last"
    tampered_b64 = (
        base64.urlsafe_b64encode(json.dumps(payload).encode()).decode("ascii").rstrip("=")
    )
    with pytest.raises(CursorInvalidError):
        _query(export_dir, limit=2, cursor_token=f"{tampered_b64}.{sig}")


# --- coverage and freshness ----------------------------------------------


def test_coverage_window_from_index_bounds(tmp_path):
    export_dir = support.copy_fixture_export(tmp_path)
    coverage = _query(export_dir)["coverage"]
    assert coverage["basis"] == "index"
    assert coverage["window_from"] == "2025-11-10T08:00:00Z"
    assert coverage["window_to"] == "2026-08-05T12:00:00Z"
    assert coverage["generation"] == support.GENERATION_CURRENT
    assert coverage["export_completed_at"] == "2026-08-05T15:23:51Z"
    assert coverage["last_successful_run_at"] == "2026-08-05T15:23:51Z"
    assert coverage["items_without_event_time"] == 0


@pytest.mark.parametrize(
    "event_to,expected",
    [
        (datetime.date(2026, 8, 5), True),   # end-of-day past window_to
        (datetime.date(2026, 8, 6), True),   # whole day beyond the window
        (datetime.date(2026, 8, 4), False),  # fully inside the window
    ],
)
def test_request_exceeds_window_to(tmp_path, event_to, expected):
    export_dir = support.copy_fixture_export(tmp_path)
    result = _query(export_dir, event_from=datetime.date(2026, 8, 1), event_to=event_to)
    assert result["coverage"]["request_exceeds_window_to"] is expected


def test_maximum_event_to_does_not_overflow(tmp_path):
    # date.max is a calendar-valid request bound, but event_to + 1 day
    # overflows; the unbounded range end must be treated as exceeding any
    # real data window instead of raising into a 500 (review issue).
    export_dir = support.copy_fixture_export(tmp_path)
    result = _query(
        export_dir,
        event_from=datetime.date(2026, 8, 1),
        event_to=datetime.date(9999, 12, 31),
    )
    assert result["coverage"]["request_exceeds_window_to"] is True
    assert result["total_count"] == 4


@pytest.mark.parametrize(
    "now,expected",
    [
        # boundary: exactly freshness_sla_hours old is still fresh (strict >)
        (datetime.datetime(2026, 8, 5, 21, 23, 51, tzinfo=datetime.timezone.utc), False),
        (datetime.datetime(2026, 8, 5, 21, 23, 52, tzinfo=datetime.timezone.utc), True),
        (support.FROZEN_NOW, False),
        (support.FROZEN_NOW_STALE, True),
    ],
)
def test_data_may_be_stale_flips_at_sla_boundary(tmp_path, now, expected):
    export_dir = support.copy_fixture_export(tmp_path)
    result = _query(export_dir, now=now)
    assert result["coverage"]["data_may_be_stale"] is expected


def test_items_without_event_time_counted_and_excluded(tmp_path):
    items = [
        support.make_item_record("good-item", "2026-08-05T10:00:00Z"),
        support.make_item_record("null-ts-item", None),
        support.make_item_record("bad-ts-item", "not-a-date"),
    ]
    support.write_generation(tmp_path, support.GENERATION_CURRENT, {"zh": items})
    support.write_pointer(tmp_path, languages=("zh",))
    result = _query(tmp_path)
    assert result["coverage"]["items_without_event_time"] == 2
    assert [a["slug"] for a in result["articles"]] == ["good-item"]
    assert result["total_count"] == 1


# --- projection: include=bullets ------------------------------------------


def test_default_projection_carries_no_bullets_key(tmp_path):
    export_dir = support.copy_fixture_export(tmp_path)
    result = _query(export_dir)
    assert all("bullets" not in article for article in result["articles"])


def test_include_bullets_never_omitted(tmp_path):
    export_dir = support.copy_fixture_export(tmp_path)
    result = _query(export_dir, include=frozenset({"bullets"}))
    by_slug = {a["slug"]: a for a in result["articles"]}
    summary_bullets = by_slug["echo-evening-briefing"]["bullets"]
    assert set(summary_bullets) == {"key_claim", "evidence_level", "objective_impact"}
    assert by_slug["bravo-tie-break"]["bullets"] is None  # publish_link
    assert all("bullets" in a for a in result["articles"])


def test_malformed_bullets_shape_is_a_500_never_silently_dropped(tmp_path):
    items = [support.make_item_record("bad-bullets", "2026-08-05T10:00:00Z",
                                      bullets={"key_claim": "only one key"})]
    support.write_generation(tmp_path, support.GENERATION_CURRENT, {"zh": items})
    support.write_pointer(tmp_path, languages=("zh",))
    with pytest.raises(MalformedExportError):
        _query(tmp_path, include=frozenset({"bullets"}))


def test_malformed_bullets_invisible_without_projection(tmp_path):
    items = [support.make_item_record("bad-bullets", "2026-08-05T10:00:00Z",
                                      bullets={"key_claim": "only one key"})]
    support.write_generation(tmp_path, support.GENERATION_CURRENT, {"zh": items})
    support.write_pointer(tmp_path, languages=("zh",))
    result = _query(tmp_path)
    assert "bullets" not in result["articles"][0]


def test_missing_bullets_key_is_malformed(tmp_path):
    item = support.make_item_record("no-bullets", "2026-08-05T10:00:00Z")
    del item["bullets"]
    support.write_generation(tmp_path, support.GENERATION_CURRENT, {"zh": [item]})
    support.write_pointer(tmp_path, languages=("zh",))
    with pytest.raises(MalformedExportError):
        _query(tmp_path, include=frozenset({"bullets"}))


# --- consistency and failure model ----------------------------------------


def test_missing_pointer_is_unavailable(tmp_path):
    with pytest.raises(ExportUnavailableError):
        _query(tmp_path)


def test_invalid_pointer_is_unavailable(tmp_path):
    export_dir = support.copy_fixture_export(tmp_path)
    support.write_pointer(export_dir, last_successful_run_at="2026-02-30T00:00:00Z")
    with pytest.raises(ExportUnavailableError):
        _query(export_dir)


def test_mid_read_sweep_retries_once_against_new_pointer(tmp_path, monkeypatch):
    export_dir = support.copy_fixture_export(tmp_path)
    original_load_item = adapter._load_item
    swept = {"done": False}

    def sweep_then_fail(items_path, slug, language):
        if not swept["done"]:
            swept["done"] = True
            # Retention sweeps the live generation mid-join; publish has
            # already switched the pointer to the previous generation.
            shutil.rmtree(export_dir / "generations" / support.GENERATION_CURRENT)
            support.write_pointer(
                export_dir,
                generation=support.GENERATION_PREVIOUS,
                export_completed_at="2026-08-04T10:00:00Z",
                last_successful_run_at="2026-08-04T10:00:00Z",
            )
            raise FileNotFoundError(str(items_path))
        return original_load_item(items_path, slug, language)

    monkeypatch.setattr(adapter, "_load_item", sweep_then_fail)
    result = _query(export_dir)
    assert result["coverage"]["generation"] == support.GENERATION_PREVIOUS
    assert result["total_count"] == 4  # the previous generation's zh set


def test_second_sweep_is_unavailable(tmp_path, monkeypatch):
    export_dir = support.copy_fixture_export(tmp_path)
    calls = {"resolve": 0}

    def always_swept(export_dir_arg, pointer):
        calls["resolve"] += 1
        raise FileNotFoundError("swept")

    monkeypatch.setattr(adapter, "resolve_generation_root", always_swept)
    with pytest.raises(ExportUnavailableError):
        _query(export_dir)
    assert calls["resolve"] == 2  # exactly one retry


def test_malformed_index_is_not_retried(tmp_path, monkeypatch):
    export_dir = support.copy_fixture_export(tmp_path)
    index_path = (
        export_dir / "generations" / support.GENERATION_CURRENT / "zh" / "index.json"
    )
    index_path.write_text("{not json", encoding="utf-8")

    reads = {"pointer": 0}
    original_read_pointer = adapter.read_pointer

    def counting_read_pointer(path):
        reads["pointer"] += 1
        return original_read_pointer(path)

    monkeypatch.setattr(adapter, "read_pointer", counting_read_pointer)
    with pytest.raises(MalformedExportError):
        _query(export_dir)
    assert reads["pointer"] == 1  # immutable generation: re-reading cannot help


def test_malformed_index_shape_rejected(tmp_path):
    items = [support.make_item_record("alpha", "2026-08-05T10:00:00Z")]
    root = support.write_generation(tmp_path, support.GENERATION_CURRENT, {"zh": items})
    support.write_pointer(tmp_path, languages=("zh",))
    support.write_json(root / "zh" / "index.json", {"not": "a list"})
    with pytest.raises(MalformedExportError):
        _query(tmp_path)


def test_missing_item_file_is_a_sweep_signal(tmp_path):
    # The join target vanished mid-page; the stable pointer means the retry
    # also fails → ExportUnavailableError (never a partial page).
    items = [
        support.make_item_record("alpha", "2026-08-05T10:00:00Z"),
        support.make_item_record("bravo", "2026-08-04T10:00:00Z"),
    ]
    root = support.write_generation(tmp_path, support.GENERATION_CURRENT, {"zh": items})
    support.write_pointer(tmp_path, languages=("zh",))
    (root / "zh" / "items" / "bravo.json").unlink()
    with pytest.raises(ExportUnavailableError):
        _query(tmp_path)


def test_malformed_item_record_is_a_500(tmp_path):
    item = support.make_item_record("alpha", "2026-08-05T10:00:00Z")
    del item["source_item_id"]
    support.write_generation(tmp_path, support.GENERATION_CURRENT, {"zh": [item]})
    support.write_pointer(tmp_path, languages=("zh",))
    with pytest.raises(MalformedExportError):
        _query(tmp_path)


# --- export-derived path components and record-shape parity -----------------


def _write_index_entries(tmp_path, entries):
    """Write a zh index.json straight from entries (bypassing
    write_generation's item derivation) so crafted slugs never touch the
    filesystem from the test side either."""
    root = tmp_path / "generations" / support.GENERATION_CURRENT
    support.write_json(root / "zh" / "index.json", entries)
    support.write_pointer(tmp_path, languages=("zh",))
    return root


def _index_entry(slug):
    return {
        "slug": slug,
        "display_title": "t",
        "summary_short": "s",
        "canonical_url": "https://example.com/x",
        "source_published_at": "2026-08-05T10:00:00Z",
        "approved_at": "2026-08-05T10:00:00Z",
        "published_at": "2026-08-05T15:23:51Z",
    }


@pytest.mark.parametrize(
    "slug",
    ["../../evil", "..\\..\\evil", "C:/Windows/win.ini", "C:\\evil", "/etc/passwd", "a/b"],
)
def test_unsafe_index_slug_rejected_before_any_path_join(tmp_path, slug):
    # Slugs are the items/ join key, so they must match the publish slugify
    # charset; an absolute path or traversal segment is malformed generation
    # data, never a path component (review issue: path-component validation).
    _write_index_entries(tmp_path, [_index_entry(slug)])
    with pytest.raises(MalformedExportError):
        _query(tmp_path)


def test_unknown_downstream_action_is_malformed(tmp_path):
    # downstream_action is served on every article, so it is validated with
    # or without the bullets projection.
    items = [
        support.make_item_record("alpha", "2026-08-05T10:00:00Z", downstream_action="publish_feature")
    ]
    support.write_generation(tmp_path, support.GENERATION_CURRENT, {"zh": items})
    support.write_pointer(tmp_path, languages=("zh",))
    with pytest.raises(MalformedExportError):
        _query(tmp_path)


def test_publish_link_item_with_bullets_object_is_malformed(tmp_path):
    items = [
        support.make_item_record(
            "alpha",
            "2026-08-05T10:00:00Z",
            downstream_action="publish_link",
            bullets={"key_claim": "x", "evidence_level": "y", "objective_impact": "z"},
        )
    ]
    support.write_generation(tmp_path, support.GENERATION_CURRENT, {"zh": items})
    support.write_pointer(tmp_path, languages=("zh",))
    with pytest.raises(MalformedExportError):
        _query(tmp_path, include=frozenset({"bullets"}))


def test_publish_summary_item_with_null_bullets_is_malformed(tmp_path):
    items = [support.make_item_record("alpha", "2026-08-05T10:00:00Z", bullets=None)]
    support.write_generation(tmp_path, support.GENERATION_CURRENT, {"zh": items})
    support.write_pointer(tmp_path, languages=("zh",))
    with pytest.raises(MalformedExportError):
        _query(tmp_path, include=frozenset({"bullets"}))


def test_blank_bullet_value_is_malformed(tmp_path):
    items = [
        support.make_item_record(
            "alpha",
            "2026-08-05T10:00:00Z",
            bullets={"key_claim": "   ", "evidence_level": "y", "objective_impact": "z"},
        )
    ]
    support.write_generation(tmp_path, support.GENERATION_CURRENT, {"zh": items})
    support.write_pointer(tmp_path, languages=("zh",))
    with pytest.raises(MalformedExportError):
        _query(tmp_path, include=frozenset({"bullets"}))


def test_non_utf8_item_file_is_malformed_export(tmp_path):
    # read_text raises UnicodeDecodeError (a ValueError, not OSError); it
    # must surface as malformed_export, never an unhandled internal error.
    items = [support.make_item_record("alpha", "2026-08-05T10:00:00Z")]
    root = support.write_generation(tmp_path, support.GENERATION_CURRENT, {"zh": items})
    support.write_pointer(tmp_path, languages=("zh",))
    (root / "zh" / "items" / "alpha.json").write_bytes(b"\xff\xfe\x00\x01 not utf-8")
    with pytest.raises(MalformedExportError):
        _query(tmp_path)


# --- index field values, ordering, and join identity ------------------------


@pytest.mark.parametrize(
    "key,value",
    [
        ("display_title", None),
        ("display_title", "   "),
        ("display_title", 123),
        ("summary_short", None),
        ("canonical_url", ""),
        ("approved_at", "not-a-date"),
        ("approved_at", "2026-02-30T00:00:00Z"),
        ("published_at", None),
    ],
)
def test_index_entry_with_invalid_field_value_is_malformed(tmp_path, key, value):
    # Presence alone is not enough: a wrong-typed or empty served field is
    # malformed generation data, never a 200 response carrying null.
    entry = _index_entry("alpha")
    entry[key] = value
    _write_index_entries(tmp_path, [entry])
    with pytest.raises(MalformedExportError):
        _query(tmp_path)


def test_misordered_index_fails_closed_instead_of_skipping(tmp_path):
    # Reviewer scenario: an older entry placed before a newer one. Trusted
    # blindly, limit=1 would serve the old entry on page 1 and an empty
    # page 2 — silently missing data with total_count=2. The ordering check
    # fails closed instead.
    older = _index_entry("older-item")
    older["source_published_at"] = "2026-08-04T09:00:00Z"
    newer = _index_entry("newer-item")
    _write_index_entries(tmp_path, [older, newer])  # wrong order
    with pytest.raises(MalformedExportError, match="contract order"):
        _query(tmp_path, limit=1)


def test_tie_break_order_violation_is_malformed(tmp_path):
    # Equal timestamps must still order slug ASC (both entries share the
    # helper's default source_published_at).
    _write_index_entries(tmp_path, [_index_entry("bravo-item"), _index_entry("alpha-item")])
    with pytest.raises(MalformedExportError, match="contract order"):
        _query(tmp_path)


def test_duplicate_slug_in_index_is_malformed(tmp_path):
    # The join key must be unique across the index. With differing
    # timestamps a duplicate passes the strict-ordering check and would
    # then join the same item file twice — duplicated articles carrying a
    # corrupted source_item_id (review issue: duplicate slugs).
    first = _index_entry("alpha")
    second = _index_entry("alpha")
    second["source_published_at"] = "2026-08-04T09:00:00Z"
    _write_index_entries(tmp_path, [first, second])
    with pytest.raises(MalformedExportError, match="more than once"):
        _query(tmp_path)


def test_unparseable_event_time_entries_sit_outside_the_ordering_check(tmp_path):
    # Entries that are never served (no parseable event time) do not
    # participate in ordering; the parseable subsequence must still be in
    # contract order around them.
    items = [
        support.make_item_record("zulu-new", "2026-08-05T10:00:00Z"),
        support.make_item_record("bad-ts", None),
        support.make_item_record("alpha-old", "2026-08-04T09:00:00Z"),
    ]
    support.write_generation(tmp_path, support.GENERATION_CURRENT, {"zh": items})
    support.write_pointer(tmp_path, languages=("zh",))
    result = _query(tmp_path)
    assert [a["slug"] for a in result["articles"]] == ["zulu-new", "alpha-old"]
    assert result["coverage"]["items_without_event_time"] == 1


def test_item_record_with_mismatched_slug_is_malformed(tmp_path):
    # The file items/alpha.json carries bravo's record: without an identity
    # check the response would mix the index entry's fields with another
    # record's source_item_id/downstream_action.
    items = [support.make_item_record("alpha", "2026-08-05T10:00:00Z")]
    root = support.write_generation(tmp_path, support.GENERATION_CURRENT, {"zh": items})
    support.write_pointer(tmp_path, languages=("zh",))
    wrong = support.make_item_record("bravo", "2026-08-05T10:00:00Z")
    support.write_json(root / "zh" / "items" / "alpha.json", wrong)
    with pytest.raises(MalformedExportError, match="mix records"):
        _query(tmp_path)


def test_item_record_with_mismatched_language_is_malformed(tmp_path):
    items = [support.make_item_record("alpha", "2026-08-05T10:00:00Z")]
    root = support.write_generation(tmp_path, support.GENERATION_CURRENT, {"zh": items})
    support.write_pointer(tmp_path, languages=("zh",))
    wrong = support.make_item_record("alpha", "2026-08-05T10:00:00Z", language="en")
    support.write_json(root / "zh" / "items" / "alpha.json", wrong)
    with pytest.raises(MalformedExportError, match="mix records"):
        _query(tmp_path)
