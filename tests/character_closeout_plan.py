"""Finite evaluation-only Character closeout schedule and fail-closed paid ledger.

Importing or preparing this module performs no provider, credential, or network operation.
The authored corpus is not a sampled result. A fresh explicit human grant is required for
one invocation, and every possible paid operation has an integer-cost reservation.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
import threading
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Protocol, cast

from satori.application.conversation.response_validation import ResponseRegenerationReason

MODEL = "gpt-5.6-terra"
EMBEDDING_MODEL = "text-embedding-3-small"
PLAN_VERSION = "character-closeout-api-v1"
PRICING_DATE = "2026-10-16"
NANO_USD = 1_000_000_000
INPUT_RATE = 2_000
CACHE_READ_RATE = 200
CACHE_WRITE_RATE = 2_500
OUTPUT_RATE = 12_000
EMBEDDING_RATE = 20
FRAMING_TOKEN_MARGIN = 4096
EMBEDDING_TOKEN_MARGIN = 512
SHORT_CONTEXT_MAX = 272_000
CONTINUOUS_CASES = ("01", "06", "12", "17", "20")
VALIDATOR_REASONS = frozenset(reason.value for reason in ResponseRegenerationReason)
RESPONSE_MODEL_ALIASES = frozenset({MODEL, "gpt-5.6-terra-2026-08-01"})


class CloseoutGuardError(RuntimeError):
    """Reject execution before any unbudgeted or ungranted provider dispatch."""


class UsageLike(Protocol):
    """Exact usage captured by evaluation adapters before any lossy projection."""

    @property
    def input_tokens(self) -> int: ...

    @property
    def output_tokens(self) -> int: ...

    @property
    def cached_input_tokens(self) -> int: ...

    @property
    def cache_write_input_tokens(self) -> int: ...

    @property
    def reasoning_output_tokens(self) -> int: ...


@dataclass(frozen=True, slots=True)
class OperationCap:
    """Wire-body bytes, a conservative token bound, and total output token cap."""

    operation: str
    max_body_bytes: int
    max_output_tokens: int
    calls_per_turn: int = 1
    embedding: bool = False

    @property
    def input_token_cap(self) -> int:
        return self.max_body_bytes + (
            EMBEDDING_TOKEN_MARGIN if self.embedding else FRAMING_TOKEN_MARGIN
        )

    @property
    def cost_nano_usd(self) -> int:
        if self.embedding:
            return self.input_token_cap * EMBEDDING_RATE
        return self.input_token_cap * CACHE_WRITE_RATE + self.max_output_tokens * OUTPUT_RATE


OPERATION_CAPS = (
    OperationCap("foreground", 49_152, 768, calls_per_turn=2),
    OperationCap("affect", 16_384, 512),
    OperationCap("relationship", 8_192, 256),
    OperationCap("episode", 32_768, 768),
    OperationCap("semantic", 24_576, 1024),
    OperationCap("embedding_query", 7_680, 0, embedding=True),
    OperationCap("embedding_index", 7_680, 0, embedding=True),
)
CAP_BY_OPERATION = {cap.operation: cap for cap in OPERATION_CAPS}


def canonical_json_bytes(value: object) -> bytes:
    """Deterministic UTF-8 bytes for digests and conservative wire-size checks."""

    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def digest(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def wire_json_bytes(value: object) -> bytes:
    """Match the existing OpenAI HTTP serializer, including its whitespace."""

    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _positive_int(value: object, name: str) -> int:
    if type(value) is not int or value < 1:
        raise CloseoutGuardError(f"{name} must be a positive integer")
    return value


def _sha(value: object, name: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise CloseoutGuardError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _turns(corpus: dict[str, Any]) -> list[dict[str, Any]]:
    cases = cast(list[dict[str, Any]], corpus["cases"])
    if [case["id"] for case in cases] != [f"{number:02d}" for number in range(1, 21)]:
        raise CloseoutGuardError("closeout corpus must contain exactly cases 01 through 20")
    by_id = {case["id"]: case for case in cases}
    result: list[dict[str, Any]] = []

    def append_case(mode: str, case_id: str, profile: str, counterparty: str) -> None:
        case = by_id[case_id]
        for authored in case["user_turns"]:
            number = authored["number"]
            options = (
                [{"text": authored["text"], "condition": "unconditional"}]
                if "text" in authored
                else authored["branches"]
            )
            if not options or any(not option["text"].strip() for option in options):
                raise CloseoutGuardError("every authored turn needs a nonempty branch")
            turn_id = f"{mode}:{case_id}:{number}"
            result.append(
                {
                    "turn_id": turn_id,
                    "mode": mode,
                    "case_id": case_id,
                    "turn_number": number,
                    "profile": profile,
                    "counterparty_id": counterparty,
                    "branch_options": copy.deepcopy(options),
                    "maximum_user_utf8_bytes": max(
                        len(option["text"].encode("utf-8")) for option in options
                    ),
                    "dependency": (
                        "actual_completed_independent_01_owner_snapshot"
                        if mode == "independent" and case_id in {"12", "17"}
                        else None
                    ),
                }
            )

    for case in cases:
        append_case("independent", case["id"], "fresh", "A")
    if len(result) != 90:
        raise CloseoutGuardError("independent design must have exactly 90 user turns")
    for case_id in CONTINUOUS_CASES:
        append_case("continuous", case_id, "continuous_actual", "A")
    for profile, counterparty in (("familiar", "A"), ("strained", "A"), ("fresh", "B")):
        append_case(f"relationship_{profile}_{counterparty}", "19", profile, counterparty)
    if len(result) != 132 or len({turn["turn_id"] for turn in result}) != 132:
        raise CloseoutGuardError("frozen complete schedule must have 132 unique user turns")
    return result


def build_execution_plan(corpus_path: Path, *, source_hashes: Mapping[str, str]) -> dict[str, Any]:
    """Build a complete ungranted plan; no provider or credential is composed."""

    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    if (
        corpus.get("status") != "AUTHORED_NOT_RUN"
        or corpus.get("generated_or_sampled_assistant_replies") != []
        or corpus.get("base_scenario_count") != 20
    ):
        raise CloseoutGuardError("only the unsampled authored closeout corpus is accepted")
    if not source_hashes:
        raise CloseoutGuardError("an execution plan must bind its source files")
    for path, sha in source_hashes.items():
        if not path or Path(path).is_absolute() or ".." in Path(path).parts:
            raise CloseoutGuardError("source bindings must use repository-relative paths")
        _sha(sha, "source binding")
    turns = _turns(corpus)
    operations: list[dict[str, Any]] = [
        {
            "operation": cap.operation,
            "model": EMBEDDING_MODEL if cap.embedding else MODEL,
            "maximum_calls_per_turn": cap.calls_per_turn,
            "maximum_body_bytes": cap.max_body_bytes,
            "guarded_input_tokens": cap.input_token_cap,
            "maximum_output_tokens": cap.max_output_tokens,
            "maximum_cost_per_call_nano_usd": cap.cost_nano_usd,
            "maximum_calls": len(turns) * cap.calls_per_turn,
            "maximum_total_nano_usd": len(turns) * cap.calls_per_turn * cap.cost_nano_usd,
        }
        for cap in OPERATION_CAPS
    ]
    total = sum(operation["maximum_total_nano_usd"] for operation in operations)
    return {
        "schema_version": 1,
        "plan_version": PLAN_VERSION,
        "status": "PREPARED_NOT_AUTHORIZED_NOT_RUN",
        "paid_authorization_present": False,
        "calls_executed": 0,
        "sampled_replies": [],
        "corpus_sha256": file_digest(corpus_path),
        "source_hashes": dict(sorted(source_hashes.items())),
        "provider": {
            "model": MODEL,
            "response_model_aliases": sorted(RESPONSE_MODEL_ALIASES),
            "embedding_model": EMBEDDING_MODEL,
            "embedding_dimensions": 768,
            "reasoning_effort": "none",
            "service_tier": "default",
            "store": False,
            "automatic_transport_retries": 0,
            "judge_calls": 0,
        },
        "behavior": {
            "policy": "V35",
            "previous_exchange_affect": False,
            "current_position": False,
            "prior_dialogue_relation": False,
            "repair_interpretation": False,
            "comparison_arms": 1,
        },
        "pricing": {
            "verified_date": PRICING_DATE,
            "unit": "nano_usd_per_token",
            "input": INPUT_RATE,
            "cache_read": CACHE_READ_RATE,
            "cache_write": CACHE_WRITE_RATE,
            "output": OUTPUT_RATE,
            "embedding_input": EMBEDDING_RATE,
            "reservation": "all input at cache-write rate; no assumed cache discount",
            "sources": [
                "https://developers.openai.com/api/docs/pricing",
                "https://developers.openai.com/api/docs/models/gpt-5.6-terra",
                "https://developers.openai.com/api/docs/guides/prompt-caching",
                "https://developers.openai.com/api/docs/guides/embeddings",
            ],
        },
        "turns": turns,
        "turn_count": len(turns),
        "mode_turn_counts": dict(Counter(turn["mode"] for turn in turns)),
        "operations": operations,
        "maximum_provider_calls": sum(operation["maximum_calls"] for operation in operations),
        "maximum_cost_nano_usd": total,
        "maximum_cost_usd": f"{total // NANO_USD}.{total % NANO_USD:09d}",
        "expected_actual_cost": "UNKNOWN_UNTIL_REAL_USAGE",
        "input_guard": {
            "method": "UTF-8 HTTP wire-body bytes plus fixed token framing margin",
            "responses_framing_token_margin": FRAMING_TOKEN_MARGIN,
            "embeddings_framing_token_margin": EMBEDDING_TOKEN_MARGIN,
            "overflow": "abort before HTTP; never silently clip or truncate",
        },
        "profile_fixture": {
            "status": "CONTROLLED_PROFILE_FIXTURE",
            "formation_evidence": "scripted source-bound proposals through existing owner",
            "actual_api_relationship_formation": False,
            "paid_conditioning_calls": 0,
            "canonical_familiar_contacts": 64,
            "canonical_strain_contacts": 4,
            "require_actual_owner_projection": True,
        },
        "restart": {
            "continuous": ["before case 17", "between case 20 turns 3 and 4"],
            "independent": ["before case 17", "between case 20 turns 3 and 4"],
            "same_database": True,
            "mid_conversation_reseed": False,
        },
        "branch_policy": {
            "rule": "one authored option per turn, bound to actual preceding public source",
            "automatic_model_judge": False,
            "unresolved_branch": "pause before next dispatch; persist source-bound choice",
            "additional_calls": 0,
        },
        "stop_conditions": [
            "missing or stale human grant, source digest, or readiness evidence",
            "transport failure or uncertain billing, including cancellation",
            "unknown, invalid, excessive, or incomplete exact provider usage",
            "unplanned operation, model, tier, payload field, call, or token bound",
            "incomplete response or invalid structured proposal",
            "unmet dependency, actual profile proof, or unresolved branch",
        ],
    }


def grant_template(plan: Mapping[str, Any]) -> dict[str, Any]:
    """An explicitly ungranted template cannot consume old or implied authorization."""

    return {
        "schema_version": 1,
        "status": "NOT_GRANTED",
        "grant_id": None,
        "plan_sha256": digest(plan),
        "maximum_cost_nano_usd": plan["maximum_cost_nano_usd"],
        "maximum_provider_calls": plan["maximum_provider_calls"],
        "human_authorization_text_sha256": None,
        "human_authorization_received_at": None,
        "authorized_invocations": 0,
        "no_repeat_or_refill": True,
    }


def write_private_json(path: Path, value: object) -> None:
    """Exclusively create a private durable artifact without following symlinks."""

    parent_mode = path.parent.stat().st_mode
    if not stat.S_ISDIR(parent_mode) or stat.S_IMODE(parent_mode) != 0o700:
        raise CloseoutGuardError("artifact parent must be an existing private 0700 directory")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical_json_bytes(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        # A partial artifact is evidence of an interrupted attempt; do not erase or overwrite it.
        raise


def claim_paid_invocation(
    plan: Mapping[str, Any],
    grant: Mapping[str, Any],
    *,
    repository_root: Path,
    claim_directory: Path,
) -> Path:
    """Consume a digest-bound single-use grant before key access or runtime composition."""

    if (
        grant.get("status") != "GRANTED"
        or grant.get("authorized_invocations") != 1
        or grant.get("no_repeat_or_refill") is not True
        or grant.get("plan_sha256") != digest(plan)
        or grant.get("maximum_cost_nano_usd") != plan["maximum_cost_nano_usd"]
        or grant.get("maximum_provider_calls") != plan["maximum_provider_calls"]
    ):
        raise CloseoutGuardError("fresh exact single-invocation human grant is absent or stale")
    grant_id = grant.get("grant_id")
    if not isinstance(grant_id, str) or re.fullmatch(r"[a-z0-9-]{8,96}", grant_id) is None:
        raise CloseoutGuardError("grant_id is missing or unsafe")
    _sha(grant.get("human_authorization_text_sha256"), "human authorization")
    if not isinstance(grant.get("human_authorization_received_at"), str):
        raise CloseoutGuardError("human authorization timestamp is absent")
    for relative, expected in plan["source_hashes"].items():
        path = repository_root / relative
        if path.is_symlink() or file_digest(path) != expected:
            raise CloseoutGuardError("source changed after the execution plan was frozen")
    claim_path = claim_directory / f"claim-{grant_id}.json"
    write_private_json(
        claim_path,
        {
            "status": "CONSUMED_BEFORE_CREDENTIAL_ACCESS",
            "grant_id": grant_id,
            "grant_sha256": digest(grant),
            "plan_sha256": digest(plan),
            "maximum_cost_nano_usd": plan["maximum_cost_nano_usd"],
            "maximum_provider_calls": plan["maximum_provider_calls"],
            "repeat_authorized": False,
        },
    )
    return claim_path


@dataclass(slots=True)
class CloseoutPaidLedger:
    """Reserve before dispatch; any uncertain or failed call poisons the whole invocation."""

    plan: Mapping[str, Any]
    on_change: Callable[[dict[str, Any]], None]
    _calls: list[dict[str, Any]] = field(default_factory=list, init=False, repr=False)
    _turn_id: str | None = field(default=None, init=False, repr=False)
    _visited: set[str] = field(default_factory=set, init=False, repr=False)
    _retry_reason: str | None = field(default=None, init=False, repr=False)
    _poisoned: bool = field(default=False, init=False, repr=False)
    _bound_plan_sha256: str = field(default="", init=False, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.plan.get("plan_version") != PLAN_VERSION or self.plan.get("turn_count") != 132:
            raise CloseoutGuardError("unknown or incomplete execution plan")
        expected_total = sum(cap.cost_nano_usd * cap.calls_per_turn * 132 for cap in OPERATION_CAPS)
        if self.plan.get("maximum_cost_nano_usd") != expected_total:
            raise CloseoutGuardError("plan monetary bounds differ from the frozen wire caps")
        if self.plan.get("maximum_provider_calls") != 1056:
            raise CloseoutGuardError("plan must account for every possible paid operation")
        operations = self.plan.get("operations")
        if not isinstance(operations, list) or len(operations) != len(OPERATION_CAPS):
            raise CloseoutGuardError("operation contract is absent or incomplete")
        for authored, cap in zip(operations, OPERATION_CAPS, strict=True):
            if (
                authored.get("operation") != cap.operation
                or authored.get("maximum_calls_per_turn") != cap.calls_per_turn
                or authored.get("maximum_body_bytes") != cap.max_body_bytes
                or authored.get("guarded_input_tokens") != cap.input_token_cap
                or authored.get("maximum_output_tokens") != cap.max_output_tokens
                or authored.get("maximum_cost_per_call_nano_usd") != cap.cost_nano_usd
            ):
                raise CloseoutGuardError("operation contract differs from the frozen bounds")
        self._bound_plan_sha256 = digest(self.plan)

    @classmethod
    def from_snapshot(
        cls,
        plan: Mapping[str, Any],
        snapshot: Mapping[str, Any],
        *,
        on_change: Callable[[dict[str, Any]], None],
    ) -> CloseoutPaidLedger:
        """Continue only a settled turn-boundary journal from the same consumed campaign.

        The caller must also validate the existing claim, runtime receipt and DB boundary.
        This does not issue a new grant or permit replaying any completed turn.
        """

        ledger = cls(plan, on_change)
        if (
            snapshot.get("schema_version") != 1
            or snapshot.get("plan_sha256") != digest(plan)
            or snapshot.get("poisoned") is not False
            or snapshot.get("active_turn_id") is not None
            or snapshot.get("maximum_cost_nano_usd") != plan["maximum_cost_nano_usd"]
            or snapshot.get("maximum_provider_calls") != plan["maximum_provider_calls"]
        ):
            raise CloseoutGuardError("restart lacks a settled exact campaign boundary")
        calls = snapshot.get("calls")
        visited = snapshot.get("visited_turn_ids")
        if not isinstance(calls, list) or not isinstance(visited, list):
            raise CloseoutGuardError("restart journal shape is invalid")
        planned_ids = {turn["turn_id"] for turn in plan["turns"]}
        if len(set(visited)) != len(visited) or not set(visited).issubset(planned_ids):
            raise CloseoutGuardError("restart journal contains duplicate or unplanned turns")
        counters: Counter[tuple[str, str]] = Counter()
        for index, call in enumerate(calls, start=1):
            if not isinstance(call, dict) or call.get("reservation_id") != f"call-{index:04d}":
                raise CloseoutGuardError("restart call sequence is invalid")
            operation = call.get("operation")
            if operation not in CAP_BY_OPERATION or call.get("turn_id") not in visited:
                raise CloseoutGuardError("restart call has an unknown operation or turn")
            cap = CAP_BY_OPERATION[operation]
            key = (call["turn_id"], operation)
            counters[key] += 1
            if (
                counters[key] > cap.calls_per_turn
                or call.get("attempt") != counters[key]
                or call.get("status") != "completed"
                or call.get("outcome") != "completed"
                or call.get("model") != (EMBEDDING_MODEL if cap.embedding else MODEL)
                or call.get("response_model")
                not in ({EMBEDDING_MODEL} if cap.embedding else RESPONSE_MODEL_ALIASES)
                or call.get("guarded_input_tokens") != cap.input_token_cap
                or call.get("guarded_output_tokens") != cap.max_output_tokens
                or call.get("reserved_nano_usd") != cap.cost_nano_usd
                or type(call.get("wire_body_bytes")) is not int
                or not 0 < call["wire_body_bytes"] <= cap.max_body_bytes
                or (counters[key] == 2 and call.get("validator_reason") not in VALIDATOR_REASONS)
            ):
                raise CloseoutGuardError("restart call exceeds its exact frozen contract")
            _sha(call.get("wire_sha256"), "wire")
            usage = call.get("usage")
            if not isinstance(usage, dict):
                raise CloseoutGuardError("restart call lacks exact usage")
            exact = cls._exact_usage(call, cast(UsageLike, SimpleNamespace(**usage)))
            if (
                exact is None
                or exact[0] != call.get("charged_nano_usd")
                or exact[0] != call.get("actual_nano_usd")
            ):
                raise CloseoutGuardError("restart cost or token usage is inconsistent")
        for turn_id in visited:
            if any(
                counters[(turn_id, operation)] < 1
                for operation in ("foreground", "affect", "relationship", "episode")
            ):
                raise CloseoutGuardError("restart turn did not finish its mandatory operations")
        ledger._calls = copy.deepcopy(calls)
        ledger._visited = set(visited)
        expected = ledger.snapshot()
        for total_key in (
            "provider_call_count",
            "completed_call_count",
            "charged_nano_usd",
            "actual_known_nano_usd",
            "usage_complete",
            "visited_turn_count",
        ):
            if expected[total_key] != snapshot.get(total_key):
                raise CloseoutGuardError("restart aggregate ledger fields are inconsistent")
        return ledger

    def begin_turn(self, turn_id: str) -> None:
        with self._lock:
            self._require_healthy()
            if self._turn_id is not None or turn_id in self._visited:
                raise CloseoutGuardError("turn is active or already consumed")
            planned = {turn["turn_id"] for turn in self.plan["turns"]}
            if turn_id not in planned:
                raise CloseoutGuardError("unplanned turn")
            next_turn = next(
                (
                    turn["turn_id"]
                    for turn in self.plan["turns"]
                    if turn["turn_id"] not in self._visited
                ),
                None,
            )
            if turn_id != next_turn:
                raise CloseoutGuardError("turn order differs from the frozen schedule")
            self._turn_id = turn_id
            self._visited.add(turn_id)
            self._retry_reason = None
            self._persist()

    def end_turn(self) -> None:
        with self._lock:
            self._require_healthy()
            if self._turn_id is None or any(call["status"] == "in_flight" for call in self._calls):
                raise CloseoutGuardError("turn is absent or still has an unsettled call")
            counts = Counter(
                call["operation"] for call in self._calls if call["turn_id"] == self._turn_id
            )
            if any(
                counts[operation] < 1
                for operation in ("foreground", "affect", "relationship", "episode")
            ):
                raise CloseoutGuardError("mandatory owner operations are incomplete")
            self._turn_id = None
            self._retry_reason = None
            self._persist()

    def authorize_validator_retry(self, reason: str) -> None:
        with self._lock:
            self._require_healthy()
            foreground = [
                call
                for call in self._calls
                if call["turn_id"] == self._turn_id and call["operation"] == "foreground"
            ]
            if (
                self._turn_id is None
                or reason not in VALIDATOR_REASONS
                or len(foreground) != 1
                or foreground[0]["status"] != "completed"
                or self._retry_reason is not None
            ):
                raise CloseoutGuardError("retry lacks one actual typed validator failure")
            self._retry_reason = reason

    def reserve(self, operation: str, model: str, payload: Mapping[str, Any]) -> str:
        with self._lock:
            self._require_healthy()
            if self._turn_id is None or operation not in CAP_BY_OPERATION:
                raise CloseoutGuardError("paid dispatch lacks a planned turn or operation")
            if any(call["status"] == "in_flight" for call in self._calls):
                raise CloseoutGuardError("concurrent or unsettled paid calls are forbidden")
            if len(self._calls) >= 1056:
                raise CloseoutGuardError("the global paid-call ceiling is exhausted")
            cap = CAP_BY_OPERATION[operation]
            self._validate_payload(cap, model, payload)
            count = sum(
                call["turn_id"] == self._turn_id and call["operation"] == operation
                for call in self._calls
            )
            if count >= cap.calls_per_turn:
                raise CloseoutGuardError("per-operation turn limit reached")
            if operation == "foreground" and count == 1 and self._retry_reason is None:
                raise CloseoutGuardError("second foreground call is not a validator retry")
            body = wire_json_bytes(payload)
            if len(body) > cap.max_body_bytes:
                raise CloseoutGuardError("wire body exceeds the frozen byte ceiling; no clipping")
            reservation = cap.cost_nano_usd
            charged = sum(call["charged_nano_usd"] for call in self._calls)
            if charged + reservation > self.plan["maximum_cost_nano_usd"]:
                raise CloseoutGuardError("next reservation would exceed the exact total budget")
            reservation_id = f"call-{len(self._calls) + 1:04d}"
            self._calls.append(
                {
                    "reservation_id": reservation_id,
                    "turn_id": self._turn_id,
                    "operation": operation,
                    "model": model,
                    "attempt": count + 1,
                    "validator_reason": self._retry_reason if count else None,
                    "status": "in_flight",
                    "wire_body_bytes": len(body),
                    "wire_sha256": hashlib.sha256(body).hexdigest(),
                    "guarded_input_tokens": cap.input_token_cap,
                    "guarded_output_tokens": cap.max_output_tokens,
                    "reserved_nano_usd": reservation,
                    "charged_nano_usd": reservation,
                    "actual_nano_usd": None,
                }
            )
            self._persist()
            return reservation_id

    @staticmethod
    def _validate_payload(cap: OperationCap, model: str, payload: Mapping[str, Any]) -> None:
        if model != (EMBEDDING_MODEL if cap.embedding else MODEL) or payload.get("model") != model:
            raise CloseoutGuardError("model drift")
        if cap.embedding:
            if set(payload) != {"model", "input", "dimensions", "encoding_format"}:
                raise CloseoutGuardError("unexpected embedding endpoint fields")
            if payload["dimensions"] != 768 or payload["encoding_format"] != "float":
                raise CloseoutGuardError("embedding vector contract drift")
            if not isinstance(payload["input"], str) or not payload["input"]:
                raise CloseoutGuardError("embedding input must be one nonempty string")
            return
        allowed = {
            "model",
            "input",
            "max_output_tokens",
            "reasoning",
            "service_tier",
            "prompt_cache_options",
            "store",
            "temperature",
            "text",
        }
        if set(payload) - allowed:
            raise CloseoutGuardError("unplanned Responses fields or tools")
        if (
            payload.get("reasoning") != {"effort": "none"}
            or payload.get("service_tier") != "default"
            or payload.get("store") is not False
            or payload.get("prompt_cache_options") != {"mode": "explicit"}
            or not isinstance(payload.get("input"), list)
        ):
            raise CloseoutGuardError("Responses wire contract drift")
        output = _positive_int(payload.get("max_output_tokens"), "max_output_tokens")
        if output > cap.max_output_tokens:
            raise CloseoutGuardError("output token ceiling exceeded")

    def settle(
        self,
        reservation_id: str,
        *,
        usage: UsageLike | None,
        response_model: str | None,
        outcome: str,
    ) -> None:
        with self._lock:
            record = next(
                (call for call in self._calls if call["reservation_id"] == reservation_id), None
            )
            if record is None or record["status"] != "in_flight":
                raise CloseoutGuardError("unknown or already settled reservation")
            exact = self._exact_usage(record, usage)
            allowed_models = (
                {EMBEDDING_MODEL}
                if record["operation"].startswith("embedding_")
                else RESPONSE_MODEL_ALIASES
            )
            valid = (
                outcome == "completed" and response_model in allowed_models and exact is not None
            )
            record["status"] = "completed" if valid else "failed"
            record["outcome"] = outcome
            record["response_model"] = response_model
            record["actual_nano_usd"] = exact[0] if exact is not None else None
            record["usage"] = exact[1] if exact is not None else None
            if valid:
                assert exact is not None
                record["charged_nano_usd"] = exact[0]
            else:
                self._poisoned = True
            self._persist()
            if not valid:
                raise CloseoutGuardError("paid invocation stopped on failed or uncertain evidence")

    @staticmethod
    def _exact_usage(
        record: dict[str, Any], usage: UsageLike | None
    ) -> tuple[int, dict[str, int]] | None:
        if usage is None:
            return None
        values = {
            name: getattr(usage, name, None)
            for name in (
                "input_tokens",
                "output_tokens",
                "cached_input_tokens",
                "cache_write_input_tokens",
                "reasoning_output_tokens",
            )
        }
        if any(type(value) is not int or value < 0 for value in values.values()):
            return None
        typed = cast(dict[str, int], values)
        inputs = typed["input_tokens"]
        outputs = typed["output_tokens"]
        cached = typed["cached_input_tokens"]
        written = typed["cache_write_input_tokens"]
        if (
            cached + written > inputs
            or inputs > record["guarded_input_tokens"]
            or outputs > record["guarded_output_tokens"]
            or typed["reasoning_output_tokens"] != 0
        ):
            return None
        if record["operation"].startswith("embedding_"):
            if outputs or cached or written:
                return None
            cost = inputs * EMBEDDING_RATE
        else:
            cost = (
                (inputs - cached - written) * INPUT_RATE
                + cached * CACHE_READ_RATE
                + written * CACHE_WRITE_RATE
                + outputs * OUTPUT_RATE
            )
        if cost > record["reserved_nano_usd"]:
            return None
        return cost, typed

    def _require_healthy(self) -> None:
        if self._poisoned:
            raise CloseoutGuardError("a failed or uncertain paid call forbids continuation")
        if digest(self.plan) != self._bound_plan_sha256:
            self._poisoned = True
            raise CloseoutGuardError("the in-memory execution plan changed after binding")

    def _persist(self) -> None:
        try:
            self.on_change(self.snapshot())
        except BaseException:
            self._poisoned = True
            raise

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "schema_version": 1,
                "plan_sha256": self._bound_plan_sha256,
                "maximum_provider_calls": self.plan["maximum_provider_calls"],
                "maximum_cost_nano_usd": self.plan["maximum_cost_nano_usd"],
                "provider_call_count": len(self._calls),
                "completed_call_count": sum(call["status"] == "completed" for call in self._calls),
                "charged_nano_usd": sum(call["charged_nano_usd"] for call in self._calls),
                "actual_known_nano_usd": sum(call["actual_nano_usd"] or 0 for call in self._calls),
                "usage_complete": all(call["status"] == "completed" for call in self._calls),
                "poisoned": self._poisoned,
                "visited_turn_count": len(self._visited),
                "visited_turn_ids": sorted(self._visited),
                "active_turn_id": self._turn_id,
                "calls": copy.deepcopy(self._calls),
            }
