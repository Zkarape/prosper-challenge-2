ALTER TABLE clinics ENABLE ROW LEVEL SECURITY;
ALTER TABLE catalog_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE agents ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_publications ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE pending_offers ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_turns ENABLE ROW LEVEL SECURITY;
ALTER TABLE reserved_slots ENABLE ROW LEVEL SECURITY;
ALTER TABLE bookings ENABLE ROW LEVEL SECURITY;
ALTER TABLE booking_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE usage_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE schema_migrations ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON TABLE clinics, catalog_snapshots, agents, agent_publications,
    conversations, pending_offers, conversation_turns, reserved_slots,
    bookings, booking_attempts, usage_events, schema_migrations,
    conversation_evaluations
FROM anon, authenticated;

ALTER VIEW conversation_evaluations SET (security_invoker = true);
