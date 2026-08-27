"""Alembic environment for the local transactional store."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from satori.config import load_settings
from satori.infrastructure.persistence.database import ensure_sqlite_parent
from satori.infrastructure.persistence.models import Base

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logging", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def database_url() -> str:
    """Resolve the programmatic URL first, then normal typed settings."""

    configured = config.attributes.get("database_url")
    if isinstance(configured, str):
        return configured
    return load_settings().database_url


def run_migrations_offline() -> None:
    """Run migrations without constructing an Engine."""

    url = database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations through a short-lived Engine."""

    url = database_url()
    ensure_sqlite_parent(url)
    engine = create_engine(url, poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
