"""Programmatic Alembic migration entry points used by bootstrap and tests."""

from pathlib import Path

from alembic import command
from alembic.config import Config

from satori.infrastructure.persistence.database import ensure_sqlite_parent


def build_alembic_config(database_url: str, config_path: Path) -> Config:
    """Create an Alembic configuration with an explicit persistence target."""

    resolved_config = config_path.expanduser().resolve()
    if not resolved_config.is_file():
        raise FileNotFoundError(f"Alembic config does not exist: {resolved_config}")
    config = Config(str(resolved_config))
    config.attributes["database_url"] = database_url
    config.attributes["configure_logging"] = False
    return config


def upgrade_database(
    database_url: str,
    *,
    config_path: Path = Path("alembic.ini"),
    revision: str = "head",
) -> None:
    """Upgrade a database to a requested revision."""

    ensure_sqlite_parent(database_url)
    command.upgrade(build_alembic_config(database_url, config_path), revision)


def downgrade_database(
    database_url: str,
    *,
    config_path: Path = Path("alembic.ini"),
    revision: str = "base",
) -> None:
    """Downgrade a database to a requested revision."""

    command.downgrade(build_alembic_config(database_url, config_path), revision)
