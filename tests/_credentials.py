"""Shared test helpers for the cloud engines.

The API key is read from the api.env file that the user placed in the project
folder and stored into Windows Credential Manager via jarvis.secrets.

This module NEVER prints, logs, or returns the key, and it asserts that the key
does not appear in captured output. Run-heavy tests should be treated as
billable: see docs/TEST_RESULTS.md for the measured cost of each run.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

API_ENV_CANDIDATES = [
    os.path.join(os.path.dirname(ROOT), "api.env"),
    os.path.join(ROOT, "api.env"),
    os.path.expanduser("~/Desktop/Jarvis/api.env"),
]

FIXTURES = os.path.join(ROOT, "tests", "fixtures")


def load_api_key(verbose: bool = True) -> bool:
    """Move the key from api.env into Credential Manager. Returns True on success."""
    from jarvis import secrets

    if secrets.get_api_key():
        if verbose:
            print(f"API key already stored (fingerprint {secrets.key_fingerprint()})")
        return True

    for path in API_ENV_CANDIDATES:
        if not os.path.exists(path):
            continue
        raw = open(path, encoding="utf-8").read().strip()
        if not raw:
            continue
        value = raw.split("=", 1)[1].strip() if "=" in raw else raw
        value = value.strip().strip('"').strip("'")
        if not value.startswith("sk-"):
            continue
        try:
            secrets.set_api_key(value)
        except ValueError as exc:
            print(f"  api.env rejected: {exc}")
            continue
        if verbose:
            print(f"API key loaded from {os.path.basename(path)} into Windows "
                  f"Credential Manager (fingerprint {secrets.key_fingerprint()})")
        return True

    print("No API key found in api.env, and none stored.")
    return False


def redaction_self_test() -> bool:
    """Prove that key-shaped strings cannot survive the log filter."""
    from jarvis.logsetup import redact
    fake = "sk-proj-" + "A" * 40
    out = redact(f"Authorization: Bearer {fake} and key={fake}")
    ok = fake not in out and "[REDACTED]" in out
    print(f"{'PASS' if ok else 'FAIL'}  log redaction removes key-shaped strings "
          f":: {out[:70]}")
    return ok


def words(text: str) -> list[str]:
    """Normalise for word-error comparison: lowercase alphanumeric tokens."""
    import re
    return re.findall(r"[a-z0-9']+", (text or "").lower())


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Levenshtein distance over words, divided by reference length.

    Computed locally from a known reference - never a claimed provider score.
    """
    ref, hyp = words(reference), words(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, rw in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, hw in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1,
                         prev[j - 1] + (0 if rw == hw else 1))
        prev = cur
    return prev[-1] / len(ref)
