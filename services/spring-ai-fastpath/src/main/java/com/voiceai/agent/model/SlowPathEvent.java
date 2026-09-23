package com.voiceai.agent.model;

import java.util.Map;

/** Payload posted to n8n after the fast path finishes streaming. Mirrors the Python service. */
public record SlowPathEvent(
        String event_type,
        String call_id,
        String phone_number,
        String user_text,
        String assistant_text,
        Map<String, Double> latency_ms,
        Map<String, Object> metadata) {
}
