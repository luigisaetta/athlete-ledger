"""Local database administration commands with sanitized error output."""

import argparse
import sys
from collections.abc import Sequence

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from athlete_ledger import __version__
from athlete_ledger.db import create_db_engine
from athlete_ledger.db.migrate import check_database, offline_sql, upgrade
from athlete_ledger.errors import LedgerError
from athlete_ledger.settings import Settings


def run(argv: Sequence[str] | None = None) -> int:
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
        engine = create_db_engine(Settings())
        try:
            if args.db_command == "upgrade":
                upgrade(engine)
                print("Database migrations applied.")
            else:
                status = check_database(engine)
                print(f"Backend: {status.backend}")
                print(f"Server version: {status.server_version}")
                print(f"Alembic revision: {status.revision or 'unversioned'}")
        finally:
            engine.dispose()
    except ValidationError:
        print(
            "Invalid database settings; check the documented environment variables.",
            file=sys.stderr,
        )
        return 1
    except (LedgerError, SQLAlchemyError, OSError, ValueError):
        # Driver and validation diagnostics may contain credentials or bound data.
        print(
            "Database operation failed; check configuration and schema privileges.", file=sys.stderr
        )
        return 1
    return 0


def main() -> None:
    """Dispatch the installed command."""
    raise SystemExit(run())
