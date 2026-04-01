-- +goose Up
-- One job row per Jira issue_key: remove duplicates (keep newest id), then enforce uniqueness.
DELETE FROM jobs
WHERE id IN (
  SELECT id
  FROM (
    SELECT id,
           ROW_NUMBER() OVER (PARTITION BY issue_key ORDER BY id DESC) AS rn
    FROM jobs
  ) sub
  WHERE rn > 1
);

CREATE UNIQUE INDEX idx_jobs_issue_key_unique ON jobs (issue_key);

-- +goose Down
DROP INDEX IF EXISTS idx_jobs_issue_key_unique;
