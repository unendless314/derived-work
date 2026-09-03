"""
Configuration and binding tests (IMPLEMENTATION_PLAN.md §4, "Configuration
and binding"): unknown keys fail fast (including a stray ``host:`` entry),
the export-root precedence rules hold, and the token env var is mandatory.
"""

import os

import pytest

from modules.api.src import cli
from modules.api.src.config import (
    BIND_ADDRESS,
    DEFAULT_CONFIG_PATH,
    EXPORT_DIR_ENV_VAR,
    WORKSPACE_ROOT,
    ConfigError,
    check_export_root_readable,
    load_config,
    load_settings,
    resolve_export_dir,
    resolve_token,
)
from modules.api.tests import support


def _write_config(tmp_path, text):
    config_path = tmp_path / "api_settings.yaml"
    config_path.write_text(text, encoding="utf-8")
    return config_path


VALID_CONFIG = """
service:
  port: 8010
export:
  export_dir: "data/publish_export"
freshness:
  freshness_sla_hours: 6
auth:
  token_env_var: "EXOPOLITICS_API_TOKEN"
"""


def _env_for(export_dir, token=support.TEST_TOKEN):
    env = {EXPORT_DIR_ENV_VAR: str(export_dir)}
    if token is not None:
        env["EXOPOLITICS_API_TOKEN"] = token
    return env


def test_shipped_config_loads_with_documented_values():
    settings = load_settings(DEFAULT_CONFIG_PATH)
    assert settings.port == 8010
    assert settings.export_dir == "data/publish_export"
    assert settings.freshness_sla_hours == 6
    assert settings.token_env_var == "EXOPOLITICS_API_TOKEN"


def test_unknown_top_level_key_fails(tmp_path):
    config_path = _write_config(tmp_path, VALID_CONFIG + "\nextra_section:\n  x: 1\n")
    with pytest.raises(ConfigError, match="unknown top-level key"):
        load_settings(config_path)


@pytest.mark.parametrize("section,key", [("service", "host"), ("export", "host"), ("auth", "token")])
def test_unknown_section_key_fails(tmp_path, section, key):
    # A stray host: entry (or any unknown key) must fail loudly instead of
    # silently reintroducing a bind-address surface (EXECUTION_POLICY.md §3).
    config_path = _write_config(
        tmp_path, VALID_CONFIG.replace(f"{section}:\n", f"{section}:\n  {key}: 0.0.0.0\n", 1)
    )
    with pytest.raises(ConfigError, match="unknown key"):
        load_settings(config_path)


@pytest.mark.parametrize(
    "text",
    [
        # Review scenario: a first service: block carrying a forbidden host:
        # must fail loudly, not be silently discarded by the later valid
        # block (PyYAML's default last-wins behavior is disabled).
        "service:\n  host: 0.0.0.0\n" + VALID_CONFIG,
        # Same failure in the other order.
        VALID_CONFIG + "\nservice:\n  host: 0.0.0.0\n",
        # Duplicate key inside a single mapping.
        VALID_CONFIG.replace("port: 8010", "port: 8010\n  port: 8020"),
    ],
)
def test_duplicate_yaml_keys_fail_fast(tmp_path, text):
    config_path = _write_config(tmp_path, text)
    with pytest.raises(ConfigError, match="(?i)duplicate"):
        load_settings(config_path)


def test_bind_address_is_a_fixed_constant():
    assert BIND_ADDRESS == "127.0.0.1"
    # No configuration surface: ApiSettings has no host-like field at all.
    import dataclasses

    from modules.api.src.config import ApiSettings

    assert "host" not in {f.name for f in dataclasses.fields(ApiSettings)}
    assert "bind" not in {f.name for f in dataclasses.fields(ApiSettings)}


@pytest.mark.parametrize("port", [0, -1, 65536, "8010", True])
def test_invalid_port_fails(tmp_path, port):
    config_path = _write_config(
        tmp_path, VALID_CONFIG.replace("port: 8010", f"port: {port!r}" if isinstance(port, str) else f"port: {port}")
    )
    with pytest.raises(ConfigError):
        load_settings(config_path)


def test_env_override_takes_precedence(tmp_path):
    export_dir = support.copy_fixture_export(tmp_path)
    settings = load_settings(DEFAULT_CONFIG_PATH)
    assert resolve_export_dir(settings, _env_for(export_dir)) == export_dir


def test_env_override_nonexistent_is_hard_error(tmp_path):
    settings = load_settings(DEFAULT_CONFIG_PATH)
    with pytest.raises(ConfigError, match=EXPORT_DIR_ENV_VAR):
        resolve_export_dir(settings, {EXPORT_DIR_ENV_VAR: str(tmp_path / "missing")})


def test_default_export_dir_resolves_against_workspace_root(tmp_path):
    settings = load_settings(DEFAULT_CONFIG_PATH)
    resolved = resolve_export_dir(settings, {})
    assert resolved == WORKSPACE_ROOT / "data" / "publish_export"


