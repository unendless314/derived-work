"""
HTTP layer: the FastAPI app wiring the query adapter to the ``/v1/`` wire
contract (API_CONTRACT.md).

- Every application response carries ``Cache-Control: no-store``
  (EXECUTION_POLICY.md §7).
- Every error response uses the shared envelope
  ``{"error": {"code", "message", ...}}`` (API_CONTRACT.md §1), including
  framework ``404``/``405`` responses; FastAPI's default ``422`` never
  leaks — malformed parameters map to the contract's ``400`` envelope.
- Bearer authentication guards all ``/v1/`` endpoints (single token,
  constant-time comparison; EXECUTION_POLICY.md §4).
"""

import datetime
import hmac
import logging
import re
from typing import Any, Dict, FrozenSet, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .adapter import (
    ALLOWED_INCLUDE,
    ExportUnavailableError,
    MalformedExportError,
    UnsupportedLanguageError,
    query_articles,
)
from .config import ResolvedConfig
from .cursor import CursorError, generate_cursor_secret

logger = logging.getLogger("api.app")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DEFAULT_LANGUAGE = "zh"
DEFAULT_LIMIT = 100
MAX_LIMIT = 500
RETRY_AFTER_SECONDS = "30"


class _AuthError(Exception):
    """Missing or invalid Bearer credentials → 401."""


