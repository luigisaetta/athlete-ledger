"""
Author: L. Saetta
Date Last Modified: 2026-11-16
License: MIT
Description: Public exceptions with messages safe for command-line output.
"""  # noqa: D205, D212

import re

from sqlalchemy.exc import StatementError

_DATABASE_CODE = re.compile(r"(?:ORA-[0-9]{5}|DPY-[0-9]{4})")
_DATABASE_CODE_IN_MESSAGE = re.compile(
    r"(?<![A-Za-z0-9_-])(?:ORA-[0-9]{5}|DPY-[0-9]{4})(?![A-Za-z0-9_-])"
)


class SettingsIssue(ValueError):
    """A model-level validation failure with field names separate from values."""

    def __init__(self, message: str, fields: tuple[str, ...]) -> None:
        """Associate a validation failure with setting names independently of their values.

        Args:
            message: Validation explanation containing no input values or credentials.
            fields: Internal Settings field names involved in the failure; the CLI maps
                allowlisted names to environment variables.
        """
        super().__init__(message)
        self.fields = fields


class LedgerError(Exception):
    """Base error for the ledger package."""


class ConfigurationError(LedgerError):
    """Invalid database configuration."""


class DatabaseError(LedgerError):
    """A database operation failed without exposing driver diagnostics."""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        """Retain a safe database failure message and an optional validated error code.

        Args:
            message: Application-controlled explanation, excluding SQL, bound values and
                driver text.
            code: Candidate ORA-nnnnn or DPY-nnnn code; malformed or absent codes are stored
                as None.
        """
        super().__init__(message)
        self.code = code if code is not None and _DATABASE_CODE.fullmatch(code) else None


def database_error_code(error: BaseException) -> str | None:
    """Extract a database code without rendering SQL statements or bound parameters.

    Args:
        error: Database or SQLAlchemy exception to inspect, following
            StatementError.orig wrappers.

    Returns:
        The first allowlisted ORA/DPY code, or None when absent, malformed or hidden
        behind a cyclic/unsupported wrapper.
    """
    seen: set[int] = set()
    while isinstance(error, StatementError):
        seen.add(id(error))
        original = error.orig
        if not isinstance(original, BaseException) or id(original) in seen:
            return None
        error = original
    if isinstance(error, DatabaseError):
        return error.code
    if isinstance(error, LedgerError):
        return None
    # python-oracledb puts an error object (with full_code/message) in args[0].
    # Some drivers/wrappers provide only a message string.
    candidates = (error, *error.args[:1])
    for candidate in candidates:
        code = getattr(candidate, "full_code", None)
        if isinstance(code, str) and _DATABASE_CODE.fullmatch(code):
            return code
    for candidate in candidates:
        message = candidate if isinstance(candidate, str) else getattr(candidate, "message", None)
        if isinstance(message, str):
            match = _DATABASE_CODE_IN_MESSAGE.search(message)
            if match:
                return match.group()
    return None
