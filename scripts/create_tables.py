#!/usr/bin/env python3
"""
Author: L. Saetta
Date Last Modified: 2026-11-16
License: MIT
Description: Apply ledger migrations to an existing Oracle schema, then verify its revision.

    Run from the repository root with the athlete-ledger Conda environment active.
    Credentials and wallet settings come from the environment and optional .env.
    The owner and ATHLETE_LEDGER_READER role must already exist. No users or roles
    are created, and no SQLcl or Oracle Client installation is needed.
"""  # noqa: D205, D212

import argparse

from athlete_ledger.cli import run
from athlete_ledger.console import banner, ok


def main() -> int:
    """Create/upgrade tables using Alembic and report sanitized database status.

    Returns:
        The operation result.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    banner("Oracle schema setup")
    status = run(["db", "upgrade"], require_oracle=True, show_banner=False)
    if status:
        return status
    status = run(["db", "check"], require_oracle=True, show_banner=False)
    if status:
        return status
    ok("Oracle schema setup completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
