package db

import (
	"context"
	"database/sql"
	"embed"
	"fmt"

	"github.com/pressly/goose/v3"
	_ "github.com/jackc/pgx/v5/stdlib"
)

//go:embed migrations/*.sql
var migrations embed.FS

// DB wraps *sql.DB for dependency injection.
type DB struct {
	*sql.DB
}

// Connect opens a database connection using DATABASE_URL (PostgreSQL).
// Same env var is used locally and on cluster.
func Connect(ctx context.Context, databaseURL string) (*DB, error) {
	sqlDB, err := sql.Open("pgx", databaseURL)
	if err != nil {
		return nil, fmt.Errorf("db open: %w", err)
	}
	if err := sqlDB.PingContext(ctx); err != nil {
		sqlDB.Close()
		return nil, fmt.Errorf("db ping: %w", err)
	}
	return &DB{sqlDB}, nil
}

// Migrate runs all pending goose migrations.
func Migrate(databaseURL string) error {
	goose.SetBaseFS(migrations)
	sqlDB, err := goose.OpenDBWithDriver("pgx", databaseURL)
	if err != nil {
		return fmt.Errorf("goose open: %w", err)
	}
	defer sqlDB.Close()
	if err := goose.Up(sqlDB, "migrations"); err != nil {
		return fmt.Errorf("goose up: %w", err)
	}
	return nil
}

// Close closes the database connection.
func (db *DB) Close() error {
	return db.DB.Close()
}
