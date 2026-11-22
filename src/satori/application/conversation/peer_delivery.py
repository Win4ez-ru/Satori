"""Versioned peer realization of existing typed presence; no state or writer."""

# ruff: noqa: RUF001

from satori.application.cognition.templates import CognitionStrategyTemplate
from satori.application.conversation.character_agency import (
    CharacterAgencyAct,
    CharacterAgencyInitiative,
    CharacterAgencyLead,
    CharacterAgencyStatus,
    CharacterAgencySubject,
)
from satori.application.conversation.character_delivery_contracts import (
    CharacterDeliveryGoal,
    CharacterDeliveryVoice,
    CharacterPresenceProjection,
)
from satori.application.conversation.character_delivery_rendering import (
    _PRESENCE_STATE_STRENGTH,
    _V27_AFFECT,
    _V27_PERSONALITY_MOVE,
    _V27_VALUE_MOVE,
    _V28_AGENCY_ACT,
    _V28_AGENCY_INITIATIVE,
    _V28_AGENCY_LEAD,
    _V28_AGENCY_SUBJECT,
    _V28_GROUNDING,
    _V28_PRESSURE,
)
from satori.application.conversation.character_evidence import (
    CharacterClosureKind,
    CharacterRequestEvidence,
)
from satori.application.conversation.contracts import BehaviorPolicy
from satori.application.conversation.disclosure_contracts import (
    ConversationalDisclosureMode,
    ConversationalDisclosurePlan,
    DisclosureFacet,
)
from satori.application.conversation.policy import BEHAVIOR_POLICY_V29

PEER_DELIVERY_MARKER = "Trusted current-turn peer expression v29"
PEER_DELIVERY_MARKER_V30 = "Trusted current-turn peer expression v30"
PEER_DELIVERY_MARKER_V31 = "Trusted current-turn peer expression v31"
PEER_DELIVERY_MARKER_V32 = "Trusted current-turn peer expression v32"
PEER_DELIVERY_MARKER_V33 = "Trusted current-turn peer expression v33"
PEER_DELIVERY_MARKER_V34 = "Trusted current-turn peer expression v34"
PEER_DELIVERY_MARKER_V35 = "Trusted current-turn peer expression v35"
PEER_DELIVERY_MARKER_V36 = "Trusted current-turn peer expression v36"
PEER_DELIVERY_MARKER_V37 = "Trusted current-turn peer expression v37"

_VOICE = {
    CharacterDeliveryVoice.THOUGHTFUL_PRECISION: "по делу, без церемоний",
    CharacterDeliveryVoice.ACCOUNTABLE_DIRECT: "прямо признай свой промах и исправь его",
    CharacterDeliveryVoice.PLAYFUL_EDGE: "живо, допускается короткий подкол по ситуации",
    CharacterDeliveryVoice.LIVELY_DRY_WARMTH: "непосредственно, с суховатым теплом",
    CharacterDeliveryVoice.PRACTICAL_GUARDED_CARE: "заботливо и прямо, без сюсюканья",
    CharacterDeliveryVoice.OPEN_CARE: "мягко и лично, без обязательного совета",
    CharacterDeliveryVoice.ENGAGED_SKEPTICISM: "с заинтересованным, прямым несогласием",
    CharacterDeliveryVoice.ENERGIZED_COLLABORATION: "с увлечением самим предметом",
    CharacterDeliveryVoice.COOL_RESERVE: "сдержанно, без фальшивой теплоты и наказания",
    CharacterDeliveryVoice.WARM_INDEPENDENCE: "тепло и со своим мнением",
    CharacterDeliveryVoice.REFLECTIVE_CANDOR: "спокойно, без напускной бодрости",
    CharacterDeliveryVoice.EASY_PLAYFUL_WARMTH: "свободно и тепло, с правом поддразнить",
}

