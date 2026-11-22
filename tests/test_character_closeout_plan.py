"""Offline schedule, integer budget, one-shot authorization and restart guard checks."""

from __future__ import annotations

import copy
import hashlib
import json
import stat
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest

from satori.application.conversation.response_validation import ResponseRegenerationReason
from tests.character_closeout_plan import (
    CAP_BY_OPERATION,
    MODEL,
    OPERATION_CAPS,
    CloseoutGuardError,
    CloseoutPaidLedger,
    build_execution_plan,
    claim_paid_invocation,
    digest,
    file_digest,
    grant_template,
    write_private_json,
)

CORPUS = Path(__file__).parents[1] / "docs/evaluation/character-closeout-scenarios.v1.json"


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 100
    output_tokens: int = 20
    cached_input_tokens: int = 0
    cache_write_input_tokens: int = 0
    reasoning_output_tokens: int = 0


DEFAULT_USAGE = Usage()


def _plan() -> dict[str, Any]:
    return build_execution_plan(
        CORPUS,
        source_hashes={"docs/evaluation/character-closeout-scenarios.v1.json": file_digest(CORPUS)},
    )


def _payload(operation: str = "foreground") -> dict[str, object]:
    cap = CAP_BY_OPERATION[operation]
    if cap.embedding:
        return {
            "model": "text-embedding-3-small",
            "input": "authored test source",
            "dimensions": 768,
            "encoding_format": "float",
        }
    return {
        "model": MODEL,
        "input": [{"role": "user", "content": "authored test source"}],
        "max_output_tokens": cap.max_output_tokens,
        "reasoning": {"effort": "none"},
        "service_tier": "default",
        "prompt_cache_options": {"mode": "explicit"},
        "store": False,
    }


def _ledger() -> tuple[CloseoutPaidLedger, list[dict[str, Any]]]:
    snapshots: list[dict[str, Any]] = []
    ledger = CloseoutPaidLedger(_plan(), snapshots.append)
    ledger.begin_turn("independent:01:1")
    return ledger, snapshots


def _complete_operation(
    ledger: CloseoutPaidLedger, operation: str, *, usage: Usage = DEFAULT_USAGE
) -> None:
    payload = _payload(operation)
    call = ledger.reserve(operation, str(payload["model"]), payload)
    if operation.startswith("embedding_"):
        usage = replace(usage, output_tokens=0)
    ledger.settle(call, usage=usage, response_model=str(payload["model"]), outcome="completed")


def _complete_turn(ledger: CloseoutPaidLedger) -> None:
    for operation in ("foreground", "affect", "relationship", "episode"):
        _complete_operation(ledger, operation)
    ledger.end_turn()


def test_schedule_accounts_for_corpus_continuous_and_relationship_contrasts() -> None:
    plan = _plan()
    assert plan["turn_count"] == 132
    assert plan["mode_turn_counts"] == {
        "independent": 90,
        "continuous": 30,
        "relationship_familiar_A": 4,
        "relationship_strained_A": 4,
        "relationship_fresh_B": 4,
    }
    assert plan["maximum_provider_calls"] == 1056
    assert plan["maximum_cost_nano_usd"] == 74_115_317_760
    assert plan["maximum_cost_usd"] == "74.115317760"
    assert plan["calls_executed"] == 0
    assert plan["sampled_replies"] == []
    assert plan["expected_actual_cost"] == "UNKNOWN_UNTIL_REAL_USAGE"
    assert plan["profile_fixture"]["actual_api_relationship_formation"] is False
    assert plan["provider"]["automatic_transport_retries"] == 0
    assert plan["behavior"]["comparison_arms"] == 1
    assert plan["behavior"]["repair_interpretation"] is False


def test_every_authored_branch_is_preserved_without_extra_paid_turns() -> None:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    plan = _plan()
    for case in corpus["cases"]:
        for turn in case["user_turns"]:
            expected = turn.get("branches") or [
                {"text": turn["text"], "condition": "unconditional"}
            ]
            actual = next(
                row
                for row in plan["turns"]
                if row["turn_id"] == f"independent:{case['id']}:{turn['number']}"
            )
            assert actual["branch_options"] == expected
            assert actual["maximum_user_utf8_bytes"] == max(
                len(option["text"].encode("utf-8")) for option in expected
            )
    memory = [
        row
        for row in plan["turns"]
        if row["mode"] == "independent" and row["case_id"] in {"12", "17"}
    ]
    assert all(
        row["dependency"] == "actual_completed_independent_01_owner_snapshot" for row in memory
    )


