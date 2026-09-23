import asyncio
import json
import time

import httpx
import pytest

import main
import slow_path
from llm import FAKE_REPLIES
from schemas import ChatCompletionRequest, ChatMessage

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def n8n_capture(monkeypatch):
    """Replace the outbound n8n client with a slow mock and record requests."""
    received: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.3)  # simulate slow n8n; must not affect the stream
        received.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(slow_path, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return received


def parse_sse(body: str) -> list[dict | str]:
    events = []
    for block in body.strip().split("\n\n"):
        assert block.startswith("data: "), block
        data = block[len("data: ") :]
        events.append(data if data == "[DONE]" else json.loads(data))
    return events


# --- message parsing -------------------------------------------------------


def test_text_content_parts_are_flattened():
    m = ChatMessage(role="user", content=[{"type": "text", "text": "hel"}, {"type": "text", "text": "lo"}])
    assert m.text() == "hello"


def test_call_id_resolution_prefers_vapi_call_object():
    req = ChatCompletionRequest(messages=[], call={"id": "call_123"}, metadata={"call_id": "meta"})
    assert main.resolve_call_id(req) == "call_123"
    req = ChatCompletionRequest(messages=[], metadata={"call_id": "meta"})
    assert main.resolve_call_id(req) == "meta"
    assert main.resolve_call_id(ChatCompletionRequest(messages=[])).startswith("local-")


def test_lc_message_roles():
    msgs = main.to_lc_messages(
        [
            ChatMessage(role="system", content="s"),
            ChatMessage(role="user", content="u"),
            ChatMessage(role="assistant", content="a"),
            ChatMessage(role="tool", content="ignored"),
        ]
    )
    assert [m.type for m in msgs] == ["system", "human", "ai"]


# --- streaming format ------------------------------------------------------


async def test_stream_is_openai_sse(client, n8n_capture):
    payload = {"model": "x", "stream": True, "messages": [{"role": "user", "content": "hi"}], "call": {"id": "c1"}}
    async with client.stream("POST", "/v1/chat/completions", json=payload) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        body = (await r.aread()).decode()

    events = parse_sse(body)
    assert events[-1] == "[DONE]"
    chunks = [e for e in events if isinstance(e, dict)]
    assert all(c["object"] == "chat.completion.chunk" for c in chunks)
    assert chunks[0]["choices"][0]["delta"]["role"] == "assistant"
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    content = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks)
    assert len([c for c in chunks if c["choices"][0]["delta"].get("content")]) > 1, "expected multiple token deltas"
    assert content in FAKE_REPLIES


async def test_non_stream_returns_full_completion(client, n8n_capture):
    r = await client.post("/v1/chat/completions", json={"stream": False, "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["content"]


async def test_conversation_is_stateful_per_call(client, n8n_capture):
    call = {"id": "stateful-1"}
    for text in ["first", "second"]:
        r = await client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": text}], "call": call})
        assert r.status_code == 200
    snapshot = await main.graph.aget_state(main.thread_config("stateful-1"))
    humans = [m.content for m in snapshot.values["messages"] if m.type == "human"]
    assert humans == ["first", "second"]


# --- non-blocking webhook --------------------------------------------------


async def test_webhook_dispatch_does_not_block_stream(client, n8n_capture):
    payload = {"messages": [{"role": "user", "content": "hi"}], "call": {"id": "nb-1", "customer": {"number": "+15550001111"}}}
    t0 = time.perf_counter()
    r = await client.post("/v1/chat/completions", json=payload)
    elapsed = time.perf_counter() - t0
    assert r.status_code == 200
    assert elapsed < 0.25, f"stream blocked on webhook: {elapsed:.3f}s"
    assert n8n_capture == [], "webhook should not have completed before response returned"

    await slow_path.shutdown()
    assert len(n8n_capture) == 1
    evt = n8n_capture[0]
    assert evt["event_type"] == "turn.completed"
    assert evt["call_id"] == "nb-1"
    assert evt["phone_number"] == "+15550001111"
    assert evt["user_text"] == "hi"
    assert evt["assistant_text"]
    assert {"ttft", "total"} <= evt["latency_ms"].keys()


async def test_webhook_failure_is_swallowed(monkeypatch, client):
    async def boom(_: httpx.Request):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(slow_path, "_client", httpx.AsyncClient(transport=httpx.MockTransport(boom)))
    r = await client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    await slow_path.shutdown()  # must not raise
