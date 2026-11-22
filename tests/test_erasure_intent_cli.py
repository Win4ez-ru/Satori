"""Operator CLI routes do not bootstrap, disclose storage failures or erase content."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import pytest

import satori.__main__ as cli
from satori.application.erasure.intent_contracts import ErasureIntentCommand, ErasureIntentRequest
from satori.config import Environment, Settings
from satori.domain.source_policy import SourceEligibilitySnapshot, SourcePolicyError
from satori.infrastructure.persistence.database import Database

_PRIVATE = "private_content_must_not_be_rendered"


@dataclass
class _Output:
    main_projection_current: bool = True

    def model_dump_json(self, *, indent: int) -> str:
        return json.dumps(
            {
                "main_projection_current": self.main_projection_current,
                "phase": "intent_committed_preparation_blocked",
                "physical_erasure": False,
                "execution_authorized": False,
            },
            indent=indent,
        )


class _Manager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ErasureIntentRequest, str | None]] = []
        self.output = _Output()
        self.error: Exception | None = None

    def _call(
        self, operation: str, request: ErasureIntentRequest, request_id: str | None = None
    ) -> _Output:
        self.calls.append((operation, request, request_id))
        if self.error is not None:
            raise self.error
        return self.output

    def preview(self, request: ErasureIntentRequest) -> _Output:
        return self._call("preview", request)

    def commit(self, request: ErasureIntentCommand) -> _Output:
        return self._call("commit", request, request.request_id)

    def resume(self, request: ErasureIntentRequest, *, request_id: str) -> _Output:
        return self._call("resume", request, request_id)

    def status(self, request: ErasureIntentRequest, *, request_id: str) -> _Output:
        return self._call("status", request, request_id)


class _Sessions:
    def __init__(self) -> None:
        self.info: dict[str, object] = {}

    def configure(self, *, info: dict[str, object]) -> None:
        self.info = info

    @contextmanager
    def __call__(self) -> Iterator[object]:
        yield object()


class _Database:
    def __init__(self) -> None:
        self.session_factory = _Sessions()
        self.disposed = 0

    def dispose(self) -> None:
        self.disposed += 1


class _Runtime:
    def __init__(self) -> None:
        self.verify_mirror: object = None
        self.upgrades = 0

    def upgrade_policy(self) -> SourceEligibilitySnapshot:
        self.upgrades += 1
        return SourceEligibilitySnapshot(
            "floor", "installation", "lineage", "satori", 1, frozenset({"user-root"})
        )


@dataclass
class _Harness:
    settings: Settings
    database: _Database = field(default_factory=_Database)
    runtime: _Runtime = field(default_factory=_Runtime)
    manager: _Manager = field(default_factory=_Manager)
    writable_calls: list[bool] = field(default_factory=list)


@pytest.fixture
def operator_cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _Harness:
    harness = _Harness(
        Settings(
            environment=Environment.TEST,
            database_url=f"sqlite+pysqlite:///{tmp_path / 'existing.db'}",
            source_policy_directory=tmp_path / "policy",
            default_counterparty_id="alice",
        )
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("intent operator control called bootstrap/provider/enrollment/ordinary bind")

    for name in (
        "_open_services",
        "upgrade_database",
        "enroll_installation",
        "repair_installation",
        "bind_installation",
        "_configured_conversation_provider",
        "_configured_episode_provider",
        "_configured_embedding_provider",
        "_configured_semantic_provider",
        "_configured_affective_provider",
        "_configured_relationship_provider",
        "_configured_model_provider",
        "_configured_position_provider",
        "_configured_reflection_provider",
        "OllamaHttpClient",
        "OpenAIHttpClient",
        "YandexAIStudioHttpClient",
    ):
        monkeypatch.setattr(cli, name, forbidden)

    def open_database(database_url: str, *, writable: bool = False) -> Database:
        assert database_url == harness.settings.database_url
        harness.writable_calls.append(writable)
        return cast(Database, harness.database)

    def make_runtime(directory: Path, identity_id: str) -> _Runtime:
        assert directory == harness.settings.source_policy_directory
        assert identity_id == "satori"
        return harness.runtime

    monkeypatch.setattr(cli, "open_existing_erasure_database", open_database)
    monkeypatch.setattr(cli, "installation_identity", lambda _database: "satori")
    monkeypatch.setattr(cli, "SourcePolicyRuntime", make_runtime)
    monkeypatch.setattr(cli, "SQLiteErasureInventory", lambda _database: object())
    monkeypatch.setattr(cli, "SQLiteErasurePreparationStore", lambda _database: object())
    monkeypatch.setattr(cli, "ErasureIntentManager", lambda *_args: harness.manager)
    return harness


@pytest.mark.parametrize(
    ("mode", "extra", "writable"),
    [
        ("preview", [], False),
        ("status", ["--request-id", "request"], False),
        ("resume", ["--request-id", "request"], True),
        (
            "commit",
            [
                "--expected-generation",
                "1",
                "--expected-erasure-revision",
                "0",
                "--sha256",
                "b" * 64,
                "--request-id",
                "request",
            ],
            True,
        ),
    ],
)
def test_intent_modes_select_exact_owner_operation_and_existing_database_access(
    operator_cli: _Harness,
    capsys: pytest.CaptureFixture[str],
    mode: str,
    extra: list[str],
    writable: bool,
) -> None:
    assert (
        cli.main(
            ["sources", "erasure-intent", "user-root", f"--{mode}", *extra],
            settings=operator_cli.settings,
        )
        == 0
    )
    assert operator_cli.writable_calls == [writable]
    assert operator_cli.database.disposed == 1
    operation, request, request_id = operator_cli.manager.calls[0]
    assert operation == mode
    assert request.message_id == "user-root"
    assert request.counterparty_id == "alice"
    assert request_id == (None if mode == "preview" else "request")
    assert operator_cli.database.session_factory.info["source_runtime"] is operator_cli.runtime
    assert callable(operator_cli.runtime.verify_mirror)
    if mode == "commit":
        assert isinstance(request, ErasureIntentCommand)
        assert request.expected_generation == 1
        assert request.expected_erasure_revision == 0
        assert request.approval_digest == "b" * 64
    rendered = capsys.readouterr()
    result = json.loads(rendered.out)
    assert result["phase"] == "intent_committed_preparation_blocked"
    assert result["physical_erasure"] is False
    assert result["execution_authorized"] is False
    assert rendered.err == ""


def test_stale_main_projection_is_nonzero_without_claiming_deletion(
    operator_cli: _Harness, capsys: pytest.CaptureFixture[str]
) -> None:
    operator_cli.manager.output.main_projection_current = False
    assert (
        cli.main(
            ["sources", "erasure-intent", "user-root", "--status", "--request-id", "request"],
            settings=operator_cli.settings,
        )
        == 2
    )
    result = json.loads(capsys.readouterr().out)
    assert result["main_projection_current"] is False
    assert result["physical_erasure"] is False


def test_policy_upgrade_is_explicit_and_opens_main_read_only(
    operator_cli: _Harness, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        cli.main(
            ["sources", "erasure-policy", "--upgrade", "--reviewed"],
            settings=operator_cli.settings,
        )
        == 0
    )
    assert operator_cli.writable_calls == [False]
    assert operator_cli.runtime.upgrades == 1
    assert operator_cli.manager.calls == []
    assert operator_cli.database.disposed == 1
    output = json.loads(capsys.readouterr().out)
    assert output["operation"] == "upgrade_erasure_policy"
    assert output["generation"] == 1
    assert output["erasure_revision"] == 0
    assert output["execution_authorized"] is False


@pytest.mark.parametrize(
    "arguments",
    [
        ["erasure-intent", "user-root", "--commit"],
        ["erasure-intent", "user-root", "--resume"],
        [
            "erasure-intent",
            "user-root",
            "--status",
            "--request-id",
            "request",
            "--sha256",
            "a" * 64,
        ],
        ["erasure-intent", "user-root", "--preview", "--request-id", "request"],
    ],
)
def test_incomplete_or_mixed_operations_are_rejected_before_opening_storage(
    operator_cli: _Harness, capsys: pytest.CaptureFixture[str], arguments: list[str]
) -> None:
    assert cli.main(["sources", *arguments], settings=operator_cli.settings) == 2
    assert operator_cli.writable_calls == []
    assert operator_cli.manager.calls == []
    assert "Erasure intent rejected:" in capsys.readouterr().err


@pytest.mark.parametrize(
    "arguments",
    [
        ["erasure-policy", "--upgrade"],
        ["erasure-policy", "--reviewed"],
        ["erasure-intent", "user-root", "--apply"],
        ["erasure-intent", "user-root", "--delete"],
        ["erasure-intent", "user-root", "--status", "--request-id", " "],
    ],
)
def test_parser_has_no_destructive_alias_and_requires_reviewed_upgrade(
    operator_cli: _Harness, capsys: pytest.CaptureFixture[str], arguments: list[str]
) -> None:
    with pytest.raises(SystemExit) as rejected:
        cli.main(["sources", *arguments], settings=operator_cli.settings)
    assert rejected.value.code == 2
    assert operator_cli.writable_calls == []
    assert operator_cli.runtime.upgrades == 0
    assert _PRIVATE not in capsys.readouterr().err


@pytest.mark.parametrize(
    ("error", "expected_code", "expected_reason"),
    [
        (OSError(_PRIVATE), 1, "storage_failure"),
        (ValueError(_PRIVATE), 2, "invalid_command"),
        (
            SourcePolicyError("source_installation_binding_mismatch"),
            2,
            "source_installation_binding_mismatch",
        ),
        (SourcePolicyError(f"raw {_PRIVATE}"), 2, "policy_rejection"),
    ],
)
def test_operator_failures_are_sanitized_and_dispose_database(
    operator_cli: _Harness,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
    expected_code: int,
    expected_reason: str,
) -> None:
    operator_cli.manager.error = error
    assert (
        cli.main(
            ["sources", "erasure-intent", "user-root", "--resume", "--request-id", "request"],
            settings=operator_cli.settings,
        )
        == expected_code
    )
    output = capsys.readouterr()
    assert output.out == ""
    assert expected_reason in output.err
    assert _PRIVATE not in output.err
    assert operator_cli.database.disposed == 1


def test_absent_database_never_creates_an_installation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "absent.db"
    policy = tmp_path / "absent-policy"
    settings = Settings(
        environment=Environment.TEST,
        database_url=f"sqlite+pysqlite:///{database}",
        source_policy_directory=policy,
        default_counterparty_id="alice",
    )
    assert (
        cli.main(["sources", "erasure-intent", "absent-root", "--preview"], settings=settings) == 2
    )
    assert not database.exists()
    assert not policy.exists()
    assert _PRIVATE not in capsys.readouterr().err
