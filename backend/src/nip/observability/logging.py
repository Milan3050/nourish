"""Structured logging with trace correlation and PII-safe processors."""

from __future__ import annotations

import logging
import sys

import structlog

from nip.config import get_settings
from nip.security.pii import redact


def _redact_processor(_, __, event: dict) -> dict:
    for k, v in list(event.items()):
        if isinstance(v, str) and k not in ("timestamp", "level", "logger"):
            event[k] = redact(v)
    return event


def _otel_ids(_, __, event: dict) -> dict:
    try:
        from opentelemetry import trace

        ctx = trace.get_current_span().get_span_context()
        if ctx.is_valid:
            event["trace_id"] = format(ctx.trace_id, "032x")
            event["span_id"] = format(ctx.span_id, "016x")
    except Exception:  # noqa: S110 - tracing is optional
        pass
    return event


def configure_logging() -> None:
    s = get_settings()
    level = getattr(logging, s.log_level.upper(), logging.INFO)
    logging.basicConfig(stream=sys.stdout, level=level, format="%(message)s")
    renderer = structlog.processors.JSONRenderer() if s.log_json else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _otel_ids,
            _redact_processor,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        cache_logger_on_first_use=True,
    )