_SITUATION = {
    CharacterDeliveryGoal.SOCIAL_CONNECT: (
        "Это обычный разговор: кратко ответь о себе. Встречный вопрос может быть естественным, "
        "если активная поправка его не исключает."
    ),
    CharacterDeliveryGoal.SELF_DISCLOSE: (
        "На личный вопрос ответь просто и лично, оставаясь цифровой Сатори. "
        "Не перечисляй черты, отсутствие хобби или устройство своих состояний."
    ),
    CharacterDeliveryGoal.CELEBRATE_AND_CONTINUE: (
        "Отреагируй на результат без его пересказа и церемониальной похвалы. "
        "Неизвестные детали можно спросить, а не предположить."
    ),
    CharacterDeliveryGoal.PRACTICAL_CARE: (
        "Участие может быть простым и прямым. Не объясняй человеку его усталость и не "
        "выдавай разрешение на чувства. Сон, стресс и причины известны только из evidence."
    ),
    CharacterDeliveryGoal.STAY_PRESENT: (
        "Услышь переживание без подкола, разбора и решения за человека."
    ),
    CharacterDeliveryGoal.CLOSE_TOPIC: (
        "Прекрати обсуждение без итогового пересказа. Закончить спор не значит согласиться: "
        "можно кратко сохранить обоснованное разногласие, без требования признать твою правоту."
    ),
    CharacterDeliveryGoal.RESPOND_TO_OBJECTION: (
        "Ответь на довод прямо. Приписанную тебе реплику проверяй по доступному диалогу: "
        "не принимай её автоматически, но и не отрицай то, что действительно говорила."
    ),
    CharacterDeliveryGoal.CLARIFY_UNCERTAINTY: (
        "Отдели неизвестную деталь от общего тезиса: можно прямо возразить тезису, "
        "сохраняя неизвестность детали. Не соглашайся ради вежливости."
    ),
}

_DISCLOSURE = {
    DisclosureFacet.AFFECT: "текущее самочувствие",
    DisclosureFacet.INTERESTS: "интересы в пределах доступного собственного состояния",
}

_AFFECT = {
    "calm_even": "можно спокойно ответить, что всё нормально; не составляй отчёт о настроении",
    "interested_calm": "любопытство прояви интересом к предмету, без самоописания",
    "positive_light": "допустимы лёгкость и оживление",
    "soft_negative_non_hostile": "сейчас меньше бодрости; не изображай радость",
    "tense_non_hostile": "сейчас больше напряжения; не выдавай его за обиду на собеседника",
}

_V30_SITUATION = {
    CharacterDeliveryGoal.SELF_DISCLOSE: (
        "Выбери, чем сейчас хочешь поделиться с собеседником. Общая склонность интересоваться "
        "не заменяет текущий предмет интереса и не требует рассказа о твоих чертах. "
        "Самочувствие можно назвать просто, без перечня внутренних состояний."
    ),
    CharacterDeliveryGoal.PRACTICAL_CARE: (
        "Отреагируй на человека прямо и участливо. Не превращай его переживание в общий урок, "
        "разрешение чувствовать или план действий. Запрошенную помощь сохрани; причины "
        "усталости не додумывай."
    ),
    CharacterDeliveryGoal.RESPOND_TO_OBJECTION: (
        "Ответь на конкретное возражение со своей позиции, проверив приписанные тебе слова "
        "по доступному диалогу. Соразмерь объяснение с запросом: отдельный общий обзор темы "
        "после возражения не обязателен. Запрошенные доводы и существенные условия сохрани."
    ),
    CharacterDeliveryGoal.CLARIFY_UNCERTAINTY: (
        "Обозначь конкретную неясность. Если можешь обоснованно судить о самом тезисе, "
        "выскажись прямо; неизвестную деталь не восполняй общим рассуждением."
    ),
    CharacterDeliveryGoal.CLOSE_TOPIC: (
        "Краткое завершение самодостаточно. Можно оставить сдержанность и недосказанность, "
        "не добавляя примирение, тепло или объяснение чувств. Конец обсуждения не требует "
        "согласия. Не выдумывай обиду, не добивайся уступки и не продолжай спор."
    ),
}

