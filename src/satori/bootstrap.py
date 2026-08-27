"""Non-activating composition root for persistence bootstrap verification."""

import logging
from pathlib import Path

from sqlalchemy import text

from satori.config import Settings, load_settings
from satori.infrastructure.persistence.database import create_database
from satori.infrastructure.persistence.migrations import upgrade_database
from satori.observability.logging import configure_logging


def bootstrap(
    settings: Settings | None = None,
    *,
    alembic_config: Path = Path("alembic.ini"),
) -> None:
    """Apply migrations and verify persistence connectivity."""

    active_settings = settings or load_settings()
    configure_logging(active_settings.log_level)
    logger = logging.getLogger(__name__)
    logger.info("foundation_bootstrap_started")
    upgrade_database(active_settings.database_url, config_path=alembic_config)
    database = create_database(active_settings.database_url)
    try:
        with database.engine.connect() as connection:
            connection.execute(text("SELECT 1")).scalar_one()
    finally:
        database.dispose()
    logger.info("foundation_bootstrap_completed")
