"""
HTTP wire-contract tests (IMPLEMENTATION_PLAN.md §4): parameter
validation, error envelope and headers, coverage/freshness exposure,
projection over the wire, and the 404/405/500/503 failure model. Edge
paths 413/429/502/504 live at the nginx TLS edge and are verified manually
in Phase 5, not here.
"""

import datetime

import pytest
from fastapi.testclient import TestClient

from modules.api.src.app import create_app
from modules.api.tests import support

AUTH = {"Authorization": f"Bearer {support.TEST_TOKEN}"}
FROZEN = support.FROZEN_NOW  # 2026-08-05T18:00:00Z
# Explicit range for multi-page reads, per the contract's caller guidance
# (default range resolves "today" at request time and would cross-test
# pagination consistency).
WIDE_RANGE = {"event_from": "2020-01-01", "event_to": "2030-01-01"}


def _client(tmp_path, **kwargs):
    export_dir = kwargs.pop("export_dir", None) or support.copy_fixture_export(tmp_path)
    now_fn = kwargs.pop("now_fn", lambda: FROZEN)
    return TestClient(create_app(support.make_resolved_config(export_dir), now_fn=now_fn))


def _error(response):
    return response.json()["error"]


# --- success shape --------------------------------------------------------


def test_articles_success_shape(tmp_path):
    response = _client(tmp_path).get(
        "/v1/articles",
        params={"event_from": "2026-08-01", "event_to": "2026-08-05"},
        headers=AUTH,
    )
    assert response.status_code == 200
    body = response.json()
    assert list(body.keys()) == [
        "range",
        "language",
        "coverage",
        "total_count",
        "returned_count",
        "next_cursor",
        "articles",
    ]
    assert body["range"] == {"event_from": "2026-08-01", "event_to": "2026-08-05"}
    assert body["language"] == "zh"
    assert list(body["coverage"].keys()) == [
        "window_from",
        "window_to",
        "basis",
        "generation",
        "export_completed_at",
        "last_successful_run_at",
        "request_exceeds_window_to",
        "data_may_be_stale",
        "items_without_event_time",
    ]
    assert [a["slug"] for a in body["articles"]] == [
        "echo-evening-briefing",
        "alpha-tie-break",
        "bravo-tie-break",
        "delta-late-approval",
    ]