def test_integer_worst_case_simulation_completes_all_1056_reserved_operations() -> None:
    plan = _plan()
    ledger = CloseoutPaidLedger(plan, lambda _: None)
    for turn in plan["turns"]:
        ledger.begin_turn(turn["turn_id"])
        for cap in OPERATION_CAPS:
            usage = Usage(
                input_tokens=cap.input_token_cap,
                output_tokens=cap.max_output_tokens,
                cache_write_input_tokens=0 if cap.embedding else cap.input_token_cap,
            )
            _complete_operation(ledger, cap.operation, usage=usage)
            if cap.operation == "foreground":
                ledger.authorize_validator_retry("masculine_self_reference")
                _complete_operation(ledger, cap.operation, usage=usage)
        ledger.end_turn()
    snapshot = ledger.snapshot()
    assert snapshot["provider_call_count"] == 1056
    assert snapshot["charged_nano_usd"] == plan["maximum_cost_nano_usd"]
    assert snapshot["actual_known_nano_usd"] == plan["maximum_cost_nano_usd"]
    assert snapshot["visited_turn_count"] == 132
    assert snapshot["usage_complete"] is True
    assert snapshot["poisoned"] is False


def test_cache_aware_usage_has_exact_integer_cost_and_retains_no_prompt_text() -> None:
    ledger, snapshots = _ledger()
    _complete_operation(
        ledger,
        "foreground",
        usage=Usage(
            input_tokens=100, output_tokens=20, cached_input_tokens=30, cache_write_input_tokens=10
        ),
    )
    snapshot = ledger.snapshot()
    assert snapshot["charged_nano_usd"] == 60 * 2000 + 30 * 200 + 10 * 2500 + 20 * 12000
    assert snapshots[-1] == snapshot
    assert "authored test source" not in json.dumps(snapshot)
    assert len(snapshot["calls"][0]["wire_sha256"]) == 64


@pytest.mark.parametrize(
    ("operation", "changes"),
    [
        ("foreground", {"tools": [{"type": "web_search"}]}),
        ("foreground", {"service_tier": "priority"}),
        ("foreground", {"reasoning": {"effort": "low"}}),
        ("foreground", {"store": True}),
        ("foreground", {"max_output_tokens": 769}),
        ("foreground", {"max_output_tokens": True}),
        ("foreground", {"prompt_cache_options": {"mode": "implicit"}}),
        ("embedding_query", {"dimensions": 1536}),
        ("embedding_index", {"input": ["one", "two"]}),
    ],
)
def test_wire_drift_rejected_before_any_reservation(
    operation: str, changes: dict[str, object]
) -> None:
    ledger, _ = _ledger()
    payload = _payload(operation)
    payload.update(changes)
    with pytest.raises(CloseoutGuardError):
        ledger.reserve(operation, str(payload["model"]), payload)
    assert ledger.snapshot()["provider_call_count"] == 0


def test_body_byte_overflow_is_rejected_without_clipping_or_paying() -> None:
    ledger, _ = _ledger()
    payload = _payload()
    payload["input"] = [{"role": "user", "content": "я" * 30_000}]
    with pytest.raises(CloseoutGuardError, match="byte ceiling"):
        ledger.reserve("foreground", MODEL, payload)
    assert len(str(payload["input"])) > 30_000
    assert ledger.snapshot()["provider_call_count"] == 0


def test_retry_requires_one_completed_foreground_and_exact_typed_reason() -> None:
    ledger, _ = _ledger()
    with pytest.raises(CloseoutGuardError):
        ledger.authorize_validator_retry("masculine_self_reference")
    _complete_operation(ledger, "foreground")
    with pytest.raises(CloseoutGuardError, match="not a validator retry"):
        ledger.reserve("foreground", MODEL, _payload())
    with pytest.raises(CloseoutGuardError):
        ledger.authorize_validator_retry("I disliked the reply")
    ledger.authorize_validator_retry("masculine_self_reference")
    _complete_operation(ledger, "foreground")
    with pytest.raises(CloseoutGuardError, match="limit"):
        ledger.reserve("foreground", MODEL, _payload())


@pytest.mark.parametrize("reason", list(ResponseRegenerationReason))
def test_every_actual_production_validator_reason_can_authorize_the_shared_retry(
    reason: ResponseRegenerationReason,
) -> None:
    ledger, _ = _ledger()
    _complete_operation(ledger, "foreground")
    ledger.authorize_validator_retry(reason.value)
    _complete_operation(ledger, "foreground")
    assert ledger.snapshot()["calls"][1]["validator_reason"] == reason.value


