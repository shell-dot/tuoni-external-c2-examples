"""Logging helpers shared by the examples; importing this never configures logging."""

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
import math
from pathlib import Path
import sys
import traceback


_context = ContextVar("tuoni_log_context", default=None)
_standard_fields = frozenset(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}
_envelope_fields = frozenset({
    "type", "id", "agentGuid", "commandId", "templateName", "restrictToAgent",
    "status", "success", "commandStartSuccessful", "validateUsingSchema", "__type__",
})


def clean_text(value, limit=256):
    """Bound text and escape terminal control characters, preserving readable Unicode."""
    original = "<%s>" % type(value).__name__ if isinstance(value, (dict, list, tuple, set)) else str(value)
    text = "".join(char if char.isprintable() else repr(char)[1:-1] for char in original[:limit])
    if len(text) > limit or len(original) > limit:
        return text[:max(0, limit - 1)] + ("…" if limit else "")
    return text


@contextmanager
def log_context(**fields):
    """Attach context to logs in this thread/task without leaking it to another request."""
    values = dict(_context.get() or {})
    values.update(fields)
    token = _context.set(values)
    try:
        yield
    finally:
        _context.reset(token)


def log_payload(logger, event, payload, *, enabled=False, limit=1024, **fields):
    """Log a bounded envelope preview, hiding all non-envelope values by default.

    Configuration, results, metadata, credentials and unknown values are never
    included. Malformed/non-object bodies are omitted rather than dumped.
    """
    if not enabled or not logger.isEnabledFor(logging.DEBUG):
        return
    preview = "<non-object payload omitted>"
    if isinstance(payload, dict):
        redacted = {}
        for index, (key, value) in enumerate(payload.items()):
            if index >= 32:
                redacted["..."] = "<additional fields omitted>"
                break
            name = clean_text(key, 64)
            if key in {"success", "commandStartSuccessful", "validateUsingSchema"} and not isinstance(value, bool):
                redacted[name] = "<invalid %s>" % type(value).__name__
            elif key in _envelope_fields and isinstance(value, (str, int, float, bool, type(None))):
                redacted[name] = clean_text(value, 128) if isinstance(value, str) else _field_value(value)
            else:
                redacted[name] = "<redacted>"
        preview = json.dumps(redacted, ensure_ascii=True, allow_nan=False, default=str)
    logger.debug(event, extra={**fields, "payload_preview": clean_text(preview, limit)})


def _field_value(value):
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, str):
        return clean_text(value, 2048)
    return "<%s>" % type(value).__name__


class DiagnosticFormatter(logging.Formatter):
    """Render one escaped text line or one JSON object per event, including tracebacks."""

    def __init__(self, output_format="text"):
        super().__init__()
        if output_format not in ("text", "json"):
            raise ValueError("output_format must be text or json")
        self.output_format = output_format

    def format(self, record):
        timestamp = datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds")
        event = {
            "timestamp": timestamp.replace("+00:00", "Z"),
            "level": clean_text(record.levelname),
            "logger": clean_text(record.name),
            "thread": clean_text(record.threadName),
            "event": clean_text(record.getMessage(), 2048),
        }
        context = dict(_context.get() or {})
        context.update({key: value for key, value in record.__dict__.items() if key not in _standard_fields})
        for key, value in context.items():
            if key not in event:
                event[clean_text(key, 64)] = _field_value(value)
        if record.exc_info:
            # No locals are captured. Escape line breaks to keep JSON/text logs one event per line.
            details = "".join(traceback.format_exception(*record.exc_info))
            event["exception"] = clean_text(details, 16384)
        if record.stack_info:
            event["stack"] = clean_text(record.stack_info, 16384)
        if self.output_format == "json":
            return json.dumps(event, ensure_ascii=True, allow_nan=False)
        prefix = "{timestamp} {level:<8} {logger} [{thread}] {event}".format(**event)
        fields = ("%s=%s" % (key, json.dumps(value, ensure_ascii=True))
                  for key, value in event.items()
                  if key not in {"timestamp", "level", "logger", "thread", "event"})
        suffix = " ".join(fields)
        return prefix + (" " + suffix if suffix else "")


def configure_logging(level="INFO", output_format="text", log_file=None,
                      max_bytes=5 * 1024 * 1024, backup_count=3):
    """Configure the application's stderr and optional rotating UTF-8 file handlers.

    Call from an entry point only: this deliberately replaces the root handlers.
    Library users can instead supply their own standard logging configuration.
    """
    if max_bytes <= 0 or backup_count <= 0:
        raise ValueError("log file size and backup count must be positive")
    formatter = DiagnosticFormatter(output_format)
    handlers = [logging.StreamHandler(sys.stderr)]
    try:
        if log_file:
            path = Path(log_file).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            handlers.append(RotatingFileHandler(
                path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8",
            ))
        for handler in handlers:
            handler.setFormatter(formatter)
        logging.basicConfig(level=level, handlers=handlers, force=True)
    except Exception:
        for handler in handlers:
            handler.close()
        raise
