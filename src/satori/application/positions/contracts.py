"""Bounded trusted-state projections of Satori positions and inclinations."""

import json
import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PositionContextItem:
    position_id: str
    kind: str
    stance: str
    proposition: str
    confidence: float
    status: str
    uncertain: bool
    competing_with_position_id: str | None


@dataclass(frozen=True, slots=True)
class SatoriPositionsContext:
    schema_version: int
    status: str
    positions: tuple[PositionContextItem, ...]

    @property
    def position_ids(self) -> tuple[str, ...]:
        return tuple(item.position_id for item in self.positions)

    @property
    def grounding_ids(self) -> tuple[str, ...]:
        return self.position_ids


def positions_context_json(context: SatoriPositionsContext) -> str:
    payload = {
        "schema_version": context.schema_version,
        "status": context.status,
        "positions": [
            {
                "position_id": item.position_id,
                "kind": item.kind,
                "stance": item.stance,
                "proposition": item.proposition,
                "confidence": item.confidence,
                "status": item.status,
                "uncertain": item.uncertain,
                "competing_with_position_id": item.competing_with_position_id,
            }
            for item in context.positions
        ],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class InclinationContextItem:
    inclination_id: str
    kind: str
    topic: str
    alternative_topic: str | None
    effective_score: float
    confidence: float
    stability: float
    preferred_topic: str | None


@dataclass(frozen=True, slots=True)
class SatoriInclinationsContext:
    schema_version: int
    status: str
    inclinations: tuple[InclinationContextItem, ...]
    curiosity_influence: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "inclinations", tuple(self.inclinations))
        if (
            isinstance(self.curiosity_influence, bool)
            or not math.isfinite(self.curiosity_influence)
            or not 0.0 <= self.curiosity_influence <= 0.20
        ):
            raise ValueError("inclination curiosity influence must be in [0, 0.20]")

    @property
    def inclination_ids(self) -> tuple[str, ...]:
        return tuple(item.inclination_id for item in self.inclinations)

    @property
    def grounding_ids(self) -> tuple[str, ...]:
        return self.inclination_ids


def inclinations_context_json(context: SatoriInclinationsContext) -> str:
    payload = {
        "schema_version": context.schema_version,
        "status": context.status,
        "curiosity_influence": context.curiosity_influence,
        "inclinations": [
            {
                "inclination_id": item.inclination_id,
                "kind": item.kind,
                "topic": item.topic,
                "alternative_topic": item.alternative_topic,
                "effective_score": item.effective_score,
                "confidence": item.confidence,
                "stability": item.stability,
                "preferred_topic": item.preferred_topic,
            }
            for item in context.inclinations
        ],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
