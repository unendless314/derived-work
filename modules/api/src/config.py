"""
Configuration loading and validation for the api module
(EXECUTION_POLICY.md §5).

Settings come from ``config/api_settings.yaml`` (module-owned). The export
root resolves with precedence ``API_PUBLISH_EXPORT_DIR`` env var > config
``export.export_dir`` > built-in default ``data/publish_export`` (the
latter two resolved against the workspace root; DATA_DEPENDENCIES.md §5).
An explicit env override that does not exist is a hard configuration error
— it must never silently fall back to the default. The Bearer token always
comes from the environment (the variable named by ``auth.token_env_var``),
never from YAML.

The bind address is a fixed loopback constant (``BIND_ADDRESS``) enforced
in code (EXECUTION_POLICY.md §3): the YAML schema carries no ``host`` key,
no CLI flag or environment variable overrides it, and unknown YAML keys at
any level are a fail-fast validation error so a stray ``host:`` entry
fails loudly instead of silently taking effect. Duplicate mapping keys are
likewise a fail-fast error: PyYAML's default last-wins behavior would
silently discard one occurrence (``_UniqueKeySafeLoader``).
"""

import dataclasses
import os
import pathlib
from typing import Any, Mapping, Optional

import yaml

WORKSPACE_ROOT = pathlib.Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = (
    pathlib.Path(__file__).resolve().parent.parent / "config" / "api_settings.yaml"
)

# Fixed loopback bind address (EXECUTION_POLICY.md §3). Not configurable.
BIND_ADDRESS = "127.0.0.1"

EXPORT_DIR_ENV_VAR = "API_PUBLISH_EXPORT_DIR"
DEFAULT_EXPORT_DIR = "data/publish_export"
DEFAULT_PORT = 8010
DEFAULT_FRESHNESS_SLA_HOURS = 6
DEFAULT_TOKEN_ENV_VAR = "EXOPOLITICS_API_TOKEN"

_ALLOWED_KEYS = {
    "service": {"port"},
    "export": {"export_dir"},
    "freshness": {"freshness_sla_hours"},
    "auth": {"token_env_var"},
}


class ConfigError(Exception):
    """Any configuration validation failure (fail-fast)."""


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """SafeLoader variant that rejects duplicate mapping keys. PyYAML's
    default silently keeps the last occurrence, which would let a stray
    forbidden key (e.g. a first ``service:`` block carrying ``host:``) be
    discarded by a later block instead of failing fast
    (EXECUTION_POLICY.md §5)."""


def _construct_mapping_unique_keys(loader, node, deep=False):
    seen = []
    for key_node, _value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in seen:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        seen.append(key)
    return yaml.constructor.SafeConstructor.construct_mapping(loader, node, deep=deep)


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping_unique_keys,
)


@dataclasses.dataclass(frozen=True)
class ApiSettings:
    port: int
    export_dir: str
    freshness_sla_hours: int
    token_env_var: str


@dataclasses.dataclass(frozen=True)
class ResolvedConfig:
    settings: ApiSettings
    export_dir: pathlib.Path
    token: str


def _section(raw: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"api_settings.yaml is invalid: '{name}' must be a mapping")
    unknown = set(value) - _ALLOWED_KEYS[name]
    if unknown:
        raise ConfigError(
            f"api_settings.yaml is invalid: unknown key(s) {sorted(unknown)} in "
            f"section '{name}'"
        )
    return value


def _positive_int(value: Any, what: str) -> int:
    # bool is a subclass of int; a YAML `true` is never a valid setting here.
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"api_settings.yaml is invalid: {what} must be a positive integer")
    return value


def _non_empty_str(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"api_settings.yaml is invalid: {what} must be a non-empty string")
    return value


