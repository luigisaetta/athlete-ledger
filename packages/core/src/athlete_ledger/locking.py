"""
Author: L. Saetta
Date Last Modified: 2026-11-26
License: MIT
Description: Exclusive per-database file lock that keeps two syncs or rebuilds from
    running at once on one machine.
"""  # noqa: D205, D212

import hashlib
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import IO

from sqlalchemy.engine import make_url

from athlete_ledger.errors import LedgerError
from athlete_ledger.settings import DatabaseSettings

BUSY_EXIT_CODE = 75
BUSY_MESSAGE = "Another sync or rebuild is already running for this database."


class SyncBusyError(LedgerError):
    """Another process holds the sync lock of the same database."""


def lock_path(settings: DatabaseSettings) -> Path:
    """Return the lock file of the configured database.

    The name holds a short hash of the database identity (the Oracle DSN, or the resolved
    SQLite file), never the identity itself.

    Args:
        settings: Database configuration.

    Returns:
        The lock file under $XDG_CACHE_HOME/athlete-ledger (default ~/.cache).
    """
    if settings.backend == "oracle":
        identity = f"oracle:{settings.db_dsn}"
    else:
        database = make_url(settings.sqlite_url).database or ""
        identity = f"sqlite:{Path(database).resolve()}"
    key = hashlib.sha256(identity.encode()).hexdigest()[:16]
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return cache / "athlete-ledger" / f"sync-{key}.lock"


def _acquire(handle: IO[str]) -> None:
    """Take an exclusive lock without waiting.

    Args:
        handle: The open lock file.

    Raises:
        OSError: If another process holds the lock.
    """
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release(handle: IO[str]) -> None:
    """Release the lock taken by _acquire.

    Args:
        handle: The open lock file.
    """
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def sync_lock(settings: DatabaseSettings) -> Iterator[None]:
    """Hold the database's sync lock for the duration of the block.

    The operating system releases the lock when the process ends, also after a crash.

    Args:
        settings: Database configuration that identifies the lock.

    Yields:
        None while the lock is held.

    Raises:
        SyncBusyError: If another process already holds the lock.
    """
    path = lock_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a+", encoding="utf-8") as handle:
        try:
            _acquire(handle)
        except OSError:
            raise SyncBusyError(BUSY_MESSAGE) from None
        try:
            yield
        finally:
            _release(handle)