def test_token_env_var_unset_or_empty_fails(tmp_path):
    export_dir = support.copy_fixture_export(tmp_path)
    settings = load_settings(DEFAULT_CONFIG_PATH)
    with pytest.raises(ConfigError, match="EXOPOLITICS_API_TOKEN"):
        resolve_token(settings, _env_for(export_dir, token=None))
    with pytest.raises(ConfigError, match="EXOPOLITICS_API_TOKEN"):
        resolve_token(settings, {EXPORT_DIR_ENV_VAR: str(export_dir), "EXOPOLITICS_API_TOKEN": "   "})


def test_cli_validate_success(tmp_path, monkeypatch, capsys):
    export_dir = support.copy_fixture_export(tmp_path)
    monkeypatch.setenv(EXPORT_DIR_ENV_VAR, str(export_dir))
    monkeypatch.setenv("EXOPOLITICS_API_TOKEN", support.TEST_TOKEN)
    assert cli.main(["validate"]) == 0
    out = capsys.readouterr().out
    assert "Configuration validated successfully." in out
    assert support.GENERATION_CURRENT in out


def test_cli_validate_does_not_require_pointer(tmp_path, monkeypatch, capsys):
    # Bootstrap state: export root exists but current.json does not — a
    # runtime 503 state, not a config error (EXECUTION_POLICY.md §2).
    export_dir = tmp_path / "empty_export"
    export_dir.mkdir()
    monkeypatch.setenv(EXPORT_DIR_ENV_VAR, str(export_dir))
    monkeypatch.setenv("EXOPOLITICS_API_TOKEN", support.TEST_TOKEN)
    assert cli.main(["validate"]) == 0
    assert "503" in capsys.readouterr().out


def test_cli_validate_fails_without_token(tmp_path, monkeypatch, capsys):
    export_dir = support.copy_fixture_export(tmp_path)
    monkeypatch.setenv(EXPORT_DIR_ENV_VAR, str(export_dir))
    monkeypatch.delenv("EXOPOLITICS_API_TOKEN", raising=False)
    assert cli.main(["validate"]) == 1
    assert "CONFIG VALIDATION FAILED" in capsys.readouterr().err


def test_cli_validate_fails_on_bad_config(tmp_path, monkeypatch, capsys):
    export_dir = support.copy_fixture_export(tmp_path)
    monkeypatch.setenv(EXPORT_DIR_ENV_VAR, str(export_dir))
    monkeypatch.setenv("EXOPOLITICS_API_TOKEN", support.TEST_TOKEN)
    config_path = _write_config(tmp_path, "service:\n  host: 0.0.0.0\n")
    assert cli.main(["--config-path", str(config_path), "validate"]) == 1
    assert "CONFIG VALIDATION FAILED" in capsys.readouterr().err


def test_cli_serve_refuses_to_start_without_token(tmp_path, monkeypatch, capsys):
    export_dir = support.copy_fixture_export(tmp_path)
    monkeypatch.setenv(EXPORT_DIR_ENV_VAR, str(export_dir))
    monkeypatch.delenv("EXOPOLITICS_API_TOKEN", raising=False)
    assert cli.main(["serve"]) == 1
    assert "CONFIG VALIDATION FAILED" in capsys.readouterr().err


def test_load_config_full_path(tmp_path):
    export_dir = support.copy_fixture_export(tmp_path)
    resolved = load_config(DEFAULT_CONFIG_PATH, _env_for(export_dir))
    assert resolved.export_dir == export_dir
    assert resolved.token == support.TEST_TOKEN


def _deny_listdir(path):
    raise PermissionError(13, "Permission denied", str(path))


def test_unlistable_export_root_fails_validation(tmp_path, monkeypatch):
    # "Readable" is a capability, not mere existence (review issue): a root
    # the service account cannot list must fail validate at startup instead
    # of surfacing per request as a misleading 503 (unreadable pointer) or
    # 500 (unreadable item file). chmod-based denial is not portable to
    # Windows, so the PermissionError is injected at the exact primitive.
    export_dir = support.copy_fixture_export(tmp_path)
    monkeypatch.setattr(os, "listdir", _deny_listdir)
    with pytest.raises(ConfigError, match="cannot be listed"):
        check_export_root_readable(export_dir)


def test_non_traversable_export_root_fails_validation(tmp_path, monkeypatch):
    # The POSIX traverse-without-read pathology (r without x): os.listdir
    # would succeed but no file inside could be opened, so the metadata
    # gate fails fast first.
    export_dir = support.copy_fixture_export(tmp_path)
    monkeypatch.setattr(os, "access", lambda path, mode: False)
    with pytest.raises(ConfigError, match="not readable and traversable"):
        check_export_root_readable(export_dir)


def test_cli_validate_fails_on_unreadable_export_root(tmp_path, monkeypatch, capsys):
    export_dir = support.copy_fixture_export(tmp_path)
    monkeypatch.setenv(EXPORT_DIR_ENV_VAR, str(export_dir))
    monkeypatch.setenv("EXOPOLITICS_API_TOKEN", support.TEST_TOKEN)
    monkeypatch.setattr(os, "listdir", _deny_listdir)
    assert cli.main(["validate"]) == 1
    assert "CONFIG VALIDATION FAILED" in capsys.readouterr().err
