-- +goose Up
CREATE TABLE IF NOT EXISTS processed_issues (
    issue_key TEXT NOT NULL PRIMARY KEY,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    job_id BIGINT
);

CREATE INDEX idx_processed_issues_processed_at ON processed_issues (processed_at);

-- +goose Down
DROP INDEX IF EXISTS idx_processed_issues_processed_at;
DROP TABLE IF EXISTS processed_issues;
