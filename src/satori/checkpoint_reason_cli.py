"""Local explicit reason controls; no providers, migrations or enrollment on read paths."""

import argparse
import json
import sys

from sqlalchemy.exc import SQLAlchemyError

from satori.application.erasure.personality_reason_contracts import (
    CheckpointReasonCommand,
    CheckpointReasonRequest,
)
from satori.application.erasure.personality_reason_use_cases import CheckpointReasonManager
from satori.config import Settings
from satori.domain.erasure_personality_reasons import (
    CheckpointReasonKind,
    CheckpointReasonSelector,
)
from satori.domain.errors import NotActivated
from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.erasure_database import open_existing_erasure_database
from satori.infrastructure.persistence.erasure_personality_reasons import (
    SQLiteCheckpointReasonBodyStore,
)
from satori.infrastructure.source_policy.installation import installation_identity
from satori.infrastructure.source_policy.runtime import SourcePolicyRuntime


def checkpoint_reason_command(arguments: argparse.Namespace, settings: Settings) -> int:
    database: Database | None = None
    try:
        policy = arguments.personality_action == "reasons-policy"
        if policy:
            database = open_existing_erasure_database(settings.database_url)
            runtime = SourcePolicyRuntime(
                settings.source_policy_directory, installation_identity(database)
            )
            snapshot = runtime.upgrade_checkpoint_reason_policy()
            print(
                json.dumps(
                    {
                        "operation": "upgrade_checkpoint_reason_policy",
                        "body_revision": snapshot.body_revision,
                        "physical_erasure": False,
                        "execution_authorized": False,
                    },
                    indent=2,
                )
            )
            return 0
        review = (
            arguments.expected_generation,
            arguments.expected_erasure_revision,
            arguments.expected_body_revision,
            arguments.sha256,
        )
        if arguments.preview and (
            arguments.request_id is not None or any(value is not None for value in review)
        ):
            raise SourcePolicyError("body_preview_does_not_apply")
        if arguments.apply and (
            arguments.request_id is None or any(value is None for value in review)
        ):
            raise SourcePolicyError("body_apply_requires_fresh_review")
        if (arguments.resume or arguments.status) and (
            arguments.request_id is None or any(value is not None for value in review)
        ):
            raise SourcePolicyError("body_receipt_requires_request_only")
        selectors = tuple(
            sorted(
                (
                    *(
                        CheckpointReasonSelector(CheckpointReasonKind.CHECKPOINT_APPROVAL, item)
                        for item in arguments.approval
                    ),
                    *(
                        CheckpointReasonSelector(CheckpointReasonKind.PERSONALITY_RESTORE, item)
                        for item in arguments.restore
                    ),
                ),
                key=lambda item: item.key,
            )
        )
        request = CheckpointReasonRequest(identity_id=arguments.identity_id, selectors=selectors)
        database = open_existing_erasure_database(
            settings.database_url, writable=arguments.apply or arguments.resume
        )
        runtime = SourcePolicyRuntime(
            settings.source_policy_directory, installation_identity(database)
        )
        manager = CheckpointReasonManager(runtime, SQLiteCheckpointReasonBodyStore(database))
        if arguments.preview:
            preview = manager.preview(request)
            print(preview.model_dump_json(indent=2))
            return 0 if preview.readiness == "ready" else 2
        if arguments.apply:
            result = manager.commit(
                CheckpointReasonCommand(
                    identity_id=request.identity_id,
                    selectors=request.selectors,
                    request_id=arguments.request_id,
                    expected_generation=arguments.expected_generation,
                    expected_erasure_revision=arguments.expected_erasure_revision,
                    expected_body_revision=arguments.expected_body_revision,
                    approval_digest=arguments.sha256,
                )
            )
        elif arguments.resume:
            result = manager.resume(request, request_id=arguments.request_id)
        else:
            result = manager.status(request, request_id=arguments.request_id)
        print(result.model_dump_json(indent=2))
        return 0 if result.main_projection_current else 2
    except SourcePolicyError as error:
        allowed = {
            "body_preview_does_not_apply",
            "body_apply_requires_fresh_review",
            "body_receipt_requires_request_only",
            "checkpoint_reason_selectors_required",
            "checkpoint_reason_scope_unavailable",
            "checkpoint_reason_selector_unavailable",
            "checkpoint_reason_unavailable_in_scope",
            "body_action_unavailable_in_scope",
            "body_erasure_request_conflict",
            "body_erasure_plan_blocked",
            "body_erasure_review_stale",
            "source_policy_binding_required",
            "source_policy_main_binding_invalid",
            "source_policy_body_application_pending",
            "body_application_pending",
            "erasure_schema_unsupported",
            "policy_checkpoint_reason_schema_upgrade_required",
        }
        reason = error.reason_code if error.reason_code in allowed else "reason_control_unavailable"
        print(f"Checkpoint reason control rejected: {reason}", file=sys.stderr)
        return 2
    except NotActivated:
        print("Checkpoint reason control rejected: installation_not_activated", file=sys.stderr)
        return 2
    except ValueError:
        print("Checkpoint reason control rejected: invalid_command", file=sys.stderr)
        return 2
    except (SQLAlchemyError, OSError):
        print("Checkpoint reason control unavailable: storage_failure", file=sys.stderr)
        return 1
    finally:
        if database is not None:
            database.dispose()
