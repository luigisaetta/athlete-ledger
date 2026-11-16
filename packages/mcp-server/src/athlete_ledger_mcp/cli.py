"""Version-only command reserved for athlete-ledger-mcp."""

from athlete_ledger_mcp import __version__


def main() -> None:
    """Print the package version and exit."""
    print(f"athlete-ledger-mcp {__version__}")
