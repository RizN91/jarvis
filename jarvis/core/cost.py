"""Application-side budget ceilings and a live spend meter.

Honesty requirements from the build spec (section 11):
  * The OpenAI *account/project* budget is not something this app can read or
    enforce. Our numbers are local estimates from observed usage. Never
    describe them as a universal, account-wide hard cap.
  * Provider-reported usage lags. In-flight work may not yet be counted.
  * Never represent plan usage as unlimited.

What this class actually does: refuses to AUTHORIZE new billable work once a
local ceiling is reached, and asks active sessions to close conservatively.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Optional

from .. import config
from ..db import db
from ..logsetup import get as _log
from . import pricing

log = _log("cost")


class BudgetExceeded(RuntimeError):
    """Raised instead of starting work that would exceed a local ceiling."""


@dataclass
class SpendSnapshot:
    day: str
    today_usd: float
    month_usd: float
    live_seconds_today: float
    transcribe_seconds_today: float
    backend_usd_today: float
    tools_usd_today: float
    daily_cap: float
    monthly_cap: float
    warn_ratio: float
    day_of_month: int = field(default_factory=lambda: date.today().day)

    @property
    def daily_ratio(self) -> float:
        return (self.today_usd / self.daily_cap) if self.daily_cap > 0 else 0.0

    @property
    def monthly_ratio(self) -> float:
        return (self.month_usd / self.monthly_cap) if self.monthly_cap > 0 else 0.0

    @property
    def projection_usd(self) -> float:
        return pricing.monthly_projection(self.month_usd, self.day_of_month)

    def as_dict(self) -> dict[str, Any]:
        return {
            "today": {
                "live_seconds": round(self.live_seconds_today, 1),
                "transcribe_seconds": round(self.transcribe_seconds_today, 1),
                "backend_usd": round(self.backend_usd_today, 6),
                "tools_usd": round(self.tools_usd_today, 6),
                "total_usd": round(self.today_usd, 6),
                "cap_usd": self.daily_cap,
                "ratio": round(self.daily_ratio, 4),
            },
            "month": {
                "total_usd": round(self.month_usd, 6),
                "cap_usd": self.monthly_cap,
                "ratio": round(self.monthly_ratio, 4),
                "projected_usd": round(self.projection_usd, 4),
            },
            "estimate_notice": (
                "Estimates from locally observed usage. OpenAI account-level "
                "project budgets are separate and are not enforced by this app."
            ),
        }


class BudgetManager:
    """Tracks spend, enforces local ceilings, and emits warnings once each."""

    def __init__(self, on_warning: Optional[Callable[[str, str], None]] = None):
        self._lock = threading.RLock()
        self._session_cost: dict[str, float] = {}
        self._warned: set[str] = set()
        self.on_warning = on_warning
        self._reset_if_new_day()

    # ------------------------------------------------------------ internals
    _last_day = date.today()

    def _reset_if_new_day(self) -> None:
        today = date.today()
        if today != BudgetManager._last_day:
            BudgetManager._last_day = today
            with self._lock:
                self._warned.clear()
                self._session_cost.clear()
            log.info("new day: budget warning state reset")

    def _snapshot(self) -> SpendSnapshot:
        t = db().usage_today()
        m = db().usage_month()
        return SpendSnapshot(
            day=date.today().isoformat(),
            today_usd=float(t.get("total_usd") or 0.0),
            month_usd=float(m.get("total_usd") or 0.0),
            live_seconds_today=float(t.get("live_seconds") or 0.0),
            transcribe_seconds_today=float(t.get("transcribe_seconds") or 0.0),
            backend_usd_today=float(t.get("backend_usd") or 0.0),
            tools_usd_today=float(t.get("tools_usd") or 0.0),
            daily_cap=float(config.get("daily_budget_usd", 3.0)),
            monthly_cap=float(config.get("monthly_budget_usd", 30.0)),
            warn_ratio=float(config.get("warn_at_ratio", 0.8)),
        )

    def _warn_once(self, key: str, level: str, message: str) -> None:
        if key in self._warned:
            return
        self._warned.add(key)
        log.warning("budget: %s", message)
        if self.on_warning:
            try:
                self.on_warning(level, message)
            except Exception:
                pass

    # -------------------------------------------------------------- checks
    def check_can_start(self, estimated_usd: float = 0.0,
                        category: str = "live_voice") -> SpendSnapshot:
        """Raise BudgetExceeded when a new billable action must not start."""
        self._reset_if_new_day()
        snap = self._snapshot()
        if snap.daily_cap > 0 and snap.today_usd >= snap.daily_cap:
            raise BudgetExceeded(
                f"Daily local estimate ceiling reached "
                f"(${snap.today_usd:.4f} of ${snap.daily_cap:.2f}). "
                f"New billable work is blocked until tomorrow or until you "
                f"raise the ceiling in Settings."
            )
        if snap.monthly_cap > 0 and snap.month_usd >= snap.monthly_cap:
            raise BudgetExceeded(
                f"Monthly local estimate ceiling reached "
                f"(${snap.month_usd:.4f} of ${snap.monthly_cap:.2f}). "
                f"New billable work is blocked until next month or until you "
                f"raise the ceiling in Settings."
            )
        if estimated_usd and snap.daily_cap > 0:
            if snap.today_usd + estimated_usd > snap.daily_cap:
                raise BudgetExceeded(
                    "That request would exceed today's local estimate ceiling "
                    f"(needs about ${estimated_usd:.4f} more)."
                )
        if estimated_usd > 0 and snap.monthly_cap > 0:
            if snap.month_usd + estimated_usd > snap.monthly_cap:
                raise BudgetExceeded("That request would exceed this month's local estimate ceiling.")
        return snap

    def would_exceed(self, additional_usd: float) -> bool:
        try:
            self.check_can_start(additional_usd)
            return False
        except BudgetExceeded:
            return True

    def warn_thresholds(self) -> Optional[SpendSnapshot]:
        """Fire the 80% (and 100%) warnings at most once per day each."""
        snap = self._snapshot()
        if snap.daily_cap > 0:
            if snap.daily_ratio >= 1.0:
                self._warn_once(
                    "day_100", "error",
                    f"Today's estimated spend ${snap.today_usd:.4f} has reached "
                    f"the ${snap.daily_cap:.2f} local ceiling. New work is blocked.",
                )
            elif snap.daily_ratio >= snap.warn_ratio:
                self._warn_once(
                    "day_warn", "warn",
                    f"Today's estimated spend is ${snap.today_usd:.4f} of "
                    f"${snap.daily_cap:.2f} ({snap.daily_ratio*100:.0f}%).",
                )
        if snap.monthly_cap > 0 and snap.monthly_ratio >= 1.0:
            self._warn_once("month_100", "error", "Monthly local spending ceiling reached. New work is blocked.")
        elif snap.monthly_cap > 0 and snap.monthly_ratio >= snap.warn_ratio:
            self._warn_once(
                "month_warn", "warn",
                f"This month's estimate is ${snap.month_usd:.4f} of "
                f"${snap.monthly_cap:.2f}. Projected ${snap.projection_usd:.2f}.",
            )
        return snap

    # ------------------------------------------------------------ recording
    def record_live_snapshot(self, session_id: str, seconds: float,
                             finalized: bool = True) -> float:
        """Record a CUMULATIVE Live-duration snapshot for one session.

        `session.usage.updated` reports cumulative voice duration in seconds,
        and `session.closed` carries the final value. We keep the maximum, so
        replaying or out-of-order snapshots can never inflate the meter.
        """
        usd = pricing.live_seconds_to_usd(seconds)
        db().record_usage(
            "live_voice", usd, seconds=seconds, session_id=session_id,
            meta={"rate_per_min": pricing.USD_PER_MIN_LIVE, "source": "session.usage"},
            finalized=finalized,
        )
        self.warn_thresholds()
        return usd

    def record_transcribe(self, audio_seconds: float, model: str = "gpt-transcribe",
                          request_id: Optional[str] = None) -> float:
        """A transcription call is a genuine increment - one row per call."""
        usd = pricing.transcribe_seconds_to_usd(audio_seconds)
        db().record_usage(
            "transcribe", usd, seconds=audio_seconds, session_id=None,
            meta={"model": model, "rate_per_min": pricing.USD_PER_MIN_TRANSCRIBE,
                  "request_id": request_id},
        )
        self.warn_thresholds()
        return usd

    def record_backend(self, model: str, input_tokens: int, output_tokens: int,
                       cached_tokens: int = 0, session_id: Optional[str] = None,
                       note: str = "") -> float:
        usd = pricing.tokens_to_usd(model, input_tokens, output_tokens, cached_tokens)
        db().record_usage(
            "backend", usd, session_id=session_id,
            meta={"model": model, "input_tokens": input_tokens,
                  "output_tokens": output_tokens, "cached_tokens": cached_tokens,
                  "note": note},
        )
        if session_id:
            self._session_cost[session_id] = self._session_cost.get(session_id, 0.0) + usd
        self.warn_thresholds()
        return usd

    def record_tool(self, name: str, usd: float, detail: Optional[dict] = None) -> float:
        usd = float(usd or 0.0)
        if usd:
            db().record_usage("tool", usd, session_id=None,
                              meta={"name": name, **(detail or {})})
        self.warn_thresholds()
        return usd

    def mark_incomplete_finalization(self, session_id: str) -> None:
        """No session.closed event arrived: the last charge is unconfirmed."""
        db().mark_unfinalized(session_id, "live_voice")
        log.warning("session %s ended without a final usage event", session_id)

    # ------------------------------------------------------------- per-task
    def start_task_timer(self, task_id: str) -> float:
        self._session_cost.setdefault(task_id, 0.0)
        return time.monotonic()

    def task_duration_ok(self, started: float) -> bool:
        limit = float(config.get("max_task_seconds", 600))
        return (time.monotonic() - started) <= limit

    def snapshot(self) -> SpendSnapshot:
        return self._snapshot()
