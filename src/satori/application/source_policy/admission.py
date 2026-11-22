"""Optional core-test boundary; managed application UoWs always expose the strict port."""

from typing import Protocol, cast

from satori.domain.source_policy import PayloadRef, SourcePolicyError


class SourceInputSession(Protocol):
    def prepare(
        self, *, root_message_ids: tuple[str, ...], payload_refs: tuple[PayloadRef, ...]
    ) -> str: ...
    def require(self, input_id: str) -> None: ...
    def dispatch(self, input_id: str) -> None: ...
    def record(self, kind: str, payload_id: str, version: int, input_id: str) -> None: ...


def prepare_source_input(
    unit: object,
    *,
    root_message_ids: tuple[str, ...] = (),
    payload_refs: tuple[PayloadRef, ...] = (),
) -> str | None:
    admission = cast(SourceInputSession | None, getattr(unit, "source_admission", None))
    if admission is None:
        return None
    result = admission.prepare(root_message_ids=root_message_ids, payload_refs=payload_refs)
    cast(_CommitUnit, unit).commit()
    return result


class _CommitUnit(Protocol):
    def commit(self) -> None: ...


def require_source_inputs(unit: object, input_id: str | None) -> None:
    admission = cast(SourceInputSession | None, getattr(unit, "source_admission", None))
    if admission is not None:
        if input_id is None:
            raise SourcePolicyError("source_input_required")
        admission.require(input_id)


def record_payload(
    unit: object, kind: str, payload_id: str, version: int, input_id: str | None
) -> None:
    admission = cast(SourceInputSession | None, getattr(unit, "source_admission", None))
    if admission is not None:
        if input_id is None:
            raise SourcePolicyError("source_input_required")
        admission.record(kind, payload_id, version, input_id)


def admit_source_dispatch(unit: object, input_id: str | None) -> None:
    admission = cast(SourceInputSession | None, getattr(unit, "source_admission", None))
    if admission is not None:
        if input_id is None:
            raise SourcePolicyError("source_input_required")
        admission.dispatch(input_id)
