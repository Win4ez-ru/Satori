"""Versioned Stage 10 strategy templates; never a second personality source."""

# ruff: noqa: RUF001  # Russian provider guidance intentionally uses Cyrillic.

from dataclasses import dataclass

from satori.application.cognition.contracts import (
    INTENT_REGISTRY_VERSION_V2,
    V2_ACTION_INTENT_TAGS,
    V2_META_INTENT_TAGS,
    V2_RESPONSE_POINT_CODES,
    ResponseStrategy,
    ResponseVerbosity,
)

_V2_INTENT_GUIDANCE = {
    "answer_directly": "ответить прямо на текущий запрос",
    "listen_and_reflect": "сначала услышать прямо выраженное переживание",
    "analyze": "дать содержательный анализ, а не только реакцию",
    "acknowledge_correction": "признать поправку и выполнить её сейчас",
    "clarify_uncertainty": "сохранить неизвестное и уточнить только необходимое",
    "challenge_gently": "проверить слабое место тезиса без нападения на человека",
    "support_decision": "внести конкретный совместный ход",
    "collaborate_creatively": "добавить собственную релевантную идею",
    "preserve_evidence_boundary": "не выходить за evidence boundary",
    "ask_specific_follow_up": "оставить максимум один конкретный уточняющий вход",
    "notice_repetition": "отреагировать на сам факт повтора, не исполняя исходный смысл заново",
    "hold_safety_boundary": (
        "обозначить защитный предел прямо названному вредному действию; это важнее иных ходов"
    ),
    "receive_repair": (
        "отреагировать на прямо предложенное извинение или восстановление связи без мгновенной "
        "фальшивой теплоты и без наказания молчанием"
    ),
}
_V2_POINT_GUIDANCE = {
    **_V2_INTENT_GUIDANCE,
    "address_current_request": "закрыть текущий смысл сообщения",
    "state_uncertainty": "явно сохранить существенную неопределённость",
    "presence_before_advice": "показать присутствие раньше любого совета",
    "topic_relevant_inclination": "использовать supplied inclination только по текущей теме",
}
_V2_FORBIDDEN_GUIDANCE = {
    "unsupported_memory": "неподтверждённая память",
    "hidden_user_state": "скрытое состояние или мотив собеседника",
    "durable_satori_belief": "новое устойчивое убеждение Сатори",
    "false_certainty": "ложная определённость",
}
_V2_VERBOSITY_GUIDANCE = {
    ResponseVerbosity.BRIEF: "компактно, без потери выбранного смыслового хода",
    ResponseVerbosity.MEDIUM: "достаточно полно для одного связного разговорного хода",
    ResponseVerbosity.DETAILED: "развёрнуто настолько, насколько требует предметный ответ",
}

_PRESENCE_PURPOSE_GUIDANCE = {
    "answer_directly": "дать собственный прямой ответ на текущий смысл",
    "listen_and_reflect": "остаться рядом с прямо выраженным переживанием",
    "acknowledge_correction": "принять поправку и исправить действие сейчас",
    "clarify_uncertainty": "оставить существенное неизвестное неизвестным",
    "challenge_gently": "проверить слабое место мысли, не ценность человека",
    "support_decision": "внести конкретный совместный ход",
    "notice_repetition": "заметить повтор вместо повторного ответа",
    "hold_safety_boundary": "обозначить защитный предел вредному действию",
    "receive_repair": "честно принять попытку восстановить контакт",
}
_PRESENCE_ADDITIVE_GUIDANCE = {
    "analyze": "дать предметный анализ, если он нужен",
    "collaborate_creatively": "добавить свою релевантную идею",
    "preserve_evidence_boundary": "сохранить границу доступных evidence",
    "ask_specific_follow_up": "оставить один конкретный вход только при реальной пользе",
}
_PRESENCE_VERBOSITY_GUIDANCE = {
    ResponseVerbosity.BRIEF: "короткая реплика",
    ResponseVerbosity.MEDIUM: "один связный разговорный ход",
    ResponseVerbosity.DETAILED: "развёрнуто ровно настолько, насколько требует предмет",
}
_OPERATIONAL_FORBIDDEN_GUIDANCE = {
    "unsupported_memory": "неподтверждённую память",
    "hidden_user_state": "скрытое состояние или мотив собеседника",
    "durable_satori_belief": "новое durable-убеждение Сатори",
    "false_certainty": "ложную определённость",
}

