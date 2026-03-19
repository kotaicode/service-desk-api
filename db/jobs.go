package db

import (
	"context"
	"database/sql"
)

// Job represents a single job row (queue + idempotency).
type Job struct {
	ID        int64
	IssueKey  string
	Status    string
	Payload   string
	CreatedAt string
	UpdatedAt string
}

// InsertJob inserts a new job with status pending. Returns the new job ID.
func (db *DB) InsertJob(ctx context.Context, issueKey, payload string) (int64, error) {
	var id int64
	err := db.QueryRowContext(ctx,
		`INSERT INTO jobs (issue_key, status, payload) VALUES ($1, $2, $3) RETURNING id`,
		issueKey, "pending", payload).Scan(&id)
	if err != nil {
		return 0, err
	}
	return id, nil
}

// GetPendingJob returns one job with status 'pending' for claiming. Uses a simple
// SELECT that the worker will follow with an immediate UPDATE to 'processing'.
func (db *DB) GetPendingJob(ctx context.Context) (*Job, error) {
	var j Job
	err := db.QueryRowContext(ctx,
		`SELECT id, issue_key, status, payload, created_at, updated_at FROM jobs WHERE status = $1 ORDER BY id ASC LIMIT 1`,
		"pending").Scan(&j.ID, &j.IssueKey, &j.Status, &j.Payload, &j.CreatedAt, &j.UpdatedAt)
	if err == sql.ErrNoRows {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &j, nil
}

// ClaimJob sets job status to 'processing'. Call after GetPendingJob in the same
// process to avoid two workers claiming the same job. For production you could use
// SELECT FOR UPDATE SKIP LOCKED.
func (db *DB) ClaimJob(ctx context.Context, jobID int64) error {
	res, err := db.ExecContext(ctx,
		`UPDATE jobs SET status = $1, updated_at = now() WHERE id = $2 AND status = $3`,
		"processing", jobID, "pending")
	if err != nil {
		return err
	}
	n, _ := res.RowsAffected()
	if n == 0 {
		return sql.ErrNoRows // already claimed or not found
	}
	return nil
}

// UpdateJobStatus sets job status to 'done' or 'failed'.
func (db *DB) UpdateJobStatus(ctx context.Context, jobID int64, status string) error {
	_, err := db.ExecContext(ctx,
		`UPDATE jobs SET status = $1, updated_at = now() WHERE id = $2`,
		status, jobID)
	return err
}
