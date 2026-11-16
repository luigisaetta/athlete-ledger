"""
Author: L. Saetta
Date Last Modified: 2026-11-16
License: MIT
Description: Derive contiguous download coverage and internal gaps from date ranges.
"""  # noqa: D205, D212

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import pairwise

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
