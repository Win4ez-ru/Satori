"""Provider-free local controls through the single MemoryManager write boundary."""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from satori.application.memory.lifecycle_contracts import (
    MemoryLifecycleCommand,
    MemoryLifecycleEffects,
    MemoryLifecycleError,
    MemoryLifecyclePreview,
    MemoryLifecycleReceipt,
    MemoryLifecycleResult,
)
from satori.application.memory.ports import EpisodicMemoryUnitOfWork
from satori.core.clock import Clock
from satori.core.ids import IdGenerator
from satori.domain.memory import MemoryLifecycleStatus, MemoryManager
from satori.domain.memory_lifecycle import (
    MemoryLifecycleProposal,
    memory_lifecycle_effects,
    memory_lifecycle_preview_digest,
)
from satori.domain.memory_scope import MemoryScope
from satori.domain.source_policy import SourcePolicyError

_SAFE_REJECTIONS = frozenset(
    {
        "memory_scope_required",
        "memory_lifecycle_target_mismatch",
        "memory_lifecycle_status_invalid",
        "memory_lifecycle_version_conflict",
        "memory_lifecycle_preview_mismatch",
        "memory_lifecycle_source_ineligible",
    }
)


@dataclass(slots=True)
class MemoryLifecycleControls:
    """Scope proof precedes all receipt lookup, evaluation and durable writes."""

    unit_of_work_factory: Callable[[], EpisodicMemoryUnitOfWork]
    manager: MemoryManager
    clock: Clock
    id_generator: IdGenerator
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("satori.memory"))

    def preview(
        self,
        memory_id: str,
        target_status: MemoryLifecycleStatus,
        *,
        scope: MemoryScope,
    ) -> MemoryLifecyclePreview:
        """Read current proven metadata without creating a request or approval."""

        if not isinstance(scope, MemoryScope):
            raise MemoryLifecycleError("memory_scope_required")
        if not isinstance(target_status, MemoryLifecycleStatus):
            raise MemoryLifecycleError("memory_lifecycle_status_invalid")
        with self.unit_of_work_factory() as unit_of_work:
            memory = unit_of_work.episodic_memory.get_memory(
                memory_id, scope=scope, include_archived=True
            )
            if memory is None:
                raise MemoryLifecycleError("memory_lifecycle_target_unavailable")
            return MemoryLifecyclePreview(
                scope=scope,
                memory_id=memory.memory_id,
                current_status=memory.lifecycle_status,
                current_version=memory.aggregate_version,
                target_status=target_status,
                effects=MemoryLifecycleEffects(**memory_lifecycle_effects(target_status)),
                preview_digest=memory_lifecycle_preview_digest(memory, scope, target_status),
            )

    def execute(
        self,
        command: MemoryLifecycleCommand,
        *,
        scope: MemoryScope,
        trace_id: str,
    ) -> MemoryLifecycleResult:
        """Atomically record a decision, or return its unchanged historical receipt."""

        if not isinstance(command, MemoryLifecycleCommand):
            raise MemoryLifecycleError("memory_lifecycle_command_required")
        if not isinstance(scope, MemoryScope):
            raise MemoryLifecycleError("memory_scope_required")
        try:
            proposal = MemoryLifecycleProposal(
                memory_id=command.memory_id,
                scope=scope,
                target_status=command.target_status,
                expected_version=command.expected_version,
                request_id=command.request_id,
                preview_digest=command.preview_digest,
            )
            # A concurrent request winner needs one fresh proof and receipt read, not a retry write.
            for attempt in range(2):
                result = self._execute_once(proposal, trace_id=trace_id, replay_only=attempt == 1)
                if result is not None:
                    return result
            raise MemoryLifecycleError("memory_lifecycle_concurrent_change")
        except (MemoryLifecycleError, SourcePolicyError):
            raise
        except ValueError as error:
            reason = str(error)
            raise MemoryLifecycleError(
                reason if reason in _SAFE_REJECTIONS else "memory_lifecycle_rejected"
            ) from None
        except Exception as error:
            # SQL, injected storage faults and transaction errors must not expose private payload.
            self.logger.warning(
                "memory_lifecycle_commit_failed",
                extra={"satori_fields": {"error_type": type(error).__name__}},
            )
            raise MemoryLifecycleError("memory_lifecycle_commit_failed") from None

    def _execute_once(
        self, proposal: MemoryLifecycleProposal, *, trace_id: str, replay_only: bool
    ) -> MemoryLifecycleResult | None:
        with self.unit_of_work_factory() as unit_of_work:
            repository = unit_of_work.episodic_memory
            current = repository.get_memory(
                proposal.memory_id, scope=proposal.scope, include_archived=True
            )
            if current is None:
                raise MemoryLifecycleError("memory_lifecycle_target_unavailable")
            prior = repository.get_lifecycle_decision(proposal.request_id)
            if prior is not None:
                if not prior.matches(proposal):
                    raise MemoryLifecycleError("memory_lifecycle_request_conflict")
                return MemoryLifecycleResult(
                    receipt=MemoryLifecycleReceipt.from_decision(prior),
                    replayed=True,
                    current_status=current.lifecycle_status,
                    current_version=current.aggregate_version,
                )
            if replay_only:
                raise MemoryLifecycleError("memory_lifecycle_concurrent_change")
            decision = self.manager.evaluate_lifecycle(
                proposal,
                current,
                scope=proposal.scope,
                decision_id=self.id_generator.new(),
                decided_at=self.clock.now(),
                trace_id=trace_id,
            )
            if not repository.record_lifecycle_decision(
                decision, audit_event_id=self.id_generator.new()
            ):
                return None
            result = MemoryLifecycleResult(
                receipt=MemoryLifecycleReceipt.from_decision(decision),
                replayed=False,
                current_status=decision.to_status,
                current_version=decision.result_version,
            )
            unit_of_work.commit()
            return result
