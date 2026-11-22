"""Prepare or execute a separately granted, bounded Character API diagnostic.

Preparation and synthetic verification never construct a real HTTP client or read
credentials. The paid command is unavailable without a fresh exact human grant.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import socket
import sqlite3
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from satori.config import Environment, Settings
from satori.infrastructure.providers.openai import OpenAIConversationAdapter
from satori.infrastructure.providers.openai_http import OpenAIHttpClient
from tests.character_closeout_api_adapters import (
    CloseoutAffectiveAppraisalAdapter,
    CloseoutEmbeddingAdapter,
    CloseoutEpisodeFormationAdapter,
    CloseoutForegroundTransport,
    CloseoutRelationshipAppraisalAdapter,
    CloseoutSemanticFormationAdapter,
)
from tests.character_closeout_plan import (
    CAP_BY_OPERATION,
    MODEL,
    CloseoutGuardError,
    CloseoutPaidLedger,
    build_execution_plan,
    claim_paid_invocation,
    digest,
    file_digest,
    grant_template,
    write_private_json,
)
from tests.character_closeout_runtime import (
    CloseoutRuntime,
    FixedEvaluationClock,
    RuntimePorts,
    build_runtime,
    condition_relationship_profile,
    json_value,
    resolve_turn_text,
    run_turn,
    verify_restart_snapshot,
)

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "docs/evaluation/character-closeout-scenarios.v1.json"


class EvaluationSettings(Settings):
    """Use reviewed literals only, including during an offline subprocess."""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings,)


def source_bindings() -> dict[str, str]:
    """Bind the complete current package/test material and execution inputs."""

    paths = [
        path
        for folder in ("src/satori", "tests", "migrations")
        for path in (ROOT / folder).rglob("*")
        if path.is_file() and path.suffix in {".py", ".json", ".typed", ".mako"}
    ]
    paths += [
        ROOT / name
        for name in (
            "pyproject.toml",
            "uv.lock",
            "alembic.ini",
            "docs/evaluation/character-closeout-scenarios.v1.json",
            "docs/character-closeout-scenarios.md",
            "docs/satori-character-specification.md",
            "docs/decisions/0078-character-closeout-api-evaluation.md",
        )
    ]
    return {str(path.relative_to(ROOT)): file_digest(path) for path in sorted(set(paths))}


def verify_installed_package() -> dict[str, Any]:
    import satori

    installed = Path(satori.__file__).parent
    source = ROOT / "src/satori"

    def material(folder: Path) -> dict[str, str]:
        return {
            str(path.relative_to(folder)): file_digest(path)
            for path in folder.rglob("*")
            if path.is_file() and path.suffix in {".py", ".json", ".typed"}
        }

    expected, actual = material(source), material(installed)
    if expected != actual or source.resolve() == installed.resolve():
        raise CloseoutGuardError("installed noneditable package differs from frozen source")
    return {"status": "PASS_EXACT_PACKAGE_PARITY", "package_files": len(expected)}


def private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=False, exist_ok=False)


def prepare(directory: Path) -> dict[str, Any]:
    parity = verify_installed_package()
    private_directory(directory)
    plan = build_execution_plan(CORPUS, source_hashes=source_bindings())
    plan["preparation_directory"] = str(directory.resolve())
    plan["package_parity"] = parity
    write_private_json(directory / "prepared-plan.json", plan)
    write_private_json(directory / "grant-template.json", grant_template(plan))
    write_private_json(
        directory / "readiness.json",
        {
            "status": "PREPARED_OFFLINE_VERIFICATION_REQUIRED",
            "plan_sha256": digest(plan),
            "api_calls": 0,
            "credential_access": False,
            "paid_authorized": False,
        },
    )
    return plan


def execution_jobs(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Explicit process boundaries; continuation reuses the same live store."""

    turns = plan["turns"]
    jobs: list[dict[str, Any]] = []
    for case in (f"{n:02d}" for n in range(1, 21)):
        selected = [t for t in turns if t["mode"] == "independent" and t["case_id"] == case]
        groups = [selected[:3], selected[3:]] if case == "20" else [selected]
        for part, group in enumerate(groups):
            jobs.append(
                {
                    "name": f"independent-{case}-{part}",
                    "store": "independent-01" if case == "17" else f"independent-{case}",
                    "activate": case not in {"12", "17"} and part == 0,
                    "new_session": case != "12",
                    "floor_store": (
                        "independent-01" if case in {"12", "17"} else f"independent-{case}"
                    ),
                    "advance_seconds": 3600 if case == "17" or part else 0,
                    "profile": None,
                    "counterparty": "A",
                    "turn_ids": [t["turn_id"] for t in group],
                }
            )
    continuous = [t for t in turns if t["mode"] == "continuous"]
    for part, group in enumerate((continuous[:18], continuous[18:27], continuous[27:])):
        jobs.append(
            {
                "name": f"continuous-{part}",
                "store": "continuous",
                "activate": part == 0,
                "new_session": part != 0,
                "advance_seconds": 3600 if part else 0,
                "profile": None,
                "counterparty": "A",
                "turn_ids": [t["turn_id"] for t in group],
            }
        )
    for mode, profile, counterparty in (
        ("relationship_familiar_A", "earned-familiar", "A"),
        ("relationship_strained_A", "relevant-strained", "A"),
        ("relationship_fresh_B", None, "B"),
    ):
        jobs.append(
            {
                "name": mode,
                "store": (
                    "relationship-profiles"
                    if mode == "relationship_familiar_A"
                    else "relationship-strained"
                ),
                "floor_store": "relationship-profiles",
                "activate": mode == "relationship_familiar_A",
                "new_session": True,
                "advance_seconds": 0,
                "profile": profile,
                "counterparty": counterparty,
                "turn_ids": [t["turn_id"] for t in turns if t["mode"] == mode],
            }
        )
    scheduled = [turn for job in jobs for turn in job["turn_ids"]]
    if len(scheduled) != 132 or len(set(scheduled)) != 132:
        raise CloseoutGuardError("process jobs do not cover the exact frozen schedule")
    return jobs


