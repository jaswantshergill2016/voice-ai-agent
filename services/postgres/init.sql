-- Slow-path event store written by the n8n workflows.
CREATE TABLE IF NOT EXISTS call_events (
    id             BIGSERIAL PRIMARY KEY,
    call_id        TEXT        NOT NULL,
    event_type     TEXT        NOT NULL,
    phone_number   TEXT,
    user_text      TEXT,
    assistant_text TEXT,
    ttft_ms        NUMERIC(10, 1),
    total_ms       NUMERIC(10, 1),
    metadata       JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS call_events_call_id_idx ON call_events (call_id, created_at);

CREATE TABLE IF NOT EXISTS call_summaries (
    id               BIGSERIAL PRIMARY KEY,
    call_id          TEXT        NOT NULL UNIQUE,
    phone_number     TEXT,
    ended_reason     TEXT,
    duration_seconds NUMERIC(10, 2),
    summary          TEXT,
    transcript       TEXT,
    recording_url    TEXT,
    cost_usd         NUMERIC(10, 4),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- n8n keeps its own tables in a separate database.
CREATE DATABASE n8n;
