"""Verified published rates and cost arithmetic.

Every number here is copied from the official pricing/model pages fetched on
2026-09-11 and recorded in docs/VERIFIED_API.md. Do not edit these values
without re-verifying the source page - they drive the spend meter and the
budget ceilings.

Sources:
  * gpt-live-1        https://developers.openai.com/api/docs/models/gpt-live-1
                      -> $0.05 / minute of Live voice-session duration, billed per second
  * gpt-transcribe    https://developers.openai.com/api/docs/models/gpt-transcribe
                      -> $0.0045 / minute of transcription audio
  * gpt-live-transcribe (streaming ASR, if ever used) -> $0.017 / minute
  * text models       https://developers.openai.com/api/docs/pricing
"""

from __future__ import annotations

from typing import Optional

# ---- per-minute voice/audio rates (USD) ----------------------------------
USD_PER_MIN_LIVE = 0.05            # gpt-live-1 session duration
USD_PER_MIN_TRANSCRIBE = 0.0045    # gpt-transcribe audio
USD_PER_MIN_LIVE_TRANSCRIBE = 0.017  # gpt-live-transcribe streaming ASR

# ---- text model token rates: USD per 1M tokens (input, cached, output) ---
# Verified on 2026-09-11 against the individual model pages, NOT the grouped
# pricing table. The first version of this table was WRONG for two models
# (luna output 1.25 vs the documented 1.2; terra 2.50/0.25/15.00 vs the
# documented 2/0.2/12). Corrected after docs/VERIFIED_API.md flagged the
# mismatch. If you change a number here, re-read the model page.
#
# Source pages: /api/docs/models/gpt-5.6-luna, /api/docs/models/gpt-5.6-terra,
# and /api/docs/pricing for the rest.
TEXT_MODELS: dict[str, tuple[float, float, float]] = {
    "gpt-5.6-luna":   (0.20, 0.02, 1.20),    # cheap backend / cleanup default
    "gpt-5.6-terra":  (2.00, 0.20, 12.00),   # escalation for hard tasks
    "gpt-5.4-nano":   (0.20, 0.02, 1.25),
    "gpt-5.4-mini":   (0.75, 0.075, 4.50),
    "gpt-5-nano":     (0.05, 0.005, 0.40),
    "gpt-5-mini":     (0.25, 0.025, 2.00),
    "gpt-4.1-mini":   (0.40, 0.10, 1.60),
}

# Documented on the model pages: "Prompts with >272K input tokens are priced at
# 2x input and 1.5x output for the full request." and "Cache writes are billed at
# 1.25x the uncached input token rate."
LONG_CONTEXT_THRESHOLD_TOKENS = 272_000
LONG_CONTEXT_INPUT_MULTIPLIER = 2.0
LONG_CONTEXT_OUTPUT_MULTIPLIER = 1.5
CACHE_WRITE_MULTIPLIER = 1.25


def live_seconds_to_usd(seconds: float) -> float:
    """Billed per second, not rounded up to the next minute."""
    return max(0.0, float(seconds)) / 60.0 * USD_PER_MIN_LIVE


def transcribe_seconds_to_usd(seconds: float) -> float:
    return max(0.0, float(seconds)) / 60.0 * USD_PER_MIN_TRANSCRIBE


def tokens_to_usd(model: str, input_tokens: int, output_tokens: int,
                  cached_input_tokens: int = 0,
                  cache_write_tokens: int = 0) -> float:
    """Estimate a Responses/chat call cost. Labelled an ESTIMATE in the UI.

    Applies the documented rules rather than a flat rate:
      * cached input is charged at the cached rate
      * cache WRITES are charged at 1.25x the uncached input rate
      * a request over 272K input tokens is priced at 2x input and 1.5x output
        for the FULL request
    """
    rates = TEXT_MODELS.get(model)
    if rates is None:
        # Unknown model: use the most expensive known rate so we under-promise
        # rather than silently under-report the spend meter.
        rates = max(TEXT_MODELS.values(), key=lambda r: r[2])
    in_rate, cached_rate, out_rate = rates

    total_input = max(0, int(input_tokens)) + max(0, int(cache_write_tokens))
    if total_input > LONG_CONTEXT_THRESHOLD_TOKENS:
        in_rate *= LONG_CONTEXT_INPUT_MULTIPLIER
        cached_rate *= LONG_CONTEXT_INPUT_MULTIPLIER
        out_rate *= LONG_CONTEXT_OUTPUT_MULTIPLIER

    fresh = max(0, int(input_tokens) - int(cached_input_tokens))
    return (
        fresh / 1_000_000.0 * in_rate
        + int(cached_input_tokens) / 1_000_000.0 * cached_rate
        + max(0, int(cache_write_tokens)) / 1_000_000.0
        * in_rate * CACHE_WRITE_MULTIPLIER
        + int(output_tokens) / 1_000_000.0 * out_rate
    )


def estimate_minutes_usd(minutes: float, category: str) -> float:
    if category == "live_voice":
        return live_seconds_to_usd(minutes * 60.0)
    if category == "transcribe":
        return transcribe_seconds_to_usd(minutes * 60.0)
    return 0.0


def cheapest_text_model() -> str:
    return min(TEXT_MODELS.items(), key=lambda kv: kv[1][2])[0]


def monthly_projection(usd_today: float, day_of_month: Optional[int] = None) -> float:
    """Straight-line projection from today's spend. Presented as an estimate."""
    from datetime import date
    d = day_of_month or date.today().day
    if d <= 0:
        return 0.0
    import calendar
    days = calendar.monthrange(date.today().year, date.today().month)[1]
    return usd_today / d * days
