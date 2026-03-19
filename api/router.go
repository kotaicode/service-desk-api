package api

import "net/http"

// Router builds the top-level HTTP handler with all routes.
func (h *Handler) Router() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /ping", h.handlePing)
	mux.HandleFunc("GET /health", h.handleHealth)
	mux.HandleFunc("POST /webhook/jira", h.HandleJiraWebhook)
	return mux
}

func (h *Handler) handlePing(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"message": "pong"})
}

func (h *Handler) handleHealth(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{"status": "ok"})
}
