"""
Author: L. Saetta
Date Last Modified: 2026-11-16
License: MIT
Description: Run migrations with an explicitly supplied connection or offline dialect.
"""  # noqa: D205, D212

from alembic import context
from sqlalchemy.engine import Connection

from athlete_ledger.db.models import Base

config = context.config

if context.is_offline_mode():
    context.configure(
        dialect_name=config.attributes["dialect_name"],
        target_metadata=Base.metadata,
        literal_binds=True,
        compare_type=True,
    )
    # SQLcl scripts cannot bind Oracle's implicit INSERT RETURNING output.
    # This dialect instance belongs only to the offline migration context.
    context.get_context().dialect.insert_returning = False
    with context.begin_transaction():
        context.run_migrations()
else:
    connection = config.attributes.get("connection")
    if not isinstance(connection, Connection):
        raise RuntimeError("Migrations require an explicit database connection.")
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()
