CREATE TABLE IF NOT EXISTS evaluation_runs (
    clinic_id text NOT NULL REFERENCES clinics(clinic_id),
    run_id text NOT NULL,
    status text NOT NULL CHECK (status IN ('RUNNING', 'COMPLETED', 'ERROR')),
    started_at timestamptz NOT NULL,
    result jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (clinic_id, run_id)
);

CREATE INDEX IF NOT EXISTS evaluation_runs_latest_idx
    ON evaluation_runs (clinic_id, started_at DESC, run_id DESC);

ALTER TABLE evaluation_runs ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE evaluation_runs FROM anon, authenticated;
