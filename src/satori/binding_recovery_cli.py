"""Explicit offline binding review/adoption; never opens the ordinary runtime."""

import argparse
from pathlib import Path

from pydantic import ValidationError

from satori.application.source_policy.binding_recovery import (
    AdoptCurrentBindingFiles,
    DeviceBindingRecoveryPlan,
)
from satori.domain.source_policy import SourcePolicyError
from satori.infrastructure.artifacts import PrivateArtifactStore
from satori.infrastructure.source_policy.binding_recovery import LedgerDeviceBindingRecovery
from satori.infrastructure.source_policy.ledger import InstallationPolicyLedger


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Review current files for explicit owner adoption")
    parser.add_argument("--policy-directory", type=Path, required=True)
    parser.add_argument("--canonical-database", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preview", action="store_true")
    mode.add_argument("--adopt", action="store_true")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--expected-review-sha256")
    parser.add_argument("--request-id")
    parser.add_argument("--owner-adopts-current-files", action="store_true")
    parser.add_argument("--runtime-confirmed-offline", action="store_true")
    arguments = parser.parse_args(argv)
    try:
        recovery = LedgerDeviceBindingRecovery(
            InstallationPolicyLedger(arguments.policy_directory), arguments.canonical_database
        )
        if arguments.preview:
            if any(
                (
                    arguments.plan,
                    arguments.expected_review_sha256,
                    arguments.request_id,
                    arguments.owner_adopts_current_files,
                    arguments.runtime_confirmed_offline,
                )
            ):
                raise SourcePolicyError("binding_recovery_preview_does_not_adopt")
            print(recovery.preview().model_dump_json(indent=2))
            return 0
        if (
            arguments.plan is None
            or arguments.expected_review_sha256 is None
            or arguments.request_id is None
            or not arguments.owner_adopts_current_files
            or not arguments.runtime_confirmed_offline
        ):
            raise SourcePolicyError("binding_recovery_explicit_owner_review_required")
        plan = DeviceBindingRecoveryPlan.model_validate_json(
            PrivateArtifactStore().read(arguments.plan, max_bytes=64_000)
        )
        result = recovery.adopt(
            AdoptCurrentBindingFiles(
                plan=plan,
                expected_review_sha256=arguments.expected_review_sha256,
                request_id=arguments.request_id,
                owner_adopts_current_files=True,
                runtime_confirmed_offline=True,
            )
        )
        print(result.model_dump_json(indent=2))
        return 0 if result.status == "adopted" else 2
    except SourcePolicyError as error:
        print("Binding recovery rejected: " + error.reason_code)
        return 2
    except (OSError, ValidationError, ValueError):
        print("Binding recovery rejected: binding_recovery_unavailable")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
