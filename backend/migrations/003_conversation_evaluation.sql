ALTER TABLE conversations
    ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'ACTIVE',
    ADD COLUMN IF NOT EXISTS intent text,
    ADD COLUMN IF NOT EXISTS outcome text,
    ADD COLUMN IF NOT EXISTS safe boolean,
    ADD COLUMN IF NOT EXISTS ended_at timestamptz;

ALTER TABLE conversations
    DROP CONSTRAINT IF EXISTS conversations_status_check;

ALTER TABLE conversations
    ADD CONSTRAINT conversations_status_check
    CHECK (status IN ('ACTIVE', 'COMPLETED', 'FAILED', 'ABANDONED'));

CREATE TABLE IF NOT EXISTS usage_events (
    usage_event_id text PRIMARY KEY,
    conversation_id text NOT NULL REFERENCES conversations(conversation_id),
    turn_id text NOT NULL,
    stage text NOT NULL,
    model text NOT NULL,
    input_tokens integer NOT NULL CHECK (input_tokens >= 0),
    cached_input_tokens integer NOT NULL DEFAULT 0
        CHECK (cached_input_tokens >= 0 AND cached_input_tokens <= input_tokens),
    output_tokens integer NOT NULL CHECK (output_tokens >= 0),
    total_tokens integer GENERATED ALWAYS AS (input_tokens + output_tokens) STORED,
    estimated_cost_usd numeric(16, 8),
    latency_ms numeric(12, 3) NOT NULL,
    provider_response_id text,
    price_snapshot text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS usage_events_provider_response_idx
    ON usage_events (provider_response_id)
    WHERE provider_response_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS usage_events_conversation_idx
    ON usage_events (conversation_id, created_at);

CREATE OR REPLACE VIEW conversation_evaluations AS
SELECT
    c.conversation_id,
    c.status,
    c.intent,
    c.outcome,
    c.safe,
    c.created_at AS started_at,
    c.ended_at,
    c.message_number AS turn_count,
    count(u.usage_event_id)::integer AS model_call_count,
    coalesce(sum(u.input_tokens), 0)::bigint AS input_tokens,
    coalesce(sum(u.cached_input_tokens), 0)::bigint AS cached_input_tokens,
    coalesce(sum(u.output_tokens), 0)::bigint AS output_tokens,
    coalesce(sum(u.total_tokens), 0)::bigint AS total_tokens,
    sum(u.estimated_cost_usd) AS estimated_cost_usd,
    coalesce(sum(u.latency_ms), 0)::numeric(14, 3) AS model_latency_ms
FROM conversations c
LEFT JOIN usage_events u USING (conversation_id)
WHERE c.deleted_at IS NULL
GROUP BY c.conversation_id;