def load_settings(config_path: pathlib.Path = DEFAULT_CONFIG_PATH) -> ApiSettings:
    """Parse and strictly validate api_settings.yaml. Raises ConfigError."""
    if not config_path.is_file():
        raise ConfigError(f"Missing configuration file: {config_path}")
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.load(f, Loader=_UniqueKeySafeLoader)
    except (OSError, yaml.YAMLError) as e:
        raise ConfigError(f"Could not read or parse {config_path}: {e}") from e
    if raw is None or not isinstance(raw, dict):
        raise ConfigError(f"{config_path.name} is invalid: top-level value must be a mapping")
    unknown = set(raw) - set(_ALLOWED_KEYS)
    if unknown:
        raise ConfigError(
            f"api_settings.yaml is invalid: unknown top-level key(s) {sorted(unknown)}"
        )

    service = _section(raw, "service")
    export = _section(raw, "export")
    freshness = _section(raw, "freshness")
    auth = _section(raw, "auth")

    port = _positive_int(service.get("port", DEFAULT_PORT), "service.port")
    if port > 65535:
        raise ConfigError("api_settings.yaml is invalid: service.port must be at most 65535")
    return ApiSettings(
        port=port,
        export_dir=_non_empty_str(export.get("export_dir", DEFAULT_EXPORT_DIR), "export.export_dir"),
        freshness_sla_hours=_positive_int(
            freshness.get("freshness_sla_hours", DEFAULT_FRESHNESS_SLA_HOURS),
            "freshness.freshness_sla_hours",
        ),
        token_env_var=_non_empty_str(auth.get("token_env_var", DEFAULT_TOKEN_ENV_VAR), "auth.token_env_var"),
    )


def resolve_export_dir(
    settings: ApiSettings, env: Optional[Mapping[str, str]] = None
) -> pathlib.Path:
    """
    Resolve the export root per the documented precedence. An explicit
    ``API_PUBLISH_EXPORT_DIR`` override (absolute or cwd-relative) must
    exist and be a directory — otherwise hard ConfigError, never a silent
    fall-back to the default (DATA_DEPENDENCIES.md §5). The config/default
    path is returned unchecked; readability is validated separately by
    ``check_export_root_readable``.
    """
    if env is None:
        env = os.environ
    override = env.get(EXPORT_DIR_ENV_VAR)
    if override is not None and override.strip():
        resolved = pathlib.Path(override.strip())
        if not resolved.is_absolute():
            resolved = pathlib.Path.cwd() / resolved
        if not resolved.is_dir():
            raise ConfigError(
                f"{EXPORT_DIR_ENV_VAR} points to \"{resolved}\", which does not "
                "exist or is not a directory"
            )
        return resolved
    configured = pathlib.Path(settings.export_dir)
    if configured.is_absolute():
        return configured
    return WORKSPACE_ROOT / configured


def check_export_root_readable(export_dir: pathlib.Path) -> None:
    """The export root must exist as a directory the service account can
    actually read and traverse (EXECUTION_POLICY §2: ``validate`` checks
    this; a missing ``current.json`` inside it is a runtime 503 state, not
    a config error). Existence alone is not enough: an unreadable root
    would pass a pure ``is_dir()`` check and then surface per request as a
    misleading 503 (unreadable pointer) or 500 (unreadable item file)."""
    if not export_dir.is_dir():
        raise ConfigError(
            f"Export root {export_dir} does not exist or is not a directory"
        )
    if not os.access(export_dir, os.R_OK | os.X_OK):
        raise ConfigError(
            f"Export root {export_dir} is not readable and traversable by "
            "the service account (needs r+x on the directory)"
        )
    try:
        # Exercise the capability, not just its metadata: os.access can be
        # fooled (ACL/SELinux edge cases, NFS root squash); a real listing
        # cannot.
        os.listdir(export_dir)
    except OSError as e:
        raise ConfigError(f"Export root {export_dir} cannot be listed: {e}") from e


def resolve_token(
    settings: ApiSettings, env: Optional[Mapping[str, str]] = None
) -> str:
    """The Bearer token comes from the environment variable named by
    ``token_env_var``; unset or empty is a hard failure — running without
    authentication is not a supported mode (EXECUTION_POLICY.md §4)."""
    if env is None:
        env = os.environ
    token = env.get(settings.token_env_var)
    if token is None or not token.strip():
        raise ConfigError(
            f"Bearer token environment variable {settings.token_env_var} is "
            "unset or empty; refusing to run without authentication"
        )
    return token


def load_config(
    config_path: pathlib.Path = DEFAULT_CONFIG_PATH,
    env: Optional[Mapping[str, str]] = None,
) -> ResolvedConfig:
    """Full startup configuration: settings + export root + token. Raises
    ConfigError on any failure (``validate`` exits non-zero; ``serve``
    refuses to start)."""
    settings = load_settings(config_path)
    export_dir = resolve_export_dir(settings, env)
    check_export_root_readable(export_dir)
    token = resolve_token(settings, env)
    return ResolvedConfig(settings=settings, export_dir=export_dir, token=token)
