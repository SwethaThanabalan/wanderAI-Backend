"""Structured logging configuration with sensitive data filtering."""

import logging
import re
import sys
import uuid

from pythonjsonlogger import jsonlogger

from app.core.config import get_settings

# Patterns that indicate sensitive values
_SENSITIVE_PATTERNS = [
    re.compile(r"(sk-[a-zA-Z0-9_-]{20,})"),          # OpenAI keys
    re.compile(r"(eyJ[a-zA-Z0-9_-]{50,})"),           # JWTs (Supabase, QStash)
    re.compile(r"(sb_secret_[a-zA-Z0-9_-]+)"),        # Supabase secret keys
    re.compile(r"(sig_[a-zA-Z0-9_-]{20,})"),          # QStash signing keys
]


def _redact_sensitive(value: str) -> str:
    """Replace sensitive tokens in a string with redacted placeholders."""
    for pattern in _SENSITIVE_PATTERNS:
        value = pattern.sub(lambda m: m.group()[:8] + "***REDACTED***", value)
    return value


class SafeJsonFormatter(jsonlogger.JsonFormatter):
    """JSON formatter that redacts sensitive values from log output."""

    def process_log_record(self, log_record: dict) -> dict:
        for key, value in log_record.items():
            if isinstance(value, str):
                log_record[key] = _redact_sensitive(value)
        return log_record


def setup_logging() -> None:
    """Configure structured JSON logging for the application."""
    settings = get_settings()

    # Use LOG_LEVEL env var, fall back to DEBUG in dev / INFO in prod
    level_name = settings.log_level.upper()
    if level_name == "DEBUG" and settings.is_production:
        level_name = "INFO"
    log_level = getattr(logging, level_name, logging.INFO)

    # Safe JSON formatter that redacts secrets
    formatter = SafeJsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    # Stream handler to stdout
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers = [handler]

    # Suppress noisy third-party loggers and HTTP debug that leaks credentials
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("hpack").setLevel(logging.WARNING)
    logging.getLogger("h2").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("supabase").setLevel(logging.WARNING)
    logging.getLogger("postgrest").setLevel(logging.WARNING)
    logging.getLogger("realtime").setLevel(logging.WARNING)
    logging.getLogger("gotrue").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger instance."""
    return logging.getLogger(name)


def generate_request_id() -> str:
    """Generate a unique request ID for tracing."""
    return str(uuid.uuid4())
