"""Public exceptions with messages safe for command-line output."""


class LedgerError(Exception):
    """Base error for the ledger package."""


class ConfigurationError(LedgerError):
    """Invalid database configuration."""


class DatabaseError(LedgerError):
    """A database operation failed without exposing driver diagnostics."""
