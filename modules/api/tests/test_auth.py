"""
Authentication tests (IMPLEMENTATION_PLAN.md §4, "Authentication";
EXECUTION_POLICY.md §4): Bearer token required on all /v1/ endpoints,
constant-time comparison, 401 + WWW-Authenticate on failure.
"""

from fastapi.testclient import TestClient

from modules.api.src.app import create_app
from modules.api.tests import support


def _client(tmp_path, token=support.TEST_TOKEN):
    export_dir = support.copy_fixture_export(tmp_path)
    return TestClient(create_app(support.make_resolved_config(export_dir, token)))


def test_missing_token_is_401(tmp_path):
    response = _client(tmp_path).get("/v1/articles")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "unauthorized"


def test_wrong_token_is_401(tmp_path):
    response = _client(tmp_path).get(
        "/v1/articles", headers={"Authorization": "Bearer wrong-token"}
    )
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_non_bearer_scheme_is_401(tmp_path):
    response = _client(tmp_path).get(
        "/v1/articles", headers={"Authorization": f"Token {support.TEST_TOKEN}"}
    )
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_bearer_without_credentials_is_401(tmp_path):
    response = _client(tmp_path).get("/v1/articles", headers={"Authorization": "Bearer"})
    assert response.status_code == 401


def test_correct_token_passes(tmp_path):
    response = _client(tmp_path).get(
        "/v1/articles", headers={"Authorization": f"Bearer {support.TEST_TOKEN}"}
    )
    assert response.status_code == 200


def test_token_with_lookalike_prefix_is_401(tmp_path):
    response = _client(tmp_path).get(
        "/v1/articles", headers={"Authorization": f"Bearer {support.TEST_TOKEN}x"}
    )
    assert response.status_code == 401
