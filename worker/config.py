"""Load config from environment only (§3.8). No hardcoded URLs."""
import os


def load_config():
    database_url = os.environ.get("DATABASE_URL", "file:./data.db")
    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    poll_interval_seconds = int(os.environ.get("WORKER_POLL_INTERVAL_SECONDS", "15"))
    jira_base_url = os.environ.get("JIRA_BASE_URL", "").rstrip("/")
    jira_api_token = os.environ.get("JIRA_API_TOKEN", "")
    jira_email = os.environ.get("JIRA_EMAIL", "")
    flow_timeout_seconds = int(os.environ.get("FLOW_TIMEOUT_SECONDS", "900"))
    return {
        "database_url": database_url,
        "log_level": log_level,
        "poll_interval_seconds": poll_interval_seconds,
        "jira_base_url": jira_base_url,
        "jira_api_token": jira_api_token,
        "jira_email": jira_email,
        "flow_timeout_seconds": flow_timeout_seconds,
    }
