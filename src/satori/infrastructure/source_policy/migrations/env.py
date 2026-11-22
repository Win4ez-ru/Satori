"""Independent Alembic stream; deliberately never imports main-state models/settings."""

from alembic import context

configuration = context.config
connection = configuration.attributes.get("connection")
if connection is None:
    raise RuntimeError("policy_migration_requires_bound_connection")
context.configure(connection=connection, target_metadata=None)
with context.begin_transaction():
    context.run_migrations()
