CREATE TABLE IF NOT EXISTS clinics (
    clinic_id text PRIMARY KEY,
    name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS catalog_snapshots (
    clinic_id text NOT NULL REFERENCES clinics(clinic_id),
    catalog_hash text NOT NULL,
    catalog jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (clinic_id, catalog_hash)
);

CREATE TABLE IF NOT EXISTS agents (
    agent_id text PRIMARY KEY,
    clinic_id text NOT NULL REFERENCES clinics(clinic_id),
    name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agent_publications (
    publication_id text PRIMARY KEY,
    agent_id text NOT NULL REFERENCES agents(agent_id),
    config_hash text NOT NULL,
    config jsonb NOT NULL,
    published_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (agent_id, config_hash)
);

CREATE TABLE IF NOT EXISTS conversations (
    conversation_id text PRIMARY KEY,
    clinic_id text NOT NULL REFERENCES clinics(clinic_id),
    agent_publication_id text REFERENCES agent_publications(publication_id),
    catalog_hash text NOT NULL,
    patient_request jsonb NOT NULL,
    message_number integer NOT NULL DEFAULT 0,
    booking jsonb,
    last_engine_result jsonb,
    rejected_alternatives jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    deleted_at timestamptz
);

CREATE TABLE IF NOT EXISTS pending_offers (
    offer_id text PRIMARY KEY,
    conversation_id text NOT NULL UNIQUE REFERENCES conversations(conversation_id),
    kind text NOT NULL,
    request_fingerprint text NOT NULL,
    catalog_hash text NOT NULL,
    options jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL DEFAULT (now() + interval '30 minutes')
);

CREATE TABLE IF NOT EXISTS conversation_turns (
    turn_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    conversation_id text NOT NULL REFERENCES conversations(conversation_id),
    message_id text NOT NULL,
    message_number integer NOT NULL,
    patient_text text NOT NULL,
    response jsonb NOT NULL,
    input_tokens integer NOT NULL DEFAULT 0,
    cached_input_tokens integer NOT NULL DEFAULT 0,
    output_tokens integer NOT NULL DEFAULT 0,
    total_latency_ms numeric(12, 3),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (conversation_id, message_id),
    UNIQUE (conversation_id, message_number)
);

CREATE INDEX IF NOT EXISTS conversation_turns_created_idx
    ON conversation_turns (conversation_id, created_at DESC);

CREATE TABLE IF NOT EXISTS reserved_slots (
    slot_id text PRIMARY KEY,
    reason text NOT NULL DEFAULT 'manual',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS bookings (
    booking_id text PRIMARY KEY,
    offer_id text NOT NULL UNIQUE,
    conversation_id text NOT NULL REFERENCES conversations(conversation_id),
    candidate_id text NOT NULL,
    slot_id text NOT NULL UNIQUE,
    slot jsonb NOT NULL,
    status text NOT NULL CHECK (status IN ('confirmed', 'cancelled')),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS booking_attempts (
    attempt_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    offer_id text NOT NULL,
    conversation_id text NOT NULL,
    slot_id text NOT NULL,
    outcome text NOT NULL,
    detail text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS booking_attempts_offer_idx
    ON booking_attempts (offer_id, created_at DESC);
