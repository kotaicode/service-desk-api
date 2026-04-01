package db

// Job queue status values (aligned with worker/run.py terminal statuses).
const (
	JobPending              = "pending"
	JobProcessing           = "processing"
	JobCompletedFull        = "completed_full"
	JobAwaitingCustomer     = "awaiting_customer"
	JobCompletedUnsupported = "completed_unsupported"
	JobSkipped              = "skipped"
	JobFailed               = "failed"
)