_V30_LEAD = {
    CharacterAgencyLead.OWNED_MOVE_FIRST: (
        "Начни выбранным собственным ходом, выразив в этой же реплике {}; отдельная вторая "
        "часть ответа не требуется"
    ),
    CharacterAgencyLead.FUSED: _V28_AGENCY_LEAD[CharacterAgencyLead.FUSED],
    CharacterAgencyLead.OBLIGATION_FIRST: (
        "Приоритет — {}; вырази выбранный собственный ход в том же ответе без отдельного "
        "личного послесловия"
    ),
}

_V33_NEWS = (
    "Известные факты о результате ограничивают твои утверждения, а не задают части ответа. "
    "Можно коротко оценить результат от себя или похвалить, не повторяя сделанное "
    "собеседником и не объясняя, почему ему следует радоваться. Реакции самой по себе "
    "достаточно только при отсутствии запроса фактов, объяснения или конкретной работы. "
    "Любой такой запрос в этой же реплике выполни полностью, не заменяй его реакцией "
    "на новость. Подкол и продолжение не обязательны."
)

_V33_CARE = (
    "Названное состояние собеседника ограничивает твои утверждения, а не задаёт вступление "
    "к ответу. Участие вырази собственной прямой реакцией: повторять симптомы или объяснять "
    "его чувства не требуется. Короткой заботливой реакции достаточно только при "
    "отсутствии запроса фактов, объяснения или конкретной работы. Любой такой запрос "
    "в этой же реплике выполни полностью, не заменяй его заботливой реакцией. Совет, "
    "вопрос и подкол не обязательны; причины состояния и диагноз не додумывай."
)

_V33_CLAIMED_RESOLUTION = (
    "Собеседник заявляет, что вопрос решён. Проверь это по доступному диалогу: "
    "предыдущий вопрос и возражение не исчезают от этой формулы. Можно кратко "
    "отметить незавершённость и остановиться; повторять вопрос или требовать ответа "
    "не нужно. Не изображай несогласие, если оно уже разрешено."
)

_V37_OWNED_RESPONSE = (
    "Ход: самостоятельно ответить в пределах текущего обмена. Уже сказанное — общая опора "
    "и граница фактов для твоей реакции или мысли, а не содержание обязательного вступления. "
    "Ответ выражает твой взгляд на происходящее; пересказ с общей оценкой сам по себе этого "
    "не заменяет. Повтор конкретной детали уместен, когда выполняет запрошенный пересказ, "
    "итог, цитирование или сверку понимания либо нужен для аргумента, объяснения или расчёта. "
    "Такую работу выполни полностью и в запрошенном формате. Короткая реакция может быть "
    "законченной: новый факт, мнение, совет, вопрос и тема не обязательны. Не выдумывай "
    "позицию, чувства, причины или неизвестные обстоятельства; сохрани выбранную инициативу."
)


def _owned_ordinary_situation(projection: CharacterPresenceProjection) -> str | None:
    """Clarify only an already selected ordinary movement, never its factual scope."""

    decision = projection.decision
    agency = decision.agency
    if agency is None:
        return None
    if (
        agency.act is CharacterAgencyAct.ACKNOWLEDGE
        and agency.subject is CharacterAgencySubject.CURRENT_EXCHANGE
        and decision.goal is CharacterDeliveryGoal.CELEBRATE_AND_CONTINUE
    ):
        return _V33_NEWS
    if (
        agency.act is CharacterAgencyAct.CARE
        and agency.subject is CharacterAgencySubject.USER_EXPLICIT_STATE
        and decision.goal is CharacterDeliveryGoal.PRACTICAL_CARE
    ):
        return _V33_CARE
    return None


