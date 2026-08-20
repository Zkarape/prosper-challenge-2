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
    coalesce(sum(u.latency_ms), 0)::numeric(14, 3) AS model_latency_ms,
    c.clinic_id
FROM conversations c
LEFT JOIN usage_events u USING (conversation_id)
WHERE c.deleted_at IS NULL
GROUP BY c.conversation_id;

ALTER VIEW conversation_evaluations SET (security_invoker = true);
REVOKE ALL ON TABLE conversation_evaluations FROM anon, authenticated;
