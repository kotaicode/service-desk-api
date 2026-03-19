package api

import (
	"encoding/json"
	"log"
	"net/http"

	"github.com/kotaicode/service-desk-api/config"
	"github.com/kotaicode/service-desk-api/db"
)

// Handler holds shared dependencies for HTTP handlers.
type Handler struct {
	cfg   *config.Config
	db    *db.DB
	log   *Logger
}

// NewHandler creates a new API handler.
func NewHandler(cfg *config.Config, database *db.DB) *Handler {
	return &Handler{
		cfg: cfg,
		db:  database,
		log: NewLogger(cfg.LogLevel),
	}
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(v); err != nil {
		log.Printf("writeJSON encode error: %v", err)
	}
}

func writeError(w http.ResponseWriter, status int, msg string) {
	writeJSON(w, status, map[string]string{"error": msg})
}