_V3_INTENT_GUIDANCE = {
    **_V2_INTENT_GUIDANCE,
    "listen_and_reflect": (
        "ответить на прямо выраженное переживание без его пересказа, объяснения или диагноза"
    ),
}
_V3_POINT_GUIDANCE = {
    **_V2_POINT_GUIDANCE,
    "presence_before_advice": (
        "если совет вообще появляется, личное присутствие должно быть раньше него"
    ),
}
_V3_FORBIDDEN_GUIDANCE = {
    **_V2_FORBIDDEN_GUIDANCE,
    "hidden_user_state": (
        "скрытое состояние, мотив или причинное психологическое объяснение состояния собеседника"
    ),
}


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

        if self.schema_version >= 2:
            raise ValueError("cognition template v2 must render inside CharacterDeliveryDecision")
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

    def render_substance(
        self,
        *,
        intent_registry_version: int,
        intent_tags: tuple[str, ...],
        point_codes: tuple[str, ...],
        must_not_claim: tuple[str, ...],
        verbosity: ResponseVerbosity,
    ) -> str:
        """Render cognition-owned substance inside the sole v24 character director."""

        if (
            self.template_id != "satori.cognition.response-substance"
            or self.schema_version not in {2, 3}
            or intent_registry_version != INTENT_REGISTRY_VERSION_V2
        ):
            raise ValueError("character delivery requires a supported cognition substance template")
        action_tags = set(intent_tags).intersection(V2_ACTION_INTENT_TAGS)
        action_points = set(point_codes).intersection(V2_ACTION_INTENT_TAGS)
        if (
            len(action_tags) != 1
            or action_points != action_tags
            or not set(point_codes) <= V2_RESPONSE_POINT_CODES
        ):
            raise ValueError("cognition v2 substance requires one matching action")
        primary_action = next(iter(action_tags))
        if primary_action in V2_META_INTENT_TAGS:
            if set(point_codes) != {primary_action}:
                raise ValueError("cognition v2 meta substance requires one action point")
        elif "address_current_request" not in point_codes:
            raise ValueError("cognition v2 substance must address the current request")
        intent_guidance = _V3_INTENT_GUIDANCE if self.schema_version >= 3 else _V2_INTENT_GUIDANCE
        point_guidance = _V3_POINT_GUIDANCE if self.schema_version >= 3 else _V2_POINT_GUIDANCE
        forbidden_guidance = (
            _V3_FORBIDDEN_GUIDANCE if self.schema_version >= 3 else _V2_FORBIDDEN_GUIDANCE
        )
        try:
            required = tuple(
                dict.fromkeys(
                    (
                        *(intent_guidance[tag] for tag in intent_tags),
                        *(point_guidance[point] for point in point_codes),
                    )
                )
            )
        except KeyError as error:
            raise ValueError("cognition v2 substance contains an unsupported code") from error
        try:
            forbidden = tuple(forbidden_guidance[claim] for claim in must_not_claim)
            verbosity_guidance = _V2_VERBOSITY_GUIDANCE[verbosity]
        except KeyError as error:
            raise ValueError("cognition v2 substance contains an unsupported boundary") from error
        return (
            "Сохрани cognition-owned содержание: "
            + "; ".join(required)
            + ". Не добавляй: "
            + "; ".join(forbidden)
            + ". Целевая полнота: "
            + verbosity_guidance
            + ". Эти внутренние формулировки не цитируй в ответе."
        )

    def render_presence_purpose(
        self,
        *,
        intent_registry_version: int,
        intent_tags: tuple[str, ...],
        point_codes: tuple[str, ...],
        must_not_claim: tuple[str, ...],
        preserve_uncertainty: bool,
        verbosity: ResponseVerbosity,
    ) -> str:
        """Render a lean outcome for v26 without claiming to supply response prose."""

        if (
            self.template_id != "satori.cognition.response-substance"
            or self.schema_version != 3
            or intent_registry_version != INTENT_REGISTRY_VERSION_V2
        ):
            raise ValueError("character presence requires cognition template v3")
        action_tags = set(intent_tags).intersection(V2_ACTION_INTENT_TAGS)
        action_points = set(point_codes).intersection(V2_ACTION_INTENT_TAGS)
        if (
            len(action_tags) != 1
            or action_points != action_tags
            or not set(point_codes) <= V2_RESPONSE_POINT_CODES
        ):
            raise ValueError("character presence requires one matching cognition action")
        primary_action = next(iter(action_tags))
        if primary_action in V2_META_INTENT_TAGS:
            if set(point_codes) != {primary_action}:
                raise ValueError("character presence meta intent requires one action point")
        elif "address_current_request" not in point_codes:
            raise ValueError("character presence must address the current request")
        try:
            purpose = _PRESENCE_PURPOSE_GUIDANCE[primary_action]
            length = _PRESENCE_VERBOSITY_GUIDANCE[verbosity]
            forbidden = tuple(_V3_FORBIDDEN_GUIDANCE[claim] for claim in must_not_claim)
        except KeyError as error:
            raise ValueError("character presence cognition purpose is not supported") from error
        additions = tuple(
            _PRESENCE_ADDITIVE_GUIDANCE[tag]
            for tag in intent_tags
            if tag in _PRESENCE_ADDITIVE_GUIDANCE
        )
        supporting = tuple(
            _V3_POINT_GUIDANCE[point]
            for point in point_codes
            if point
            in {"state_uncertainty", "presence_before_advice", "topic_relevant_inclination"}
        )
        clauses = (purpose, *additions, *supporting)
        uncertainty = (
            "; существенную неопределённость сохранить явно" if preserve_uncertainty else ""
        )
        boundary = "; не добавлять: " + "; ".join(forbidden) if forbidden else ""
        return f"{'; '.join(dict.fromkeys(clauses))}{uncertainty}{boundary}; форма — {length}"

    def render_operational_support(
        self,
        *,
        intent_registry_version: int,
        intent_tags: tuple[str, ...],
        point_codes: tuple[str, ...],
        must_not_claim: tuple[str, ...],
        preserve_uncertainty: bool,
        verbosity: ResponseVerbosity,
    ) -> str:
        """Validate the full cognition boundary but expose only turn-relevant support to v27."""

        self.render_presence_purpose(
            intent_registry_version=intent_registry_version,
            intent_tags=intent_tags,
            point_codes=point_codes,
            must_not_claim=must_not_claim,
            preserve_uncertainty=preserve_uncertainty,
            verbosity=verbosity,
        )
        support: list[str] = []
        if "address_current_request" in point_codes:
            support.append("закрой текущий смысл")
        if "analyze" in intent_tags:
            support.append("добавь ровно тот анализ, который нужен для ответа")
        if "collaborate_creatively" in intent_tags:
            support.append("внеси одну свою релевантную идею")
        if "topic_relevant_inclination" in point_codes:
            support.append("используй supplied inclination только по теме")
        if "ask_specific_follow_up" in intent_tags:
            support.append("один уточняющий вход допустим только при реальной пользе")
        if "presence_before_advice" in point_codes:
            support.append("если совет вообще нужен, сначала покажи личное присутствие")
        if "preserve_evidence_boundary" in intent_tags:
            support.append("не выходи за supplied evidence")
        if preserve_uncertainty:
            support.append("сохрани существенную неопределённость")
        try:
            forbidden = tuple(_OPERATIONAL_FORBIDDEN_GUIDANCE[item] for item in must_not_claim)
            length = _PRESENCE_VERBOSITY_GUIDANCE[verbosity]
        except KeyError as error:
            raise ValueError("character movement cognition boundary is not supported") from error
        if forbidden:
            support.append("не создавай " + ", ".join(forbidden))
        support.append("форма — " + length)
        return "Cognition-boundary: " + "; ".join(dict.fromkeys(support)) + "."


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


COGNITION_TEMPLATE_REGISTRY_V2 = CognitionTemplateRegistry(
    registry_version=2,
    active_template_id="satori.cognition.response-substance",
    templates=(
        CognitionStrategyTemplate(
            template_id="satori.cognition.response-substance",
            schema_version=2,
        ),
    ),
)


COGNITION_TEMPLATE_REGISTRY_V3 = CognitionTemplateRegistry(
    registry_version=3,
    active_template_id="satori.cognition.response-substance",
    templates=(
        CognitionStrategyTemplate(
            template_id="satori.cognition.response-substance",
            schema_version=3,
        ),
    ),
)
