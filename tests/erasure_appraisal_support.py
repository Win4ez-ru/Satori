"""Managed actual Talk and uncited appraisal inputs, using only synthetic providers."""

from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from satori.application.erasure.appraisal_use_cases import AppraisalBodyManager
from satori.application.erasure.body_contracts import BodyErasureCommand, BodyErasureRequest
from satori.composition import ConversationServices
from satori.core.affect import AffectiveAppraisalProviderResponse, AffectiveAppraisalRequest
from satori.infrastructure.persistence.database import Database
from satori.infrastructure.persistence.erasure_appraisal import SQLiteAppraisalBodyStore
from tests.erasure_reflection_support import (
    ReflectionFixture,
    create_reflection_fixture,
    prepare_reflection,
    reflection_command,
)
from tests.fakes import (
    FakeAffectiveAppraisalProvider,
    FakeConversationProvider,
    FakeEmbeddingProvider,
)
from tests.test_source_exclusion_production import SECRET, _services
from tests.test_stage7_affect_integration import proposal_for


def create_appraisal_fixture(database: Database, directory: Path) -> ReflectionFixture:
    """A is read in B's actual context but B's declared source_refs cite only B."""

    def services(
        database: Database,
        directory: Path,
        prefix: str,
        day: int,
        *,
        form: bool = True,
        foreground: FakeConversationProvider | None = None,
        counterparty: str = "alice",
    ) -> tuple[ConversationServices, FakeConversationProvider, FakeEmbeddingProvider]:
        # Test factory deliberately retains the production composition and owner finalize.
        service, foreground, embedding = _services(
            database,
            directory,
            prefix,
            day,
            form=form,
            foreground=foreground,
            counterparty=counterparty,
        )
        prepared = service.talk.prepare_affect
        assert prepared is not None

        def response(request: AffectiveAppraisalRequest) -> AffectiveAppraisalProviderResponse:
            original = proposal_for(request.interaction_id)
            reasons = (
                (f"Observed synthetic code {SECRET}",)
                if SECRET in repr(request)
                else ("independent_synthetic_appraisal",)
            )
            return replace(original, proposal=replace(original.proposal, reason_codes=reasons))

        prepared.provider = FakeAffectiveAppraisalProvider(response_factory=response)
        return service, foreground, embedding

    with (
        patch("tests.erasure_support._services", services),
        patch("tests.erasure_reflection_support._services", services),
    ):
        return create_reflection_fixture(database, directory)


def prepare_appraisal(
    fixture: ReflectionFixture,
) -> tuple[AppraisalBodyManager, BodyErasureRequest]:
    reflection, request = prepare_reflection(fixture)
    assert reflection.commit(reflection_command(reflection, request)).main_projection_current
    fixture.case.runtime.upgrade_appraisal_policy()
    return AppraisalBodyManager(
        fixture.case.runtime, SQLiteAppraisalBodyStore(fixture.case.database)
    ), request


def appraisal_command(
    manager: AppraisalBodyManager, request: BodyErasureRequest, request_id: str = "appraisal-action"
) -> BodyErasureCommand:
    preview = manager.preview(request)
    assert preview.readiness == "ready", preview.blockers
    return BodyErasureCommand(
        **request.model_dump(),
        request_id=request_id,
        expected_generation=preview.generation,
        expected_erasure_revision=preview.erasure_revision,
        expected_body_revision=preview.body_revision,
        approval_digest=preview.approval_digest,
    )
