"""Small terminal presentation helpers without external dependencies."""

import os
import sys

from athlete_ledger import __version__


def _style(text: str, color: str) -> str:
    """Use ANSI colors only on a capable terminal, respecting NO_COLOR."""
    if sys.stdout.isatty() and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb":
        return f"\033[{color}m{text}\033[0m"
    return text


def banner(title: str) -> None:
    """Show a compact heading on terminals; keep redirected output concise."""
    if not sys.stdout.isatty():
        return
    width = 54
    border = "  +" + "-" * width + "+"
    print()
    print(_style(border, "36"))
    print(_style("  |" + f"  ATHLETE LEDGER  /  v{__version__}".ljust(width) + "|", "1;36"))
    print(_style("  |" + f"  {title}".ljust(width) + "|", "36"))
    print(_style(border, "36"))
    print()


def ok(message: str) -> None:
    """Mark a completed operation, coloring only the success label."""
    print(f"  {_style('[OK]', '1;32')} {message}")


def info(message: str) -> None:
    """Report neutral state without suggesting a test has passed."""
    print(f"  {_style('[INFO]', '36')} {message}")