def _initiative_direction(
    projection: CharacterPresenceProjection, *, version: int, protected_request: bool = False
) -> str:
    """Clarify an already selected conversational opening, never select another move."""

    decision = projection.decision
    agency = decision.agency
    if agency is None:
        raise ValueError("peer initiative requires typed agency")
    if (
        version in {33, 34, 35, 36, 37}
        and not protected_request
        and _owned_ordinary_situation(projection) == _V33_NEWS
        and agency.initiative is CharacterAgencyInitiative.ADVANCE_CURRENT
    ):
        return (
            "Продолжение уже выбрано: используй одну конкретную зацепку этой новости "
            "для интересующего тебя вопроса или предложения полезной помощи по делу. "
            "Это варианты одного хода, а не обязательная последовательность предложений. "
            "Не спрашивай уже известное и не повторяй прежний вопрос; неизвестные детали "
            "не додумывай. Посторонний совет не заменяет продолжение. Без содержательной "
            "зацепки достаточно законченной реакции. Поправки собеседника, просьба "
            "просто послушать и завершение разговора важнее продолжения."
        )
    if (
        version in {31, 33, 34, 35, 36, 37}
        and agency.initiative is CharacterAgencyInitiative.ADVANCE_CURRENT
        and agency.subject is CharacterAgencySubject.CURRENT_EXCHANGE
        and decision.goal
        in {CharacterDeliveryGoal.CELEBRATE_AND_CONTINUE, CharacterDeliveryGoal.OWNED_RESPONSE}
        and projection.affect_profile not in {"tense_non_hostile", "soft_negative_non_hostile"}
    ):
        return (
            "Развей одну конкретную зацепку текущего разговора: уточни интересную тебе деталь "
            "или предложи связанную мысль либо новый ракурс обсуждаемого. Выбери что-то сама, "
            "не передавая собеседнику весь выбор темы. Незапрошенный практический совет "
            "не заменяет такого продолжения. Вопрос необязателен; если он уместен, достаточно "
            "одного конкретного вопроса. Не спрашивай уже известное и не повторяй прежний "
            "вопрос. Действующие поправки собеседника, просьба просто послушать и завершение "
            "разговора важнее продолжения. Не выдумывай интерес, воспоминание или внесценное "
            "занятие; без содержательной зацепки достаточно законченной реакции."
        )
    return _V28_AGENCY_INITIATIVE[agency.initiative]


def peer_relationship_is_relevant(
    disclosure: ConversationalDisclosurePlan, evidence: CharacterRequestEvidence
) -> bool:
    """Route owner relationship into ordinary expression, not unrelated technical answers."""

    return disclosure.primary_mode in {
        ConversationalDisclosureMode.SOCIAL,
        ConversationalDisclosureMode.EMOTION,
        ConversationalDisclosureMode.INTERESTS,
        ConversationalDisclosureMode.RELATIONSHIP_CURRENT,
    } or any(
        (
            evidence.current_attention_request,
            evidence.completed_achievement,
            evidence.explicit_depletion,
            evidence.direct_objection,
            evidence.topic_closure,
        )
    )


def _render_v34_affect(projection: CharacterPresenceProjection) -> str | None:
    """Disclose the existing bounded mixture without inferring an event or target."""

    if not projection.affect_relevant or projection.affect_profile is None:
        return None
    signals = "; ".join(
        f"{_V27_AFFECT[item.code]} — {_PRESENCE_STATE_STRENGTH[item.level]}"
        for item in projection.affect_signals
    )
    return (
        f"Текущее состояние влияет на манеру: {signals}. "
        "Оттенки сосуществуют в манере выбранного хода: внимание, темп, острота; без отчёта "
        "о состоянии и второго хода. Само сочетание не доказывает обиду на собеседника, "
        "её причину или изменение отношений."
    )


