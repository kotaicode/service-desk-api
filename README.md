# Service Desk API

Go API and Python worker for the Service Desk POC — L1 support automation (Jira webhook → queue → CrewAI flow). This repo implements the **webhook receiver** and **worker** as per the Service Desk POC Technical Specification.

**Phase 1 (Foundation):** Webhook → validate secret → store job in DB → worker polls, claims, and processes jobs. No Jira API or CrewAI yet; single env contract and logging.

## Architecture

- **Go API:** Receives Jira webhook at `POST /webhook/jira`, validates `X-Webhook-Secret`, parses `issue_key` from body, inserts a job into the database, returns 200.
- **Database:** PostgreSQL (`DATABASE_URL`); jobs table with `id`, `issue_key`, `status`, `payload`, `created_at`, `updated_at`.
- **Worker (Python):** Polls for `pending` jobs, claims one (sets `processing`), runs skeleton processing, sets `done` or `failed`. Later phases add CrewAI and Jira.

Configuration is **env-only** (§3.8): same variable names locally (`.env`) and on cluster (Kubernetes Secrets). No code fork.

## Prerequisites

- Go 1.22+
- Python 3.10+
- PostgreSQL (local or remote; create a database e.g. `service_desk`)
- (Optional) ngrok or similar for exposing webhook to Jira

## Setup

1. Create a PostgreSQL database and copy env template:
   ```sh
   createdb service_desk   # or use your existing DB
   cp .env.example .env
   # Edit .env: set WEBHOOK_SECRET (required), set DATABASE_URL and LOG_LEVEL if needed.
   ```

2. Install Go deps and run migrations (migrations run on API startup):
   ```sh
   go mod tidy
   ```

3. Install Python deps for the worker:
   ```sh
   cd worker && pip install -r requirements.txt && cd ..
   ```

## Run locally

1. Start the Go API (loads `.env` automatically):
   ```sh
   go run .
   ```
   API listens on `:8080` (or `PORT` from env).

2. In another terminal, start the worker:
   ```sh
   python -m worker
   ```
   Worker reads `DATABASE_URL` and `LOG_LEVEL` from env, polls every 15s by default.

## Smoke tests

### 1. Manual job insert (no Jira)

Insert a pending job and confirm the worker picks it up:

```sh
psql "$DATABASE_URL" -c "INSERT INTO jobs (issue_key, status, payload) VALUES ('TEST-1', 'pending', '{}');"
# Watch worker logs: "job claimed", then "job completed" with status=done.
```

### 2. Webhook (valid secret)

```sh
curl -X POST http://localhost:8080/webhook/jira \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: YOUR_SECRET_FROM_ENV" \
  -d '{"issueKey": "PROJ-123"}'
```

Expect `200` and `{"ok":true,"job_id":...,"issue_key":"PROJ-123"}`. Check API logs for "webhook received" and "job stored". Worker should claim and complete the job.

### 3. Webhook (invalid secret)

```sh
curl -X POST http://localhost:8080/webhook/jira \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: wrong" \
  -d '{"issueKey": "PROJ-456"}'
```

Expect `401` and no new job in the database.

## Endpoints

| Method | Path            | Description                    |
|--------|-----------------|--------------------------------|
| GET    | `/ping`         | Liveness                       |
| GET    | `/health`       | Health status                  |
| POST   | `/webhook/jira` | Jira webhook (X-Webhook-Secret) |

## Testing Jira push notifications with ngrok (localhost)

Use ngrok to expose your local API so Jira Automation can send webhooks to it.

### 1. Install ngrok

- **macOS (Homebrew):** `brew install ngrok`
- **Or:** sign up at [ngrok.com](https://ngrok.com), download the binary, and add it to your PATH.

### 2. Start your stack

In separate terminals:

```sh
# Terminal 1: Go API
go run .

# Terminal 2: Worker
python -m worker
```

Leave both running.

### 3. Start ngrok tunnel

```sh
ngrok http 8080
```

Note the **HTTPS Forwarding** URL (e.g. `https://abc123.ngrok-free.app`).  
**Free tier:** this URL changes each time you restart ngrok.

### 4. Configure Jira Service Management Automation

1. In your Jira Service Management project: **Project settings** → **Automation** (or **Apps** → **Automation**).
2. Create a new rule:
   - **Trigger:** **When request created** (or **When issue is created** for Jira Software).
3. Add action: **Send web request**.
   - **URL:** `https://<your-ngrok-url>/webhook/jira`  
     Example: `https://abc123.ngrok-free.app/webhook/jira`
   - **Method:** `POST`
   - **Headers:** add one header:
     - **Name:** `X-Webhook-Secret`
     - **Value:** the same value as `WEBHOOK_SECRET` in your `.env`
   - **Body:** choose **JSON** and send the issue key. For example:
     ```json
     {
       "issueKey": "{{issue.key}}"
     }
     ```
     (Use your project’s smart value for the issue key if different, e.g. `{{request.key}}` for JSM.)
4. Save and enable the rule.

### 5. Test

1. Create a new request/issue in the project.
2. In the ngrok terminal you should see an HTTP request to `/webhook/jira`.
3. In the Go API terminal you should see logs: `webhook received`, `job stored` with the issue key.
4. In the worker terminal you should see `job claimed` and `job completed` for that issue key.
5. Optionally check the database: `psql "$DATABASE_URL" -c "SELECT id, issue_key, status FROM jobs ORDER BY id DESC LIMIT 5;"`

### Troubleshooting

- **401 from API:** Ensure the header `X-Webhook-Secret` in Jira exactly matches `WEBHOOK_SECRET` in `.env` (no extra spaces).
- **URL not reachable:** Ensure ngrok is running and the URL in the Automation rule uses **https** and includes `/webhook/jira`.
- **URL changed:** After restarting ngrok, update the webhook URL in the Jira Automation rule.

## Project layout

```
.
├── api/           # Go HTTP handlers (webhook, ping, health)
├── config/        # Env-only config
├── db/            # PostgreSQL connection, migrations, job operations
├── worker/        # Python worker (poll, claim, process)
├── main.go
├── .env.example
├── .gitignore
└── README.md
```

## Reference

- Service Desk POC Technical Specification (see `agentic-dev/service-desk-poc-tech-spec.md` when in a monorepo)
- Service Desk Implementation Plan (see `agentic-dev/service-desk-implementation-plan.md`)
