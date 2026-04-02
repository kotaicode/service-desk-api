# Service Desk POC — test scenarios

Manual and curl-based checks for the Go API, worker, PostgreSQL, Jira, CrewAI flow, and **Phase 4 kagent MCP**. Replace placeholder issue keys (`YOURPROJ-101`, etc.) with real keys in your Jira project.

**References:** `RUN.md`, `README.md` (smoke tests), `config/required_fields.yml`, `config/routing.yml`, `kagent_setup.md`.

---

## 0. Cluster fixture — failing deployment (for §4.3 / §5.1)

Use this so **kagent / `k8s-agent`** has real **unhealthy pods** and **events** to report (same narrative as the original “payments API” case, in a dedicated test namespace).

**Apply (kubeconfig must point at the cluster kagent can access, e.g. kind):**

```bash
kubectl apply -f test/k8s/failing-deployment.yaml
```

**Verify:**

```bash
kubectl get pods -n sd-poc-test
kubectl describe pod -n sd-poc-test -l app=payments-api
kubectl get events -n sd-poc-test --sort-by='.lastTimestamp' | tail -20
```

Expect **CrashLoopBackOff** (or `Error` / **Back-off** events).

**Jira ticket text for the first scenario (K8s + MCP happy path)** — align namespace and workload with the manifest:

```text
Summary: Payments API returning 503

Description:
Kubernetes deployment payments-api in namespace sd-poc-test is unhealthy.
Pods are in CrashLoopBackOff. Namespace: sd-poc-test. Service / workload: payments-api.
Seeing 503 from the ingress for about 45 minutes. Please check pod events and deployment status.
```

Then run webhook **§1.3** and worker as usual (**§5.1**).

**Tear down:**

```bash
kubectl delete namespace sd-poc-test
```

---

## Prerequisites for any run

| Requirement | Notes |
|-------------|--------|
| PostgreSQL | Database exists; `DATABASE_URL` in `.env` |
| `.env` | Copy from `.env.example`; never commit secrets |
| Go API | `go run .` — migrations on startup |
| Worker | `pip install -e ./service-desk-crew` + `pip install -r worker/requirements.txt`; `python -m worker` |
| Jira (flow tests) | `JIRA_BASE_URL`, `JIRA_API_TOKEN`, `JIRA_EMAIL` |
| LLM | `OPENAI_API_KEY` |
| Phase 4 MCP | `KAGENT_MCP_URL` (e.g. `http://127.0.0.1:8083/mcp`) + `kubectl port-forward -n kagent svc/kagent-controller 8083:8083` |

**Webhook curl template:**

```bash
curl -s -X POST http://localhost:8080/webhook/jira \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: YOUR_WEBHOOK_SECRET" \
  -d '{"issueKey":"YOUR-ISSUE-KEY"}'
```

---

## 1. API and webhook (no Jira processing needed)

| ID | Scenario | Steps | Expected |
|----|----------|--------|----------|
| **1.1** | Health / ping | `curl -s http://localhost:8080/ping` | HTTP 200, pong or documented body |
| **1.2** | Webhook — wrong secret | POST with bad/missing `X-Webhook-Secret` | **401**, no new `jobs` row |
| **1.3** | Webhook — valid secret | POST with `issueKey` and correct secret | **200**, job upserted (`pending` or per upsert rules) |
| **1.4** | Webhook — invalid JSON / missing `issue_key` | Malformed body | **5xx** or documented error; check API logs |

---

## 2. Worker + database (minimal)

| ID | Scenario | Steps | Expected |
|----|----------|--------|----------|
| **2.1** | Manual pending job | `INSERT` a row with `status = pending` and a test `issue_key` | Worker claims job; logs `job claimed`; terminal status after flow |
| **2.2** | Poll interval | Only one job processed per poll cycle with default settings | Matches `WORKER_POLL_INTERVAL_SECONDS` behaviour |
| **2.3** | DB connectivity | Wrong `DATABASE_URL` | Worker logs connection error |

---

## 3. Jira credentials and LLM

| ID | Scenario | Steps | Expected |
|----|----------|--------|----------|
| **3.1** | Missing Jira env | Omit `JIRA_*` in `.env`, enqueue job | Job **`failed`**; worker logs missing credentials |
| **3.2** | Missing `OPENAI_API_KEY` | Valid Jira, no OpenAI key | Job **`failed`**; log mentions OpenAI |
| **3.3** | Invalid Jira issue | Webhook for non-existent `issue_key` | Flow / Jira error → job **`failed`** (or documented behaviour) |

---

## 4. CrewAI flow — Intake and routing

Use **real Jira issues**; ticket text must match what Intake and routing expect (`config/required_fields.yml`, `config/routing.yml`).

| ID | Scenario | Ticket content (summary + description) | Expected |
|----|----------|----------------------------------------|----------|
| **4.1** | **Missing info** — no namespace / service | Vague text (“slow website”, “help”) without namespace and workload name | Internal comment asking for details; **`awaiting_customer`**; no `processed_issues` insert |
| **4.2** | **Unsupported** — not K8s | e.g. “VPN access request”, “password reset” — no k8s routing keywords | Internal comment out-of-scope; **`completed_unsupported`**; no `processed_issues` |
| **4.3** | **K8s path** — proceed to diagnostics | Include **namespace**, **service/app name**, and a **routing keyword** (`kubernetes`, `pod`, `deployment`, `503`, etc.) | Flow enters K8s diagnostics branch (see §5) |

