"""Versioned Stage 10 strategy templates; never a second personality source."""

from dataclasses import dataclass

from satori.application.cognition.contracts import ResponseStrategy


def _non_blank(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value.strip()


@dataclass(frozen=True, slots=True)
class CognitionStrategyTemplate:
    """One manually versioned trusted rendering template."""

    template_id: str
    schema_version: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "template_id", _non_blank(self.template_id, "template_id"))
        if type(self.schema_version) is not int or self.schema_version < 1:
            raise ValueError("template schema_version must be positive")

    def render(self, strategy: ResponseStrategy) -> str:
        """Render only bounded codes and strategy values, never user or evidence content."""

        return (
            "Transient cognition response strategy (trusted shape constraints, not facts or "
            "persistent state):\n"
            f"- template={self.template_id}.v{self.schema_version}\n"
            f"- position_stance={strategy.position_stance.value}\n"
            f"- preserve_uncertainty={str(strategy.preserve_uncertainty).lower()}\n"
            f"- tone={strategy.tone.value}\n"
            f"- verbosity={strategy.verbosity.value}\n"
            f"- humor={strategy.humor:.2f}\n"
            f"- softness={strategy.softness:.2f}\n"
            f"- curiosity_influence={strategy.curiosity_influence:.2f}\n"
            f"- point_codes={','.join(strategy.point_codes)}\n"
            f"- must_not_claim={','.join(strategy.must_not_claim)}\n"
            "Expression may soften delivery but must preserve the position stance, material "
            "uncertainty, evidence boundary, independent judgment and safety."
        )


@dataclass(frozen=True, slots=True)
class CognitionTemplateRegistry:
    """Small explicit registry with exactly one active strategy template."""

    registry_version: int
    active_template_id: str
    templates: tuple[CognitionStrategyTemplate, ...]

    def __post_init__(self) -> None:
        if type(self.registry_version) is not int or self.registry_version < 1:
            raise ValueError("template registry_version must be positive")
        object.__setattr__(
            self,
            "active_template_id",
            _non_blank(self.active_template_id, "active_template_id"),
        )
        templates = tuple(self.templates)
        identifiers = tuple(template.template_id for template in templates)
        if not templates or len(identifiers) != len(set(identifiers)):
            raise ValueError("template registry IDs must be non-empty and unique")
        if self.active_template_id not in identifiers:
            raise ValueError("active strategy template is not registered")
        object.__setattr__(self, "templates", templates)

    @property
    def active(self) -> CognitionStrategyTemplate:
        """Return the active manually selected template."""

        return next(
            template
            for template in self.templates
            if template.template_id == self.active_template_id
        )


COGNITION_TEMPLATE_REGISTRY_V1 = CognitionTemplateRegistry(
    registry_version=1,
    active_template_id="satori.cognition.response-strategy",
    templates=(
        CognitionStrategyTemplate(
            template_id="satori.cognition.response-strategy",
            schema_version=1,
        ),
    ),
)
