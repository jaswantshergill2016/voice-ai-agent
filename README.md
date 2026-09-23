# Ultra-Low Latency Voice AI Agent

Production-ready scaffold for a Vapi-powered voice agent targeting **< 300 ms
turn-around**. Two interchangeable OpenAI-compatible streaming fast paths
(LangGraph/FastAPI and Spring AI/WebFlux) feed Vapi; all side effects run
asynchronously through n8n so they never touch the token stream.

```
              ┌─────────────── FAST PATH (live, streaming) ────────────────┐
 caller ─▶ Vapi ─▶ Deepgram Nova-3 (STT, eot 500ms) ─▶ POST /v1/chat/completions ─▶ SSE tokens ─▶ Cartesia Sonic (TTS) ─▶ caller
              │              langgraph-fastpath :8000  |  spring-ai-fastpath :8080  │
              └────────────────────────────┬────────────────────────────────┘
                                           │ fire-and-forget after [DONE]
              ┌─────────────── SLOW PATH (async) ─────────────────────────┐
              │  n8n :5678  ─▶ Postgres :5432 (call_events, call_summaries) │
              │             ─▶ CRM upsert (HubSpot/Salesforce contract)      │
              │             ─▶ Twilio SMS (simulated / real node)            │
              └────────────────────────────────────────────────────────────┘
```

## Layout

```
.
├── docker-compose.yml           # langgraph-fastpath, spring-ai-fastpath, n8n, postgres (+ n8n-import)
├── .env.example
├── services/
│   ├── langgraph-fastpath/      # Python 3.12 · FastAPI · LangGraph (stateful per call) · Groq/OpenAI
│   │   ├── main.py              # POST /v1/chat/completions (SSE), /healthz, local CRM/Twilio mocks
│   │   ├── graph.py             # StateGraph + MemorySaver checkpointer keyed by Vapi call id
│   │   ├── llm.py               # Groq / OpenAI / fake provider factory
│   │   ├── slow_path.py         # asyncio.create_task() dispatcher → n8n webhook
│   │   ├── schemas.py, config.py, tests/
│   ├── spring-ai-fastpath/      # Java 21 · Spring Boot 3.4 · WebFlux · Spring AI 1.0
│   │   └── src/main/java/com/voiceai/agent/
│   │       ├── Application.java
│   │       ├── controller/VoiceAgentController.java   # Flux<String> SSE, ChatClient.prompt().stream().content()
│   │       ├── service/SlowPathDispatcher.java        # WebClient .subscribe() fire-and-forget
│   │       └── model/, config/
│   ├── n8n/
│   │   ├── workflows/async_action_trigger.json        # mid-call turn events → Postgres → CRM note
│   │   ├── workflows/post_call_crm_sync.json          # Vapi end-of-call-report → Postgres → CRM → SMS
│   │   └── credentials/postgres.json
│   └── postgres/init.sql
└── vapi/
    ├── assistant_config.json    # deepgram nova-3 (eotTimeoutMs 500) · custom-llm · cartesia sonic-english
    └── deployment_guide.md
```

## Quick start

```bash
cp .env.example .env            # add GROQ_API_KEY (or OPENAI_API_KEY)
docker compose up -d --build
docker compose logs -f n8n-import   # imports + activates both workflows once
```

| Service | URL |
|---|---|
| LangGraph fast path | http://localhost:8000/v1/chat/completions |
| Spring AI fast path | http://localhost:8080/v1/chat/completions |
| n8n editor | http://localhost:5678 |
| Postgres | `postgres://voiceai:voiceai@localhost:5432/voiceai` |

No LLM key yet? `LLM_PROVIDER=fake docker compose up langgraph-fastpath` serves a
deterministic local model so you can exercise the whole pipeline offline.

## Verify

### 1. Streaming + TTFB

```bash
curl -N -sS -w '\nTTFB %{time_starttransfer}s  total %{time_total}s\n' \
  http://localhost:8000/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"stream":true,"messages":[{"role":"user","content":"Hello"}],"call":{"id":"smoke-1","customer":{"number":"+15550001111"}}}'
```

Expected output shape (identical for :8080):

```
data: {"id":"chatcmpl-…","object":"chat.completion.chunk","created":…,"model":"…","choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]}

data: {"id":"chatcmpl-…","object":"chat.completion.chunk",…,"choices":[{"index":0,"delta":{"content":"Sure,"},"finish_reason":null}]}
…
data: {"id":"chatcmpl-…",…,"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
TTFB 0.012s  total 0.31s
```

TTFB with `LLM_PROVIDER=fake` measures pure transport overhead (single-digit
ms). With Groq the first `data:` frame arrives immediately and the first token
follows at the provider's TTFT (~100–200 ms). Both services log
`call=… ttft_ms=… total_ms=…` per turn.

### 2. Slow path is non-blocking

The webhook is dispatched *after* `[DONE]` and is never awaited. Prove it by
pointing the fast path at an endpoint that sleeps and observing that
`total` is unchanged:

```bash
N8N_WEBHOOK_URL=http://httpbin.org/delay/3 LLM_PROVIDER=fake docker compose up -d langgraph-fastpath
curl -N -sS -o /dev/null -w 'total %{time_total}s\n' http://localhost:8000/v1/chat/completions \
  -H 'Content-Type: application/json' -d '{"messages":[{"role":"user","content":"hi"}]}'
# total 0.0Xs  ← not 3s
```

Then confirm delivery: `docker compose logs n8n | grep call-event` or
`psql … -c 'select call_id, ttft_ms, total_ms from call_events order by id desc limit 5;'`.

### 3. Tests

```bash
# Python: message parsing, SSE format, statefulness, non-blocking webhook (mocked slow n8n)
cd services/langgraph-fastpath && python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt && .venv/bin/pytest -q

# Java: SSE frame format + webhook does not delay the stream (stubbed ChatModel, no network)
cd services/spring-ai-fastpath && mvn -q test
```

## Design notes

- **Statefulness.** LangGraph keeps history per `call.id` in a `MemorySaver`
  checkpointer, so each turn only submits the newest utterance (Vapi still
  sends the whole history; the first turn seeds it). Swap `MemorySaver` for
  `langgraph.checkpoint.postgres.AsyncPostgresSaver` when running >1 replica.
  The Spring service is stateless and replays Vapi's full history each turn.
- **Slow path never blocks.** Python uses `asyncio.create_task` on a shared
  `httpx.AsyncClient`; Java `.subscribe()`s a `WebClient` `Mono` on
  `boundedElastic`. Errors are logged and swallowed. n8n replies `202`
  from a *Respond to Webhook* node before doing any work.
- **Provider switching.** Groq and OpenAI share the wire protocol, so the
  Spring service is just `LLM_BASE_URL` + `LLM_MODEL`; Python uses
  `LLM_PROVIDER=groq|openai|fake`.
- **Latency levers** (see `vapi/deployment_guide.md`): `eotTimeoutMs: 500` hard
  cap with `endpointing: 150`; `max_tokens: 256`; a system prompt that forbids
  markdown/lists; single-sentence `chunkPlan` for TTS; deploy in Vapi's region.
