"""
Author: L. Saetta
Date Last Modified: 2026-11-16
License: MIT
Description: Define safe sync metadata, counters and provider interfaces.
"""  # noqa: D205, D212

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Protocol

from athlete_ledger.errors import LedgerError

DATASETS = ("activities", "sleep", "hrv", "stress")
PROVIDER = "garmin_connect"
ENDPOINTS = {
    "activities": "search_activities",
    "sleep": "get_daily_sleep_summaries",
    "hrv": "get_daily_hrv_summaries",
    "stress": "get_daily_stress",
}


class ProviderError(LedgerError):
    """A provider failure whose message contains only safe diagnostics."""


class UnexpectedResponseError(ProviderError):
    """A response cannot be safely interpreted for the requested endpoint."""


@dataclass(frozen=True)
class Response:
    """One raw response with the exact parameters used to fetch it."""

    endpoint: str
    params: dict[str, Any]
    payload: Any = field(repr=False)


class Provider(Protocol):
    """A stream of responses; implementations must sanitize transport errors."""

    request_count: int
    sdk_version: str

    def responses(self, dataset: str, start: date, end: date) -> Iterator[Response]:
        """Yield responses for one inclusive chunk.

        Args:
            dataset: One supported dataset.
            start: First calendar date.
            end: Last calendar date.

        Yields:
            Pages in request order.
        """
        ...

    def close(self) -> None:
        """Release transport resources."""
        ...


def date_chunks(start: date, end: date) -> Iterator[tuple[date, date]]:
    """Split an inclusive range into windows of at most seven days.

    Args:
        start: Inclusive first date.
        end: Inclusive last date.

    Yields:
        Inclusive chunk boundaries in increasing date order.

    Raises:
        ValueError: If the range is reversed.
    """
    if start > end:
        raise ValueError("Start date must not exceed end date.")
    current = start
    while current <= end:
        last = min(current + timedelta(days=6), end)
        yield current, last
        if last == end:
            break
        current = last + timedelta(days=1)
