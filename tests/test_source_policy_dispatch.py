"""Dispatch re-admission uses actual transport attempts, not reusable queue permits."""

import asyncio
import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Self
from urllib.request import Request

import pytest

from satori.application.source_policy.contracts import SourceExclusionCommand
from satori.application.source_policy.dispatch import (
    admit_provider_dispatch,
    source_dispatch_admission,
)
from satori.application.source_policy.use_cases import SourcePolicyControls
from satori.core.conversation import (
    ConversationGenerationParameters,
    ConversationMessage,
    ConversationMessageRole,
    ConversationProviderRequest,
)
from satori.domain.source_policy import (
    CanonicalRootRef,
    ProtectedContentFootprint,
    SourcePolicyError,
)
from satori.infrastructure.providers.inference_scheduler import (
    InferencePriority,
    OllamaInferenceScheduler,
)
from satori.infrastructure.providers.ollama import OllamaConversationAdapter
from satori.infrastructure.providers.openai_http import OpenAIHttpClient
from satori.infrastructure.source_policy.runtime import SourcePolicyRuntime


@pytest.fixture
def runtime(tmp_path: Path) -> SourcePolicyRuntime:
    runtime = SourcePolicyRuntime(tmp_path / "trusted", "satori")
    runtime.enroll(reviewed=True, new_installation=True, lineage_id="lineage")
    return runtime


def _exclude(runtime: SourcePolicyRuntime) -> None:
    root = CanonicalRootRef("lineage", "satori", "alice", "session", "turn", "user")
    controls = SourcePolicyControls(runtime)
    preview = controls.preview(root)
    controls.apply(
        SourceExclusionCommand(
            message_id="user",
            expected_generation=preview.generation,
            request_id="exclude",
            preview_digest=preview.preview_digest,
        ),
        root,
    )


@dataclass
class _Admission:
    runtime: SourcePolicyRuntime
    events: list[str]

    def require(self, input_id: str) -> None:
        if input_id != "bound-input":
            raise SourcePolicyError("source_input_unknown")
        self.runtime.check_footprint(
            ProtectedContentFootprint(root_message_ids=("user",)), "dispatch"
        )
        self.events.append("admitted")

    def dispatch(self, input_id: str) -> None:
        self.require(input_id)


@dataclass
class _Unit:
    source_admission: _Admission


@contextmanager
def _unit(runtime: SourcePolicyRuntime, events: list[str]) -> Iterator[_Unit]:
    with runtime.shared():
        yield _Unit(_Admission(runtime, events))
    events.append("closed")


class _Response:
    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def read(self, _limit: int) -> bytes:
        return json.dumps(
            {
                "model": "synthetic",
                "message": {"role": "assistant", "content": "reply"},
                "done": True,
                "done_reason": "stop",
            }
        ).encode()


def _request() -> ConversationProviderRequest:
    return ConversationProviderRequest(
        schema_version=1,
        trace_id="synthetic",
        context_schema_version=1,
        messages=(ConversationMessage(ConversationMessageRole.USER, "synthetic input"),),
        parameters=ConversationGenerationParameters(1, 0.0, 32),
    )


def test_dispatch_lease_closes_before_network_and_context_propagates_to_thread(
    runtime: SourcePolicyRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    def transport(_request: Request, *, timeout: float) -> _Response:
        assert events == ["admitted", "closed"]
        with runtime.exclusive():
            events.append("network")
        return _Response()

    monkeypatch.setattr("satori.infrastructure.providers.ollama.urlopen", transport)
    provider = OllamaConversationAdapter("http://127.0.0.1:1", "synthetic", 1)
    with source_dispatch_admission(lambda: _unit(runtime, events), "bound-input"):
        response = asyncio.run(provider.generate(_request()))
    assert response.text == "reply"
    assert events == ["admitted", "closed", "network"]
    admit_provider_dispatch()
    assert len(events) == 3


def test_queued_ollama_attempt_rechecks_after_scheduler_wait(
    runtime: SourcePolicyRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[str] = []

    def transport(_request: Request, *, timeout: float) -> _Response:
        sent.append("sent")
        return _Response()

    monkeypatch.setattr("satori.infrastructure.providers.ollama.urlopen", transport)

    async def scenario() -> None:
        scheduler = OllamaInferenceScheduler()
        provider = OllamaConversationAdapter(
            "http://127.0.0.1:1", "synthetic", 1, scheduler=scheduler
        )

        async def inference() -> None:
            with source_dispatch_admission(lambda: _unit(runtime, []), "bound-input"):
                await provider.generate(_request())

        async with scheduler.reserve(InferencePriority.EPISODE):
            task = asyncio.create_task(inference())
            await asyncio.sleep(0)
            assert scheduler.waiting_count == 1
            _exclude(runtime)
        with pytest.raises(SourcePolicyError, match="canonical_source_excluded"):
            await task

    asyncio.run(scenario())
    assert sent == []


def test_retry_and_fallback_get_fresh_admission(runtime: SourcePolicyRuntime) -> None:
    events: list[str] = []
    with source_dispatch_admission(lambda: _unit(runtime, events), "bound-input"):
        admit_provider_dispatch()
        _exclude(runtime)
        with pytest.raises(SourcePolicyError, match="canonical_source_excluded"):
            admit_provider_dispatch()
    assert events == ["admitted", "closed"]


def test_managed_dispatch_never_accepts_missing_input(runtime: SourcePolicyRuntime) -> None:
    with (
        source_dispatch_admission(lambda: _unit(runtime, []), None),
        pytest.raises(SourcePolicyError, match="source_input_required"),
    ):
        admit_provider_dispatch()


def test_http_pool_wait_rechecks_immediately_before_request(
    runtime: SourcePolicyRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[str] = []

    class Connection:
        timeout = 1.0

        def request(self, *args: object, **kwargs: object) -> None:
            sent.append("sent")

        def close(self) -> None:
            pass

    connection = Connection()

    def queued_acquire(self: OpenAIHttpClient, timeout_seconds: float) -> Connection:
        _exclude(runtime)
        return connection

    monkeypatch.setattr(OpenAIHttpClient, "_acquire", queued_acquire)
    monkeypatch.setattr(OpenAIHttpClient, "_release", lambda *args, **kwargs: None)
    client = OpenAIHttpClient("https://api.openai.com/v1", "synthetic-key")
    with (
        source_dispatch_admission(lambda: _unit(runtime, []), "bound-input"),
        pytest.raises(SourcePolicyError, match="canonical_source_excluded"),
    ):
        client.post_json(
            "/responses", {"input": "synthetic"}, timeout_seconds=1, max_response_bytes=100
        )
    assert sent == []


def test_two_coroutines_do_not_share_bound_dispatch_receipts(runtime: SourcePolicyRuntime) -> None:
    observed: list[str] = []

    async def scenario() -> None:
        async def checked(input_id: str) -> None:
            with source_dispatch_admission(lambda: _unit(runtime, observed), input_id):
                await asyncio.sleep(0)
                await asyncio.to_thread(admit_provider_dispatch)

        results = await asyncio.gather(
            checked("bound-input"), checked("foreign-input"), return_exceptions=True
        )
        assert results[0] is None
        assert isinstance(results[1], SourcePolicyError)
        assert results[1].reason_code == "source_input_unknown"

    asyncio.run(scenario())
    assert observed.count("admitted") == 1
