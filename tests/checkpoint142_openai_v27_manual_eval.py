"""Retired immutable facade for the first V27 OpenAI production attempt.

The one-shot grant was consumed and the run failed after its nineteenth paid base call.  Current
source must never recompute an executable plan under that historical authorization identity.
Inspection exposes only frozen public evidence identifiers; every execution path fails before
source fingerprinting, Settings, filesystem access, runtime construction or provider I/O.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from typing import Any, NoReturn

from tests.checkpoint142_openai_manual_support import content_digest, unsafe_artifact_paths

AUTHORIZATION_ID = "satori.checkpoint142.openai.v27.phase1.2026-08-30.one-shot"
AUTHORIZATION_CLAIM_NAME = "checkpoint142-openai-v27-phase1-2026-08-30.claim.json"
REPORT_NAME = "checkpoint142-openai-v27-phase1-2026-08-30.json"
REPORT_RELATIVE_PATH = f"var/evaluations/{REPORT_NAME}"
REVIEW_RELATIVE_PATH = "var/evaluations/checkpoint142-openai-v27-phase1-2026-08-30.review.json"
CLAIM_RELATIVE_PATH = f"var/evaluation-authorizations/{AUTHORIZATION_CLAIM_NAME}"

ARCHIVED_EXECUTION_PLAN_DIGEST = (
    "sha256:5e6bcc1fc53100e66990feb25d9448465a1a6bb1364e7b98eb6f14ddb4d94feb"
)
ARCHIVED_SOURCE_FINGERPRINT_DIGEST = (
    "sha256:e3546c5125adc4f8f923f359550c64a4ade0fe730745e8a18631356292b8f5e7"
)
ARCHIVED_EVALUATOR_BUNDLE_DIGEST = (
    "sha256:444c508ddeb5c16605aad062b2f816d7c4c2e4bb86b3d0de88922c4c5ffe778c"
)
ARCHIVED_SOURCE_PACKAGE_DIGEST = (
    "sha256:ff949a535e59c99edf6fadf61c04e6e74265e008d1ecc1c2efe4a329ae1db331"
)
ARCHIVED_REPORT_CONTENT_DIGEST = (
    "sha256:38ed1cbc963892fe4e983ccb5d88047e1599ebe3cc428f287a5badb5459e4c0b"
)
ARCHIVED_CLAIM_CONTENT_DIGEST = (
    "sha256:5896af5337dd2ac3991ac300a89011c9e7df3fd1a6a31ebc9154f5770ab669af"
)
RETIREMENT_REASON = (
    "V27 attempt 1 is retired after its consumed one-shot failed report; "
    "current source cannot execute or recompute that historical grant"
)


class V27ManualEvaluationConfigurationError(RuntimeError):
    """Reject every paid path for the consumed first V27 attempt."""


def inspect_plan() -> dict[str, Any]:
    """Return frozen public evidence without touching current source or local artifacts."""

    return {
        "schema_version": 1,
        "checkpoint": "14.2",
        "purpose": "v27_live_state_selected_character_movement_production_gate",
        "mode": "archived_inspect_only",
        "status": "failed_immutable",
        "network_attempted": False,
        "policy_id": "satori.conversation.behavior.v27",
        "provider": "openai",
        "model": "gpt-5.6-terra",
        "reasoning_effort": "medium",
        "reasoning_token_allowance": 1024,
        "fresh_replica_count": 3,
        "turns_per_replica": 8,
        "required_base_calls": 24,
        "maximum_provider_calls": 30,
        "maximum_cost_usd": 0.15,
        "authorization_id": AUTHORIZATION_ID,
        "claim_path": CLAIM_RELATIVE_PATH,
        "report_path": REPORT_RELATIVE_PATH,
        "review_path": REVIEW_RELATIVE_PATH,
        "execution_plan_digest": ARCHIVED_EXECUTION_PLAN_DIGEST,
        "source_fingerprint_digest": ARCHIVED_SOURCE_FINGERPRINT_DIGEST,
        "evaluator_bundle_digest": ARCHIVED_EVALUATOR_BUNDLE_DIGEST,
        "source_package_digest": ARCHIVED_SOURCE_PACKAGE_DIGEST,
        "report_content_digest": ARCHIVED_REPORT_CONTENT_DIGEST,
        "claim_content_digest": ARCHIVED_CLAIM_CONTENT_DIGEST,
        "failure_evidence": {
            "error_type": "InvalidProviderResponse",
            "provider_call_count": 19,
            "successful_provider_call_count": 18,
            "actual_successful_usage_cost_usd": 0.057856,
            "conservative_guarded_cost_usd": 0.100594,
            "failed_turn": 3,
            "failed_turn_id": "broad-self-disclosure",
            "requested_visible_output_token_limit": 160,
            "observed_visible_output_tokens": 164,
            "observed_reasoning_output_tokens": 63,
        },
        "paid_execution": {
            "status": "retired",
            "available": False,
            "authorization_reusable": False,
            "reason": RETIREMENT_REASON,
        },
    }


def _validate_no_private_keys(value: Mapping[str, Any]) -> None:
    unsafe = unsafe_artifact_paths(value)
    if unsafe:
        raise ValueError("archived V27 artifact contains private keys: " + ", ".join(unsafe))


def validate_archived_attempt1_report(report: Mapping[str, Any]) -> None:
    """Validate the exact immutable failed report without current-source comparison."""

    _validate_no_private_keys(report)
    if content_digest(report) != ARCHIVED_REPORT_CONTENT_DIGEST:
        raise ValueError("archived V27 attempt-1 report content drift")


def validate_archived_attempt1_claim(claim: Mapping[str, Any]) -> None:
    """Validate the exact immutable one-shot claim without reopening it."""

    _validate_no_private_keys(claim)
    if content_digest(claim) != ARCHIVED_CLAIM_CONTENT_DIGEST:
        raise ValueError("archived V27 attempt-1 claim content drift")


def _retired() -> NoReturn:
    raise V27ManualEvaluationConfigurationError(RETIREMENT_REASON)


def _preflight_shape(**_kwargs: object) -> NoReturn:
    """Compatibility entry point that retires execution before fingerprinting or I/O."""

    _retired()


async def run(**_kwargs: object) -> dict[str, Any]:
    """Retire the old one-shot before evaluating any execution argument."""

    _retired()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect the retired first V27 production gate.")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--authorization-id")
    parser.add_argument("--max-provider-calls", type=int)
    parser.add_argument("--max-cost-usd", type=float)
    parser.add_argument("--authorized-plan-digest")
    parser.add_argument("--show-replies", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.execute:
        _retired()
    print(json.dumps(inspect_plan(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
