-- +goose Up
CREATE TABLE IF NOT EXISTS jobs (
    id BIGSERIAL PRIMARY KEY,
    issue_key TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    payload TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_jobs_status ON jobs (status);
CREATE INDEX idx_jobs_issue_key ON jobs (issue_key);

-- +goose Down
DROP INDEX IF EXISTS idx_jobs_issue_key;
DROP INDEX IF EXISTS idx_jobs_status;
DROP TABLE IF EXISTS jobs;
