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
    """Apply ANSI styling only when terminal capabilities and NO_COLOR permit it.

    Args:
        text: Label or banner text to decorate.
        color: ANSI SGR parameters, such as 36 for cyan or 1;32 for bold green.

    Returns:
        The text wrapped in ANSI style/reset sequences, or the unchanged text when
        colors are disabled.
    """
    if sys.stdout.isatty() and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb":
        return f"\033[{color}m{text}\033[0m"
    return text


def banner(title: str) -> None:
    """Print an interactive heading with the package name, version and operation title.

    Args:
        title: Human-readable operation title shown below the package version; supply no
            secrets.
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
    """Print a completed-step message with a green OK label when color is enabled.

    Args:
        message: Success description or aggregate counts safe to display on stdout.
    """
    print(f"  {_style('[OK]', '1;32')} {message}")


def info(message: str) -> None:
    """Print a neutral status message without marking it as a successful check.

    Args:
        message: Non-sensitive state information, such as an unversioned schema notice.
    """
    print(f"  {_style('[INFO]', '36')} {message}")
