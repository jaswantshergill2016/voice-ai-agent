from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str | list[dict[str, Any]] | None = None

    def text(self) -> str:
        if isinstance(self.content, list):
            return "".join(p.get("text", "") for p in self.content if isinstance(p, dict))
        return self.content or ""


class ChatCompletionRequest(BaseModel):
    model: str = "voice-agent"
    messages: list[ChatMessage]
    stream: bool = True
    temperature: float | None = None
    max_tokens: int | None = None
    # Vapi attaches the full call object; we only need a few fields.
    call: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SlowPathEvent(BaseModel):
    """Payload posted to n8n after the fast path finishes streaming."""

    event_type: Literal["turn.completed", "call.ended", "tool.requested"]
    call_id: str
    phone_number: str | None = None
    user_text: str
    assistant_text: str
    latency_ms: dict[str, float]
    metadata: dict[str, Any] = Field(default_factory=dict)
