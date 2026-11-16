"""
Author: L. Saetta
Date Last Modified: 2026-11-16
License: MIT
Description: Portable document storage and UTC timestamps.
"""  # noqa: D205, D212

import json
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import CLOB, DateTime, Text
from sqlalchemy.dialects.oracle import TIMESTAMP
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator, TypeEngine

JSONValue = dict[str, Any] | list[Any]


class JSONDocument(TypeDecorator[JSONValue]):
    """JSON documents as text, backed by constrained CLOB columns on Oracle.

    Oracle-only IS JSON constraints are part of the table definition. None maps
    to SQL NULL, rather than the JSON literal null. Replace documents as a whole;
    in-place mutations are not tracked by the ORM.
    """

    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        """Handle load dialect impl.

        Args:
            dialect: Dialect for this operation.

        Returns:
            The operation result.
        """
        return dialect.type_descriptor(CLOB() if dialect.name == "oracle" else Text())

    def process_bind_param(self, value: JSONValue | None, dialect: Dialect) -> str | None:
        """Handle process bind param.

        Args:
            value: Value for this operation.
            dialect: Dialect for this operation.

        Returns:
            The operation result.

        Raises:
            ValueError: If the operation cannot be completed.
        """
        if value is None:
            return None
        if not isinstance(value, dict | list):
            raise ValueError("JSONDocument requires a dict or list.")
        try:
            return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
        except (ValueError, TypeError):
            raise ValueError("Invalid JSON document.") from None

    def process_result_value(self, value: Any, dialect: Dialect) -> JSONValue | None:
        """Handle process result value.

        Args:
            value: Value for this operation.
            dialect: Dialect for this operation.

        Returns:
            The operation result.

        Raises:
            ValueError: If the operation cannot be completed.
        """
        if value is None:
            return None
        # Drivers can decode columns marked IS JSON before the SQLAlchemy hook.
        decoded = json.loads(value) if isinstance(value, str | bytes) else value
        if not isinstance(decoded, dict | list):
            raise ValueError("Stored JSON document must be a dict or list.")
        return cast(JSONValue, decoded)


class UTCDateTime(TypeDecorator[datetime]):
    """Accept aware datetimes, store naive UTC, return aware UTC on every backend."""

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        """Handle load dialect impl.

        Args:
            dialect: Dialect for this operation.

        Returns:
            The operation result.
        """
        if dialect.name == "oracle":
            return dialect.type_descriptor(TIMESTAMP(timezone=False))
        return dialect.type_descriptor(DateTime(timezone=False))

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """Handle process bind param.

        Args:
            value: Value for this operation.
            dialect: Dialect for this operation.

        Returns:
            The operation result.

        Raises:
            ValueError: If the operation cannot be completed.
        """
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("UTCDateTime requires a timezone-aware datetime.")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        """Handle process result value.

        Args:
            value: Value for this operation.
            dialect: Dialect for this operation.

        Returns:
            The operation result.
        """
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
