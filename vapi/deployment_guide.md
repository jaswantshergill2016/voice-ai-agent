# Vapi Deployment Guide

## Latency budget (target < 300 ms turn-around)

| Stage | Component | Budget |
|---|---|---|
| End-of-turn detection | Deepgram Nova-3, `eotTimeoutMs: 500` (hard cap), `endpointing: 150` (typical) | ~150 ms |
| Network to fast path | Vapi -> your `/v1/chat/completions` | ~20-40 ms |
| Time-to-first-token | Groq `llama-3.3-70b-versatile` (~100-200 ms) or `gpt-4o-mini` | ~150 ms |
| TTS first audio | Cartesia Sonic (~90 ms) / ElevenLabs Flash v2.5 (~75 ms) | ~90 ms |

Everything that is not on this path (CRM sync, DB logging, SMS) runs through
n8n and is dispatched *after* `[DONE]` is emitted.

## 1. Expose the fast path

The fast path must be reachable over HTTPS from Vapi.

```bash
docker compose up -d langgraph-fastpath            # or spring-ai-fastpath
ngrok http 8000                                    # dev only; deploy behind a real LB in prod
```

Deploy in the same region as your Vapi org (US-East by default) to minimize RTT.

## 2. Create the assistant

Edit `assistant_config.json`:

- `model.url` -> `https://<host>/v1` (Vapi appends `/chat/completions`).
  Use port 8000 for LangGraph or 8080 for Spring AI; both speak the same protocol.
- `serverUrl` -> your public n8n URL for `end-of-call-report` webhooks
  (workflow: `services/n8n/workflows/post_call_crm_sync.json`).
- `voice.voiceId` -> any Cartesia voice id from https://play.cartesia.ai/voices.

```bash
curl -s https://api.vapi.ai/assistant \
  -H "Authorization: Bearer $VAPI_API_KEY" \
  -H "Content-Type: application/json" \
  -d @vapi/assistant_config.json | jq .id
```

## 3. Alternative TTS: ElevenLabs Flash v2.5

Replace the `voice` block with:

```json
"voice": {
  "provider": "11labs",
  "model": "eleven_flash_v2_5",
  "voiceId": "21m00Tcm4TlvDq8ikWAM",
  "stability": 0.5,
  "similarityBoost": 0.75,
  "optimizeStreamingLatency": 4
}
```

## 4. Provider keys

Vapi hosts the STT/TTS calls, so Deepgram and Cartesia keys are entered in
the Vapi dashboard (**Provider Keys**), not in this repo's containers.
`DEEPGRAM_API_KEY` / `CARTESIA_API_KEY` in `.env.example` exist for
self-hosted pipelines or local scripts.

## 5. What Vapi sends to the fast path

```json
{
  "model": "voice-agent",
  "stream": true,
  "messages": [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}],
  "call": {"id": "…", "customer": {"number": "+1555…"}, "assistantId": "…"},
  "metadata": {}
}
```

Both services use `call.id` as the LangGraph thread id / correlation id and
forward `call.customer.number` to n8n for the SMS step.

## 6. Verify

```bash
# Streaming smoke test — first `data:` frame should arrive in < 200 ms
curl -N -sS -w '\nTTFB: %{time_starttransfer}s\n' http://localhost:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"stream":true,"messages":[{"role":"user","content":"Hello"}],"call":{"id":"test-1"}}'
```

Then place a test call from the Vapi dashboard and watch
`docker compose logs -f langgraph-fastpath n8n` for `ttft_ms` and webhook hits.
