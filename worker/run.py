"""
Worker: poll DB for pending jobs, claim, run skeleton processing, update status.
Phase 1: no Jira or CrewAI; validates pipeline only. Logging per §3.5.
"""
import time

import psycopg2
from dotenv import load_dotenv

from .config import load_config
from .logger import get_logger

load_dotenv()
cfg = load_config()
log = get_logger(cfg["log_level"])


def get_pending_job(conn: psycopg2.extensions.connection):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, issue_key, status, payload, created_at FROM jobs WHERE status = %s ORDER BY id ASC LIMIT 1",
            ("pending",),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {"id": row[0], "issue_key": row[1], "status": row[2], "payload": row[3], "created_at": row[4]}


def claim_job(conn: psycopg2.extensions.connection, job_id: int) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE jobs SET status = %s, updated_at = now() WHERE id = %s AND status = %s",
            ("processing", job_id, "pending"),
        )
        conn.commit()
        return cur.rowcount > 0


def update_job_status(conn: psycopg2.extensions.connection, job_id: int, status: str):
    with conn.cursor() as cur:
        cur.execute("UPDATE jobs SET status = %s, updated_at = now() WHERE id = %s", (status, job_id))
    conn.commit()


def process_job(job: dict) -> str:
    """Phase 1: skeleton only. Returns 'done' or 'failed'."""
    # No Jira or CrewAI yet; just validate the pipeline.
    return "done"


def run_once(conn: psycopg2.extensions.connection) -> bool:
    job = get_pending_job(conn)
    if not job:
        return False
    job_id = job["id"]
    issue_key = job["issue_key"]
    if not claim_job(conn, job_id):
        return False  # another worker claimed it
    log.info("job claimed job_id=%s issue_key=%s", job_id, issue_key)
    try:
        status = process_job(job)
        update_job_status(conn, job_id, status)
        log.info("job completed job_id=%s issue_key=%s status=%s", job_id, issue_key, status)
    except Exception as e:
        log.exception("job failed job_id=%s issue_key=%s error=%s", job_id, issue_key, e)
        update_job_status(conn, job_id, "failed")
    return True


def main():
    database_url = cfg["database_url"]
    poll_interval = cfg["poll_interval_seconds"]
    log.info("worker starting database_url=%s poll_interval=%s", database_url, poll_interval)

    while True:
        try:
            conn = psycopg2.connect(database_url, connect_timeout=10)
            try:
                while run_once(conn):
                    pass  # process one job per iteration
            finally:
                conn.close()
        except Exception as e:
            log.error("worker loop error error=%s", e)
        time.sleep(poll_interval)


if __name__ == "__main__":
    main()
