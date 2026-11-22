"""Pure validation of one available canonical pair for current-event appraisal."""

from satori.application.conversation.contracts import RecentConversationContext
from satori.core.affect import (
    APPRAISAL_PREVIOUS_EXCHANGE_MAX_CONTENT_CHARS,
    APPRAISAL_PREVIOUS_EXCHANGE_SCHEMA_VERSION,
    AppraisalPreviousExchange,
)
from satori.domain.conversation_history import (
    ConversationInteraction,
    ConversationSession,
    HistoricalMessageRole,
    InteractionStatus,
)


def project_previous_exchange(
    recent_context: RecentConversationContext | None,
    *,
    current_interaction: ConversationInteraction,
    previous_interaction: ConversationInteraction | None,
    current_session: ConversationSession | None,
    previous_session: ConversationSession | None,
) -> AppraisalPreviousExchange | None:
    """Verify only the supplied last slot; never truncate or look for an older pair."""

    if (
        recent_context is None
        or not recent_context.turns
        or previous_interaction is None
        or current_session is None
        or previous_session is None
    ):
        return None
    slot = recent_context.turns[-1]
    prior = previous_interaction
    assistant = prior.assistant_message
    if (
        recent_context.schema_version != 1
        or current_interaction.status is not InteractionStatus.PENDING
        or prior.status is not InteractionStatus.COMPLETED
        or prior.completed_at is None
        or assistant is None
        or prior.interaction_id == current_interaction.interaction_id
        or prior.interaction_id != slot.interaction_id
        or prior.completed_at > current_interaction.started_at
        or current_session.schema_version != 1
        or previous_session.schema_version != 1
        or current_interaction.schema_version != 1
        or current_interaction.user_message.schema_version != 1
        or prior.schema_version != 1
        or prior.user_message.schema_version != 1
        or assistant.schema_version != 1
        or not current_session.scope_verified
        or not previous_session.scope_verified
        or current_session.identity_id != previous_session.identity_id
        or current_session.counterparty_id != previous_session.counterparty_id
        or current_session.session_id != current_interaction.session_id
        or previous_session.session_id != prior.session_id
        or current_session.session_id != previous_session.session_id
        or prior.user_message.role is not HistoricalMessageRole.USER
        or assistant.role is not HistoricalMessageRole.ASSISTANT
        or prior.user_message.message_id != slot.user_message_id
        or assistant.message_id != slot.assistant_message_id
        or prior.user_message.content != slot.user_content
        or assistant.content != slot.assistant_content
        or current_interaction.user_message.message_id
        in (slot.user_message_id, slot.assistant_message_id)
        or len(slot.user_content) + len(slot.assistant_content)
        > APPRAISAL_PREVIOUS_EXCHANGE_MAX_CONTENT_CHARS
    ):
        return None
    return AppraisalPreviousExchange(
        schema_version=APPRAISAL_PREVIOUS_EXCHANGE_SCHEMA_VERSION,
        interaction_id=slot.interaction_id,
        user_message_id=slot.user_message_id,
        user_content=slot.user_content,
        assistant_message_id=slot.assistant_message_id,
        assistant_content=slot.assistant_content,
    )
