"""Synthetic actual-composition/CLI erasure preview, stale check and old-backup drill.

Run: uv run --no-sync python -m tests.erasure_preview_scenario --output /private/tmp/report.json
Fresh processes use fake-generated stored data; no real inference or physical erasure occurs.
Only metadata/timings are emitted. Existing installations are never selected.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from time import perf_counter

from sqlalchemy import select, update

from satori.infrastructure.artifacts import PrivateArtifactStore
from satori.infrastructure.persistence.database import create_database
from satori.infrastructure.persistence.migrations import upgrade_database
from satori.infrastructure.persistence.models import (
    ConversationMessageRow,
    EpisodicMemoryRow,
    MemoryEvidenceRow,
    SemanticClaimRow,
)
from tests.erasure_support import create_case, provider_counts, stored_state
from tests.test_recovery_whole_state import stored_rows
from tests.test_source_exclusion_production import DERIVE_TEXT, ROOT_TEXT, SECRET, _services

CLI_ENTRY = (
    "import sys; from satori.__main__ import main; from satori.config import Settings; "
    "raise SystemExit(main(sys.argv[1:], settings=Settings(_env_file=None)))"
)


def run(
    project_root: Path, directory: Path, *, report_path: Path | None = None
) -> dict[str, object]:
    timings: list[dict[str, object]] = []

    def phase(name: str, operation: Callable[[], object]) -> object:
        started = perf_counter()
        result = operation()
        timings.append({"phase": name, "elapsed_ms": round((perf_counter() - started) * 1000, 3)})
        return result

    url = f"sqlite+pysqlite:///{directory / 'synthetic.db'}"
    upgrade_database(url, config_path=project_root / "alembic.ini")
    database = create_database(url)
    restored_database = None
    try:
        case = create_case(database, directory / "trusted-policy", excluded=False)
        protected_texts = {SECRET, ROOT_TEXT, DERIVE_TEXT}
        with database.session_factory() as session:
            for column in (
                ConversationMessageRow.content,
                EpisodicMemoryRow.summary,
                MemoryEvidenceRow.quote,
                SemanticClaimRow.value,
            ):
                protected_texts.update(session.scalars(select(column)))
        environment = {
            **os.environ,
            "SATORI_DATABASE_URL": url,
            "SATORI_SOURCE_POLICY_DIRECTORY": str(case.directory),
            "SATORI_DEFAULT_COUNTERPARTY_ID": "alice",
            "SATORI_ENVIRONMENT": "test",
            "SATORI_LOG_LEVEL": "CRITICAL",
        }

        def cli(
            arguments: list[str],
            *,
            expected_exit: int = 0,
            require_json: bool = True,
            database_url: str = url,
            counterparty: str = "alice",
        ) -> dict[str, object]:
            completed = subprocess.run(
                [sys.executable, "-c", CLI_ENTRY, *arguments],
                cwd=project_root,
                env={
                    **environment,
                    "SATORI_DATABASE_URL": database_url,
                    "SATORI_DEFAULT_COUNTERPARTY_ID": counterparty,
                },
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            assert completed.returncode == expected_exit, "scenario_cli_exit_mismatch"
            assert all(
                content not in completed.stdout + completed.stderr for content in protected_texts
            ), "scenario_cli_content_leak"
            if not require_json:
                return {}
            decoded: object = json.JSONDecoder().raw_decode(completed.stdout.lstrip())[0]
            assert isinstance(decoded, dict), "scenario_cli_metadata_shape"
            return decoded

        source_path = case.path
        archive_path = directory / "pre-exclusion.state"
        restored_path = directory / "restored-old.db"
        if report_path is not None:
            forbidden = {
                *(path.resolve() for path in case.runtime.reserved_paths),
                archive_path.resolve(),
                restored_path.resolve(),
                source_path.resolve(),
                *(
                    Path(f"{path}{suffix}").resolve()
                    for path in (source_path, restored_path)
                    for suffix in ("-wal", "-shm", "-journal", ".receipt.json")
                ),
            }
            if report_path.resolve() in forbidden:
                raise ValueError("scenario_report_target_reserved")
        exported = phase(
            "actual_cli_pre_exclusion_backup",
            lambda: cli(["state", "export", "--output", str(archive_path)]),
        )
        assert isinstance(exported, dict)
        archive_bytes = archive_path.read_bytes()
        source_preview = cli(["sources", "exclude", case.message_id, "--preview"])
        phase(
            "actual_cli_source_exclusion",
            lambda: cli(
                [
                    "sources",
                    "exclude",
                    case.message_id,
                    "--apply",
                    "--expected-generation",
                    str(source_preview["generation"]),
                    "--sha256",
                    str(source_preview["preview_digest"]),
                    "--request-id",
                    "scenario-exclusion",
                ]
            ),
        )

        before = stored_state(case)
        main_bytes = source_path.read_bytes()
        floor_bytes = case.runtime.ledger.database_path.read_bytes()
        preview = phase(
            "actual_cli_erasure_preview",
            lambda: cli(["sources", "erasure", case.message_id, "--preview"], expected_exit=2),
        )
        assert isinstance(preview, dict)
        assert preview["assessment_status"] == "blocked"
        assert preview["assessment_blockers"]
        assert preview["physical_readiness"] == "blocked"
        assert preview["physical_erasure"] is False
        assert preview["execution_authorized"] is False
        blockers = preview["execution_blockers"]
        assert isinstance(blockers, list)
        assert "physical_erasure_executor_not_implemented" in blockers
        assert preview == cli(["sources", "erasure", case.message_id, "--preview"], expected_exit=2)
        for descendant in (
            case.derived_message_id,
            case.derived_interaction_id,
            *case.episode_ids,
            *case.claim_ids,
        ):
            assert descendant not in json.dumps(preview)

        def checked_arguments(value: dict[str, object]) -> list[str]:
            return [
                "sources",
                "erasure",
                case.message_id,
                "--check",
                "--expected-generation",
                str(value["generation"]),
                "--sha256",
                str(value["preview_digest"]),
            ]

        matched = phase(
            "fresh_process_review_check", lambda: cli(checked_arguments(preview), expected_exit=2)
        )
        assert isinstance(matched, dict)
        assert matched["review_status"] == "current"
        phase(
            "foreign_scope_denied",
            lambda: cli(
                ["sources", "erasure", case.message_id, "--preview"],
                counterparty="bob",
                expected_exit=2,
                require_json=False,
            ),
        )
        assert stored_state(case) == before
        assert source_path.read_bytes() == main_bytes
        assert case.runtime.ledger.database_path.read_bytes() == floor_bytes
        assert provider_counts(case.services) == case.initial_provider_calls

        # A legitimate metadata owner write stales a whole-store review at the same generation.
        extra, _, _ = _services(database, case.directory, "erasure-new-session", 3, form=False)
        extra.start_session.execute()
        phase(
            "same_generation_owner_write_stales_review",
            lambda: cli(checked_arguments(preview), expected_exit=2, require_json=False),
        )
        fresh = cli(["sources", "erasure", case.message_id, "--preview"], expected_exit=2)
        assert fresh["generation"] == preview["generation"]
        assert fresh["preview_digest"] != preview["preview_digest"]

        # A controlled synthetic opaque-value mutation tests non-versioned content coverage.
        with database.engine.begin() as connection:
            connection.execute(
                update(ConversationMessageRow)
                .where(
                    ConversationMessageRow.interaction_id == case.derived_interaction_id,
                    ConversationMessageRow.role == "assistant",
                )
                .values(content=f"{SECRET}-SYNTHETIC-OPAQUE-DRIFT")
            )
        phase(
            "opaque_value_drift_stales_review",
            lambda: cli(checked_arguments(fresh), expected_exit=2, require_json=False),
        )
        current = cli(["sources", "erasure", case.message_id, "--preview"], expected_exit=2)
        assert current["generation"] == preview["generation"]

        phase(
            "actual_cli_old_archive_preview",
            lambda: cli(["state", "import", str(archive_path), "--preview"]),
        )
        phase(
            "actual_cli_old_archive_offline_restore",
            lambda: cli(
                [
                    "state",
                    "import",
                    str(archive_path),
                    "--activate",
                    "--target",
                    str(restored_path),
                    "--sha256",
                    str(exported["archive_sha256"]),
                ]
            ),
        )
        old_url = f"sqlite+pysqlite:///{restored_path}"
        phase(
            "old_main_preview_denied_until_floor_reconciliation",
            lambda: cli(
                ["sources", "erasure", case.message_id, "--preview"],
                database_url=old_url,
                expected_exit=2,
                require_json=False,
            ),
        )
        phase(
            "explicit_trusted_floor_repair_old_main",
            lambda: cli(
                ["sources", "repair", "--request-id", "scenario-exclusion"],
                database_url=old_url,
            ),
        )
        restored = phase(
            "restored_old_main_requires_new_review",
            lambda: cli(
                ["sources", "erasure", case.message_id, "--preview"],
                database_url=old_url,
                expected_exit=2,
            ),
        )
        assert isinstance(restored, dict)
        assert restored["generation"] == current["generation"]
        assert restored["preview_digest"] != current["preview_digest"]
        assert restored["physical_erasure"] is False
        cli(
            checked_arguments(current),
            database_url=old_url,
            expected_exit=2,
            require_json=False,
        )
        assert archive_path.read_bytes() == archive_bytes
        restored_database = create_database(old_url)
        with restored_database.session_factory() as session:
            retained = session.scalar(
                select(ConversationMessageRow.content).where(
                    ConversationMessageRow.message_id == case.message_id
                )
            )
            assert retained == ROOT_TEXT

        old_rows = stored_rows(restored_path)
        original_floor = case.runtime.ledger.database_path
        retained_floor = case.directory / "synthetic-held-floor"
        original_floor.rename(retained_floor)
        try:
            phase(
                "missing_floor_denied_without_replacement",
                lambda: cli(
                    ["sources", "erasure", case.message_id, "--preview"],
                    database_url=old_url,
                    expected_exit=2,
                    require_json=False,
                ),
            )
            assert not original_floor.exists()
            assert stored_rows(restored_path) == old_rows
        finally:
            retained_floor.rename(original_floor)
        assert provider_counts(case.services) == case.initial_provider_calls
        routes = preview["routes"]
        assert isinstance(routes, list)
        return {
            "scenario": "read-only-erasure-preview-production-v1",
            "checks": {
                "actual_uncited_derivative_routes": True,
                "preview_and_check_preserve_main_and_floor": True,
                "public_metadata_has_no_descendant_ids_or_content": True,
                "fresh_process_digest_stable": True,
                "foreign_scope_denied": True,
                "same_generation_owner_write_stales_review": True,
                "opaque_content_drift_stales_review": True,
                "old_main_denied_until_trusted_repair": True,
                "restored_main_requires_new_review": True,
                "old_archive_and_raw_root_bytes_retained": True,
                "missing_floor_not_recreated": True,
                "physical_executor_and_intent_commit_absent": True,
                "opaque_retention_uncertainty_reported": True,
            },
            "affected_payload_versions": preview["affected_payload_versions"],
            "scanned_rows": preview["scanned_rows"],
            "route_count": len(routes),
            "assessment_status": preview["assessment_status"],
            "control_provider_calls": 0,
            "paid_provider_calls": 0,
            "source_text_logged": False,
            "physical_erasure": False,
            "timings": timings,
        }
    finally:
        if restored_database is not None:
            restored_database.dispose()
        database.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="new private metadata report; no overwrite")
    parser.add_argument("--directory", type=Path, help="new or empty synthetic fixture directory")
    arguments = parser.parse_args()
    project_root = Path(__file__).resolve().parents[1]
    if arguments.directory is None:
        with tempfile.TemporaryDirectory(prefix="satori-erasure-preview-scenario-") as temporary:
            report = run(project_root, Path(temporary), report_path=arguments.output)
    else:
        directory = arguments.directory.absolute()
        if directory.is_symlink() or (
            directory.exists() and (not directory.is_dir() or any(directory.iterdir()))
        ):
            raise ValueError("scenario_directory_not_empty")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        report = run(project_root, directory, report_path=arguments.output)
    payload = (json.dumps(report, sort_keys=True, indent=2) + "\n").encode("utf-8")
    if arguments.output is None:
        print(payload.decode("utf-8"), end="")
    else:
        PrivateArtifactStore().write(arguments.output, payload)
        print(json.dumps({"report_written": True, "physical_erasure": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
