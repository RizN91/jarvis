"""Shared error handling for the cloud engines.

Requirements from the build spec:
  * "API failures, unavailable models, rate limits and budget limits show useful
    messages without exposing keys, duplicating actions or silently switching
    providers."
  * Never silently substitute a different model.

So: every failure becomes an EngineError with a friendly, actionable message,
a machine-readable `kind`, and a `retryable` flag. Messages are passed through
logsetup.redact() so a leaked key can never reach the UI or the log.
"""

from __future__ import annotations

from typing import Optional

from ..logsetup import redact


class EngineError(RuntimeError):
    """A cloud call failed in a way we can explain to the user."""

    def __init__(self, message: str, *, kind: str = "unknown",
                 status: Optional[int] = None, retryable: bool = False,
                 detail: str = ""):
        super().__init__(redact(message))
        self.kind = kind
        self.status = status
        self.retryable = retryable
        self.detail = redact(detail or "")

    def as_dict(self) -> dict:
        return {"ok": False, "kind": self.kind, "status": self.status,
                "error": str(self), "retryable": self.retryable}


# OpenAI error `type` values worth distinguishing.
_TYPE_KINDS = {
    "invalid_request_error": "bad_request",
    "authentication_error": "auth",
    "invalid_api_key": "auth",
    "permission_error": "permission",
    "not_found_error": "not_found",
    "rate_limit_error": "rate_limit",
    "insufficient_quota": "quota",
    "server_error": "server",
    "overloaded_error": "overloaded",
    "model_not_found": "model_unavailable",
    "billing_hard_limit_reached": "quota",
    "insufficient_quota_error": "quota",
}


def classify(status: Optional[int], code: str = "", message: str = "") -> tuple[str, bool]:
    """Return (kind, retryable)."""
    low = f"{code} {message}".lower()
    if code in _TYPE_KINDS:
        kind = _TYPE_KINDS[code]
    elif status == 401:
        kind = "auth"
    elif status == 403:
        kind = "permission"
    elif status == 404:
        kind = "not_found"
    elif status == 429:
        kind = "rate_limit"
    elif status == 400:
        kind = "bad_request"
    elif status and 500 <= status < 600:
        kind = "server"
    else:
        kind = "unknown"

    if "model" in low and ("not found" in low or "does not exist" in low
                           or "unsupported" in low or "no access" in low):
        kind = "model_unavailable"
    if "quota" in low or "billing" in low or "credit" in low:
        kind = "quota"
    if "context length" in low or "too long" in low:
        kind = "too_large"

    retryable = kind in ("server", "overloaded", "rate_limit", "unknown")
    return kind, retryable


def friendly(kind: str, status: Optional[int] = None,
             model: str = "", extra: str = "") -> str:
    prefix = f"{model}: " if model else ""
    if kind == "auth":
        return (f"{prefix}the API key was rejected ({status or 401}). "
                f"Open Settings and re-enter your key in the setup screen.")
    if kind == "permission":
        return (f"{prefix}this API key does not have permission for that model or "
                f"endpoint ({status or 403}). Check the key's project access.")
    if kind == "model_unavailable":
        return (f"{prefix}this model is not available to your account. "
                f"The app will not silently switch to a different model — "
                f"choose another in Settings.")
    if kind == "rate_limit":
        return (f"{prefix}rate limit reached (429). Nothing was charged twice; "
                f"wait a moment and try again.")
    if kind == "quota":
        return (f"{prefix}the account has no remaining quota or credits for this "
                f"model. Add credits or raise the project budget in the OpenAI "
                f"dashboard. This app's own budget ceilings are separate.")
    if kind == "bad_request":
        return f"{prefix}the request was rejected as invalid ({status or 400}). {extra}".strip()
    if kind == "too_large":
        return (f"{prefix}the audio or text was too long for a single request. "
                f"Try a shorter utterance.")
    if kind == "server" or kind == "overloaded":
        return f"{prefix}OpenAI reported a temporary problem ({status or 500}). Safe to retry."
    if kind == "network":
        return f"{prefix}network problem reaching OpenAI: {extra or 'check your connection'}."
    if kind == "budget":
        return f"{prefix}blocked by the app's own spending ceiling. {extra}".strip()
    return f"{prefix}unexpected failure ({status or 'no status'}). {extra}".strip()


def from_http(status: int, body: str = "", model: str = "") -> EngineError:
    """Build an EngineError from an HTTP status and raw body."""
    import json
    code = ""
    message = ""
    try:
        parsed = json.loads(body) if body else {}
        err = parsed.get("error") or {}
        code = str(err.get("code") or err.get("type") or "")
        message = str(err.get("message") or "")
    except Exception:
        message = (body or "")[:400]
    kind, retryable = classify(status, code, message)
    return EngineError(friendly(kind, status, model, message), kind=kind,
                       status=status, retryable=retryable, detail=message)


def from_exception(exc: BaseException, model: str = "") -> EngineError:
    """Build an EngineError from an SDK/network exception."""
    if isinstance(exc, EngineError):
        return exc
    status = getattr(exc, "status_code", None)
    body = ""
    try:
        resp = getattr(exc, "response", None)
        if resp is not None:
            status = status or getattr(resp, "status_code", None)
            body = getattr(resp, "text", "") or ""
    except Exception:
        pass
    if status:
        return from_http(int(status), body, model)
    name = type(exc).__name__
    text = str(exc)
    low = text.lower()
    if any(k in low for k in ("timed out", "timeout", "connection", "getaddrinfo",
                              "temporary failure", "ssl", "network")):
        return EngineError(friendly("network", model=model, extra=text[:200]),
                           kind="network", retryable=True, detail=text)
    kind, retryable = classify(None, name, text)
    return EngineError(friendly(kind, model=model, extra=text[:200]),
                       kind=kind, retryable=retryable, detail=text)
