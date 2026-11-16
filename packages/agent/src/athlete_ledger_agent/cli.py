"""Version-only command reserved for athlete-ledger-agent."""

from athlete_ledger_agent import __version__


def main() -> None:
    """Print the package version and exit."""
    print(f"athlete-ledger-agent {__version__}")