class RetryAuthorization(logging.Handler):
    def __init__(self, ledger: CloseoutPaidLedger) -> None:
        super().__init__(logging.INFO)
        self.ledger = ledger

    def emit(self, record: logging.LogRecord) -> None:
        if record.msg in {"duplicate_response_detected", "self_consistency_violation_detected"}:
            fields = getattr(record, "satori_fields", {})
            self.ledger.authorize_validator_retry(fields["reason"])


def _read(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text()))


def _event(value: Mapping[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False), flush=True)


def _paid_worker_authority(
    preparation: Path, control: Mapping[str, Any], plan: Mapping[str, Any]
) -> None:
    claim_path = Path(control["paid_claim_path"])
    registry = ROOT / "var/evaluations/.character-closeout-paid-claims"
    if claim_path.parent.resolve() != registry.resolve():
        raise CloseoutGuardError("worker claim is outside the canonical single-use registry")
    claim = _read(claim_path)
    grant = _read(Path(control["human_grant_path"]))
    ready = _read(preparation / "offline-receipt.json")
    if (
        grant.get("status") != "GRANTED"
        or grant.get("authorized_invocations") != 1
        or grant.get("plan_sha256") != digest(plan)
        or grant.get("maximum_cost_nano_usd") != plan["maximum_cost_nano_usd"]
        or grant.get("maximum_provider_calls") != plan["maximum_provider_calls"]
        or claim.get("grant_sha256") != digest(grant)
        or claim.get("plan_sha256") != digest(plan)
        or claim.get("status") != "CONSUMED_BEFORE_CREDENTIAL_ACCESS"
        or claim.get("repeat_authorized") is not False
        or ready.get("plan_sha256") != digest(plan)
        or ready.get("status") != "PASS_SYNTHETIC_ONLY"
    ):
        raise CloseoutGuardError("paid worker lacks its exact grant, claim or offline evidence")


def _ports(http: Any, ledger: CloseoutPaidLedger, *, credential: str) -> RuntimePorts:
    def structured(cls: Any, operation: str) -> Any:
        return cls(MODEL, 120.0, CAP_BY_OPERATION[operation].max_output_tokens, http, ledger)

    foreground = OpenAIConversationAdapter(
        "https://api.openai.com/v1",
        credential,
        MODEL,
        120.0,
        "none",
        0,
        http_client=CloseoutForegroundTransport(http, ledger),
    )
    return RuntimePorts(
        foreground=foreground,
        affect=structured(CloseoutAffectiveAppraisalAdapter, "affect"),
        relationship=structured(CloseoutRelationshipAppraisalAdapter, "relationship"),
        episode=structured(CloseoutEpisodeFormationAdapter, "episode"),
        semantic=structured(CloseoutSemanticFormationAdapter, "semantic"),
        embedding=CloseoutEmbeddingAdapter(
            model="text-embedding-3-small",
            dimensions=768,
            input_schema_version=1,
            timeout_seconds=120.0,
            http_client=http,
            guard=ledger,
            operation="embedding_query",
        ),
        embedding_index=CloseoutEmbeddingAdapter(
            model="text-embedding-3-small",
            dimensions=768,
            input_schema_version=1,
            timeout_seconds=120.0,
            http_client=http,
            guard=ledger,
            operation="embedding_index",
        ),
    )


