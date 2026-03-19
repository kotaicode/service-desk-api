package api

import (
	"fmt"
	"log"
	"strings"
)

// LogLevel represents logging verbosity.
type LogLevel int

const (
	LogLevelDebug LogLevel = iota
	LogLevelInfo
	LogLevelWarn
	LogLevelError
)

// Logger provides level-aware logging; never logs secrets.
type Logger struct {
	level LogLevel
}

// NewLogger parses LOG_LEVEL (DEBUG, INFO, WARN, ERROR) and returns a logger.
func NewLogger(level string) *Logger {
	switch strings.ToUpper(level) {
	case "DEBUG":
		return &Logger{level: LogLevelDebug}
	case "INFO":
		return &Logger{level: LogLevelInfo}
	case "WARN":
		return &Logger{level: LogLevelWarn}
	case "ERROR":
		return &Logger{level: LogLevelError}
	default:
		return &Logger{level: LogLevelInfo}
	}
}

func (l *Logger) Debug(msg string, keysAndValues ...any) {
	if l.level <= LogLevelDebug {
		l.log("DEBUG", msg, keysAndValues...)
	}
}

func (l *Logger) Info(msg string, keysAndValues ...any) {
	if l.level <= LogLevelInfo {
		l.log("INFO", msg, keysAndValues...)
	}
}

func (l *Logger) Warn(msg string, keysAndValues ...any) {
	if l.level <= LogLevelWarn {
		l.log("WARN", msg, keysAndValues...)
	}
}

func (l *Logger) Error(msg string, keysAndValues ...any) {
	if l.level <= LogLevelError {
		l.log("ERROR", msg, keysAndValues...)
	}
}

func (l *Logger) log(level, msg string, keysAndValues ...any) {
	if len(keysAndValues) == 0 {
		log.Printf("[%s] %s", level, msg)
		return
	}
	// Simple key=value pairs for correlation (job_id, issue_key, etc.)
	var b strings.Builder
	b.WriteString("[")
	b.WriteString(level)
	b.WriteString("] ")
	b.WriteString(msg)
	for i := 0; i+1 < len(keysAndValues); i += 2 {
		b.WriteString(" ")
		b.WriteString(formatKey(keysAndValues[i]))
		b.WriteString("=")
		b.WriteString(formatValue(keysAndValues[i+1]))
	}
	log.Println(b.String())
}

func formatKey(k any) string {
	if s, ok := k.(string); ok {
		return s
	}
	return "?"
}

func formatValue(v any) string {
	if v == nil {
		return ""
	}
	return fmt.Sprint(v)
}
