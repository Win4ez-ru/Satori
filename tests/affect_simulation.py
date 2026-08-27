"""Deterministic no-provider Stage 7 simulation harness."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from satori.core.affect import AffectiveAppraisalProposal
from satori.domain.affect import (
    AFFECT_POLICY_V1,
    AffectiveStateSnapshot,
    AppraisalDecision,
    EmotionManager,
    initial_affective_state,
    materialize_affective_state,
)
from satori.domain.personality import Personality


@dataclass(frozen=True, slots=True)
class SimulationMetrics:
    """Small deterministic measurements used to justify and regress policy parameters."""

    minimum: dict[str, float]
    maximum: dict[str, float]
    mood_peak: dict[str, float]
    final_drift: dict[str, float]


class AffectSimulation:
    """Apply timestamped controlled appraisals without any LLM or persistence adapter."""

    def __init__(self, personality: Personality, *, origin: datetime) -> None:
        self.personality = personality
        self.origin = origin
        self.manager = EmotionManager()
        self.state = initial_affective_state("simulation", initialized_at=origin)
        self.snapshots: list[AffectiveStateSnapshot] = [self.state]

    def apply(
        self,
        proposal: AffectiveAppraisalProposal,
        *,
        seconds: float,
        interaction_id: str,
    ) -> AppraisalDecision:
        decision = self.manager.evaluate(
            proposal,
            self.state,
            self.personality,
            interaction_id=interaction_id,
            allowed_source_refs=(interaction_id,),
            event_time=self.origin + timedelta(seconds=seconds),
        )
        if decision.transition is not None:
            self.state = decision.transition.after
            self.snapshots.append(self.state)
        return decision

    def read(self, *, seconds: float) -> AffectiveStateSnapshot:
        return materialize_affective_state(
            self.state,
            at=self.origin + timedelta(seconds=seconds),
        )

    def metrics(self, *, final_seconds: float) -> SimulationMetrics:
        final = self.read(seconds=final_seconds)
        observed = (*self.snapshots, final)
        minimum = {
            key: min(getattr(item.fast, key) for item in observed)
            for key in self.state.fast.field_names()
        }
        maximum = {
            key: max(getattr(item.fast, key) for item in observed)
            for key in self.state.fast.field_names()
        }
        mood_peak = {
            key: max(getattr(item.mood, key) for item in observed)
            for key in self.state.mood.field_names()
        }
        final_drift = {
            key: abs(getattr(final.fast, key) - AFFECT_POLICY_V1.fast_dimension(key).baseline)
            for key in self.state.fast.field_names()
        }
        return SimulationMetrics(minimum, maximum, mood_peak, final_drift)