class _ParamError(Exception):
    """A malformed query parameter → 400 with a machine-readable code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    **extra: Any,
) -> JSONResponse:
    error: Dict[str, Any] = {"code": code, "message": message}
    error.update(extra)
    all_headers = {"Cache-Control": "no-store"}
    if headers:
        all_headers.update(headers)
    return JSONResponse(
        status_code=status_code, content={"error": error}, headers=all_headers
    )


def _parse_date_param(name: str, raw: Optional[str], default: datetime.date) -> datetime.date:
    if raw is None:
        return default
    if _DATE_RE.match(raw) is None:
        raise _ParamError(
            "malformed_date", f"'{name}' must be a YYYY-MM-DD date, got {raw!r}"
        )
    try:
        return datetime.date.fromisoformat(raw)
    except ValueError:
        raise _ParamError(
            "malformed_date", f"'{name}' is not a calendar-valid date: {raw!r}"
        )


def _parse_limit(raw: Optional[str]) -> int:
    if raw is None:
        return DEFAULT_LIMIT
    try:
        limit = int(raw)
    except ValueError:
        raise _ParamError("invalid_limit", f"'limit' must be an integer, got {raw!r}")
    if not 1 <= limit <= MAX_LIMIT:
        raise _ParamError(
            "invalid_limit", f"'limit' must be between 1 and {MAX_LIMIT}, got {limit}"
        )
    return limit


def _parse_include(request: Request) -> FrozenSet[str]:
    """Strict include validation (API_CONTRACT.md §4): the raw query string
    is inspected so a repeated ``include`` key — which framework parsing
    would silently collect — is a 400 like any other malformed value."""
    values = request.query_params.getlist("include")
    if not values:
        return frozenset()
    if len(values) > 1:
        raise _ParamError(
            "invalid_include", "the 'include' query key must appear at most once"
        )
    entries = values[0].split(",")
    if any(entry == "" for entry in entries):
        raise _ParamError("invalid_include", "'include' carries an empty entry")
    if len(set(entries)) != len(entries):
        raise _ParamError("invalid_include", "'include' carries a duplicated entry")
    unknown = set(entries) - ALLOWED_INCLUDE
    if unknown:
        raise _ParamError(
            "invalid_include",
            f"unknown 'include' value(s) {sorted(unknown)}; supported: {sorted(ALLOWED_INCLUDE)}",
        )
    return frozenset(entries)


def create_app(config: ResolvedConfig, *, now_fn=None) -> FastAPI:
    """Build the FastAPI app. ``now_fn`` supplies the UTC clock for
    default-range resolution and freshness computation; tests inject a
    frozen clock through it."""
    # Single-consumer machine API: no docs/openapi surface; unknown paths
    # under /v1/ are 404s, never redirects (API_CONTRACT.md §4 error table).
    app = FastAPI(
        title="exopolitics-api",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        redirect_slashes=False,
    )
    expected_token = config.token
    # Cursors are HMAC-signed with a server-only random secret generated at
    # startup — never derived from the Bearer token, which the caller holds
    # and could sign with itself (API_CONTRACT.md §4). A service restart
    # invalidates outstanding cursors; clients restart from page 1.
    cursor_secret = generate_cursor_secret()
    if now_fn is None:
        now_fn = lambda: datetime.datetime.now(datetime.timezone.utc)

    def _authenticate(request: Request) -> None:
        header = request.headers.get("authorization")
        if header is None:
            raise _AuthError("missing Authorization header")
        scheme, _, credentials = header.partition(" ")
        provided = credentials.strip()
        if scheme.lower() != "bearer" or not provided:
            raise _AuthError("credentials must use the Bearer scheme")
        # Header values arrive latin-1-decoded; encoding back yields the
        # wire bytes, so an exotic credential simply never matches.
        if not hmac.compare_digest(
            provided.encode("latin-1"), expected_token.encode("utf-8")
        ):
            raise _AuthError("invalid Bearer token")

    @app.middleware("http")
    async def add_cache_control(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(_AuthError)
    async def auth_error_handler(request: Request, exc: _AuthError):
        return _error_response(
            401,
            "unauthorized",
            str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.exception_handler(_ParamError)
    async def param_error_handler(request: Request, exc: _ParamError):
        return _error_response(400, exc.code, exc.message)

    @app.exception_handler(CursorError)
    async def cursor_error_handler(request: Request, exc: CursorError):
        return _error_response(400, exc.code, str(exc))

    @app.exception_handler(UnsupportedLanguageError)
    async def unsupported_language_handler(
        request: Request, exc: UnsupportedLanguageError
    ):
        return _error_response(
            400,
            "unsupported_language",
            str(exc),
            supported_languages=exc.supported,
        )

    @app.exception_handler(ExportUnavailableError)
    async def export_unavailable_handler(request: Request, exc: ExportUnavailableError):
        return _error_response(
            503,
            "export_unavailable",
            str(exc),
            headers={"Retry-After": RETRY_AFTER_SECONDS},
        )

    @app.exception_handler(MalformedExportError)
    async def malformed_export_handler(request: Request, exc: MalformedExportError):
        return _error_response(500, "malformed_export", str(exc))

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        if exc.status_code == 405:
            allow = "GET"
            if exc.headers and exc.headers.get("Allow"):
                allow = exc.headers["Allow"]
            return _error_response(
                405,
                "method_not_allowed",
                "this endpoint supports GET only",
                headers={"Allow": allow},
            )
        if exc.status_code == 404:
            return _error_response(404, "not_found", "no /v1/ route matches the request")
        return _error_response(
            exc.status_code, "http_error", str(getattr(exc, "detail", exc))
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(request: Request, exc: RequestValidationError):
        # Safety net: framework validation failures map to the contract's
        # 400 envelope; FastAPI's default 422 is not part of the contract.
        return _error_response(400, "invalid_parameter", "malformed request parameters")

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        # Runs at the server-error middleware layer (outside the Cache-
        # Control middleware), so this response sets no-store itself.
        logger.exception("unhandled error while serving %s", request.url.path)
        return _error_response(500, "internal_error", "internal server error")

    @app.get("/v1/articles")
    async def list_articles(request: Request):
        _authenticate(request)

        now = now_fn()
        today = now.date()
        event_from = _parse_date_param(
            "event_from", request.query_params.get("event_from"), today
        )
        event_to = _parse_date_param(
            "event_to", request.query_params.get("event_to"), today
        )
        if event_from > event_to:
            raise _ParamError(
                "invalid_date_range",
                f"event_from ({event_from}) is after event_to ({event_to})",
            )
        language = request.query_params.get("language", DEFAULT_LANGUAGE)
        limit = _parse_limit(request.query_params.get("limit"))
        include = _parse_include(request)
        cursor_token = request.query_params.get("cursor")

        result = query_articles(
            config.export_dir,
            event_from=event_from,
            event_to=event_to,
            language=language,
            limit=limit,
            cursor_token=cursor_token,
            include=include,
            freshness_sla_hours=config.settings.freshness_sla_hours,
            now=now,
            cursor_secret=cursor_secret,
        )
        logger.info(
            "GET /v1/articles served generation=%s language=%s total=%d returned=%d",
            result["coverage"]["generation"],
            result["language"],
            result["total_count"],
            result["returned_count"],
        )
        return result

    # Starlette auto-adds HEAD to GET routes; the contract supports GET only
    # (a method other than GET on an existing /v1/ route is a 405).
    for route in app.routes:
        if getattr(route, "path", None) == "/v1/articles" and hasattr(route, "methods"):
            route.methods.discard("HEAD")

    return app
