"""Production composition, fresh CLI processes, restart and restore with synthetic memory.

Usage: uv run --no-sync python -m tests.memory_lifecycle_scenario
Reports contain metadata only. No real model, account or existing user database is used.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path

from satori.application.memory.lifecycle_contracts import MemoryLifecycleCommand
from satori.application.recovery.use_cases import WholeStateRecovery
from satori.composition import build_initial_self_services, build_memory_lifecycle_services
from satori.domain.memory import MemoryLifecycleStatus
from satori.infrastructure.artifacts import PrivateArtifactStore
from satori.infrastructure.persistence.database import create_database
from satori.infrastructure.persistence.migrations import upgrade_database
from satori.infrastructure.persistence.recovery import SQLiteStoredSnapshot
from tests.memory_scope_scenario import _calls, _cli, _phase
from tests.test_memory_scope_integration import RECALL, _probe, _turn
from tests.test_recovery_whole_state import stored_rows
from tests.test_stage4_conversation_memory import activate


def run(project_root: Path, directory: Path) -> dict[str, object]:
    source = directory / "source.db"
    source_url = f"sqlite+pysqlite:///{source}"
    upgrade_database(source_url, config_path=project_root / "alembic.ini")
    database = create_database(source_url)
    timings: list[dict[str, object]] = []
    try:
        activate(database)
        alice = _probe(database, counterparty="alice", prefix="archive-alice", day=0)
        bob = _probe(database, counterparty="bob", prefix="archive-bob", day=1)
        _turn(alice, "Меня зовут ALICE_PRIVATE_ARCHIVE.", "archive-alice")
        _turn(bob, "Меня зовут BOB_OWN_ARCHIVE.", "archive-bob")
        memory = bob.services.memories.execute(scope=bob.scope)[0]
        claim = bob.services.semantic_claims.list(scope=bob.scope)[0]
        baseline = stored_rows(source)
        archive_command = ["memories", "archive", memory.memory_id]
        preview_text = _phase(
            timings,
            "fresh_cli_preview",
            lambda: _cli(project_root, source, [*archive_command, "--preview"]),
        )
        assert "BOB_OWN_ARCHIVE" not in preview_text
        preview = json.loads(preview_text)
        assert stored_rows(source) == baseline
        apply = [
            *archive_command,
            "--apply",
            "--expected-version",
            str(preview["current_version"]),
            "--sha256",
            preview["preview_digest"],
            "--request-id",
            "archive-scenario-1",
        ]
        receipt = json.loads(
            _phase(timings, "fresh_cli_archive", lambda: _cli(project_root, source, apply))
        )
        assert receipt["current_status"] == "archived"
        archived = stored_rows(source)
        unchanged = set(baseline) - {
            "episodic_memories",
            "memory_lifecycle_decisions",
            "audit_events",
        }
        assert all(archived[table] == baseline[table] for table in unchanged)
        assert bob.services.memories.execute(scope=bob.scope) == ()
        assert bob.services.semantic_claims.list(scope=bob.scope) == (claim,)
        assert bob.services.talk.retrieve_semantic is not None
        assert bob.services.talk.retrieve_semantic.execute((), scope=bob.scope).claims == ()
        assert alice.services.memories.execute(scope=alice.scope)
        assert (
            bob.services.memories.scope_status(scope=bob.scope).quarantined_episodes_for_identity
            == 0
        )
        # Old episode formation replay is an intentional skip, with no renewed episode work.
        calls_before = _calls(bob)
        report = asyncio.run(
            bob.services.post_response.execute(
                memory.source_interaction_id, trace_id="archived-replay"
            )
        )
        assert report.succeeded
        assert _calls(bob) == calls_before

        restarted = _probe(
            database, counterparty="bob", prefix="archive-restart", day=2, create_episode=False
        )
        recall = _phase(
            timings, "archived_recall", lambda: _turn(restarted, RECALL, "archived-recall")
        )
        assert memory.memory_id not in recall.context_manifest.retrieved_memory_ids
        assert claim.claim_id not in recall.context_manifest.retrieved_semantic_claim_ids
        # A fresh active root retains semantic identity and reinforces the existing claim.
        reinforcement = _probe(database, counterparty="bob", prefix="archive-reinforce", day=3)
        _turn(reinforcement, "Меня зовут BOB_OWN_ARCHIVE.", "archive-reinforce")
        merged = reinforcement.services.semantic_claims.list(scope=bob.scope)
        assert len(merged) == 1
        assert merged[0].claim_id == claim.claim_id
        assert merged[0].aggregate_version > claim.aggregate_version
        assert len(merged[0].evidence) == 2
        still_archived = bob.services.memories.execute(scope=bob.scope, include_archived=True)
        assert (
            next(
                item for item in still_archived if item.memory_id == memory.memory_id
            ).lifecycle_status
            is MemoryLifecycleStatus.ARCHIVED
        )
        restart_replay = json.loads(
            _phase(timings, "fresh_cli_replay", lambda: _cli(project_root, source, apply))
        )
        assert restart_replay["receipt"] == receipt["receipt"]
        assert restart_replay["replayed"] is True

        state_archive = directory / "private" / "memory.state"
        recovery = WholeStateRecovery(
            SQLiteStoredSnapshot(source_url, alembic_config=project_root / "alembic.ini"),
            PrivateArtifactStore(),
        )
        captured = stored_rows(source)
        exported = _phase(timings, "whole_state_export", lambda: recovery.export(state_archive))
        target = directory / "restored.db"
        _phase(
            timings,
            "whole_state_restore",
            lambda: recovery.restore(
                state_archive, target, expected_sha256=exported.archive_sha256
            ),
        )
        assert stored_rows(source) == captured == stored_rows(target)
        restored = create_database(f"sqlite+pysqlite:///{target}")
        try:
            initial = build_initial_self_services(restored)
            assert initial.get_self.execute().identity.identity_id == bob.scope.identity_id
            controls = build_memory_lifecycle_services(restored)
            review = controls.preview(
                memory.memory_id, MemoryLifecycleStatus.ACTIVE, scope=bob.scope
            )
            result = _phase(
                timings,
                "reactivate_restored",
                lambda: controls.execute(
                    MemoryLifecycleCommand(
                        memory_id=memory.memory_id,
                        target_status=MemoryLifecycleStatus.ACTIVE,
                        expected_version=review.current_version,
                        request_id="reactivate-scenario-1",
                        preview_digest=review.preview_digest,
                    ),
                    scope=bob.scope,
                    trace_id="reactivate-restored",
                ),
            )
            assert result.current_version == 3
            replayed = json.loads(_cli(project_root, target, apply))
            assert replayed["receipt"] == receipt["receipt"]
            assert replayed["current_status"] == "active"
            assert replayed["current_version"] == 3
            # Existing recall deduplicates identical summaries in favour of the newer root.
            # Explicitly archive that alternative in the restored branch to demonstrate that
            # the older reactivated episode's retained vector is independently useful.
            restored_probe = _probe(
                restored,
                counterparty="bob",
                prefix="archive-restored-read",
                day=4,
                create_episode=False,
            )
            alternative = next(
                item
                for item in restored_probe.services.memories.execute(scope=bob.scope)
                if item.memory_id != memory.memory_id
            )
            alternative_review = controls.preview(
                alternative.memory_id, MemoryLifecycleStatus.ARCHIVED, scope=bob.scope
            )
            controls.execute(
                MemoryLifecycleCommand(
                    memory_id=alternative.memory_id,
                    target_status=MemoryLifecycleStatus.ARCHIVED,
                    expected_version=alternative_review.current_version,
                    request_id="archive-restored-alternative",
                    preview_digest=alternative_review.preview_digest,
                ),
                scope=bob.scope,
                trace_id="archive-restored-alternative",
            )
            resumed = _probe(
                restored, counterparty="bob", prefix="archive-restored", day=4, create_episode=False
            )
            useful = _phase(
                timings, "restored_recall", lambda: _turn(resumed, RECALL, "restored-recall")
            )
            assert memory.memory_id in useful.context_manifest.retrieved_memory_ids
            assert claim.claim_id in useful.context_manifest.retrieved_semantic_claim_ids
            assert all(
                "ALICE_PRIVATE_ARCHIVE" not in repr(item) for item in resumed.conversation.requests
            )
        finally:
            restored.dispose()
        return {
            "schema_version": 1,
            "scenario": "owner_episodic_archival_restart_restore",
            "tables": len(captured),
            "rows_at_restore": sum(map(len, captured.values())),
            "original_rows_preserved": True,
            "foreign_partition_preserved": True,
            "archived_recall_excluded": True,
            "semantic_identity_preserved_and_reinforced": True,
            "restart_receipt_preserved": True,
            "exact_restored_rows": True,
            "reactivation_useful_recall": True,
            "historical_receipt_never_reapplied": True,
            "real_provider_calls": 0,
            "lifecycle_provider_calls": 0,
            "controlled_provider_calls": {
                "alice": _calls(alice),
                "bob": _calls(bob),
                "restart": _calls(restarted),
                "reinforcement": _calls(reinforcement),
                "resumed": _calls(resumed),
            },
            "timings": timings,
            "archive_sha256": exported.archive_sha256,
        }
    finally:
        database.dispose()


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    directory = Path(tempfile.mkdtemp(prefix="satori-memory-lifecycle-scenario-"))
    report = run(project_root, directory)
    destination = directory / "verification.json"
    PrivateArtifactStore().write(destination, (json.dumps(report, indent=2) + "\n").encode())
    print(destination)


if __name__ == "__main__":
    main()
