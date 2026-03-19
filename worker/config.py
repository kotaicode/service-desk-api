"""Load config from environment only (§3.8). No hardcoded URLs."""
import os

def load_config():
    database_url = os.environ.get("DATABASE_URL", "file:./data.db")
    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    poll_interval_seconds = int(os.environ.get("WORKER_POLL_INTERVAL_SECONDS", "15"))
    return {
        "database_url": database_url,
        "log_level": log_level,
        "poll_interval_seconds": poll_interval_seconds,
    }
