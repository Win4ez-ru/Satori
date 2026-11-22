"""Explicit isolated installation paths for CLI tests; never reuse a developer's floor."""

import tempfile
from pathlib import Path

from sqlalchemy.engine import make_url

from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.models.source_policy import SourcePolicyBindingRow
from satori.infrastructure.source_policy.installation import enroll_installation


def policy_directory(database_url: str) -> Path:
    database = make_url(database_url).database
    if database and database != ":memory:":
        return Path(database).absolute().parent / "source-policy"
    return Path(tempfile.mkdtemp(prefix="satori-test-policy-")) / "floor"


def enroll_for_cli(database: Database) -> Path:
    """Explicitly review an already seeded, isolated pre-policy CLI fixture once."""
    directory = policy_directory(str(database.engine.url))
    with database.session_factory() as session:
        already_bound = session.query(SourcePolicyBindingRow).first() is not None
    if not already_bound:
        enroll_installation(database, directory, reviewed=True, new_installation=True)
    return directory
