"""
Worker: poll DB for pending jobs, claim, run L1 CrewAI Flow; idempotency per §4.3. Logging per §3.5.

Job terminal statuses: completed_full, awaiting_customer, completed_unsupported, skipped, failed.
processed_issues is updated only for completed_full (full K8s + synthesis path).
"""
import os
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout

import psycopg2
from dotenv import load_dotenv

from .config import load_config
from .logger import get_logger

load_dotenv()
cfg = load_config()
log = get_logger(cfg["log_level"])


def is_processed(conn: psycopg2.extensions.connection, issue_key: str) -> bool:
    """Return True if this issue_key already has a processed marker (idempotency)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM processed_issues WHERE issue_key = %s LIMIT 1",
            (issue_key,),
        )
        return cur.fetchone() is not None


def set_processed(conn: psycopg2.extensions.connection, issue_key: str, job_id: int) -> None:
    """Record full-resolution completion for issue_key (K8s + synthesis only)."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO processed_issues (issue_key, job_id) VALUES (%s, %s) ON CONFLICT (issue_key) DO UPDATE SET processed_at = now(), job_id = EXCLUDED.job_id",
            (issue_key, job_id),
        )
    conn.commit()


def get_pending_job(conn: psycopg2.extensions.connection):
    """Only `pending` jobs are eligible; awaiting_customer / completed_unsupported never run until webhook resets to pending."""
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


def process_job(conn: psycopg2.extensions.connection, job: dict, cfg: dict) -> str:
    """
    Idempotency (full-resolution only), then L1 CrewAI Flow.
    Returns job row status: completed_full, awaiting_customer, completed_unsupported, skipped, failed.
    """
    job_id = job["id"]
    issue_key = job["issue_key"]

    if is_processed(conn, issue_key):
        log.warning("idempotency skip job_id=%s issue_key=%s (already full_resolution)", job_id, issue_key)
        return "skipped"

    base_url = cfg.get("jira_base_url") or ""
    api_token = cfg.get("jira_api_token") or ""
    email = cfg.get("jira_email") or ""
    if not base_url or not api_token or not email:
        log.error("job_id=%s issue_key=%s Jira credentials missing (JIRA_BASE_URL, JIRA_API_TOKEN, JIRA_EMAIL)", job_id, issue_key)
        return "failed"

    if not os.environ.get("OPENAI_API_KEY"):
        log.error("job_id=%s issue_key=%s OPENAI_API_KEY missing (required for CrewAI LLM)", job_id, issue_key)
        return "failed"

    from service_desk_crew.flow import (
        FLOW_OUTCOME_AWAITING_CUSTOMER,
        FLOW_OUTCOME_FULL_RESOLUTION,
        FLOW_OUTCOME_UNSUPPORTED,
    )
    from service_desk_crew.main import run_l1_support

    timeout = cfg.get("flow_timeout_seconds", 900)
    log.info("flow start job_id=%s issue_key=%s timeout_seconds=%s", job_id, issue_key, timeout)
    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(run_l1_support, issue_key, job_id)
            outcome = fut.result(timeout=timeout)
    except FuturesTimeout:
        log.error("job_id=%s issue_key=%s flow timeout after %s seconds", job_id, issue_key, timeout)
        return "failed"
    except Exception as e:
        log.error("job_id=%s issue_key=%s flow failed error=%s", job_id, issue_key, e)
        return "failed"

    if outcome == FLOW_OUTCOME_FULL_RESOLUTION:
        log.info("flow end full_resolution job_id=%s issue_key=%s", job_id, issue_key)
        set_processed(conn, issue_key, job_id)
        return "completed_full"
    if outcome == FLOW_OUTCOME_AWAITING_CUSTOMER:
        log.info("flow end awaiting_customer job_id=%s issue_key=%s", job_id, issue_key)
        return "awaiting_customer"
    if outcome == FLOW_OUTCOME_UNSUPPORTED:
        log.info("flow end completed_unsupported job_id=%s issue_key=%s", job_id, issue_key)
        return "completed_unsupported"

    log.error("job_id=%s issue_key=%s unexpected flow outcome=%s", job_id, issue_key, outcome)
    return "failed"


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
        status = process_job(conn, job, cfg)
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
