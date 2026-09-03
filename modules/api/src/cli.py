"""
Command-line interface (EXECUTION_POLICY.md §2):

- ``python -m modules.api.src.cli validate`` — loads configuration,
  validates it, checks that the export root is readable, and exits
  non-zero on any failure. Does not require ``current.json`` to exist
  (bootstrap is a runtime ``503`` state, not a config error).
- ``python -m modules.api.src.cli serve`` — starts uvicorn with the
  FastAPI app, bound to the fixed loopback address on the configured port.

The bind address is the fixed ``BIND_ADDRESS`` constant from
``config.py``; there is deliberately no flag, key, or env var for it
(EXECUTION_POLICY.md §3).
"""

import argparse
import logging
import pathlib
import sys
from typing import List, Optional

from .config import (
    BIND_ADDRESS,
    DEFAULT_CONFIG_PATH,
    ConfigError,
    ResolvedConfig,
    load_config,
)
from .export_pointer import PointerError, read_pointer, resolve_generation_root

logger = logging.getLogger("api.cli")


def _load_or_report(config_path: pathlib.Path) -> Optional[ResolvedConfig]:
    try:
        return load_config(config_path)
    except ConfigError as e:
        print(f"CONFIG VALIDATION FAILED: {e}", file=sys.stderr)
        return None


def _pointer_status_line(config: ResolvedConfig) -> str:
    """Best-effort pointer resolution for operator visibility. A missing or
    invalid pointer is a runtime 503 state, never a config failure."""
    try:
        pointer = read_pointer(config.export_dir)
        resolve_generation_root(config.export_dir, pointer)
    except (PointerError, FileNotFoundError) as e:
        return f"no valid pointer currently resolvable (requests will serve 503): {e}"
    return f"pointer resolvable; serving generation {pointer.generation}"


def cmd_validate(config_path: pathlib.Path) -> int:
    config = _load_or_report(config_path)
    if config is None:
        return 1
    print(f"Export root: {config.export_dir}")
    print(f"Pointer: {_pointer_status_line(config)}")
    print("Configuration validated successfully.")
    return 0


def cmd_serve(config_path: pathlib.Path) -> int:
    config = _load_or_report(config_path)
    if config is None:
        return 1
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logger.info("Export root: %s", config.export_dir)
    logger.info("Binding: %s:%d (fixed loopback)", BIND_ADDRESS, config.settings.port)
    logger.info("Startup pointer check: %s", _pointer_status_line(config))
    # Imported lazily so `validate` runs without the web stack present.
    import uvicorn

    from .app import create_app

    uvicorn.run(
        create_app(config),
        host=BIND_ADDRESS,
        port=config.settings.port,
        log_config=None,
    )
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="modules.api.src.cli",
        description="API module CLI (read-only query layer over publish exports)",
    )
    parser.add_argument(
        "--config-path",
        type=pathlib.Path,
        default=DEFAULT_CONFIG_PATH,
        help="Path to the api_settings.yaml configuration file",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate", help="Validate configuration and export root")
    subparsers.add_parser("serve", help="Start the API service (fixed loopback binding)")
    args = parser.parse_args(argv)

    if args.command == "validate":
        return cmd_validate(args.config_path)
    return cmd_serve(args.config_path)


if __name__ == "__main__":
    sys.exit(main())