@pytest.mark.parametrize(
    ("usage", "outcome", "response_model"),
    [
        (None, "completed", MODEL),
        (Usage(cache_write_input_tokens=101), "completed", MODEL),
        (Usage(cached_input_tokens=70, cache_write_input_tokens=40), "completed", MODEL),
        (Usage(output_tokens=769), "completed", MODEL),
        (Usage(input_tokens=60_000), "completed", MODEL),
        (Usage(reasoning_output_tokens=1), "completed", MODEL),
        (Usage(), "transport_error", None),
        (Usage(), "incomplete", MODEL),
        (Usage(), "completed", "gpt-5.6-sol"),
    ],
)
def test_uncertain_or_failed_paid_call_keeps_reservation_and_blocks_whole_run(
    usage: Usage | None, outcome: str, response_model: str | None
) -> None:
    ledger, _ = _ledger()
    reservation = ledger.reserve("foreground", MODEL, _payload())
    with pytest.raises(CloseoutGuardError, match="stopped"):
        ledger.settle(reservation, usage=usage, response_model=response_model, outcome=outcome)
    snapshot = ledger.snapshot()
    assert snapshot["poisoned"] is True
    assert snapshot["charged_nano_usd"] == CAP_BY_OPERATION["foreground"].cost_nano_usd
    with pytest.raises(CloseoutGuardError, match="forbids continuation"):
        ledger.reserve("affect", MODEL, _payload("affect"))


def test_durable_reservation_failure_prevents_dispatch_and_continuation() -> None:
    counter = 0

    def persist(_: dict[str, Any]) -> None:
        nonlocal counter
        counter += 1
        if counter > 1:
            raise OSError("disk full")

    ledger = CloseoutPaidLedger(_plan(), persist)
    ledger.begin_turn("independent:01:1")
    with pytest.raises(OSError, match="disk full"):
        ledger.reserve("foreground", MODEL, _payload())
    assert ledger.snapshot()["poisoned"] is True
    with pytest.raises(CloseoutGuardError):
        ledger.reserve("foreground", MODEL, _payload())


def test_concurrent_unsettled_call_and_repeated_settlement_are_rejected() -> None:
    ledger, _ = _ledger()
    reservation = ledger.reserve("foreground", MODEL, _payload())
    with pytest.raises(CloseoutGuardError, match="unsettled"):
        ledger.reserve("affect", MODEL, _payload("affect"))
    ledger.settle(reservation, usage=Usage(), response_model=MODEL, outcome="completed")
    with pytest.raises(CloseoutGuardError, match="already settled"):
        ledger.settle(reservation, usage=Usage(), response_model=MODEL, outcome="completed")


def test_restart_restores_exact_costs_and_forbids_completed_turn_replay() -> None:
    ledger, _ = _ledger()
    _complete_turn(ledger)
    restored = CloseoutPaidLedger.from_snapshot(
        _plan(), ledger.snapshot(), on_change=lambda _: None
    )
    assert restored.snapshot() == ledger.snapshot()
    with pytest.raises(CloseoutGuardError, match="already consumed"):
        restored.begin_turn("independent:01:1")
    restored.begin_turn("independent:01:2")
    _complete_turn(restored)
    assert restored.snapshot()["visited_turn_count"] == 2


@pytest.mark.parametrize("tamper", ["cost", "usage", "plan", "active", "in_flight", "duplicate"])
def test_restart_rejects_tampered_or_uncertain_journals(tamper: str) -> None:
    ledger, _ = _ledger()
    _complete_turn(ledger)
    snapshot = ledger.snapshot()
    if tamper == "cost":
        snapshot["calls"][0]["charged_nano_usd"] = 0
    elif tamper == "usage":
        snapshot["calls"][0]["usage"]["input_tokens"] += 1
    elif tamper == "plan":
        snapshot["plan_sha256"] = "0" * 64
    elif tamper == "active":
        snapshot["active_turn_id"] = "independent:01:2"
    elif tamper == "in_flight":
        snapshot["calls"][0]["status"] = "in_flight"
    else:
        snapshot["visited_turn_ids"].append("independent:01:1")
    with pytest.raises(CloseoutGuardError):
        CloseoutPaidLedger.from_snapshot(_plan(), snapshot, on_change=lambda _: None)


def _granted(plan: dict[str, Any]) -> dict[str, Any]:
    grant = grant_template(plan)
    grant.update(
        status="GRANTED",
        grant_id="offline-test-only-grant",
        authorized_invocations=1,
        human_authorization_text_sha256=hashlib.sha256(b"offline synthetic grant").hexdigest(),
        human_authorization_received_at="2026-10-16T00:00:00Z",
    )
    return grant


