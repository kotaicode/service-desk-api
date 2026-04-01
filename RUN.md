# Run the full stack (local)

PostgreSQL must be running. **Set `WEBHOOK_SECRET` and worker vars** in `.env` before starting.

**Go** has no virtualenv: dependencies live in **`go.mod`** for this repo. **Python** (worker) should use a **venv** so installs don’t touch your system Python (`.venv/` is gitignored).

## One-time setup (from repo root)

```sh
createdb service_desk   # or create the DB your DATABASE_URL will use

cp .env.example .env
# Edit .env: WEBHOOK_SECRET, DATABASE_URL, JIRA_BASE_URL, JIRA_API_TOKEN, JIRA_EMAIL, OPENAI_API_KEY

go mod tidy

python3 -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -U pip
pip install -e ./service-desk-crew
pip install -r worker/requirements.txt
```

The Go API runs **migrations on startup** (creates/updates `jobs`, `processed_issues`).

## Run (three terminals, repo root)

**Terminal 1 — API (webhook + health)**

```sh
cd /path/to/service-desk-api
go run .
```

Listens on **`:8080`** unless `PORT` is set. Wait for `migrations complete` and `server listening`.

**Terminal 2 — Worker (CrewAI + Jira)**

```sh
cd /path/to/service-desk-api
source .venv/bin/activate    # Windows: .venv\Scripts\activate
python -m worker
```

**Terminal 3 — (Optional) expose webhook for Jira Automation**

```sh
ngrok http 8080
```

Use the HTTPS URL Jira shows + path `/webhook/jira` in Automation. Header: `X-Webhook-Secret` = same as `.env`.

## Quick checks (no Jira)

```sh
curl -s http://localhost:8080/ping
```

```sh
curl -s -X POST http://localhost:8080/webhook/jira \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: YOUR_WEBHOOK_SECRET" \
  -d '{"issueKey":"TEST-1"}'
```

Expect `200` and `"ok":true`. Worker logs should show the job claimed and processed (Jira/LLM errors → job `failed` if env is wrong).

## See also

- **README.md** — smoke tests, DB queries, Jira Automation JSON body (`issueKey`), troubleshooting.