def _latest_ledger(campaign: Path) -> tuple[int, dict[str, Any] | None]:
    files = sorted(campaign.glob("ledger-*.json"))
    return (int(files[-1].stem.split("-")[1]), _read(files[-1])) if files else (0, None)


def _previous_reply(campaign: Path, turn_id: str) -> str | None:
    path = campaign / ("turn-" + turn_id.replace(":", "_") + ".json")
    return _read(path)["actual_public_reply"] if path.exists() else None


def _operator_choice(
    *, turn: dict[str, Any], prior: str | None, synthetic: bool
) -> dict[str, Any] | None:
    if "text" in turn:
        return None
    if synthetic:
        # The wire-only foreground contains no joke, proposal or supporting argument.
        branch = turn["branches"][-1]
        return {
            "record": branch["record"],
            "prior_public_reply_sha256": digest_public(prior),
            "operator_reviewed": True,
        }
    _event(
        {
            "event": "branch_choice_required",
            "actual_prior_public_reply": prior,
            "actual_prior_public_reply_sha256": digest_public(prior),
            "branches": turn["branches"],
        }
    )
    # Human/operator reads the actual public reply. No model or phrase matcher decides.
    return cast(dict[str, Any], json.loads(input()))


def digest_public(reply: str | None) -> str:
    from tests.character_closeout_runtime import digest as runtime_digest

    return runtime_digest(reply)


