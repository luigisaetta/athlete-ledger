"""Version-only command reserved for athlete-ledger."""

from athlete_ledger import __version__


def main() -> None:
    """Print the package version and exit."""
    print(f"athlete-ledger {__version__}")
