"""Explicit write-free whole-main retention controls without cognitive services."""

import argparse
import sys

from sqlalchemy.exc import SQLAlchemyError

from satori.application.erasure.retention_contracts import (
    RetentionCheckRequest,
    RetentionStatusRequest,
)
from satori.application.erasure.retention_use_cases import RetentionPlanner
from satori.config import Settings
from satori.domain.errors import NotActivated
from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.erasure_database import open_existing_erasure_database
from satori.infrastructure.persistence.retention_inventory import SQLiteRetentionInventory
from satori.infrastructure.source_policy.installation import installation_identity
from satori.infrastructure.source_policy.runtime import SourcePolicyRuntime


def retention_command(arguments: argparse.Namespace, settings: Settings) -> int:
    database: Database | None = None
    try:
        review = (
            arguments.expected_generation,
            arguments.expected_erasure_revision,
            arguments.expected_body_revision,
            arguments.sha256,
        )
        if arguments.status and any(value is not None for value in review):
            raise SourcePolicyError("retention_status_does_not_approve")
        if arguments.check and any(value is None for value in review):
            raise SourcePolicyError("retention_check_requires_review")
        request = RetentionStatusRequest(identity_id=arguments.identity_id)
        database = open_existing_erasure_database(settings.database_url)
        runtime = SourcePolicyRuntime(
            settings.source_policy_directory, installation_identity(database)
        )
        planner = RetentionPlanner(runtime, SQLiteRetentionInventory(database))
        if arguments.check:
            checked = planner.check(
                RetentionCheckRequest(
                    identity_id=request.identity_id,
                    expected_generation=arguments.expected_generation,
                    expected_erasure_revision=arguments.expected_erasure_revision,
                    expected_body_revision=arguments.expected_body_revision,
                    review_digest=arguments.sha256,
                )
            )
            print(checked.model_dump_json(indent=2))
            return 0 if checked.coverage == "complete" else 2
        status = planner.status(request)
        print(status.model_dump_json(indent=2))
        return 0 if status.coverage == "complete" else 2
    except SourcePolicyError as error:
        allowed = {
            "retention_status_does_not_approve",
            "retention_check_requires_review",
            "retention_identity_unavailable",
            "retention_schema_unsupported",
            "retention_head_conflict",
            "retention_review_stale",
            "retention_assessment_capacity_exceeded",
            "erasure_inventory_capacity_exceeded",
            "source_installation_binding_mismatch",
            "source_policy_binding_required",
            "source_policy_main_binding_invalid",
            "source_policy_reconciliation_required",
            "source_owner_reconciliation_required",
            "erasure_reconciliation_required",
            "body_reconciliation_required",
            "source_policy_body_application_pending",
        }
        reason = (
            error.reason_code if error.reason_code in allowed else "retention_control_unavailable"
        )
        print(f"Retention control rejected: {reason}", file=sys.stderr)
        return 2
    except NotActivated:
        print("Retention control rejected: installation_not_activated", file=sys.stderr)
        return 2
    except ValueError:
        print("Retention control rejected: invalid_command", file=sys.stderr)
        return 2
    except (SQLAlchemyError, OSError):
        print("Retention control unavailable: storage_failure", file=sys.stderr)
        return 1
    finally:
        if database is not None:
            database.dispose()
