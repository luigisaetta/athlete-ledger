"""Portable document storage and UTC timestamps."""

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
        return dialect.type_descriptor(CLOB() if dialect.name == "oracle" else Text())

    def process_bind_param(self, value: JSONValue | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        if not isinstance(value, dict | list):
            raise ValueError("JSONDocument requires a dict or list.")
        try:
            return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
        except (ValueError, TypeError):
            raise ValueError("Invalid JSON document.") from None

    def process_result_value(self, value: Any, dialect: Dialect) -> JSONValue | None:
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
        if dialect.name == "oracle":
            return dialect.type_descriptor(TIMESTAMP(timezone=False))
        return dialect.type_descriptor(DateTime(timezone=False))

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("UTCDateTime requires a timezone-aware datetime.")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
