package config

import (
	"github.com/kelseyhightower/envconfig"
)

// Config holds application configuration from environment variables only (§3.8).
type Config struct {
	Port          int    `envconfig:"PORT" default:"8080"`
	DatabaseURL   string `envconfig:"DATABASE_URL" default:"postgres://localhost:5432/service_desk?sslmode=disable"`
	WebhookSecret string `envconfig:"WEBHOOK_SECRET" required:"true"`
	LogLevel     string `envconfig:"LOG_LEVEL" default:"INFO"`
}

// Load reads configuration from the environment.
func Load() (*Config, error) {
	var cfg Config
	if err := envconfig.Process("", &cfg); err != nil {
		return nil, err
	}
	return &cfg, nil
}
