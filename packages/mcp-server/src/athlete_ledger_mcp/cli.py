"""
Author: L. Saetta
Date Last Modified: 2026-11-16
License: MIT
Description: Start the database-only MCP stdio server with private stderr diagnostics.
"""  # noqa: D205, D212

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import oracledb
from athlete_ledger.errors import ConfigurationError, SettingsIssue, database_error_code
from opentelemetry import trace
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from athlete_ledger_mcp import __version__
from athlete_ledger_mcp.database import MCPSettings, create_reader_engine
from athlete_ledger_mcp.server import create_server


class ProtocolParser(argparse.ArgumentParser):
    """Send even help/version diagnostics to stderr, preserving protocol-only stdout."""

    def _print_message(self, message: str | None, file: Any = None) -> None:
        """Write parser-generated help and diagnostics to stderr only.

        Args:
            message: Parser-generated text for help, version or syntax diagnostics.
            file: Argparse's suggested stream, deliberately replaced by stderr.
        """
        if message:
            sys.stderr.write(message)


def configuration_names(error: ValidationError) -> str:
    """Map reader validation failures to allowlisted environment names without values.

    Args:
        error: Settings validation failure whose input/context must not be rendered.

    Returns:
        A colon-prefixed list of recognized setting names, or an empty string.
    """
    fields: set[str] = set()
    for detail in error.errors(include_input=False, include_url=False):
        loc = detail["loc"]
        if loc and isinstance(loc[0], str) and loc[0] in MCPSettings.model_fields:
            fields.add(loc[0])
        issue = detail.get("ctx", {}).get("error")
        if isinstance(issue, SettingsIssue):
            fields.update(name for name in issue.fields if name in MCPSettings.model_fields)
    names = sorted(f"ATHLETE_LEDGER_{name.upper()}" for name in fields)
    return ": " + ", ".join(names) if names else ""


def run(argv: Sequence[str] | None = None) -> int:
    """Validate reader settings/connectivity, then run only the official stdio transport.

    Args:
        argv: CLI arguments without the executable name; None uses process arguments.

    Returns:
        Zero after clean protocol shutdown or one after a sanitized startup/runtime failure.

    Raises:
        SystemExit: When argparse handles help, version or invalid command syntax.
    """
    parser = ProtocolParser(prog="athlete-ledger-mcp")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--version", action="version", version=f"athlete-ledger-mcp {__version__}")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr, format="%(message)s")
    for namespace in ("mcp", "sqlalchemy", "oracledb"):
        logger = logging.getLogger(namespace)
        logger.handlers = [logging.NullHandler()]
        logger.propagate = False
    trace.set_tracer_provider(trace.NoOpTracerProvider())
    try:
        settings_options: dict[str, Any] = {"_env_file": args.env_file}
        settings = MCPSettings(**settings_options)
        engine = create_reader_engine(settings)
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1 FROM DUAL" if settings.db_dsn else "SELECT 1"))
            server = create_server(
                engine, include_activity_names=settings.mcp_include_activity_names
            )
            server.run(transport="stdio")
        finally:
            engine.dispose()
    except ValidationError as error:
        print(f"Invalid database settings{configuration_names(error)}.", file=sys.stderr)
        return 1
    except ConfigurationError:
        print(
            "Invalid database settings: existing database and reader settings required.",
            file=sys.stderr,
        )
        return 1
    except OSError as error:
        print(f"Database operation failed: {type(error).__name__}.", file=sys.stderr)
        return 1
    except (SQLAlchemyError, oracledb.Error) as error:
        code = database_error_code(error)
        print(f"Database operation failed{': ' + code if code else ''}.", file=sys.stderr)
        return 1
    except Exception:
        # SDK/runtime failures must never emit arbitrary exception text or a traceback.
        print("MCP server failed.", file=sys.stderr)
        return 1
    return 0


def main() -> None:
    """Exit with the stdio server's sanitized startup/shutdown result.

    Raises:
        SystemExit: Always, carrying the status returned by run.
    """
    raise SystemExit(run())
