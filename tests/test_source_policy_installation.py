"""Managed synthetic main-DB enrollment, older-backup admission and local controls."""

import json
from pathlib import Path

import pytest
from sqlalchemy import delete, select

import satori.__main__ as cli
from satori.application.recovery.contracts import RecoveryError
from satori.application.recovery.use_cases import WholeStateRecovery
from satori.application.source_policy.contracts import SourceExclusionCommand
from satori.application.source_policy.use_cases import SourcePolicyControls
from satori.config import Environment, Settings
from satori.domain.source_policy import (
    ExclusionPhase,
    SourceEligibilitySnapshot,
    SourceExclusionReceipt,
    SourcePolicyError,
)
from satori.infrastructure.artifacts import PrivateArtifactStore
from satori.infrastructure.persistence.database import Database, create_database
from satori.infrastructure.persistence.models import (
    ConversationInteractionRow,
    ConversationMessageRow,
    ConversationSessionRow,
    SourceOwnerRepairRow,
    SourcePayloadRow,
    UserModelClaimRow,
    WorldModelClaimEvidenceRow,
    WorldModelClaimRow,
)
from satori.infrastructure.persistence.recovery import SQLiteStoredSnapshot
from satori.infrastructure.persistence.source_policy import (
    eligible_payload,
    require_owner_reconciled,
)
from satori.infrastructure.persistence.unit_of_work import SQLAlchemyUnitOfWork
from satori.infrastructure.source_policy.installation import (
    bind_installation,
    canonical_root,
    enroll_installation,
    repair_installation,
)
from satori.infrastructure.source_policy.runtime import SourcePolicyRuntime
from tests.test_source_policy_owners import _form_models
from tests.test_stage4_conversation_memory import ACTIVATION_TIME, activate

PRIVATE_MARKER = "SYNTHETIC_PRIVATE_SOURCE_CONTENT"


def _canonical(database: Database) -> str:
    identity_id = activate(database).identity.identity_id
    with database.session_factory() as session:
        session.add(
            ConversationSessionRow(
                session_id="session",
                identity_id=identity_id,
                counterparty_id="alice",
                scope_verified=True,
                schema_version=1,
                kind="explicit",
                status="open",
                started_at=ACTIVATION_TIME,
            )
        )
        session.flush()
        session.add(
            ConversationInteractionRow(
                interaction_id="turn",
                session_id="session",
                client_request_id="turn-request",
                trace_id="synthetic",
                schema_version=1,
                status="pending",
                started_at=ACTIVATION_TIME,
                relationship_processing_required=False,
                model_processing_required=False,
                position_processing_required=False,
            )
        )
        session.flush()
        session.add(
            ConversationMessageRow(
                message_id="user",
                session_id="session",
                interaction_id="turn",
                schema_version=1,
                role="user",
                content=PRIVATE_MARKER,
                created_at=ACTIVATION_TIME,
                sequence=1,
            )
        )
        session.commit()
    return identity_id


def _enrolled(database: Database, tmp_path: Path) -> SourcePolicyRuntime:
    _canonical(database)
    runtime = enroll_installation(
        database, tmp_path / "policy", reviewed=True, new_installation=True
    )
    with SQLAlchemyUnitOfWork(database.session_factory) as unit:
        assert unit.source_admission is not None
        input_id = unit.source_admission.prepare(root_message_ids=("user",), payload_refs=())
        unit.commit()
    with SQLAlchemyUnitOfWork(database.session_factory) as unit:
        assert unit.source_admission is not None
        unit.source_admission.record("synthetic", "payload", 1, input_id)
        unit.commit()
    return runtime


def _exclude_command(database: Database, runtime: SourcePolicyRuntime) -> SourceExclusionCommand:
    root = canonical_root(database, runtime, "user", counterparty_id="alice")
    preview = SourcePolicyControls(runtime).preview(root)
    return SourceExclusionCommand(
        message_id=root.message_id,
        expected_generation=preview.generation,
        request_id="exclude",
        preview_digest=preview.preview_digest,
    )


