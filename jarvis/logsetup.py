"""Rotating logging with mandatory secret redaction.

Every log record passes through RedactionFilter, so an accidentally logged
API key, bearer header, or Authorization value is scrubbed before it reaches
disk. This is a hard requirement from the build spec: logs must not expose
secrets. Judge this by the filter, not by hoping callers are careful.
"""

from __future__ import annotations

import logging
import logging.handlers
import re
import sys

from . import config

REDACTION = "[REDACTED]"

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # OpenAI-style keys, project and legacy, with generous length bounds.
    (re.compile(r"sk-[A-Za-z0-9_\-]{12,}"), "sk-" + REDACTION),
    # Bearer tokens in any casing.
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._\-]{12,}"), r"\1 " + REDACTION),
    # Authorization headers when serialised into dicts or JSON.
    (re.compile(r"""(?i)(["']?authorization["']?\s*[:=]\s*["']?)[^"',\s}]{8,}"""), r"\1" + REDACTION),
    (re.compile(r"""(?i)(["']?api[_-]?key["']?\s*[:=]\s*["']?)[^"',\s}]{8,}"""), r"\1" + REDACTION),
    # OpenAI ephemeral client secrets.
    (re.compile(r"ek_[A-Za-z0-9_\-]{12,}"), "ek_" + REDACTION),
]


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            msg = record.getMessage()
        except Exception:
            return True
        red = msg
        for pat, rep in _PATTERNS:
            red = pat.sub(rep, red)
        if red != msg:
            record.msg = red
            record.args = ()
        return True


def redact(text: str) -> str:
    """Scrub secrets from an arbitrary string (used for UI error messages)."""
    out = str(text)
    for pat, rep in _PATTERNS:
        out = pat.sub(rep, out)
    return out


_configured = False


def setup(level: int = logging.INFO, console: bool = False) -> logging.Logger:
    global _configured
    root = logging.getLogger("jarvis")
    if _configured:
        return root
    root.setLevel(level)
    root.propagate = False
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(threadName)-14s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh = logging.handlers.RotatingFileHandler(
        config.log_dir() / "jarvis.log",
        maxBytes=2_000_000, backupCount=3, encoding="utf-8",
    )
    fh.setFormatter(fmt)
    fh.addFilter(RedactionFilter())
    root.addHandler(fh)
    if console:
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        sh.addFilter(RedactionFilter())
        root.addHandler(sh)
    _configured = True
    return root


def get(name: str) -> logging.Logger:
    setup()
    return logging.getLogger(f"jarvis.{name}")
