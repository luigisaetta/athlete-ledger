"""
Author: L. Saetta
Date Last Modified: 2026-11-16
License: MIT
Description: Derive contiguous download coverage and internal gaps from date ranges.
"""  # noqa: D205, D212

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import pairwise

from sqlalchemy import Engine, select

from athlete_ledger.db.models import RawResponse
from athlete_ledger.sync_types import DATASETS, ENDPOINTS, PROVIDER

DateRange = tuple[date, date]


@dataclass(frozen=True)
class Coverage:
    """Merged inclusive download intervals and the inclusive gaps between them."""

    intervals: tuple[DateRange, ...]
    gaps: tuple[DateRange, ...]


def merge_coverage(ranges: Iterable[DateRange]) -> Coverage:
    """Merge overlapping or adjacent ranges and identify only internal gaps.

    Args:
        ranges: Inclusive start/end pairs in any order, possibly repeated.

    Returns:
        Sorted disjoint intervals and their intervening uncovered ranges. Empty
        input produces no intervals or gaps; exterior dates are not inferred.

    Raises:
        ValueError: If any range ends before it starts.
    """
    merged: list[DateRange] = []
    for start, end in sorted(ranges):
        if end < start:
            raise ValueError("Coverage range must not end before it starts.")
        if merged and (start - merged[-1][1]).days <= 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    gaps = tuple(
        (left[1] + timedelta(days=1), right[0] - timedelta(days=1))
        for left, right in pairwise(merged)
    )
    return Coverage(tuple(merged), gaps)


def sync_coverage(engine: Engine, datasets: Sequence[str] = DATASETS) -> dict[str, Coverage]:
    """Read distinct committed chunk boundaries for each requested dataset.

    Args:
        engine: Ledger database engine; only SELECT statements are executed.
        datasets: Supported dataset names to inspect, defaulting to all four areas.

    Returns:
        Download intervals and internal gaps keyed by dataset. Responses with a
        missing boundary, other providers and other endpoints are excluded.
        Only range_start and range_end are selected; no JSON columns are loaded.

    Raises:
        KeyError: If a dataset has no entry in the endpoint mapping.
        ValueError: If a stored range ends before it starts.
        sqlalchemy.exc.SQLAlchemyError: If the database query fails.
    """
    result: dict[str, Coverage] = {}
    with engine.connect() as connection:
        for dataset in dict.fromkeys(datasets):
            statement = (
                select(RawResponse.range_start, RawResponse.range_end)
                .distinct()
                .where(
                    RawResponse.provider == PROVIDER,
                    RawResponse.endpoint == ENDPOINTS[dataset],
                    RawResponse.range_start.is_not(None),
                    RawResponse.range_end.is_not(None),
                )
            )
            ranges = [
                (start, end)
                for start, end in connection.execute(statement)
                if start is not None and end is not None
            ]
            result[dataset] = merge_coverage(ranges)
    return result