def test_old_main_backup_cannot_resurrect_current_floor(
    migrated_database: Database,
    tmp_path: Path,
) -> None:
    runtime = _enrolled(migrated_database, tmp_path)
    source = Path(str(migrated_database.engine.url.database))
    restored = tmp_path / "older-restored.db"
    with runtime.shared():
        restored.write_bytes(source.read_bytes())
    command = _exclude_command(migrated_database, runtime)
    root = canonical_root(migrated_database, runtime, "user", counterparty_id="alice")
    receipt = SourcePolicyControls(runtime).apply(
        command,
        root,
        repair=lambda snapshot, decision: repair_installation(
            migrated_database, runtime, snapshot, decision
        ),
    )
    assert receipt.phase is ExclusionPhase.COMPLETE
    with runtime.shared() as trusted_snapshot:
        assert trusted_snapshot.generation == 1
    binding = runtime.ledger.binding_path.read_bytes()
    older = create_database(f"sqlite+pysqlite:///{restored}")
    try:
        with pytest.raises(SourcePolicyError, match="source_policy_reconciliation_required"):
            bind_installation(older, runtime.ledger.directory)
        repair_runtime = SourcePolicyRuntime(runtime.ledger.directory, root.identity_id)
        replay = SourcePolicyControls(repair_runtime).apply(
            command,
            root,
            repair=lambda snapshot, decision: repair_installation(
                older, repair_runtime, snapshot, decision
            ),
        )
        assert replay.decision_id == receipt.decision_id
        active = bind_installation(older, runtime.ledger.directory)
        with SQLAlchemyUnitOfWork(older.session_factory) as unit:
            assert not eligible_payload(unit.session, "synthetic", "payload")
            assert unit.session.scalar(select(ConversationMessageRow.content)) == PRIVATE_MARKER
        with active.shared() as snapshot:
            assert snapshot == trusted_snapshot
        assert runtime.ledger.binding_path.read_bytes() == binding
    finally:
        older.dispose()


def test_missing_or_wrong_installation_is_never_fresh_enrollment(
    migrated_database: Database,
    tmp_path: Path,
) -> None:
    runtime = _enrolled(migrated_database, tmp_path)
    with pytest.raises(SourcePolicyError, match="policy_installation_binding_unavailable"):
        bind_installation(migrated_database, tmp_path / "different")
    runtime.ledger.database_path.unlink()
    with pytest.raises(SourcePolicyError, match="source_existing_lineage_cannot_create_floor"):
        enroll_installation(
            migrated_database, runtime.ledger.directory, reviewed=True, new_installation=True
        )
    assert not runtime.ledger.database_path.exists()


@pytest.mark.parametrize("damage", ["wrong_scope", "missing_evidence"])
def test_enrollment_quarantines_unproven_legacy_at_generation_zero(
    migrated_database: Database,
    tmp_path: Path,
    damage: str,
) -> None:
    identity_id, _, user_id, world_id = _form_models(migrated_database)
    with migrated_database.session_factory() as session:
        if damage == "wrong_scope":
            session.get_one(UserModelClaimRow, user_id).counterparty_id = "bob"
        else:
            session.execute(
                delete(WorldModelClaimEvidenceRow).where(
                    WorldModelClaimEvidenceRow.claim_id == world_id
                )
            )
        session.commit()
    if damage == "wrong_scope":
        runtime = enroll_installation(
            migrated_database, tmp_path / "policy", reviewed=True, new_installation=True
        )
    else:
        # Invalid historical state cannot be invented into an owner retirement.
        # Durable withholding and an absent watermark block unsafe reuse.
        with pytest.raises(SourcePolicyError, match="source_owner_reconciliation_required"):
            enroll_installation(
                migrated_database, tmp_path / "policy", reviewed=True, new_installation=True
            )
        runtime = bind_installation(migrated_database, tmp_path / "policy")
    with runtime.shared() as snapshot:
        assert snapshot.generation == 0
        assert snapshot.excluded_message_ids == frozenset()
    with SQLAlchemyUnitOfWork(migrated_database.session_factory) as unit:
        kind, claim_id = (
            ("user_model_claim", user_id)
            if damage == "wrong_scope"
            else ("world_model_claim", world_id)
        )
        receipt = unit.session.get_one(SourcePayloadRow, (kind, claim_id, 1))
        assert receipt.proof_status == "unknown"
        assert not receipt.legacy
        assert not receipt.eligible
        if damage == "wrong_scope":
            assert len(receipt.root_message_ids) == 1
            assert unit.session.get_one(UserModelClaimRow, user_id).status == "retracted"
            watermark = unit.session.get_one(SourceOwnerRepairRow, ("current_models", identity_id))
            assert watermark.policy_generation == 0
        else:
            assert receipt.root_message_ids == []
            assert unit.session.get_one(WorldModelClaimRow, world_id).status == "current"
            assert unit.session.get(SourceOwnerRepairRow, ("current_models", identity_id)) is None
            with pytest.raises(SourcePolicyError, match="source_owner_reconciliation_required"):
                require_owner_reconciled(unit.session, "current_models")


