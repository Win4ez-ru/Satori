"""Concrete replaceable generation-provider adapters."""

from satori.infrastructure.providers.ollama import OllamaConversationAdapter
from satori.infrastructure.providers.ollama_episode import OllamaEpisodeFormationAdapter
from satori.infrastructure.providers.yandex_ai_studio import (
    YandexAIStudioConversationAdapter,
)

__all__ = [
    "OllamaConversationAdapter",
    "OllamaEpisodeFormationAdapter",
    "YandexAIStudioConversationAdapter",
]
