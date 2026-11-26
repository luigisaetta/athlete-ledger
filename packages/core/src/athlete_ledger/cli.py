"""
Author: L. Saetta
Date Last Modified: 2026-11-26
License: MIT
Description: Local database administration and sync commands with sanitized error
    output; --env-file selects the settings file.
"""  # noqa: D205, D212

import argparse
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import oracledb
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from athlete_ledger import __version__
from athlete_ledger.console import banner, info, ok
from athlete_ledger.db import create_db_engine
from athlete_ledger.db.migrate import check_database, offline_sql, upgrade
from athlete_ledger.errors import LedgerError, database_error_code
from athlete_ledger.locking import BUSY_EXIT_CODE, SyncBusyError
from athlete_ledger.settings import DatabaseSettings, Settings, validation_environment_names
from athlete_ledger.sync_cli import add_sync_commands, emit_failure, run_sync, wants_json
from athlete_ledger.sync_types import ProviderError


def run(
    argv: Sequence[str] | None = None, *, require_oracle: bool = False, show_banner: bool = True
) -> int:
    """Parse a ledger command and report only sanitized diagnostics.

    Args:
        argv: Command-line arguments without the executable name; None reads
            sys.argv[1:].
        require_oracle: Reject SQLite fallback for commands launched by the Oracle setup
            script.
        show_banner: Display the operation heading when stdout is an interactive
            terminal.

    Returns:
        Zero when the command succeeds, one when a handled
        configuration/provider/database error occurs, or 75 when another sync holds the
        database's lock.

    Raises:
        SystemExit: When argparse handles help, version output or invalid command
            syntax.
    """
    parser = argparse.ArgumentParser(prog="athlete-ledger")
    parser.add_argument("--version", action="version", version=f"athlete-ledger {__version__}")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Settings file to read instead of ./.env (use absolute paths inside it)",
    )
    commands = parser.add_subparsers(dest="command")
    add_sync_commands(commands)
    db_parser = commands.add_parser("db", help="Manage the ledger database")
    db_commands = db_parser.add_subparsers(dest="db_command", required=True)
    db_commands.add_parser("upgrade", help="Apply migrations to the configured database")
    sql_parser = db_commands.add_parser("sql", help="Print migration DDL without connecting")
    sql_parser.add_argument("--dialect", choices=("oracle", "sqlite"), default="oracle")
    db_commands.add_parser("check", help="Print backend, server version and schema revision")
    args = parser.parse_args(argv)
    if args.command is None:
        print(f"athlete-ledger {__version__}")
        return 0
    if args.env_file is not None and not args.env_file.is_file():
        emit_failure(args, "Env file not found: --env-file.")
        return 1
    # Without --env-file the settings keep their default ./.env lookup.
    options: dict[str, Any] = {} if args.env_file is None else {"_env_file": args.env_file}
    try:
        if args.command == "db" and args.db_command == "sql":
            print(offline_sql(args.dialect), end="")
            return 0
        if show_banner and not wants_json(args):
            banner(
                "Summary sync"
                if args.command == "sync"
                else ("Database upgrade" if args.db_command == "upgrade" else "Database check")
            )
        settings: DatabaseSettings
        if args.command == "sync" and args.sync_command == "rebuild":
            settings = DatabaseSettings(**options)
        else:
            settings = Settings(**options)
        if require_oracle and settings.backend != "oracle":
            emit_failure(args, "Invalid database settings: ATHLETE_LEDGER_DB_DSN.")
            return 1
        engine = create_db_engine(settings)
        try:
            if args.command == "sync":
                return run_sync(args, engine, settings)
            if args.db_command == "upgrade":
                upgrade(engine)
                ok("Database migrations applied.")
            else:
                status = check_database(engine)
                ok("Database connection verified.")
                print(f"       Backend: {status.backend}")
                print(f"       Server version: {status.server_version}")
                if status.revision:
                    ok(f"Alembic revision: {status.revision}")
                else:
                    info("Alembic revision: unversioned (migrations not yet applied).")
        finally:
            engine.dispose()
    except SyncBusyError as error:
        emit_failure(args, str(error), busy=True)
        return BUSY_EXIT_CODE
    except ValidationError as error:
        names = validation_environment_names(error)
        detail = ": " + ", ".join(names) if names else ""
        emit_failure(args, f"Invalid database settings{detail}.")
        return 1
    except ProviderError as error:
        emit_failure(args, str(error))
        return 1
    except OSError as error:
        emit_failure(args, f"Database operation failed: {type(error).__name__}.")
        return 1
    except (LedgerError, SQLAlchemyError, oracledb.Error) as error:
        code = database_error_code(error)
        detail = f": {code}" if code else ""
        emit_failure(args, f"Database operation failed{detail}.")
        return 1
    except ValueError:
        emit_failure(args, "Database operation failed.")
        return 1
    return 0


def main() -> None:
    """Exit the installed command with the dispatcher return code.

    Raises:
        SystemExit: Always, carrying the command exit status returned by run.
    """
    raise SystemExit(run())