def test_ungranted_template_cannot_claim_or_create_an_artifact(tmp_path: Path) -> None:
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    plan = _plan()
    with pytest.raises(CloseoutGuardError, match="grant"):
        claim_paid_invocation(
            plan, grant_template(plan), repository_root=CORPUS.parents[2], claim_directory=private
        )
    assert list(private.iterdir()) == []


def test_grant_is_exact_single_use_private_and_source_bound(tmp_path: Path) -> None:
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    plan = _plan()
    grant = _granted(plan)
    claimed = claim_paid_invocation(
        plan, grant, repository_root=CORPUS.parents[2], claim_directory=private
    )
    assert stat.S_IMODE(claimed.stat().st_mode) == 0o600
    assert json.loads(claimed.read_text())["repeat_authorized"] is False
    with pytest.raises(FileExistsError):
        claim_paid_invocation(
            plan, grant, repository_root=CORPUS.parents[2], claim_directory=private
        )


def test_stale_source_or_budget_cannot_consume_a_fresh_grant(tmp_path: Path) -> None:
    source = tmp_path / "source.py"
    source.write_text("original", encoding="utf-8")
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    plan = build_execution_plan(CORPUS, source_hashes={"source.py": file_digest(source)})
    grant = _granted(plan)
    source.write_text("changed", encoding="utf-8")
    with pytest.raises(CloseoutGuardError, match="source changed"):
        claim_paid_invocation(plan, grant, repository_root=tmp_path, claim_directory=private)
    grant["maximum_cost_nano_usd"] -= 1
    with pytest.raises(CloseoutGuardError, match="grant"):
        claim_paid_invocation(plan, grant, repository_root=tmp_path, claim_directory=private)
    assert list(private.iterdir()) == []


def test_artifacts_are_exclusive_and_refuse_public_or_symlink_targets(tmp_path: Path) -> None:
    public = tmp_path / "public"
    public.mkdir(mode=0o755)
    public.chmod(0o755)
    with pytest.raises(CloseoutGuardError, match="0700"):
        write_private_json(public / "public.json", {})
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    target = private / "artifact.json"
    write_private_json(target, {"status": "NOT_RUN"})
    with pytest.raises(FileExistsError):
        write_private_json(target, {"status": "fake"})
    symlink = private / "link.json"
    symlink.symlink_to(target)
    with pytest.raises(FileExistsError):
        write_private_json(symlink, {})
    assert json.loads(target.read_text()) == {"status": "NOT_RUN"}


def test_source_bindings_and_plan_digest_include_every_turn_and_cap() -> None:
    plan = _plan()
    original = digest(plan)
    altered = copy.deepcopy(plan)
    altered["turns"][0]["branch_options"][0]["text"] += "changed"
    assert digest(altered) != original
    altered = copy.deepcopy(plan)
    altered["operations"][0]["maximum_body_bytes"] += 1
    assert digest(altered) != original


def test_in_memory_plan_drift_poisoned_before_another_paid_call() -> None:
    plan = _plan()
    ledger = CloseoutPaidLedger(plan, lambda _: None)
    ledger.begin_turn("independent:01:1")
    plan["maximum_cost_nano_usd"] += 1
    with pytest.raises(CloseoutGuardError, match="in-memory execution plan changed"):
        ledger.reserve("foreground", MODEL, _payload())
    assert ledger.snapshot()["provider_call_count"] == 0
    assert ledger.snapshot()["poisoned"] is True


@pytest.mark.parametrize("response_model", [MODEL, "gpt-5.6-terra-2026-08-01"])
def test_actual_foreground_bridge_and_ledger_agree_on_closed_model_aliases(
    response_model: str,
) -> None:
    from tests.character_closeout_api_adapters import CloseoutForegroundTransport
    from tests.test_character_closeout_api_adapters import (
        ScriptedCloseoutTransport,
        offline_api_response,
    )

    ledger, _ = _ledger()
    response = offline_api_response(text="Synthetic adapter plumbing only.")
    response["model"] = response_model
    transport = ScriptedCloseoutTransport(json.dumps(response).encode())
    bridge = CloseoutForegroundTransport(transport, ledger)
    bridge.post_json("/responses", _payload(), timeout_seconds=1.0, max_response_bytes=10_000)
    snapshot = ledger.snapshot()
    assert len(transport.calls) == 1
    assert snapshot["calls"][0]["response_model"] == response_model
    assert snapshot["calls"][0]["status"] == "completed"
