"""Local database administration commands with sanitized error output."""

import argparse
import sys
from collections.abc import Sequence

import oracledb
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from athlete_ledger import __version__
from athlete_ledger.console import banner, info, ok
from athlete_ledger.db import create_db_engine
from athlete_ledger.db.migrate import check_database, offline_sql, upgrade
from athlete_ledger.errors import LedgerError, database_error_code
from athlete_ledger.settings import Settings, validation_environment_names


def run(
    argv: Sequence[str] | None = None, *, require_oracle: bool = False, show_banner: bool = True
) -> int:
    """Execute a command, returning an exit status without printing driver errors."""
    parser = argparse.ArgumentParser(prog="athlete-ledger")
    parser.add_argument("--version", action="version", version=f"athlete-ledger {__version__}")
    commands = parser.add_subparsers(dest="command")
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
    try:
        if args.db_command == "sql":
            print(offline_sql(args.dialect), end="")
            return 0
        if show_banner:
            banner("Database upgrade" if args.db_command == "upgrade" else "Database check")
        settings = Settings()
        if require_oracle and settings.backend != "oracle":
            print("Invalid database settings: ATHLETE_LEDGER_DB_DSN.", file=sys.stderr)
            return 1
        engine = create_db_engine(settings)
        try:
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
    except ValidationError as error:
        names = validation_environment_names(error)
        detail = ": " + ", ".join(names) if names else ""
        print(f"Invalid database settings{detail}.", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"Database operation failed: {type(error).__name__}.", file=sys.stderr)
        return 1
    except (LedgerError, SQLAlchemyError, oracledb.Error) as error:
        code = database_error_code(error)
        detail = f": {code}" if code else ""
        print(f"Database operation failed{detail}.", file=sys.stderr)
        return 1
    except ValueError:
        print("Database operation failed.", file=sys.stderr)
        return 1
    return 0


def main() -> None:
    """Dispatch the installed command."""
    raise SystemExit(run())
