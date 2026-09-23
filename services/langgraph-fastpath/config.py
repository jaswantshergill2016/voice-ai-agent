from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    llm_provider: str = field(default_factory=lambda: _env("LLM_PROVIDER", "groq"))
    groq_api_key: str = field(default_factory=lambda: _env("GROQ_API_KEY"))
    groq_model: str = field(default_factory=lambda: _env("GROQ_MODEL", "llama-3.3-70b-versatile"))
    openai_api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    openai_model: str = field(default_factory=lambda: _env("OPENAI_MODEL", "gpt-4o-mini"))
    n8n_webhook_url: str = field(default_factory=lambda: _env("N8N_WEBHOOK_URL", "http://n8n:5678/webhook/call-event"))
    n8n_timeout_s: float = field(default_factory=lambda: float(_env("N8N_TIMEOUT_S", "5")))
    max_tokens: int = field(default_factory=lambda: int(_env("MAX_TOKENS", "256")))
    temperature: float = field(default_factory=lambda: float(_env("TEMPERATURE", "0.4")))
    system_prompt: str = field(
        default_factory=lambda: _env(
            "SYSTEM_PROMPT",
            "You are a concise, friendly voice assistant. Reply in 1-2 short sentences. "
            "Never use markdown, lists, or emojis; your output is spoken aloud.",
        )
    )

    @property
    def model_name(self) -> str:
        if self.llm_provider == "groq":
            return self.groq_model
        if self.llm_provider == "openai":
            return self.openai_model
        return self.llm_provider


settings = Settings()
