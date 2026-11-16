"""
Author: L. Saetta
Date Last Modified: 2026-11-16
License: MIT
Description: Version-only command reserved for athlete-ledger-agent.
"""  # noqa: D205, D212

from athlete_ledger_agent import __version__


def main() -> None:
    """Print the package version and exit."""
    print(f"athlete-ledger-agent {__version__}")
