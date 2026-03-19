package api

import (
	"encoding/json"
	"io"
	"net/http"
)

// jiraWebhookBody is the minimal payload we expect from Jira Automation (issueKey or issue_key).
type jiraWebhookBody struct {
	IssueKey  string `json:"issueKey"`
	IssueKey2 string `json:"issue_key"`
}

// HandleJiraWebhook handles POST /webhook/jira. Validates X-Webhook-Secret, parses
// issue_key from body, inserts job into DB, returns 200. Logs per §3.5.
func (h *Handler) HandleJiraWebhook(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}

	secret := r.Header.Get("X-Webhook-Secret")
	if secret == "" {
		h.log.Warn("webhook: secret missing")
		writeError(w, http.StatusUnauthorized, "missing webhook secret")
		return
	}
	if secret != h.cfg.WebhookSecret {
		h.log.Warn("webhook: secret invalid")
		writeError(w, http.StatusUnauthorized, "invalid webhook secret")
		return
	}
	h.log.Debug("webhook: secret valid")

	body, err := io.ReadAll(io.LimitReader(r.Body, 1<<20))
	if err != nil {
		h.log.Error("webhook: failed to read body", "error", err.Error())
		writeError(w, http.StatusBadRequest, "failed to read body")
		return
	}

	var payload jiraWebhookBody
	if err := json.Unmarshal(body, &payload); err != nil {
		h.log.Error("webhook: invalid JSON", "error", err.Error())
		writeError(w, http.StatusBadRequest, "invalid payload")
		return
	}

	issueKey := payload.IssueKey
	if issueKey == "" {
		issueKey = payload.IssueKey2
	}
	if issueKey == "" {
		h.log.Warn("webhook: missing issue_key in payload")
		writeError(w, http.StatusBadRequest, "missing issue_key")
		return
	}

	h.log.Info("webhook received", "issue_key", issueKey)

	jobID, err := h.db.InsertJob(r.Context(), issueKey, string(body))
	if err != nil {
		h.log.Error("webhook: failed to store job", "issue_key", issueKey, "error", err.Error())
		writeError(w, http.StatusInternalServerError, "failed to store job")
		return
	}

	h.log.Info("job stored", "job_id", jobID, "issue_key", issueKey)
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":        true,
		"job_id":    jobID,
		"issue_key": issueKey,
	})
}
