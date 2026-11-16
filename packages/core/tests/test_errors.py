"""Database diagnostics retain codes, never statement text or bound values."""

import pytest
from athlete_ledger.errors import DatabaseError, database_error_code
from sqlalchemy.exc import OperationalError


@pytest.mark.parametrize(
    "message",
    [
        "ORA-123456 synthetic-secret",
        "DPY-123 synthetic-secret",
        "XORA-01017 synthetic-secret",
        "ORA-01017synthetic-secret",
        "ora-01017 synthetic-secret",
        "synthetic-secret",
    ],
)
def test_database_code_rejects_nonmatching_tokens(message: str) -> None:
    assert database_error_code(Exception(message)) is None


@pytest.mark.parametrize(
    "code", ["ORA-01017 synthetic-secret", "DPY-6005\nsynthetic-secret", "e3q8"]
)
def test_database_error_metadata_is_allowlisted(code: str) -> None:
    assert database_error_code(DatabaseError("safe", code=code)) is None


def test_sqlalchemy_values_are_not_treated_as_diagnostics() -> None:
    error = OperationalError("SELECT 'ORA-01017'", {"password": "DPY-6005"}, Exception("no code"))
    assert database_error_code(error) is None


def test_database_error_handles_cyclic_wrapper_without_rendering() -> None:
    error = OperationalError("synthetic-private-sql", {}, Exception("no code"))
    error.orig = error
    assert database_error_code(error) is None