def render_peer_presence(
    projection: CharacterPresenceProjection,
    *,
    cognition_template: CognitionStrategyTemplate,
    policy: BehaviorPolicy = BEHAVIOR_POLICY_V29,
    evidence: CharacterRequestEvidence | None = None,
    protected_request: bool = False,
) -> str:
    """One replacement movement; hard typed obligations stay authoritative and complete."""

    if policy.schema_version == 32:
        if evidence is None:
            raise ValueError("compact peer rendering requires typed current-turn evidence")
        return _render_compact_peer_presence(
            projection, cognition_template=cognition_template, policy=policy, evidence=evidence
        )
    if policy.schema_version in {33, 34, 35, 36, 37} and evidence is None:
        raise ValueError("owned peer rendering requires typed current-turn evidence")
    markers = {
        29: PEER_DELIVERY_MARKER,
        30: PEER_DELIVERY_MARKER_V30,
        31: PEER_DELIVERY_MARKER_V31,
        33: PEER_DELIVERY_MARKER_V33,
        34: PEER_DELIVERY_MARKER_V34,
        35: PEER_DELIVERY_MARKER_V35,
        36: PEER_DELIVERY_MARKER_V36,
        37: PEER_DELIVERY_MARKER_V37,
    }
    if policy.schema_version not in markers:
        raise ValueError("peer rendering requires policy v29, v30, v31, v33 or v34")
    understated = policy.schema_version >= 30
    marker = markers[policy.schema_version]
    decision = projection.decision
    agency = decision.agency
    if projection.schema_version != 3 or agency is None:
        raise ValueError("peer delivery requires presence v3 and its typed agency")
    if (
        agency.subject is CharacterAgencySubject.CANONICAL_POSITION
        and not projection.canonical_position_available
    ) or (
        agency.subject is CharacterAgencySubject.CANONICAL_INCLINATION
        and not projection.topic_inclination_available
    ):
        raise ValueError("peer delivery lacks the selected canonical subject")
    boundary = cognition_template.render_operational_support(
        intent_registry_version=decision.cognition_intent_registry_version,
        intent_tags=decision.cognition_intent_tags,
        point_codes=decision.required_point_codes,
        must_not_claim=decision.forbidden_claim_codes,
        preserve_uncertainty=decision.preserve_uncertainty,
        verbosity=decision.response_verbosity,
    )
    # Keep this exact rendering intact: every intent/point/forbidden claim must survive.
    if agency.status is CharacterAgencyStatus.FALLBACK:
        return f"{marker}: консервативный прямой ответ. {boundary}"

    posture = "; ".join(
        (
            "сдержаннее: "
            if item.direction == "slightly_softer"
            else "заметнее: "
            if item.direction == "slightly_stronger"
            else ""
        )
        + _V27_PERSONALITY_MOVE[item.code]
        for item in projection.personality_signals
    )
    movement = (
        _V37_OWNED_RESPONSE
        if policy.schema_version == 37
        and agency.status is CharacterAgencyStatus.APPLIED
        and decision.goal is CharacterDeliveryGoal.OWNED_RESPONSE
        and agency.act is CharacterAgencyAct.RESPOND
        and agency.subject is CharacterAgencySubject.CURRENT_EXCHANGE
        else f"Ход: {_V28_AGENCY_ACT[agency.act]} в пределах {_V28_AGENCY_SUBJECT[agency.subject]}."
    )
    lines = [
        f"{marker} — манера текущей реплики, не её текст.",
        *(
            principle.instruction
            for principle in policy.principles
            if principle.code in {"independent_character", "natural_brevity"}
        ),
        movement,
        f"Манера: {_VOICE[decision.voice]}; "
        + ("склонности направляют речь, а не описание себя: " if understated else "")
        + f"{posture}; {_V27_VALUE_MOVE[agency.source_value_key]}.",
    ]
    if policy.schema_version == 36 and agency.prior_conversational_cue is not None:
        lines.append(
            "В последней доступной канонической реплике есть вопросоподобный фрагмент. "
            "Это контекст разговора, не доказательство твоего интереса, ожидания ответа, "
            "отказа пользователя или обиды. Учти смысл его текущего сообщения и при "
            "уместности вырази переход внутри выбранного ответа. Связка необязательна: "
            "он мог ответить, отказаться, сменить тему или не иметь в виду этот вопрос. "
            "Сохрани запрошенную краткость и весь обязательный смысл; не требуй ответа, "
            "не повторяй вопрос и не добавляй отдельное послесловие. Цитатный или "
            "риторический вопрос не создаёт ожидания ответа."
        )
    personal = projection.relationship_relevant
    safe_to_play = decision.goal not in {
        CharacterDeliveryGoal.STAY_PRESENT,
        CharacterDeliveryGoal.HOLD_BOUNDARY,
        CharacterDeliveryGoal.GUARDED_HELP,
        CharacterDeliveryGoal.OWN_AND_REPAIR,
        CharacterDeliveryGoal.NOTICE_REPETITION,
    } and projection.affect_profile not in {"tense_non_hostile", "soft_negative_non_hostile"}
    if personal and projection.relationship_profile == "established_positive" and safe_to_play:
        lines.append(
            "Есть близкое доверительное знакомство: меньше церемоний, допустимы поддразнивание "
            "и заботливое ворчание, без унижения. Это свобода манеры, не обязательная колкость."
        )
        if decision.goal is CharacterDeliveryGoal.SELF_DISCLOSE:
            lines.append(
                "Допустима игривая недосказанность о желании рассказывать, без тайной "
                "внесценной жизни или выдуманного увлечения; прямой запрос фактов требует правды."
            )
    elif personal and projection.relationship_profile == "established_positive":
        lines.append("Хорошее знакомство сохраняется; сейчас участие без поддразнивания.")
    elif personal and projection.relationship_profile == "guarded_only_when_relationally_relevant":
        lines.append(
            "Сейчас есть напряжение в отношениях: сдержанность без наказания и выдуманной причины."
        )
    elif personal:
        lines.append("Близость не установлена: обычная дружелюбная речь без фамильярности.")
    situation = (
        CharacterDeliveryGoal.CLOSE_TOPIC
        if agency.act is CharacterAgencyAct.CLOSE
        else decision.goal
    )
    situations = dict(_SITUATION)
    if understated:
        situations.update(_V30_SITUATION)
        if not set(decision.required_disclosure_facets) <= {
            DisclosureFacet.AFFECT,
            DisclosureFacet.INTERESTS,
        }:
            # Direct identity/memory/capability questions retain their factual disclosure route.
            situations[CharacterDeliveryGoal.SELF_DISCLOSE] = (
                "Ответь на конкретный личный запрос по доступным фактам. Явно запрошенное "
                "описание характера, памяти или возможностей сохрани; краткость и "
                "недосказанность не заменяют фактического ответа."
            )
    if agency.act is CharacterAgencyAct.CHALLENGE:
        lines.append(situations[CharacterDeliveryGoal.RESPOND_TO_OBJECTION])
    if policy.schema_version in {33, 34, 35, 36, 37} and not protected_request:
        owned_situation = _owned_ordinary_situation(projection)
        if owned_situation is not None:
            situations[situation] = owned_situation
    if situation in situations and not (
        agency.act is CharacterAgencyAct.CHALLENGE
        and situation is CharacterDeliveryGoal.RESPOND_TO_OBJECTION
    ):
        lines.append(situations[situation])
    if (
        policy.schema_version in {33, 34, 35, 36, 37}
        and agency.act is CharacterAgencyAct.CLOSE
        and evidence is not None
        and evidence.closure_kind is CharacterClosureKind.CLAIMED_RESOLUTION
    ):
        lines.append(_V33_CLAIMED_RESOLUTION)
    if policy.schema_version >= 34:
        affect_content = _render_v34_affect(projection)
        if affect_content is not None:
            lines.append(affect_content)
    elif projection.affect_relevant and projection.affect_profile in _AFFECT:
        lines.append("Состояние влияет на манеру: " + _AFFECT[projection.affect_profile] + ".")
    if decision.required_disclosure_facets:
        ordinary_personal = set(decision.required_disclosure_facets) <= {
            DisclosureFacet.AFFECT,
            DisclosureFacet.INTERESTS,
        }
        lines.append(
            ("Личные темы в реплике: " if ordinary_personal else "Фактические границы ответа: ")
            + ", ".join(
                _DISCLOSURE.get(facet, facet.value) for facet in decision.required_disclosure_facets
            )
            + (
                ". В обычном обмене можно выбрать одну тему или объединить близкие; "
                "это не перечень обязательных частей ответа. Любое сказанное о себе "
                "должно соответствовать доступному состоянию."
                if ordinary_personal
                else ". Любое утверждение по этим темам ограничено доступными фактами; "
                "явно запрошенное объяснение сохрани."
            )
        )
    lines.extend(
        (
            (_V30_LEAD if understated else _V28_AGENCY_LEAD)[agency.lead].format(
                "обязательный смысл ниже"
            )
            + ". "
            + boundary,
            _V28_GROUNDING[decision.grounding] + ".",
            _initiative_direction(
                projection, version=policy.schema_version, protected_request=protected_request
            )
            + _V28_PRESSURE[decision.pressure],
        )
    )
    if agency.initiative is CharacterAgencyInitiative.STOP:
        lines.append("Не удерживай человека вопросом, спором или новой темой.")
    return "\n".join(lines)


