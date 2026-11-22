"""Strict public intent metadata; commitment never authorizes physical execution."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from satori.application.erasure.contracts import ErasurePreview, ErasurePreviewRequest
from satori.domain.erasure_intent import ErasureIntent
from satori.domain.erasure_preparation import ErasureOwnerPreparation


class ErasureIntentRequest(ErasurePreviewRequest):
    """Select one canonical root in the configured counterparty scope."""


class ErasureIntentCommand(ErasureIntentRequest):
    expected_generation: int = Field(ge=0)
    expected_erasure_revision: int = Field(ge=0)
    approval_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_id: str = Field(min_length=1, max_length=128, pattern=r"^\S(?:.*\S)?$")


class _NonExecutingIntentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    execution_authorized: Literal[False] = False

    @field_validator("execution_authorized", mode="before")
    @classmethod
    def reject_execution_permission(cls, value: object) -> Literal[False]:
        if value is not False:
            raise ValueError("erasure_intent_never_authorizes_execution")
        return False


class ErasureIntentPreview(_NonExecutingIntentModel):
    inventory: ErasurePreview
    erasure_revision: int = Field(ge=0)
    approval_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    retention_policy: str = Field(min_length=1, max_length=128)
    operation: Literal["record_terminal_erasure_intent"] = "record_terminal_erasure_intent"
    physical_execution_authorized: Literal[False] = False

    @field_validator("physical_execution_authorized", mode="before")
    @classmethod
    def reject_physical_permission(cls, value: object) -> Literal[False]:
        if value is not False:
            raise ValueError("erasure_intent_never_authorizes_execution")
        return False


class ErasureIntentResult(_NonExecutingIntentModel):
    intent: ErasureIntent
    main_projection_current: bool
    preparation: ErasureOwnerPreparation | None = None
    preparation_current: bool = False
    phase: Literal[
        "intent_committed_preparation_pending",
        "intent_committed_preparation_blocked",
        "intent_committed_preparation_retention_only",
    ]
    failure_reason: str | None = Field(default=None, max_length=128, pattern=r"^[a-z0-9_]+$")
    physical_erasure: Literal[False] = False

    @field_validator("physical_erasure", mode="before")
    @classmethod
    def reject_physical_completion(cls, value: object) -> Literal[False]:
        if value is not False:
            raise ValueError("erasure_intent_never_proves_physical_erasure")
        return False

    @model_validator(mode="after")
    def validate_preparation_binding(self) -> Self:
        if self.preparation is not None and self.preparation.intent != self.intent:
            raise ValueError("erasure_preparation_intent_mismatch")
        if self.preparation_current and (
            self.preparation is None or not self.main_projection_current
        ):
            raise ValueError("erasure_preparation_cannot_be_current")
        return self