async def worker(preparation: Path, campaign: Path, job_index: int, *, synthetic: bool) -> None:
    plan = _read(preparation / "prepared-plan.json")
    jobs = execution_jobs(plan)
    if type(job_index) is not int or not 0 <= job_index < len(jobs):
        raise CloseoutGuardError("internal worker process segment is outside the exact plan")
    job = jobs[job_index]
    control = _read(campaign / "campaign.json")
    if (
        control["plan_sha256"] != digest(plan)
        or control["synthetic"] is not synthetic
        or source_bindings() != plan["source_hashes"]
        or plan.get("preparation_directory") != str(preparation.resolve())
    ):
        raise CloseoutGuardError("worker input or frozen sources changed")
    if not synthetic:
        _paid_worker_authority(preparation, control, plan)
    verify_installed_package()
    for previous in range(job_index):
        completed_path = campaign / f"job-{previous:02d}-complete.json"
        if not completed_path.exists():
            raise CloseoutGuardError("previous process segment is incomplete")
        completed = _read(completed_path)
        if (
            completed["job"] != jobs[previous]
            or completed["synthetic"] is not synthetic
            or completed["ledger"]["plan_sha256"] != digest(plan)
            or completed["ledger"]["active_turn_id"] is not None
            or completed["ledger"]["poisoned"] is not False
            or not completed["ledger"]["usage_complete"]
        ):
            raise CloseoutGuardError("previous process receipt does not match this campaign")
    write_private_json(
        campaign / f"job-{job_index:02d}-claimed.json",
        {"plan_sha256": digest(plan), "job": job, "process_id": os.getpid()},
    )
    number, snapshot = _latest_ledger(campaign)

    def persist(value: dict[str, Any]) -> None:
        nonlocal number
        number += 1
        write_private_json(campaign / f"ledger-{number:05d}.json", value)

    ledger = (
        CloseoutPaidLedger(plan, persist)
        if snapshot is None
        else CloseoutPaidLedger.from_snapshot(plan, snapshot, on_change=persist)
    )
    expected_previous = {
        turn for previous_job in jobs[:job_index] for turn in previous_job["turn_ids"]
    }
    if set(ledger.snapshot()["visited_turn_ids"]) != expected_previous:
        raise CloseoutGuardError("durable ledger does not match the next process boundary")
    http: Any
    if synthetic:
        from tests.test_character_closeout_api_adapters import ScriptedCloseoutTransport

        def deny_network(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError("synthetic verification attempted a network connection")

        socket.create_connection = deny_network
        socket.socket.connect = deny_network  # type: ignore[method-assign]
        http = ScriptedCloseoutTransport()
        credential = "synthetic-never-sent"
    else:
        # All grant, source, previous-segment and exclusive-claim checks precede this read.
        credential = os.environ.get("OPENAI_API_KEY", "")
        if not credential:
            raise CloseoutGuardError("OPENAI_API_KEY is unavailable after authorized claim")
        http = OpenAIHttpClient("https://api.openai.com/v1", credential, pool_size=1)
    store = campaign / job["store"]
    if job["activate"]:
        private_directory(store)
    elif job["name"] == "independent-12-0":
        original = campaign / "independent-01"
        private_directory(store)
        with (
            sqlite3.connect(f"file:{original / 'state.db'}?mode=ro", uri=True) as origin_db,
            sqlite3.connect(store / "state.db") as target_db,
        ):
            origin_db.backup(target_db)
        os.chmod(store / "state.db", 0o600)
        write_private_json(store / "continuation.json", _read(original / "continuation.json"))
    elif not store.is_dir():
        raise CloseoutGuardError("required actual owner snapshot/store is absent")
    state_path = store / "continuation.json"
    prior_state = _read(state_path) if state_path.exists() else {}
    clock = FixedEvaluationClock(
        datetime.fromisoformat(prior_state["clock"])
        if prior_state
        else datetime(2026, 10, 16, 9, tzinfo=UTC)
    )
    clock.advance(job["advance_seconds"])
    active: CloseoutRuntime | None = None
    isolation_before: dict[str, Any] | None = None
    try:
        active = build_runtime(
            store / "state.db",
            source_policy_directory=(
                campaign / job.get("floor_store", job["store"]) / "source-policy"
            ),
            settings=EvaluationSettings(environment=Environment.TEST),
            ports=_ports(http, ledger, credential=credential),
            clock=clock,
            activate=job["activate"],
            alembic_config=ROOT / "alembic.ini",
            counterparty_id="character-closeout-" + job["counterparty"].lower(),
        )
        if prior_state:
            current = active.snapshot()
            if (
                current["identity_id"] != prior_state["snapshot"]["identity_id"]
                or current["self_sha256"] != prior_state["snapshot"]["self_sha256"]
            ):
                raise CloseoutGuardError("persistent self changed across process restart")
            if prior_state["snapshot"]["counterparty_id"] == current["counterparty_id"]:
                if prior_state["process_id"] == os.getpid():
                    raise CloseoutGuardError("declared restart reused the original process")
                proof = verify_restart_snapshot(prior_state["snapshot"], current)
                proof["before_process_id"] = prior_state["process_id"]
                proof["after_process_id"] = os.getpid()
                write_private_json(campaign / f"restart-{job_index:02d}.json", proof)
            elif current["relationship"]["state"]["processed_interaction_count"] != 0:
                raise CloseoutGuardError(
                    "counterparty B did not start with an isolated relationship"
                )
            else:
                isolation_before = json_value(
                    active.services.relationship_status.execute(
                        active.identity_id, "character-closeout-a"
                    )
                )
        if job["profile"]:
            fixture = await condition_relationship_profile(active, profile=job["profile"])
            write_private_json(campaign / f"profile-{job_index:02d}.json", fixture)
            if job["profile"] == "earned-familiar":
                fork = campaign / "relationship-strained"
                private_directory(fork)
                # Snapshot the canonical test store before the familiar probes.
                # The authoritative floor stays at its original inode/path.
                with (
                    sqlite3.connect(f"file:{store / 'state.db'}?mode=ro", uri=True) as source_db,
                    sqlite3.connect(fork / "state.db") as target,
                ):
                    source_db.backup(target)
                os.chmod(fork / "state.db", 0o600)
                write_private_json(
                    fork / "continuation.json",
                    {
                        "clock": clock.now().isoformat(),
                        "session_id": None,
                        "prior_public_reply": None,
                        "snapshot": active.snapshot(),
                        "process_id": os.getpid(),
                    },
                )
        session_id = (
            active.services.start_session.execute().session_id
            if job["new_session"] or not prior_state
            else prior_state["session_id"]
        )
        logger = active.services.talk.logger
        logger.addHandler(RetryAuthorization(ledger))
        logger.setLevel(logging.INFO)
        cases = {c["id"]: c for c in _read(CORPUS)["cases"]}
        rows = {t["turn_id"]: t for t in plan["turns"]}
        previous_reply: str | None = prior_state.get("prior_public_reply")
        for turn_id in job["turn_ids"]:
            row = rows[turn_id]
            authored = cases[row["case_id"]]["user_turns"][row["turn_number"] - 1]
            if row["case_id"] == "17" and row["turn_number"] == 1:
                memory = active.snapshot()["memories"]
                origin_mode = "independent" if row["mode"] == "independent" else "continuous"
                source = _read(campaign / f"turn-{origin_mode}_01_4.json")
                root = source["canonical_user_message_id"]
                if not any(root in m["source_user_message_ids"] for m in memory):
                    raise CloseoutGuardError("required actual episode source is missing")
            repair = (
                row["mode"] == "continuous" and (campaign / "turn-continuous_12_6.json").exists()
            )
            repair_branch = row["case_id"] == "20" and row["turn_number"] == 3
            choice = (
                None
                if repair_branch
                else _operator_choice(turn=authored, prior=previous_reply, synthetic=synthetic)
            )
            text, selected_branch = resolve_turn_text(
                authored,
                choice=choice,
                prior_public_reply=previous_reply,
                actual_prior_repair_present=repair,
            )
            ledger.begin_turn(turn_id)
            outcome = await run_turn(active, turn_id=turn_id, user_text=text, session_id=session_id)
            outcome["evidence_kind"] = "SYNTHETIC_WIRE_ONLY" if synthetic else "ACTUAL_API_BASELINE"
            outcome["selected_branch"] = selected_branch
            outcome["operator_choice"] = choice
            write_private_json(campaign / ("turn-" + turn_id.replace(":", "_") + ".json"), outcome)
            if not outcome["complete"]:
                raise CloseoutGuardError("owner lifecycle failed after the actual reply")
            ledger.end_turn()
            persist(ledger.snapshot())
            previous_reply = outcome["actual_public_reply"]
            clock.advance(60)
            _event({"event": "turn_complete", "turn_id": turn_id, "synthetic": synthetic})
        continuation = {
            "clock": clock.now().isoformat(),
            "session_id": session_id,
            "prior_public_reply": previous_reply,
            "snapshot": active.snapshot(),
            "process_id": os.getpid(),
        }
        if isolation_before is not None:
            isolation_after = json_value(
                active.services.relationship_status.execute(
                    active.identity_id, "character-closeout-a"
                )
            )
            if isolation_before != isolation_after:
                raise CloseoutGuardError("counterparty B mutated A's relationship")
            write_private_json(
                campaign / "counterparty-isolation.json",
                {
                    "status": "PASS_OWNER_ISOLATION",
                    "a_before": isolation_before,
                    "a_after": isolation_after,
                    "b_after": active.snapshot()["relationship"],
                    "same_identity_id": active.identity_id,
                },
            )
        # Continuation pointers are replaceable; every completed segment also has an
        # immutable private copy and the paid ledger is append-only.
        temporary = store / "continuation-next.json"
        write_private_json(temporary, continuation)
        temporary.replace(state_path)
        write_private_json(
            campaign / f"job-{job_index:02d}-complete.json",
            {
                "job": job,
                "continuation": continuation,
                "ledger": ledger.snapshot(),
                "process_id": os.getpid(),
                "synthetic": synthetic,
            },
        )
    finally:
        if active is not None:
            active.close()
        if not synthetic:
            http.close()


def run_campaign(
    preparation: Path, campaign: Path, *, synthetic: bool, grant_path: Path | None = None
) -> dict[str, Any]:
    plan = _read(preparation / "prepared-plan.json")
    if source_bindings() != plan["source_hashes"] or plan.get("preparation_directory") != str(
        preparation.resolve()
    ):
        raise CloseoutGuardError("source differs from frozen preparation")
    verify_installed_package()
    private_directory(campaign)
    claim: Path | None = None
    if not synthetic:
        if grant_path is None:
            raise CloseoutGuardError("a fresh human grant is required")
        readiness = _read(preparation / "offline-receipt.json")
        if readiness["plan_sha256"] != digest(plan) or readiness["status"] != "PASS_SYNTHETIC_ONLY":
            raise CloseoutGuardError("the exact plan lacks successful offline verification")
        registry = ROOT / "var/evaluations/.character-closeout-paid-claims"
        if registry.is_symlink():
            raise CloseoutGuardError("single-use registry must not be a symbolic link")
        registry.mkdir(mode=0o700, exist_ok=True)
        claim = claim_paid_invocation(
            plan, _read(grant_path), repository_root=ROOT, claim_directory=registry
        )
    write_private_json(
        campaign / "campaign.json",
        {
            "plan_sha256": digest(plan),
            "synthetic": synthetic,
            "paid_claim_path": str(claim) if claim else None,
            "human_grant_path": str(grant_path.absolute()) if grant_path else None,
        },
    )
    for index, _job in enumerate(execution_jobs(plan)):
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "tests.character_closeout_api_test",
                "_worker",
                "--preparation",
                str(preparation),
                "--campaign",
                str(campaign),
                "--job-index",
                str(index),
                *(["--synthetic"] if synthetic else []),
            ],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        assert process.stdout is not None
        assert process.stdin is not None
        try:
            for line in process.stdout:
                event = json.loads(line)
                if event.get("event") == "branch_choice_required":
                    _event(event)
                    process.stdin.write(input() + "\n")
                    process.stdin.flush()
            code = process.wait()
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        if code:
            raise CloseoutGuardError(f"campaign stopped at process segment {index}")
    _, ledger = _latest_ledger(campaign)
    if ledger is None or ledger["visited_turn_count"] != 132 or not ledger["usage_complete"]:
        raise CloseoutGuardError("complete real schedule evidence is missing")
    result = {
        "status": "PASS_SYNTHETIC_ONLY" if synthetic else "COMPLETED_AWAITING_HUMAN_REVIEW",
        "plan_sha256": digest(plan),
        "turns": 132,
        "api_calls": 0 if synthetic else ledger["provider_call_count"],
        "synthetic_http_calls": ledger["provider_call_count"] if synthetic else 0,
        "actual_cost_nano_usd": 0 if synthetic else ledger["actual_known_nano_usd"],
        "behavioral_acceptance": "NOT_ASSESSED",
        "ledger": ledger,
    }
    write_private_json(campaign / "result.json", result)
    moments = _read(CORPUS)["human_review"]["preselected_moments"]
    review = {
        "status": "SYNTHETIC_NOT_FOR_CHARACTER_REVIEW" if synthetic else "AWAITING_HUMAN_REVIEW",
        "plan_sha256": digest(plan),
        "comparison_arms": 1,
        "before_after_comparison": "NOT_AVAILABLE_BASELINE_ONLY",
        "rules": [
            "Read actual preceding dialogue, not isolated rewritten answers.",
            "Relative preference is not Character acceptance.",
            "Free-form feedback is not automatically converted into numeric scores.",
        ],
        "moments": [
            {
                **moment,
                "human_outcome": "unassessed",
                "feedback": "",
                "actual_transcripts": [
                    path.name
                    for path in sorted(campaign.glob("turn-*.json"))
                    if any(
                        path.stem.startswith(f"turn-independent_{case}_")
                        or ("relationship_" in path.stem and case == "19")
                        for case in moment["cases"]
                    )
                ],
            }
            for moment in moments
        ],
    }
    write_private_json(campaign / "human-review.json", review)
    if synthetic:
        write_private_json(preparation / "offline-receipt.json", result)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "offline", "execute", "_worker"))
    parser.add_argument("--preparation", type=Path, required=True)
    parser.add_argument("--campaign", type=Path)
    parser.add_argument("--grant", type=Path)
    parser.add_argument("--job-index", type=int)
    parser.add_argument("--synthetic", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            plan = prepare(args.preparation.absolute())
            _event(
                {
                    "status": plan["status"],
                    "maximum_cost_usd": plan["maximum_cost_usd"],
                    "maximum_calls": plan["maximum_provider_calls"],
                    "api_calls": 0,
                }
            )
        elif args.command == "_worker":
            if args.campaign is None or args.job_index is None:
                raise CloseoutGuardError("internal worker requires an exact process segment")
            asyncio.run(
                worker(
                    args.preparation.absolute(),
                    args.campaign.absolute(),
                    args.job_index,
                    synthetic=args.synthetic,
                )
            )
        else:
            if args.campaign is None:
                raise CloseoutGuardError("a new private campaign path is required")
            result = run_campaign(
                args.preparation.absolute(),
                args.campaign.absolute(),
                synthetic=args.command == "offline",
                grant_path=args.grant,
            )
            _event(
                {
                    key: result[key]
                    for key in ("status", "turns", "api_calls", "synthetic_http_calls")
                }
            )
    except (CloseoutGuardError, ValueError, FileExistsError) as error:
        _event({"status": "STOPPED", "error_type": type(error).__name__})
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
