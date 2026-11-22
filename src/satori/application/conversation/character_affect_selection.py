"""Pure current-turn affect reduction for discretionary character selection.

This read projection has no event interpretation, text, relationship authority or
write capability. Its load is a bounded selection weight, not an emotion intensity.
"""

import math
from dataclasses import dataclass
from typing import Literal

from satori.application.affect.contracts import EmotionalExpressionContext
from satori.domain.affect import AFFECT_POLICY_V1


@dataclass(frozen=True, slots=True)
class DiscretionaryAffectSelection:
    """One graded compatibility weight over the existing canonical affect mixture."""

    load: float

    def __post_init__(self) -> None:
        if (
            isinstance(self.load, bool)
            or not math.isfinite(self.load)
            or not 0.0 <= self.load <= 1.0
        ):
            raise ValueError("discretionary affect load must be finite and bounded")

    @property
    def readiness(self) -> float:
        return 1.0 - self.load

    def positive_excess(self, family: Literal["fast", "mood"], key: str, value: float) -> float:
        """Scale positive deviation without erasing a resting baseline or negative value."""

        dimension = (
            AFFECT_POLICY_V1.fast_dimension(key)
            if family == "fast"
            else AFFECT_POLICY_V1.mood_dimension(key)
        )
        if self.load == 0.0 or value <= dimension.baseline:
            return value
        return dimension.baseline + (value - dimension.baseline) * self.readiness


def project_discretionary_affect(
    context: EmotionalExpressionContext | None,
) -> DiscretionaryAffectSelection | None:
    """Use canonical baselines/caps; concern does not imply reluctance to engage."""

    if context is None:
        return None

    def normalized(family: Literal["fast", "mood"], key: str, value: float) -> float:
        dimension = (
            AFFECT_POLICY_V1.fast_dimension(key)
            if family == "fast"
            else AFFECT_POLICY_V1.mood_dimension(key)
        )
        return (value - dimension.baseline) / dimension.max_absolute_delta

    load = max(
        normalized("fast", "frustration", context.fast.frustration),
        normalized("fast", "tension", context.fast.tension),
        normalized("mood", "tension", context.mood.tension),
        -normalized("fast", "valence", context.fast.valence),
        -normalized("mood", "valence", context.mood.valence),
    )
    return DiscretionaryAffectSelection(min(max(load, 0.0), 1.0))