def test_floor_committed_repair_failure_blocks_operational_mirror(
    migrated_database: Database,
    tmp_path: Path,
) -> None:
    runtime = _enrolled(migrated_database, tmp_path)
    command = _exclude_command(migrated_database, runtime)
    root = canonical_root(migrated_database, runtime, "user", counterparty_id="alice")

    def fail(snapshot: SourceEligibilitySnapshot, receipt: SourceExclusionReceipt) -> None:
        raise RuntimeError("synthetic private failure")

    receipt = SourcePolicyControls(runtime).apply(command, root, repair=fail)
    assert receipt.phase is ExclusionPhase.REPAIR_PENDING
    assert receipt.exclusion_committed
    with pytest.raises(SourcePolicyError, match="source_policy_reconciliation_required"):
        bind_installation(migrated_database, runtime.ledger.directory)
    with runtime.exclusive() as snapshot:
        assert snapshot.excluded_message_ids == frozenset({"user"})


@pytest.mark.parametrize("reserved_index", range(6))
def test_offline_recovery_cannot_publish_over_floor_or_sidecars(
    migrated_database: Database,
    tmp_path: Path,
    project_root: Path,
    reserved_index: int,
) -> None:
    runtime = _enrolled(migrated_database, tmp_path)
    recovery = WholeStateRecovery(
        SQLiteStoredSnapshot(
            str(migrated_database.engine.url), alembic_config=project_root / "alembic.ini"
        ),
        PrivateArtifactStore(),
        reserved_paths=runtime.reserved_paths,
    )
    before = runtime.ledger.database_path.read_bytes()
    with pytest.raises(RecoveryError, match="canonical_database_target"):
        recovery.export(runtime.reserved_paths[reserved_index], overwrite=True)
    assert runtime.ledger.database_path.read_bytes() == before


def test_metadata_commands_and_digest_replay_never_construct_provider(
    migrated_database: Database,
    tmp_path: Path,
    project_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _canonical(migrated_database)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("deterministic source control constructed a provider")

    for name in (
        "_configured_conversation_provider",
        "_configured_episode_provider",
        "_configured_embedding_provider",
        "_configured_semantic_provider",
        "_configured_affective_provider",
        "_configured_relationship_provider",
        "_configured_model_provider",
        "_configured_position_provider",
        "_configured_reflection_provider",
    ):
        monkeypatch.setattr(cli, name, forbidden)
    settings = Settings(
        environment=Environment.TEST,
        database_url=str(migrated_database.engine.url),
        source_policy_directory=tmp_path / "policy",
        default_counterparty_id="alice",
    )
    configuration = project_root / "alembic.ini"
    assert (
        cli.main(
            ["sources", "enroll", "--reviewed", "--new-installation"],
            settings=settings,
            alembic_config=configuration,
        )
        == 0
    )
    capsys.readouterr()
    assert (
        cli.main(
            ["sources", "exclude", "user", "--preview"],
            settings=settings,
            alembic_config=configuration,
        )
        == 0
    )
    serialized = capsys.readouterr().out
    assert PRIVATE_MARKER not in serialized
    preview = json.loads(serialized)
    command = [
        "sources",
        "exclude",
        "user",
        "--apply",
        "--expected-generation",
        str(preview["generation"]),
        "--sha256",
        preview["preview_digest"],
        "--request-id",
        "cli-exclusion",
    ]
    assert cli.main(command, settings=settings, alembic_config=configuration) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["phase"] == "complete"
    assert PRIVATE_MARKER not in json.dumps(receipt)
    assert cli.main(command, settings=settings, alembic_config=configuration) == 0
    replay = json.loads(capsys.readouterr().out)
    assert replay["decision_id"] == receipt["decision_id"]
    malformed = list(command)
    malformed[malformed.index("--sha256") + 1] = "f" * 64
    assert cli.main(malformed, settings=settings, alembic_config=configuration) == 2
    error = capsys.readouterr().err
    assert "source_request_conflict" in error
    assert PRIVATE_MARKER not in error
