ALTER TABLE conversations
    ADD COLUMN IF NOT EXISTS processing_token text,
    ADD COLUMN IF NOT EXISTS processing_until timestamptz;

CREATE INDEX IF NOT EXISTS conversations_processing_until_idx
    ON conversations (processing_until)
    WHERE processing_token IS NOT NULL;