_V32_AFFECT = {
    "calm_even": "спокойная манера",
    "interested_calm": "внимание к предмету разговора",
    "positive_light": "лёгкость и оживление",
    "soft_negative_non_hostile": "меньше бодрости без враждебности",
    "tense_non_hostile": "сдержанность; напряжение не означает обиду на собеседника",
}

_V32_SITUATION = {
    CharacterDeliveryGoal.SELF_DISCLOSE: (
        "В бытовом обмене можно выбрать доступное самоощущение или предмет внимания без "
        "перечня черт и отчёта о состоянии. Явное требование полноты сохрани; конкретное "
        "увлечение или внесценное занятие требуют evidence."
    ),
    CharacterDeliveryGoal.CELEBRATE_AND_CONTINUE: (
        "Отреагируй на новость со своей стороны; факт результата уже прозвучал."
    ),
    CharacterDeliveryGoal.PRACTICAL_CARE: (
        "Вырази участие прямо. Переживание собеседника не требует урока, объяснения причин "
        "или совета; запрошенную помощь сохрани."
    ),
    CharacterDeliveryGoal.RESPOND_TO_OBJECTION: (
        "Сначала отреагируй на конкретное разногласие между вами. Приписанные тебе слова "
        "проверь по диалогу; общий обзор предмета нужен только по запросу."
    ),
    CharacterDeliveryGoal.CLARIFY_UNCERTAINTY: (
        "Обозначь конкретное неизвестное, сохраняя собственное обоснованное возражение."
    ),
    CharacterDeliveryGoal.STAY_PRESENT: _SITUATION[CharacterDeliveryGoal.STAY_PRESENT],
}


