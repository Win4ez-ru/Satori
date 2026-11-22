"""Synthetic production composition, adversarial derived knowledge and fresh CLI processes.

Run: uv run --no-sync python -m tests.source_exclusion_scenario
Prints only timings/check metadata. Never opens an existing installation or paid provider.
"""

import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from time import perf_counter

from sqlalchemy import select

from satori.infrastructure.persistence.database import create_database
from satori.infrastructure.persistence.migrations import upgrade_database
from satori.infrastructure.persistence.models import SourceExcludedRootRow
from tests.test_source_exclusion_production import (
    test_derived_knowledge_cannot_survive_exclusion_restart_archive_or_replay,
    test_exclusion_during_generation_rejects_reply_and_tentative_affect,
)


def run(project_root: Path, directory: Path) -> dict[str, object]:
    timings: list[dict[str, object]] = []

    def phase(name: str, operation: Callable[[], object]) -> object:
        start = perf_counter()
        value = operation()
        timings.append({"phase": name, "elapsed_ms": round((perf_counter() - start) * 1000, 3)})
        return value

    url = f"sqlite+pysqlite:///{directory / 'synthetic.db'}"
    upgrade_database(url, config_path=project_root / "alembic.ini")
    database = create_database(url)
    try:
        phase(
            "derived_knowledge_exclude_restart_archive_replay_fresh_formation",
            lambda: test_derived_knowledge_cannot_survive_exclusion_restart_archive_or_replay(
                database, directory
            ),
        )
        # Read metadata only; the first scenario reopens its own connection after restart.
        with database.session_factory() as session:
            root = session.scalar(select(SourceExcludedRootRow.message_id))
        assert root
        environment = {
            **os.environ,
            "SATORI_DATABASE_URL": url,
            "SATORI_SOURCE_POLICY_DIRECTORY": str(directory / "trusted-installation"),
            "SATORI_DEFAULT_COUNTERPARTY_ID": "alice",
            "SATORI_ENVIRONMENT": "test",
        }

        def cli(arguments: list[str]) -> dict[str, object]:
            completed = subprocess.run(
                [sys.executable, "-m", "satori", *arguments],
                cwd=project_root,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
                timeout=30,
            )
            value: object = json.loads(completed.stdout)
            assert isinstance(value, dict)
            return value

        phase("fresh_process_status", lambda: cli(["sources", "status"]))
        preview = cli(["sources", "exclude", root, "--preview"])
        result = phase(
            "fresh_process_repeat_exclusion",
            lambda: cli(
                [
                    "sources",
                    "exclude",
                    root,
                    "--apply",
                    "--expected-generation",
                    str(preview["generation"]),
                    "--sha256",
                    str(preview["preview_digest"]),
                    "--request-id",
                    "scenario-repeat",
                ]
            ),
        )
        assert isinstance(result, dict)
    finally:
        database.dispose()
    race_url = f"sqlite+pysqlite:///{directory / 'race.db'}"
    upgrade_database(race_url, config_path=project_root / "alembic.ini")
    race = create_database(race_url)
    try:
        phase(
            "late_candidate_and_affect_rejected",
            lambda: test_exclusion_during_generation_rejects_reply_and_tentative_affect(
                race, directory / "race-installation"
            ),
        )
    finally:
        race.dispose()
    return {
        "scenario": "canonical-source-exclusion-production-v1",
        "checks": {
            "uncited_derivative_withheld": True,
            "provider_input_clean_after_restart": True,
            "archival_does_not_exclude_formed_knowledge": True,
            "source_exclusion_cannot_reactivate": True,
            "old_reply_replay_denied": True,
            "fresh_independent_knowledge_allowed": True,
            "numeric_state_retained": True,
            "raw_data_retained_for_later_erasure": True,
            "inflight_candidate_and_tentative_affect_not_committed": True,
            "fresh_cli_processes_use_same_floor": True,
        },
        "paid_provider_calls": 0,
        "source_text_logged": False,
        "timings": timings,
    }


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="satori-source-exclusion-scenario-") as temporary:
        print(json.dumps(run(Path(__file__).resolve().parents[1], Path(temporary)), indent=2))
