package db

import (
	"context"
	"database/sql"
	"errors"

	"github.com/jackc/pgx/v5/pgconn"
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

func isUniqueViolation(err error) bool {
	var pgErr *pgconn.PgError
	return errors.As(err, &pgErr) && pgErr.Code == "23505"
}

// InsertJob inserts a new job with status pending. Prefer UpsertJobFromWebhook for API paths;
// this fails if issue_key already exists (unique index).
func (db *DB) InsertJob(ctx context.Context, issueKey, payload string) (int64, error) {
	var id int64
	err := db.QueryRowContext(ctx,
		`INSERT INTO jobs (issue_key, status, payload) VALUES ($1, $2, $3) RETURNING id`,
		issueKey, JobPending, payload).Scan(&id)
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
		JobPending).Scan(&j.ID, &j.IssueKey, &j.Status, &j.Payload, &j.CreatedAt, &j.UpdatedAt)
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
		JobProcessing, jobID, JobPending)
	if err != nil {
		return err
	}
	n, _ := res.RowsAffected()
	if n == 0 {
		return sql.ErrNoRows // already claimed or not found
	}
	return nil
}

// UpdateJobStatus sets the job's terminal status (e.g. completed_full, awaiting_customer,
// completed_unsupported, skipped, failed). The worker owns valid values; column is unconstrained TEXT.
func (db *DB) UpdateJobStatus(ctx context.Context, jobID int64, status string) error {
	_, err := db.ExecContext(ctx,
		`UPDATE jobs SET status = $1, updated_at = now() WHERE id = $2`,
		status, jobID)
	return err
}

// UpsertJobFromWebhook maintains at most one jobs row per issue_key (enforced by migration).
// - No row: insert pending.
// - awaiting_customer, completed_unsupported, or failed: set pending + payload (reopened=true).
// - pending or processing: refresh payload only (deduped=true).
// - Other terminal (e.g. completed_full, skipped): set pending + payload for another worker pass.
func (db *DB) UpsertJobFromWebhook(ctx context.Context, issueKey, payload string) (jobID int64, reopened bool, deduped bool, err error) {
	var id int64
	var st string
	err = db.QueryRowContext(ctx,
		`SELECT id, status FROM jobs WHERE issue_key = $1`,
		issueKey,
	).Scan(&id, &st)
	if err == sql.ErrNoRows {
		err = db.QueryRowContext(ctx,
			`INSERT INTO jobs (issue_key, status, payload) VALUES ($1, $2, $3) RETURNING id`,
			issueKey, JobPending, payload,
		).Scan(&jobID)
		if err != nil && isUniqueViolation(err) {
			return db.UpsertJobFromWebhook(ctx, issueKey, payload)
		}
		if err != nil {
			return 0, false, false, err
		}
		return jobID, false, false, nil
	}
	if err != nil {
		return 0, false, false, err
	}

	switch st {
	case JobAwaitingCustomer, JobCompletedUnsupported, JobFailed:
		_, err = db.ExecContext(ctx,
			`UPDATE jobs SET status = $1, payload = $2, updated_at = now() WHERE id = $3`,
			JobPending, payload, id,
		)
		if err != nil {
			return 0, false, false, err
		}
		return id, true, false, nil
	case JobPending, JobProcessing:
		_, err = db.ExecContext(ctx,
			`UPDATE jobs SET payload = $1, updated_at = now() WHERE id = $2`,
			payload, id,
		)
		if err != nil {
			return 0, false, false, err
		}
		return id, false, true, nil
	default:
		// completed_full, skipped, or any other terminal — re-enqueue on same row
		_, err = db.ExecContext(ctx,
			`UPDATE jobs SET status = $1, payload = $2, updated_at = now() WHERE id = $3`,
			JobPending, payload, id,
		)
		if err != nil {
			return 0, false, false, err
		}
		return id, false, false, nil
	}
}
