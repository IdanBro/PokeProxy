"""Structured JSON logging to stdout.

The container contract is one JSON object per line on stdout. Nothing writes
log files, and nothing rotates them — that is the platform's job.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from typing import TextIO

# Attribute names the stdlib puts on every LogRecord. Anything a caller attaches
# via `extra=` is not in here, which is how we tell structured fields apart from
# stdlib bookkeeping without maintaining a hand-written list.
_STDLIB_FIELDS = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__
) | {
    "asctime",
    "message",
    "taskName",
    # Uvicorn attaches an ANSI-coloured duplicate of its own message; useful for
    # a terminal, pure noise in a JSON log line.
    "color_message",
}

# Uvicorn installs its own handlers and formatters. Left alone, its lines stay
# plaintext while ours are JSON, which defeats the point.
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


class JSONFormatter(logging.Formatter):
    """Render a LogRecord as a single line of JSON."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": dt.datetime.fromtimestamp(record.created, tz=dt.UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        for key, value in record.__dict__.items():
            if key not in _STDLIB_FIELDS:
                payload[key] = value

        if record.exc_info and record.exc_info[0] is not None:
            # Short, machine-readable summary stays inside the JSON object so it
            # can be grepped and alerted on.
            payload["error"] = f"{record.exc_info[0].__name__}: {record.exc_info[1]}"

        line = json.dumps(payload, default=str)

        if record.exc_info:
            # The full traceback follows the JSON object as ordinary text.
            # Escaping it into the object would technically be one line, but an
            # unreadable one — you cannot skim an escaped traceback at 3 AM.
            line += "\n" + self.formatException(record.exc_info)

        return line


def setup_logging(level: str = "INFO", stream: TextIO | None = None) -> None:
    """Route every logger through one JSON handler. Safe to call more than once.

    `stream` exists so tests can capture output; production leaves it as stderr,
    which is what container runtimes collect.
    """
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JSONFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    for name in _UVICORN_LOGGERS:
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True

    # We emit our own access line, with request_id, outcome and duration.
    logging.getLogger("uvicorn.access").disabled = True