def test_default_range_is_today_utc(tmp_path):
    response = _client(tmp_path).get("/v1/articles", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["range"] == {"event_from": "2026-08-05", "event_to": "2026-08-05"}
    assert [a["slug"] for a in body["articles"]] == ["echo-evening-briefing"]


def test_cache_control_no_store_on_success(tmp_path):
    response = _client(tmp_path).get("/v1/articles", headers=AUTH)
    assert response.headers["Cache-Control"] == "no-store"


def test_generation_logged_per_request(tmp_path, caplog):
    with caplog.at_level("INFO", logger="api.app"):
        _client(tmp_path).get("/v1/articles", headers=AUTH)
    assert any(support.GENERATION_CURRENT in record.getMessage() for record in caplog.records)


# --- parameter validation (400 envelope; the framework 422 never leaks) ---


@pytest.mark.parametrize("raw", ["2026-8-5", "2026/08/05", "2026-02-30", "yesterday"])
def test_malformed_date_is_400(tmp_path, raw):
    response = _client(tmp_path).get(
        "/v1/articles", params={"event_from": raw}, headers=AUTH
    )
    assert response.status_code == 400
    assert _error(response)["code"] == "malformed_date"


def test_inverted_range_is_400(tmp_path):
    response = _client(tmp_path).get(
        "/v1/articles",
        params={"event_from": "2026-08-05", "event_to": "2026-08-01"},
        headers=AUTH,
    )
    assert response.status_code == 400
    assert _error(response)["code"] == "invalid_date_range"


def test_maximum_date_range_is_200(tmp_path):
    # event_to=9999-12-31 is calendar-valid; the exclusive range-end
    # computation must not overflow into an internal_error 500.
    response = _client(tmp_path).get(
        "/v1/articles",
        params={"event_from": "2026-08-01", "event_to": "9999-12-31"},
        headers=AUTH,
    )
    assert response.status_code == 200
    assert response.json()["coverage"]["request_exceeds_window_to"] is True


@pytest.mark.parametrize("raw,ok", [("0", False), ("501", False), ("abc", False),
                                    ("1", True), ("500", True)])
def test_limit_range(tmp_path, raw, ok):
    response = _client(tmp_path).get(
        "/v1/articles", params={"limit": raw}, headers=AUTH
    )
    if ok:
        assert response.status_code == 200
    else:
        assert response.status_code == 400
        assert _error(response)["code"] == "invalid_limit"


def test_unsupported_language_is_400_with_supported_list(tmp_path):
    response = _client(tmp_path).get(
        "/v1/articles", params={"language": "ja"}, headers=AUTH
    )
    assert response.status_code == 400
    error = _error(response)
    assert error["code"] == "unsupported_language"
    assert error["supported_languages"] == ["zh", "en"]


def test_stale_residue_directories_are_never_served(tmp_path):
    # The real 2026-08-05 residue shape (DATA_DEPENDENCIES.md §3): flat
    # pre-refactor language directories beside a valid generation pointer.
    export_dir = support.copy_fixture_export(tmp_path)
    residue = export_dir / "ja"
    residue.mkdir()
    support.write_json(residue / "index.json", [{"slug": "ghost"}])
    response = _client(tmp_path, export_dir=export_dir).get(
        "/v1/articles", params={"language": "ja"}, headers=AUTH
    )
    assert response.status_code == 400
    assert _error(response)["supported_languages"] == ["zh", "en"]


# --- include projection over the wire -------------------------------------


def test_default_projection_has_no_bullets(tmp_path):
    body = _client(tmp_path).get("/v1/articles", params=WIDE_RANGE, headers=AUTH).json()
    assert all("bullets" not in article for article in body["articles"])


def test_include_bullets_over_the_wire(tmp_path):
    body = _client(tmp_path).get(
        "/v1/articles", params={**WIDE_RANGE, "include": "bullets"}, headers=AUTH
    ).json()
    by_slug = {a["slug"]: a for a in body["articles"]}
    assert set(by_slug["echo-evening-briefing"]["bullets"]) == {
        "key_claim",
        "evidence_level",
        "objective_impact",
    }
    assert by_slug["bravo-tie-break"]["bullets"] is None


@pytest.mark.parametrize(
    "raw",
    ["unknown", "", "bullets,", ",bullets", "bullets,bullets", "bullets,unknown"],
)
def test_invalid_include_is_400(tmp_path, raw):
    response = _client(tmp_path).get(
        "/v1/articles", params={"include": raw}, headers=AUTH
    )
    assert response.status_code == 400
    assert _error(response)["code"] == "invalid_include"


def test_repeated_include_query_key_is_400(tmp_path):
    response = _client(tmp_path).get(
        "/v1/articles?include=bullets&include=bullets", headers=AUTH
    )
    assert response.status_code == 400
    assert _error(response)["code"] == "invalid_include"


# --- pagination over the wire ----------------------------------------------


def test_cursor_pagination_over_the_wire(tmp_path):
    client = _client(tmp_path)
    first = client.get(
        "/v1/articles", params={**WIDE_RANGE, "limit": "4"}, headers=AUTH
    ).json()
    assert first["returned_count"] == 4
    assert first["next_cursor"] is not None
    second = client.get(
        "/v1/articles",
        params={**WIDE_RANGE, "limit": "4", "cursor": first["next_cursor"]},
        headers=AUTH,
    ).json()
    assert [a["slug"] for a in second["articles"]] == [
        "foxtrot-window-floor",
        "charlie-old-event",
    ]
    assert second["next_cursor"] is None
    assert first["total_count"] == second["total_count"] == 6


def test_cursor_expired_over_the_wire(tmp_path):
    export_dir = support.copy_fixture_export(tmp_path)
    client = _client(tmp_path, export_dir=export_dir)
    first = client.get(
        "/v1/articles", params={**WIDE_RANGE, "limit": "2"}, headers=AUTH
    ).json()
    support.write_pointer(
        export_dir,
        generation=support.GENERATION_PREVIOUS,
        export_completed_at="2026-08-04T10:00:00Z",
        last_successful_run_at="2026-08-04T10:00:00Z",
    )
    response = client.get(
        "/v1/articles",
        params={**WIDE_RANGE, "limit": "2", "cursor": first["next_cursor"]},
        headers=AUTH,
    )
    assert response.status_code == 400
    assert _error(response)["code"] == "cursor_expired"


def test_cursor_query_mismatch_over_the_wire(tmp_path):
    client = _client(tmp_path)
    first = client.get(
        "/v1/articles", params={**WIDE_RANGE, "limit": "2"}, headers=AUTH
    ).json()
    response = client.get(
        "/v1/articles",
        params={**WIDE_RANGE, "limit": "2", "cursor": first["next_cursor"], "language": "en"},
        headers=AUTH,
    )
    assert response.status_code == 400
    assert _error(response)["code"] == "cursor_query_mismatch"


def test_forged_cursor_is_400(tmp_path):
    response = _client(tmp_path).get(
        "/v1/articles", params={"cursor": "forged"}, headers=AUTH
    )
    assert response.status_code == 400
    assert _error(response)["code"] == "invalid_cursor"


def test_non_ascii_cursor_is_400(tmp_path):
    # Over the wire: a cursor carrying a non-ASCII segment is invalid_cursor,
    # never an unhandled 500 from the signature computation (review issue).
    response = _client(tmp_path).get(
        "/v1/articles", params={**WIDE_RANGE, "cursor": "é.sig"}, headers=AUTH
    )
    assert response.status_code == 400
    assert _error(response)["code"] == "invalid_cursor"


def test_reencoded_tampered_cursor_is_400(tmp_path):
    # Over the wire: a shape-valid re-encoded payload carrying the original
    # signature is rejected — tampering never silently shifts the page
    # position (API_CONTRACT.md §4).
    import base64
    import json as jsonlib

    client = _client(tmp_path)
    first = client.get(
        "/v1/articles", params={**WIDE_RANGE, "limit": "2"}, headers=AUTH
    ).json()
    payload_b64, _, sig = first["next_cursor"].rpartition(".")
    payload = jsonlib.loads(base64.urlsafe_b64decode(payload_b64 + "=="))
    payload["last_slug"] = "zzzzz-last"
    tampered_b64 = (
        base64.urlsafe_b64encode(jsonlib.dumps(payload).encode()).decode("ascii").rstrip("=")
    )
    response = client.get(
        "/v1/articles",
        params={**WIDE_RANGE, "limit": "2", "cursor": f"{tampered_b64}.{sig}"},
        headers=AUTH,
    )
    assert response.status_code == 400
    assert _error(response)["code"] == "invalid_cursor"


# --- failure model ----------------------------------------------------------


def test_unknown_route_is_404_envelope(tmp_path):
    response = _client(tmp_path).get("/v1/unknown", headers=AUTH)
    assert response.status_code == 404
    assert _error(response)["code"] == "not_found"
    assert response.headers["Cache-Control"] == "no-store"


def test_non_get_method_is_405_envelope(tmp_path):
    response = _client(tmp_path).post("/v1/articles", headers=AUTH)
    assert response.status_code == 405
    assert _error(response)["code"] == "method_not_allowed"
    assert response.headers["Allow"] == "GET"
    assert response.headers["Cache-Control"] == "no-store"


def test_head_method_is_405(tmp_path):
    # Starlette auto-adds HEAD to GET routes; the contract supports GET only.
    response = _client(tmp_path).head("/v1/articles", headers=AUTH)
    assert response.status_code == 405
    assert response.headers["Allow"] == "GET"


def test_trailing_slash_is_404_not_redirect(tmp_path):
    response = _client(tmp_path).get("/v1/articles/", headers=AUTH)
    assert response.status_code == 404
    assert _error(response)["code"] == "not_found"


def test_missing_pointer_is_503_with_retry_after(tmp_path):
    export_dir = tmp_path / "publish_export"
    export_dir.mkdir()
    response = _client(tmp_path, export_dir=export_dir).get("/v1/articles", headers=AUTH)
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "30"
    assert _error(response)["code"] == "export_unavailable"
    assert response.headers["Cache-Control"] == "no-store"


def test_invalid_pointer_is_503(tmp_path):
    export_dir = support.copy_fixture_export(tmp_path)
    support.write_pointer(export_dir, last_successful_run_at="2026-02-30T00:00:00Z")
    response = _client(tmp_path, export_dir=export_dir).get("/v1/articles", headers=AUTH)
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "30"


def test_malformed_generation_data_is_500(tmp_path):
    export_dir = support.copy_fixture_export(tmp_path)
    index_path = (
        export_dir / "generations" / support.GENERATION_CURRENT / "zh" / "index.json"
    )
    index_path.write_text("{not json", encoding="utf-8")
    response = _client(tmp_path, export_dir=export_dir).get("/v1/articles", headers=AUTH)
    assert response.status_code == 500
    assert _error(response)["code"] == "malformed_export"
    assert response.headers["Cache-Control"] == "no-store"


def test_non_utf8_item_file_is_500_malformed_export(tmp_path):
    # Undecodable bytes are malformed generation data (UnicodeDecodeError is
    # a ValueError, not OSError) — never the unhandled internal_error.
    items = [support.make_item_record("alpha", "2026-08-05T10:00:00Z")]
    root = support.write_generation(tmp_path, support.GENERATION_CURRENT, {"zh": items})
    support.write_pointer(tmp_path, languages=("zh",))
    (root / "zh" / "items" / "alpha.json").write_bytes(b"\xff\xfe\x00\x01")
    response = _client(tmp_path, export_dir=tmp_path).get(
        "/v1/articles", params=WIDE_RANGE, headers=AUTH
    )
    assert response.status_code == 500
    assert _error(response)["code"] == "malformed_export"


def test_unsafe_index_slug_is_500_malformed_export(tmp_path):
    # A traversal/absolute-path slug never reaches the items/ join.
    root = tmp_path / "generations" / support.GENERATION_CURRENT
    support.write_json(
        root / "zh" / "index.json",
        [
            {
                "slug": "../../evil",
                "display_title": "t",
                "summary_short": "s",
                "canonical_url": "https://example.com/x",
                "source_published_at": "2026-08-05T10:00:00Z",
                "approved_at": "2026-08-05T10:00:00Z",
                "published_at": "2026-08-05T15:23:51Z",
            }
        ],
    )
    support.write_pointer(tmp_path, languages=("zh",))
    response = _client(tmp_path, export_dir=tmp_path).get(
        "/v1/articles", params=WIDE_RANGE, headers=AUTH
    )
    assert response.status_code == 500
    assert _error(response)["code"] == "malformed_export"


def test_unsafe_pointer_language_is_503(tmp_path):
    # current.json languages entries double as path components; an unsafe
    # value is an invalid pointer, never a path traversal vector.
    export_dir = support.copy_fixture_export(tmp_path)
    support.write_pointer(export_dir, languages=["zh", "../en"])
    response = _client(tmp_path, export_dir=export_dir).get("/v1/articles", headers=AUTH)
    assert response.status_code == 503
    assert _error(response)["code"] == "export_unavailable"
    assert response.headers["Retry-After"] == "30"


def test_misordered_index_is_500_over_the_wire(tmp_path):
    # The adapter must verify the contract order it paginates by; a
    # misordered index fails closed, never silently skips entries.
    items = [
        support.make_item_record("older-item", "2026-08-04T09:00:00Z"),
        support.make_item_record("newer-item", "2026-08-05T10:00:00Z"),
    ]
    support.write_generation(tmp_path, support.GENERATION_CURRENT, {"zh": items})
    support.write_pointer(tmp_path, languages=("zh",))
    response = _client(tmp_path, export_dir=tmp_path).get(
        "/v1/articles", params=WIDE_RANGE, headers=AUTH
    )
    assert response.status_code == 500
    assert _error(response)["code"] == "malformed_export"


def test_item_identity_mismatch_is_500_over_the_wire(tmp_path):
    # items/alpha.json carrying another slug's record must never be mixed
    # into alpha's response.
    items = [support.make_item_record("alpha", "2026-08-05T10:00:00Z")]
    root = support.write_generation(tmp_path, support.GENERATION_CURRENT, {"zh": items})
    support.write_pointer(tmp_path, languages=("zh",))
    support.write_json(
        root / "zh" / "items" / "alpha.json",
        support.make_item_record("bravo", "2026-08-05T10:00:00Z"),
    )
    response = _client(tmp_path, export_dir=tmp_path).get(
        "/v1/articles", params=WIDE_RANGE, headers=AUTH
    )
    assert response.status_code == 500
    assert _error(response)["code"] == "malformed_export"


def test_freshness_flag_over_the_wire(tmp_path):
    client = _client(tmp_path, now_fn=lambda: support.FROZEN_NOW_STALE)
    body = client.get("/v1/articles", headers=AUTH).json()
    assert body["coverage"]["data_may_be_stale"] is True


def test_401_also_carries_no_store(tmp_path):
    response = _client(tmp_path).get("/v1/articles")
    assert response.status_code == 401
    assert response.headers["Cache-Control"] == "no-store"


def test_malformed_bullets_projection_is_500_over_the_wire(tmp_path):
    items = [
        support.make_item_record(
            "bad-bullets", "2026-08-05T10:00:00Z", bullets={"key_claim": "only"}
        )
    ]
    support.write_generation(tmp_path, support.GENERATION_CURRENT, {"zh": items})
    support.write_pointer(tmp_path, languages=("zh",))
    response = _client(tmp_path, export_dir=tmp_path).get(
        "/v1/articles", params={**WIDE_RANGE, "include": "bullets"}, headers=AUTH
    )
    assert response.status_code == 500
    assert _error(response)["code"] == "malformed_export"


def test_framework_validation_error_never_leaks_422(tmp_path):
    # All query parameters are validated by hand, so FastAPI's
    # RequestValidationError is a safety net — prove it maps to the
    # contract's 400 envelope if it ever fires.
    import asyncio
    import json as jsonlib

    from fastapi.exceptions import RequestValidationError

    app = create_app(support.make_resolved_config(support.copy_fixture_export(tmp_path)))
    handler = app.exception_handlers[RequestValidationError]
    response = asyncio.run(handler(None, RequestValidationError([])))
    assert response.status_code == 400
    body = jsonlib.loads(response.body)
    assert body["error"]["code"] == "invalid_parameter"
    assert response.headers["Cache-Control"] == "no-store"
