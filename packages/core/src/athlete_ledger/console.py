"""
Author: L. Saetta
Date Last Modified: 2026-11-16
License: MIT
Description: Small terminal presentation helpers without external dependencies.
"""  # noqa: D205, D212

import os
import sys

from athlete_ledger import __version__


def _style(text: str, color: str) -> str:
    """Use ANSI colors only on a capable terminal, respecting NO_COLOR.

    Args:
        text: Text for this operation.
        color: Color for this operation.

    Returns:
        The operation result.
    """
    if sys.stdout.isatty() and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb":
        return f"\033[{color}m{text}\033[0m"
    return text


def banner(title: str) -> None:
    """Show a compact heading on terminals; keep redirected output concise.

    Args:
        title: Title for this operation.
    """
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
    """Mark a completed operation, coloring only the success label.

    Args:
        message: Message for this operation.
    """
    print(f"  {_style('[OK]', '1;32')} {message}")


def info(message: str) -> None:
    """Report neutral state without suggesting a test has passed.

    Args:
        message: Message for this operation.
    """
    print(f"  {_style('[INFO]', '36')} {message}")
