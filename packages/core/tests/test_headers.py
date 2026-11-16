"""
Author: L. Saetta
Date Last Modified: 2026-11-16
License: MIT
Description: Enforce metadata headers on every tracked Python file.
"""  # noqa: D205, D212

import ast
import re
import subprocess
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_tracked_python_headers() -> None:
    """Verify author, ISO modification date, license and description headers."""
    names = (
        subprocess.check_output(["git", "-C", str(ROOT), "ls-files", "-z", "*.py"])
        .decode()
        .split("\0")
    )
    for name in filter(None, names):
        source = (ROOT / name).read_text()
        lines = source.splitlines()
        offset = 1 if source.startswith("#!") else 0
        assert lines[offset] == '"""', name
        doc = ast.get_docstring(ast.parse(source))
        assert doc is not None, name
        assert doc.startswith("Author: L. Saetta\n"), name
        assert "\nLicense: MIT\n" in doc, name
        match = re.search(r"^Date Last Modified: (\d{4}-\d{2}-\d{2})$", doc, re.MULTILINE)
        assert match, name
        date.fromisoformat(match.group(1))
        assert re.search(r"^Description: \S", doc, re.MULTILINE), name
