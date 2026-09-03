"""
Cursor codec tests (IMPLEMENTATION_PLAN.md §4, "Pagination"): round-trip,
signature/tamper rejection, and the generation/query binding rules of
API_CONTRACT.md §4.
"""

import base64
import datetime
import hashlib
import hmac
import json

import pytest

from modules.api.src.cursor import (
    CursorExpiredError,
    CursorInvalidError,
    CursorQueryMismatchError,
    check_cursor,
    decode_cursor,
    encode_cursor,
    generate_cursor_secret,
)
from modules.api.tests import support

SECRET = support.TEST_CURSOR_SECRET
EVENT_FROM = datetime.date(2026, 8, 1)
EVENT_TO = datetime.date(2026, 8, 5)


def _encode(**overrides):
    kwargs = {
        "secret": SECRET,
        "generation": support.GENERATION_CURRENT,
        "event_from": EVENT_FROM,
        "event_to": EVENT_TO,
        "language": "zh",
        "include": frozenset(),
        "last_source_published_at": "2026-08-04T09:00:00Z",
        "last_slug": "bravo-tie-break",
    }
    kwargs.update(overrides)
    return encode_cursor(**kwargs)


def _sign(payload_b64: str, secret: str) -> str:
    """Test-local reimplementation of the token signature so the wire
    format is pinned independently of the production codec."""
    digest = hmac.new(
        secret.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256
    ).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _signed_token(payload: dict, secret: str = SECRET) -> str:
    payload_b64 = (
        base64.urlsafe_b64encode(json.dumps(payload).encode()).decode("ascii").rstrip("=")
    )
    return f"{payload_b64}.{_sign(payload_b64, secret)}"


def _payload_of(token: str) -> dict:
    payload_b64 = token.split(".")[0]
    return json.loads(base64.urlsafe_b64decode(payload_b64 + "==").decode("utf-8"))


VALID_PAYLOAD = {
    "v": 1,
    "generation": support.GENERATION_CURRENT,
    "event_from": EVENT_FROM.isoformat(),
    "event_to": EVENT_TO.isoformat(),
    "language": "zh",
    "include": [],
    "last_source_published_at": "2026-08-04T09:00:00Z",
    "last_slug": "bravo-tie-break",
}


def test_generate_cursor_secret_is_random_and_server_side():
    first = generate_cursor_secret()
    second = generate_cursor_secret()
    assert first and isinstance(first, str)
    assert first != second  # fresh per call; never derived from caller input


def test_round_trip():
    token = _encode(include=frozenset({"bullets"}))
    cursor = decode_cursor(token, secret=SECRET)
    assert cursor.generation == support.GENERATION_CURRENT
    assert cursor.event_from == EVENT_FROM
    assert cursor.event_to == EVENT_TO
    assert cursor.language == "zh"
    assert cursor.include == frozenset({"bullets"})
    assert cursor.last_source_published_at == "2026-08-04T09:00:00Z"
    assert cursor.last_slug == "bravo-tie-break"


def test_limit_is_not_cursor_bound():
    assert "limit" not in _payload_of(_encode())


@pytest.mark.parametrize(
    "token",
    [
        "",
        "not-base64-json",
        # base64url payload without a signature segment
        base64.urlsafe_b64encode(json.dumps(VALID_PAYLOAD).encode()).decode().rstrip("="),
        # signature segment present but garbage
        base64.urlsafe_b64encode(json.dumps(VALID_PAYLOAD).encode()).decode().rstrip("=")
        + ".not-a-signature",
    ],
)
def test_unsigned_or_malformed_cursor_rejected(token):
    with pytest.raises(CursorInvalidError):
        decode_cursor(token, secret=SECRET)


@pytest.mark.parametrize(
    "token",
    [
        "é.c2ln",      # non-ASCII payload segment
        "cGF5.bG9hé",  # non-ASCII signature segment
    ],
)
def test_non_ascii_cursor_segments_rejected(token):
    # A non-ASCII byte string is by definition not a token this service
    # issued; the pre-signature ASCII gate must fail closed with
    # invalid_cursor instead of leaking UnicodeEncodeError as a 500.
    with pytest.raises(CursorInvalidError):
        decode_cursor(token, secret=SECRET)


def test_tampered_payload_with_original_signature_rejected():
    # The review scenario: decode a genuine cursor, alter last_slug,
    # re-encode the payload, and attach the original signature — the
    # signature no longer covers the payload, so this fails closed instead
    # of silently skipping or duplicating records.
    token = _encode()
    payload_b64, _, sig = token.rpartition(".")
    tampered = {**_payload_of(token), "last_slug": "zzzzz-last"}
    tampered_b64 = (
        base64.urlsafe_b64encode(json.dumps(tampered).encode()).decode("ascii").rstrip("=")
    )
    with pytest.raises(CursorInvalidError):
        decode_cursor(f"{tampered_b64}.{sig}", secret=SECRET)
    # Sanity: the untouched original still verifies.
    decode_cursor(f"{payload_b64}.{sig}", secret=SECRET)


def test_cursor_signed_with_a_different_secret_rejected():
    token = _encode()
    with pytest.raises(CursorInvalidError):
        decode_cursor(token, secret="attacker-guessed-secret")


def test_signed_but_shape_invalid_payload_rejected():
    # Signature validation never weakens shape validation: a correctly
    # signed payload with an unsorted include set is still invalid.
    payload = {**VALID_PAYLOAD, "include": ["zz", "bullets"]}
    with pytest.raises(CursorInvalidError):
        decode_cursor(_signed_token(payload), secret=SECRET)


@pytest.mark.parametrize(
    "payload",
    [
        "hello",  # not an object
        {"v": 99},  # wrong version
        {"v": 1, "generation": "g"},  # missing fields
    ],
)
def test_signed_but_malformed_payload_rejected(payload):
    with pytest.raises(CursorInvalidError):
        decode_cursor(_signed_token(payload), secret=SECRET)


def test_check_cursor_accepts_matching_request():
    cursor = decode_cursor(_encode(), secret=SECRET)
    check_cursor(
        cursor,
        generation=support.GENERATION_CURRENT,
        event_from=EVENT_FROM,
        event_to=EVENT_TO,
        language="zh",
        include=frozenset(),
    )


def test_generation_switch_expires_cursor():
    cursor = decode_cursor(_encode(), secret=SECRET)
    with pytest.raises(CursorExpiredError) as excinfo:
        check_cursor(
            cursor,
            generation=support.GENERATION_PREVIOUS,
            event_from=EVENT_FROM,
            event_to=EVENT_TO,
            language="zh",
            include=frozenset(),
        )
    assert excinfo.value.code == "cursor_expired"


@pytest.mark.parametrize(
    "field",
    ["event_from", "event_to", "language", "include"],
)
def test_query_change_mismatches_cursor(field):
    cursor = decode_cursor(_encode(include=frozenset({"bullets"})), secret=SECRET)
    kwargs = {
        "generation": support.GENERATION_CURRENT,
        "event_from": EVENT_FROM,
        "event_to": EVENT_TO,
        "language": "zh",
        "include": frozenset({"bullets"}),
    }
    kwargs[field] = {
        "event_from": datetime.date(2026, 8, 2),
        "event_to": datetime.date(2026, 8, 4),
        "language": "en",
        "include": frozenset(),
    }[field]
    with pytest.raises(CursorQueryMismatchError) as excinfo:
        check_cursor(cursor, **kwargs)
    assert excinfo.value.code == "cursor_query_mismatch"
