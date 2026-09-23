from __future__ import annotations

from functools import lru_cache
from itertools import cycle

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from config import settings

FAKE_REPLIES = [
    "Sure, I can help with that. What would you like to do next?",
    "Got it. Anything else I can do for you today?",
]


@lru_cache(maxsize=1)
def get_llm() -> BaseChatModel:
    """Streaming-enabled chat model. Groq by default for lowest TTFT.

    LLM_PROVIDER=fake returns a deterministic local model (no network) used by
    tests and for latency benchmarking of the transport layer itself.
    """
    if settings.llm_provider == "fake":
        return GenericFakeChatModel(messages=cycle(AIMessage(content=r) for r in FAKE_REPLIES))
    if settings.llm_provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=settings.openai_model,
            api_key=settings.openai_api_key,
            temperature=settings.temperature,
            max_tokens=settings.max_tokens,
            streaming=True,
        )
    from langchain_groq import ChatGroq

    return ChatGroq(
        model=settings.groq_model,
        api_key=settings.groq_api_key,
        temperature=settings.temperature,
        max_tokens=settings.max_tokens,
        streaming=True,
    )