**Example for 4.3 (happy-path shape)** — for **live cluster evidence**, apply **`test/k8s/failing-deployment.yaml`** first (**§0**) and use **`sd-poc-test` / `payments-api`** in the ticket.

**Generic example (any namespace you created):**

```text
Summary: Payments API returning 503

Description:
Kubernetes deployment in namespace production is unhealthy.
Pods restarting. Service: payments-api. Namespace: production.
502/503 errors from ingress for ~45 minutes.
```

---

## 5. Phase 4 — kagent MCP

| ID | Scenario | Steps | Expected |
|----|----------|--------|----------|
| **5.1** | **MCP available** | `KAGENT_MCP_URL` set; port-forward `kagent-controller` **8083:8083**; optional **`kubectl apply -f test/k8s/failing-deployment.yaml`** (**§0**); ticket as **4.3** with matching namespace/workload | Diagnostics use MCP (`invoke_agent` / `kagent/k8s-agent`); evidence may mention **CrashLoopBackOff** / events; artifact not starting with `[DIAGNOSTICS_UNAVAILABLE]`; synthesis + internal comment; **`completed_full`** on success; **`processed_issues`** row |
| **5.2** | **MCP unavailable** | Stop port-forward or wrong URL; ticket as **4.3** | Artifact / flow treats failure; internal comment that diagnostics could not be gathered; **`awaiting_customer`**; **no** `processed_issues` for full resolution |
| **5.3** | **Stub mode (offline)** | `USE_DIAGNOSTICS_STUB=true`; no `KAGENT_MCP_URL` required | Stub diagnostic bundle; no live cluster; flow can complete **`completed_full`** if rest of flow succeeds (per stub behaviour) |
| **5.4** | **Optional auth** | If MCP requires bearer token, set `KAGENT_MCP_TOKEN` | Successful MCP calls |

---

## 6. Idempotency and webhook upsert

| ID | Scenario | Steps | Expected |
|----|----------|--------|----------|
| **6.1** | **Full resolution idempotency** | After a successful **`completed_full`** for `issue_key` X, enqueue another job for **same** X | Worker **`skipped`**; log “idempotency skip”; no duplicate full-resolution comment |
| **6.2** | **Re-open after awaiting customer** | Job finished **`awaiting_customer`**; customer updates ticket; webhook again | **New** `pending` processing possible; **not** blocked by `processed_issues` (no row until full resolution) |
| **6.3** | **Dedupe while pending** | Two webhooks quickly for same key while **`pending`** / **`processing`** | Payload refresh / dedupe behaviour per `UpsertJobFromWebhook` (see README) |

---

## 7. Timeouts and failures

| ID | Scenario | Steps | Expected |
|----|----------|--------|----------|
| **7.1** | Flow timeout | Set `FLOW_TIMEOUT_SECONDS` low (e.g. 60) and heavy ticket | Job **`failed`** if worker thread hits timeout |
| **7.2** | MCP tool timeout | `MCP_TOOL_TIMEOUT_SECONDS` (optional) | MCP errors surfaced; may match **5.2** behaviour |

---

## 8. SQL verification (optional)

```sql
-- Latest jobs
SELECT id, issue_key, status, updated_at FROM jobs ORDER BY id DESC LIMIT 10;

-- Full-resolution markers
SELECT issue_key, processed_at, job_id FROM processed_issues ORDER BY processed_at DESC LIMIT 10;
```

---

## 9. Jira Automation (optional)

| ID | Scenario | Steps | Expected |
|----|----------|--------|----------|
| **9.1** | Tunnel | ngrok (or similar) to API `:8080` | Public HTTPS URL |
| **9.2** | Automation rule | JSM “When request created” → POST to webhook with `X-Webhook-Secret` and body containing `issueKey` | Same behaviour as curl **1.3**; end-to-end from real request |

---

## 10. Checklist before release

- [ ] **1.1**–**1.4** API / webhook behaviour  
- [ ] **2.1** worker picks up DB job  
- [ ] **3.1**–**3.3** env failure modes  
- [ ] **4.1**–**4.3** Intake + routing  
- [ ] **5.1**–**5.3** MCP / stub  
- [ ] **6.1**–**6.3** idempotency + upsert  
- [ ] **7.1**–**7.2** timeouts (if applicable)  
- [ ] **9.x** Jira Automation (if used)

---

## Quick issue-key table (copy and fill)

| Test ID | Jira issue key | Notes |
|---------|----------------|--------|
| 4.1 | YOURPROJ-201 | Missing info |
| 4.2 | YOURPROJ-202 | Unsupported |
| 4.3 / 5.1 | YOURPROJ-203 | K8s + MCP happy path |
| 5.2 | YOURPROJ-204 | MCP down |
| 6.1 | YOURPROJ-203 (reuse) | Second webhook after full resolution |

---

## See also

- `RUN.md` — terminals and commands  
- `kagent_setup.md` — kagent install, port-forward, `KAGENT_MCP_URL`  
- `documents/service-desk-implementation-plan.md` — phase deliverables and §12 success criteria  