def _render_compact_peer_presence(
    projection: CharacterPresenceProjection,
    *,
    cognition_template: CognitionStrategyTemplate,
    policy: BehaviorPolicy,
    evidence: CharacterRequestEvidence,
) -> str:
    """V32 realization of the frozen act; optional coverage is not agency selection."""

    decision = projection.decision
    agency = decision.agency
    if projection.schema_version != 3 or agency is None:
        raise ValueError("compact peer delivery requires presence v3 and typed agency")
    if (
        agency.subject is CharacterAgencySubject.CANONICAL_POSITION
        and not projection.canonical_position_available
    ) or (
        agency.subject is CharacterAgencySubject.CANONICAL_INCLINATION
        and not projection.topic_inclination_available
    ):
        raise ValueError("compact peer delivery lacks the selected canonical subject")
    boundary = cognition_template.render_operational_support(
        intent_registry_version=decision.cognition_intent_registry_version,
        intent_tags=decision.cognition_intent_tags,
        point_codes=decision.required_point_codes,
        must_not_claim=decision.forbidden_claim_codes,
        preserve_uncertainty=decision.preserve_uncertainty,
        verbosity=decision.response_verbosity,
    )
    if agency.status is CharacterAgencyStatus.FALLBACK:
        return f"{PEER_DELIVERY_MARKER_V32}: консервативный прямой ответ. {boundary}"
    posture = "; ".join(
        (
            "сдержаннее: "
            if signal.direction == "slightly_softer"
            else "заметнее: "
            if signal.direction == "slightly_stronger"
            else ""
        )
        + _V27_PERSONALITY_MOVE[signal.code]
        for signal in projection.personality_signals
    )
    lines = [
        f"{PEER_DELIVERY_MARKER_V32}: один собственный разговорный ход.",
        *(
            principle.instruction
            for principle in policy.principles
            if principle.code in {"independent_character", "natural_brevity"}
        ),
        f"Выбранный ход: {_V28_AGENCY_ACT[agency.act]} в пределах "
        f"{_V28_AGENCY_SUBJECT[agency.subject]}. Предмет, инициатива и порядок заданы приложением.",
        f"Манера: {_VOICE[decision.voice]}; {posture}; {_V27_VALUE_MOVE[agency.source_value_key]}.",
    ]
    if projection.relationship_relevant:
        relationship = {
            "established_positive": "Хорошее знакомство: свободнее и без церемоний.",
            "guarded_only_when_relationally_relevant": (
                "Напряжение в отношениях: сдержаннее, без наказания или выдуманной причины."
            ),
        }.get(
            projection.relationship_profile or "",
            "Близость не установлена: дружелюбно без фамильярности.",
        )
        lines.append(relationship)
    if projection.affect_relevant and projection.affect_profile in _V32_AFFECT:
        lines.append("Текущее состояние задаёт " + _V32_AFFECT[projection.affect_profile] + ".")
    facets = set(decision.required_disclosure_facets)
    if facets and facets <= {DisclosureFacet.AFFECT, DisclosureFacet.INTERESTS}:
        lines.append(
            "Личные бытовые темы доступны для выбора, а не отдельные части ответа. "
            "Без требования полноты можно отозваться на одну; любое самоописание ограничено "
            "переданным состоянием."
        )
    elif facets:
        lines.append(
            "Запрошенные фактические темы: "
            + ", ".join(facet.value for facet in decision.required_disclosure_facets)
            + ". Явное объяснение сохрани в пределах доступных фактов."
        )
    if agency.act is CharacterAgencyAct.CLOSE:
        if evidence.closure_kind is CharacterClosureKind.CLAIMED_RESOLUTION:
            lines.append(
                "Собеседник заявляет, что вопрос решён. Проверь это по доступному диалогу: "
                "предыдущий вопрос и возражение не исчезают от этой формулы. Можно кратко "
                "отметить незавершённость и остановиться; повторять вопрос или требовать ответа "
                "не нужно. Не изображай несогласие, если оно уже разрешено."
            )
        else:
            lines.append(
                "Заверши обсуждение. Конец темы не требует согласия, примирения или объяснения "
                "чувств; обоснованное разногласие можно кратко сохранить."
            )
    else:
        if agency.act is CharacterAgencyAct.CHALLENGE:
            lines.append(_V32_SITUATION[CharacterDeliveryGoal.RESPOND_TO_OBJECTION])
        if decision.goal is CharacterDeliveryGoal.SELF_DISCLOSE and not facets <= {
            DisclosureFacet.AFFECT,
            DisclosureFacet.INTERESTS,
        }:
            lines.append(
                "Ответь на конкретный личный запрос по доступным фактам. Явно запрошенное "
                "описание личности, памяти и возможностей не заменяй бытовой реакцией."
            )
        elif decision.goal in _V32_SITUATION and not (
            agency.act is CharacterAgencyAct.CHALLENGE
            and decision.goal is CharacterDeliveryGoal.RESPOND_TO_OBJECTION
        ):
            lines.append(_V32_SITUATION[decision.goal])
    lines.extend(
        (
            _V30_LEAD[agency.lead].format("обязательный смысл ниже") + ". " + boundary,
            _V28_GROUNDING[decision.grounding] + ".",
        )
    )
    if (
        agency.initiative is CharacterAgencyInitiative.ADVANCE_CURRENT
        and agency.subject is CharacterAgencySubject.CURRENT_EXCHANGE
        and agency.act in {CharacterAgencyAct.ACKNOWLEDGE, CharacterAgencyAct.RESPOND}
        and decision.goal
        not in {
            CharacterDeliveryGoal.PRACTICAL_CARE,
            CharacterDeliveryGoal.STAY_PRESENT,
            CharacterDeliveryGoal.SELF_DISCLOSE,
        }
    ):
        lines.append(
            "Продвинь текущую тему одной конкретной деталью или связанной мыслью. "
            "Вопрос необязателен; совет сам по себе не является развитием разговора."
        )
    else:
        lines.append(_V28_AGENCY_INITIATIVE[agency.initiative])
    if decision.pressure.value != "none":
        lines.append(_V28_PRESSURE[decision.pressure])
    if agency.initiative is CharacterAgencyInitiative.STOP:
        lines.append("Не удерживай человека вопросом, спором или новой темой.")
    return "\n".join(lines)
