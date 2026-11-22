"""Run a synthetic production-composition/CLI scope scenario, with private metadata only.

Usage: uv run --no-sync python -m tests.memory_scope_scenario [--directory NEW_DIRECTORY]
The scenario creates only new disposable databases. Every generation adapter is a capturing fake;
no Character quality verdict or real-provider latency is inferred from these measurements.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from sqlalchemy import text

from satori.application.conversation.errors import (
    ConversationSessionNotFound,
    InteractionIdempotencyConflict,
)
from satori.infrastructure.artifacts import PrivateArtifactStore
from satori.infrastructure.persistence.database import create_database
from satori.infrastructure.persistence.migrations import upgrade_database
from tests.test_memory_scope_integration import (
    ALICE_MARKER,
    BOB_MARKER,
    ScopeProbe,
    _assert_own_recall,
    _assert_quarantine,
    _no_foreign_request_data,
    _private_handles,
    _probe,
    _turn,
)
from tests.test_recovery_whole_state import stored_rows
from tests.test_stage4_conversation_memory import activate

# Each command runs in a fresh interpreter against the rebuilt installed package. Injecting all
# adapters and TEST settings excludes provider/network activity even for inspection commands.
_CLI_CHILD = """
import json
import sys
from pathlib import Path
from satori.__main__ import main
from satori.config import Environment, LogLevel, Settings
from tests.fakes import FakeEmbeddingProvider
from tests.test_stage4_conversation_memory import conversation_provider, skip_episode_provider
arguments = json.loads(sys.argv[1])
settings = Settings(
    _env_file=None,
    environment=Environment.TEST,
    database_url=arguments['database_url'],
    default_counterparty_id=arguments['counterparty'],
    log_level=LogLevel.CRITICAL,
)
raise SystemExit(main(
    arguments['argv'], settings=settings,
    alembic_config=Path(arguments['alembic_config']),
    conversation_provider=conversation_provider('Приняла.'),
    episode_formation_provider=skip_episode_provider(),
    embedding_provider=FakeEmbeddingProvider({}),
))
"""


def _phase[Result](
    timings: list[dict[str, object]], name: str, operation: Callable[[], Result]
) -> Result:
    started = time.perf_counter()
    result = operation()
    timings.append({"phase": name, "elapsed_ms": round((time.perf_counter() - started) * 1000, 3)})
    return result


def _cli(project_root: Path, database: Path, argv: list[str], *, counterparty: str = "bob") -> str:
    arguments = {
        "database_url": f"sqlite+pysqlite:///{database}",
        "counterparty": counterparty,
        "alembic_config": str(project_root / "alembic.ini"),
        "argv": argv,
    }
    # Do not inherit provider credentials, configured DBs, application logs or user .env settings.
    environment = {"PYTHONIOENCODING": "utf-8", "PATH": os.environ.get("PATH", "")}
    result = subprocess.run(
        [sys.executable, "-c", _CLI_CHILD, json.dumps(arguments)],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    if result.returncode != 0:
        # CLI streams can contain synthetic history; failure reports never copy those streams.
        raise RuntimeError("scenario_cli_command_failed")
    return result.stdout


def _calls(probe: ScopeProbe) -> dict[str, int]:
    return {
        "foreground": len(probe.conversation.requests),
        "episode": len(probe.episode.requests),
        "semantic": len(probe.semantic.requests) if probe.semantic is not None else 0,
        "appraisal": len(probe.appraisal.requests),
        "embedding": len(probe.embedding.requests),
    }


def _new_directory(requested: Path | None) -> Path:
    if requested is None:
        return Path(tempfile.mkdtemp(prefix="satori-memory-scope-scenario-"))
    # A supplied directory must be absent: the runner never selects or replaces user databases.
    directory = requested.expanduser().absolute()
    directory.mkdir(mode=0o700)
    PrivateArtifactStore().write(directory / ".synthetic-scenario", b"synthetic scenario\n")
    return directory


def run_scenario(directory: Path, *, project_root: Path) -> dict[str, object]:
    timings: list[dict[str, object]] = []
    probes: list[ScopeProbe] = []
    source = directory / "source.db"
    url = f"sqlite+pysqlite:///{source}"
    _phase(
        timings,
        "migration",
        lambda: upgrade_database(url, config_path=project_root / "alembic.ini"),
    )
    database = create_database(url)
    try:
        _phase(timings, "activation", lambda: activate(database))
        alice = _probe(database, counterparty="alice", prefix="scenario-a", day=1)
        a_reply = _phase(
            timings,
            "alice_source",
            lambda: _turn(alice, f"Меня зовут {ALICE_MARKER}_01.", "scenario-a-source"),
        )
        probes.append(alice)
        bob = _probe(database, counterparty="bob", prefix="scenario-b", day=2)
        foreign_handles = _private_handles(database, "alice")
        _phase(
            timings,
            "bob_source",
            lambda: _turn(bob, f"Меня зовут {BOB_MARKER}_01.", "scenario-b-source"),
        )
        _no_foreign_request_data(bob, ALICE_MARKER, foreign_handles)
        probes.append(bob)
        newest_alice = _probe(database, counterparty="alice", prefix="scenario-a-new", day=3)
        _phase(
            timings,
            "newest_foreign_source",
            lambda: _turn(newest_alice, f"Меня зовут {ALICE_MARKER}_02.", "scenario-a-new-source"),
        )
        _no_foreign_request_data(newest_alice, BOB_MARKER, _private_handles(database, "bob"))
        probes.append(newest_alice)
        probes.append(
            _phase(
                timings,
                "positive_own_recall",
                lambda: _assert_own_recall(database, prefix="scenario-recall", day=4),
            )
        )

        denied = _probe(
            database,
            counterparty="bob",
            prefix="scenario-denied",
            day=5,
            create_episode=False,
            semantic_factory=None,
        )
        before_denied = stored_rows(source)
        for session_id, request_id in (
            (a_reply.session_id, "scenario-foreign-session"),
            (None, "scenario-a-source"),
        ):
            started = time.perf_counter()
            try:
                _turn(denied, f"Меня зовут {ALICE_MARKER}_01.", request_id, session_id=session_id)
            except (ConversationSessionNotFound, InteractionIdempotencyConflict):
                pass
            else:
                raise AssertionError("scenario_foreign_access_was_not_denied")
            timings.append(
                {
                    "phase": "foreign_session_denied" if session_id else "foreign_replay_denied",
                    "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
                }
            )
        assert stored_rows(source) == before_denied
        assert all(count == 0 for count in _calls(denied).values())
        probes.append(denied)

        unknown_id = newest_alice.services.memories.execute(scope=newest_alice.scope)[0].memory_id
        with database.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE conversation_sessions SET scope_verified=0 WHERE counterparty_id=:party"
                ),
                {"party": "alice"},
            )
        probes.append(
            _phase(
                timings,
                "quarantine_precheck_and_recall",
                lambda: _assert_quarantine(
                    database, memory_id=unknown_id, prefix="scenario-quarantine", day=6
                ),
            )
        )
    finally:
        database.dispose()

    restarted = create_database(url)
    try:
        probes.append(
            _phase(
                timings,
                "process_restart_positive_recall",
                lambda: _assert_own_recall(restarted, prefix="scenario-restart", day=7),
            )
        )
        before_inspection = stored_rows(source)
        history = _phase(
            timings, "fresh_cli_history", lambda: _cli(project_root, source, ["history"])
        )
        assert BOB_MARKER in history
        assert ALICE_MARKER not in history
        scope_status = json.loads(
            _phase(
                timings,
                "fresh_cli_scope_status",
                lambda: _cli(project_root, source, ["memories", "scope-status"]),
            )
        )
        assert scope_status["eligible_episodes_in_scope"] == 1
        assert scope_status["quarantined_episodes_for_identity"] == 2
        assert stored_rows(source) == before_inspection
        archive = directory / "scope.state"
        exported = json.loads(
            _phase(
                timings,
                "fresh_cli_export",
                lambda: _cli(project_root, source, ["state", "export", "--output", str(archive)]),
            )
        )
        missing = directory / "never-created.db"
        preview = json.loads(
            _phase(
                timings,
                "fresh_cli_preview",
                lambda: _cli(project_root, missing, ["state", "import", str(archive), "--preview"]),
            )
        )
        assert preview == exported
        target = directory / "restored.db"
        restore_stdout = _phase(
            timings,
            "fresh_cli_offline_restore",
            lambda: _cli(
                project_root,
                missing,
                [
                    "state",
                    "import",
                    str(archive),
                    "--activate",
                    "--target",
                    str(target),
                    "--sha256",
                    preview["archive_sha256"],
                ],
            ),
        )
        assert "automatic_processing=false" in restore_stdout
        assert not missing.exists()
        assert stored_rows(source) == before_inspection
        assert stored_rows(target) == before_inspection
    finally:
        restarted.dispose()

    restored = create_database(f"sqlite+pysqlite:///{target}")
    try:
        probes.append(
            _phase(
                timings,
                "restored_positive_recall",
                lambda: _assert_own_recall(restored, prefix="scenario-restored", day=8),
            )
        )
        probes.append(
            _phase(
                timings,
                "restored_quarantine_exclusion",
                lambda: _assert_quarantine(
                    restored, memory_id=unknown_id, prefix="scenario-restored-unknown", day=9
                ),
            )
        )
    finally:
        restored.dispose()

    call_counts = {name: sum(_calls(probe)[name] for probe in probes) for name in _calls(probes[0])}
    table_counts = preview["manifest"]["table_counts"]
    return {
        "schema_version": 1,
        "scenario": "counterparty_memory_scope_restart_recovery",
        "synthetic_content_only": True,
        "counterparty_count": 2,
        "forced_equal_vectors": True,
        "real_provider_calls": 0,
        "character_quality_evaluated": False,
        "provider_call_counts": call_counts,
        "foreign_denial_provider_calls": 0,
        "foreign_denial_canonical_writes": 0,
        "foreign_context_items_found": 0,
        "positive_recall_before_restart_after_restart_after_restore": True,
        "quarantined_sources_excluded_before_and_after_restore": True,
        "inspection_export_restore_source_rows_unchanged": True,
        "restored_canonical_rows_equal": True,
        "automatic_processing": False,
        "archive_sha256": preview["archive_sha256"],
        "alembic_revision": preview["manifest"]["alembic_revision"],
        "canonical_table_count": len(table_counts),
        "canonical_row_count": sum(table_counts.values()),
        "scope_counts": {key: value for key, value in scope_status.items() if type(value) is int},
        "phase_timings": timings,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, help="new disposable directory; must be absent")
    parser.add_argument("--output", type=Path, help="new private metadata report; no overwrite")
    arguments = parser.parse_args(argv)
    directory = _new_directory(arguments.directory)
    output = arguments.output or directory / "verification.json"
    report = run_scenario(directory, project_root=Path(__file__).resolve().parents[1])
    PrivateArtifactStore().write(
        output, (json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2) + "\n").encode()
    )
    print(f"Synthetic memory-scope scenario passed; private metadata report: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
