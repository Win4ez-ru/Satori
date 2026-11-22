"""Raw SQLite projection damage is denied before it can escape operator status."""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

from satori.application.erasure.dialogue_use_cases import DialogueBodyManager
from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.persistence.database import Database, create_database
from satori.infrastructure.persistence.erasure_bodies import SQLiteBodyErasureStore
from satori.infrastructure.persistence.erasure_dialogue import SQLiteDialogueBodyStore
from satori.infrastructure.persistence.models import ErasureBodyApplicationRow
from satori.infrastructure.persistence.source_policy import verify_mirror
from satori.infrastructure.source_policy.installation import bind_installation
from tests.erasure_preview_scenario import CLI_ENTRY
from tests.erasure_support import create_case, provider_counts, stored_state
from tests.test_erasure_dialogue_integration import dialogue_command, prepare_dialogue
from tests.test_recovery_whole_state import stored_rows
from tests.test_source_exclusion_production import DERIVE_TEXT, ROOT_TEXT, SECRET


@pytest.mark.parametrize("shape", ["deep_array", "deep_object", "malformed"])
def test_raw_receipt_json_cannot_escape_current_or_fresh_cli_status(
    migrated_database: Database, tmp_path: Path, project_root: Path, shape: str
) -> None:
    case = create_case(migrated_database, tmp_path / "corruption-floor")
    manager, request = prepare_dialogue(case)
    command = dialogue_command(manager, request)
    backup = tmp_path / "restored-main.db"
    with sqlite3.connect(case.path) as original, sqlite3.connect(backup) as restored:
        original.backup(restored)
    assert manager.commit(command).main_projection_current

    # Restore available canonical bodies, copy the approved receipt and forge a current-looking
    # ACK. Its raw JSON reaches SQLAlchemy's JSON loader before the action codec can validate it.
    # CPython 3.12's C JSON nesting bound is separate from the Python recursion limit.
    depth = max(10_000, sys.getrecursionlimit() + 100)
    damaged = {
        "deep_array": "[" * depth + "0" + "]" * depth,
        "deep_object": '{"nested":' * depth + "0" + "}" * depth,
        "malformed": '{"proposal":',
    }[shape]
    with sqlite3.connect(case.path) as original, sqlite3.connect(backup) as restored:
        receipt = original.execute(
            "SELECT * FROM erasure_body_applications WHERE body_revision=2"
        ).fetchone()
        assert receipt is not None
        restored.execute(
            "INSERT INTO erasure_body_applications VALUES (?,?,?,?,?,?,?,?,?)", receipt
        )
        restored.commit()
        # Current DDL rejects malformed/missing scope before the ORM loader. Prove
        # that normal write protection, then emulate privileged file corruption in
        # this disposable restored copy without weakening the schema fingerprint.
        with pytest.raises(sqlite3.IntegrityError, match="closed_scope_binding"):
            restored.execute(
                "UPDATE erasure_body_applications SET action=? WHERE body_revision=2", (damaged,)
            )
        restored.rollback()
        restored.execute("PRAGMA ignore_check_constraints=ON")
        restored.execute(
            "UPDATE erasure_body_applications SET action=? WHERE body_revision=2", (damaged,)
        )
        restored.execute("PRAGMA ignore_check_constraints=OFF")
        restored.execute("UPDATE erasure_body_bindings SET body_revision=2")
        assert restored.execute(
            "SELECT count(*) FROM conversation_messages WHERE content IS NULL"
        ).fetchone() == (0,)

    damaged_database = create_database(f"sqlite+pysqlite:///{backup}")
    baseline = stored_rows(backup)
    trusted_baseline = stored_state(case)
    calls = provider_counts(case.services)
    try:
        if shape != "malformed":
            # This deliberately bypasses the adapter to prove the regression exercises a real
            # stored-value ORM decode failure, rather than a monkeypatched codec or fake port.
            with (
                damaged_database.session_factory() as session,
                pytest.raises(RecursionError),
            ):
                session.scalar(
                    select(ErasureBodyApplicationRow).where(
                        ErasureBodyApplicationRow.body_revision == 2
                    )
                )
        with case.runtime.exclusive() as snapshot:
            assert snapshot.body_revision == 2
            with (
                damaged_database.session_factory() as session,
                pytest.raises(SourcePolicyError, match=r"^body_reconciliation_required$") as denied,
            ):
                verify_mirror(session, snapshot)
            assert denied.value.__cause__ is None
            actions = case.runtime.body_actions()
            assert not SQLiteBodyErasureStore(damaged_database).current(actions, snapshot=snapshot)
            assert not SQLiteDialogueBodyStore(damaged_database).current(actions, snapshot=snapshot)

        status = DialogueBodyManager(
            case.runtime, SQLiteDialogueBodyStore(damaged_database)
        ).status(request, request_id=command.request_id)
        assert status.phase == "body_application_pending"
        assert not status.main_projection_current
        assert not status.selected_bodies_removed
        assert status.physical_erasure is False
        with pytest.raises(SourcePolicyError, match=r"^body_reconciliation_required$"):
            bind_installation(damaged_database, case.directory)

        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                CLI_ENTRY,
                "sources",
                "erasure-dialogue",
                case.message_id,
                "--intent-request-id",
                request.intent_request_id,
                "--request-id",
                command.request_id,
                "--status",
            ],
            cwd=project_root,
            env={
                **os.environ,
                "SATORI_DATABASE_URL": str(damaged_database.engine.url),
                "SATORI_SOURCE_POLICY_DIRECTORY": str(case.directory),
                "SATORI_DEFAULT_COUNTERPARTY_ID": "alice",
                "SATORI_ENVIRONMENT": "test",
                "SATORI_LOG_LEVEL": "CRITICAL",
            },
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert completed.returncode == 2, "operator_status_did_not_return_pending"
        assert "Traceback" not in completed.stdout + completed.stderr
        decoded = json.loads(completed.stdout)
        assert decoded["phase"] == "body_application_pending"
        assert decoded["main_projection_current"] is False
        assert decoded["physical_erasure"] is False
        protected = (
            SECRET,
            ROOT_TEXT,
            DERIVE_TEXT,
            damaged,
            case.derived_message_id,
            case.derived_interaction_id,
            *case.episode_ids,
            *case.claim_ids,
        )
        assert all(value not in completed.stdout + completed.stderr for value in protected)
        assert stored_rows(backup) == baseline
        assert stored_state(case) == trusted_baseline
        assert provider_counts(case.services) == calls
    finally:
        damaged_database.dispose()
