"""OpenAI-compatible SSE fast path for Vapi custom-llm.

Latency budget: TTFT is dominated by the LLM provider. Everything here is
non-blocking: no DB, no CRM, no awaiting n8n. Slow-path work is dispatched
after `[DONE]` is emitted via `asyncio.create_task`.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

import slow_path
from config import settings
from graph import graph, has_history, thread_config
from schemas import ChatCompletionRequest, ChatMessage, SlowPathEvent

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("fastpath")


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await slow_path.shutdown()


app = FastAPI(title="LangGraph Voice Fast Path", version="1.0.0", lifespan=lifespan)

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def to_lc_messages(messages: list[ChatMessage]):
    out = []
    for m in messages:
        text = m.text()
        if m.role == "system":
            out.append(SystemMessage(content=text))
        elif m.role == "assistant":
            out.append(AIMessage(content=text))
        elif m.role == "user":
            out.append(HumanMessage(content=text))
    return out


def resolve_call_id(req: ChatCompletionRequest) -> str:
    if req.call and req.call.get("id"):
        return str(req.call["id"])
    if req.metadata.get("call_id"):
        return str(req.metadata["call_id"])
    return f"local-{uuid.uuid4().hex[:12]}"


def resolve_phone(req: ChatCompletionRequest) -> str | None:
    if not req.call:
        return None
    customer = req.call.get("customer") or {}
    return customer.get("number")


def sse_chunk(completion_id: str, created: int, delta: dict, finish_reason: str | None = None) -> str:
    payload = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": settings.model_name,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"


async def stream_completion(req: ChatCompletionRequest) -> AsyncIterator[str]:
    t0 = time.perf_counter()
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())
    call_id = resolve_call_id(req)
    cfg = thread_config(call_id)

    lc_messages = to_lc_messages(req.messages)
    user_text = next((m.text() for m in reversed(req.messages) if m.role == "user"), "")
    # Stateful: the checkpointer already holds prior turns, so only feed the
    # latest user utterance. First turn (or stateless client) seeds everything.
    graph_input = {"messages": lc_messages[-1:] if await has_history(call_id) and lc_messages else lc_messages}

    yield sse_chunk(completion_id, created, {"role": "assistant", "content": ""})

    parts: list[str] = []
    ttft_ms: float | None = None
    async for msg_chunk, meta in graph.astream(graph_input, cfg, stream_mode="messages"):
        if meta.get("langgraph_node") != "respond":
            continue
        token = msg_chunk.content if isinstance(msg_chunk.content, str) else ""
        if not token:
            continue
        if ttft_ms is None:
            ttft_ms = (time.perf_counter() - t0) * 1000
        parts.append(token)
        yield sse_chunk(completion_id, created, {"content": token})

    yield sse_chunk(completion_id, created, {}, finish_reason="stop")
    yield "data: [DONE]\n\n"

    total_ms = (time.perf_counter() - t0) * 1000
    log.info("call=%s ttft_ms=%.1f total_ms=%.1f", call_id, ttft_ms or -1, total_ms)

    slow_path.dispatch(
        SlowPathEvent(
            event_type="turn.completed",
            call_id=call_id,
            phone_number=resolve_phone(req),
            user_text=user_text,
            assistant_text="".join(parts),
            latency_ms={"ttft": round(ttft_ms or 0, 1), "total": round(total_ms, 1)},
            metadata=req.metadata,
        )
    )


@app.post("/v1/chat/completions")
async def chat_completions(req: ChatCompletionRequest, request: Request):
    if not req.stream:
        text_parts: list[str] = []
        async for line in stream_completion(req):
            if line.startswith("data: {"):
                delta = json.loads(line[6:]).get("choices", [{}])[0].get("delta", {})
                text_parts.append(delta.get("content", ""))
        return JSONResponse(
            {
                "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": settings.model_name,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "".join(text_parts)},
                        "finish_reason": "stop",
                    }
                ],
            }
        )
    return StreamingResponse(stream_completion(req), media_type="text/event-stream", headers=SSE_HEADERS)


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "provider": settings.llm_provider, "model": settings.model_name}


# --- Local mocks for slow-path integrations (used by the n8n templates in dev) ---


@app.post("/mock/twilio/Messages.json")
async def mock_twilio(request: Request):
    form = await request.form()
    log.info("MOCK TWILIO SMS to=%s from=%s body=%r", form.get("To"), form.get("From"), form.get("Body"))
    return JSONResponse({"sid": f"SM{uuid.uuid4().hex}", "status": "queued", "to": form.get("To")}, status_code=201)


@app.post("/mock/crm/{path:path}")
async def mock_crm(path: str, request: Request):
    body = await request.json()
    log.info("MOCK CRM %s payload=%s", path, json.dumps(body)[:500])
    return JSONResponse({"id": uuid.uuid4().hex[:8], "path": path, "properties": body.get("properties", {})}, status_code=201)
