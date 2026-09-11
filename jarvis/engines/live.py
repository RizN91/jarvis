"""GPT-Live-1 sessions over the documented primary WebSocket.

VERIFIED CONTRACT (docs/VERIFIED_API.md, fetched 2026-09-11)
------------------------------------------------------------
  URL          wss://api.openai.com/v1/live/sessions   (NO query parameters)
  Auth         Authorization: Bearer <project API key>  (key stays on this machine)
  Startup      send `session.start` first, then wait for `session.started`
  Audio in     `session.input_audio.append`, base64 raw bytes in `audio`
               -> PCM must be complete 16-bit samples: byte length must be EVEN
  Audio out    `session.output_audio.delta`, base64 in `delta`
  Transcripts  `session.input_transcript.delta` / `session.output_transcript.delta`
               -> each has `delta` plus `start_ms`/`end_ms` on the SESSION
                  TIMELINE. Doc quote: "Transcript deltas have no item ID or
                  authoritative turn-completed event."
  Usage        `session.usage.updated` -> {usage:{seconds}} CUMULATIVE SNAPSHOT
  Close        `session.close` -> `session.closed` carrying final usage + reason
  Updates      `session.instructions.append`, `session.input_audio.mute`/`unmute`,
               `session.update` -> `session.updated`
  Errors       `error` events, with `error.client_event_id` when relevant
  Billing      $0.05 / minute of session duration, billed per second

CONSEQUENCES IMPLEMENTED HERE (the parts that are easy to get wrong)
--------------------------------------------------------------------
1. THERE IS NO "USER TURN COMPLETE" EVENT. Release of the push-to-talk button is
   an APP-SIDE boundary, not server confirmation. So we never treat a quiet
   period as proof the utterance ended.
2. THE FINAL WORD CAN BE CUT OFF. We track how much audio we actually sent
   (audio_ms_sent) and compare it with the furthest `end_ms` any transcript
   fragment has covered. While the transcript lags the audio we keep draining,
   within a hard bound. If it never catches up we report incomplete capture
   instead of silently inserting truncated text.
3. USAGE IS CUMULATIVE, NOT INCREMENTAL. We keep the maximum, never a sum.
4. A MISSING `session.closed` MEANS FINAL USAGE IS UNCONFIRMED. We surface that
   rather than inventing a number.

The installed SDK (openai 2.24.0) has no `client.live`, so this module speaks the
documented wire protocol directly.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .. import secrets
from ..logsetup import get as _log, redact
from . import errors

log = _log("live")

LIVE_WS_URL = "wss://api.openai.com/v1/live/sessions"
MODEL = "gpt-live-1"
DEFAULT_VOICE = "marin"
DEFAULT_RATE = 24000

# The documented voice roster, transcribed from the "Voice options" table in
# developers.openai.com/api/docs/guides/live-conversations and re-verified
# against the live page on 2026-09-11. `marin` is the documented default and is
# not in that table, so it is listed first and separately.
#
# Set through `audio.output.voice` at session start; the docs are explicit that
# it CANNOT be changed on a running session, which is why switching voices in
# Settings only takes effect on the next session.
#
# "Regional influence describes a voice's speaking style, not a guarantee of
# accent fidelity" - do not present these as accents in the UI.
#
# Do NOT add names that are not on that page. An unknown voice is rejected by
# the API at session start, which fails the connection rather than degrading.
VOICES: tuple[dict, ...] = (
    {"id": "marin",    "label": "Marin",    "language": "English",
     "region": "Default",        "presentation": "",          "source": ""},
    {"id": "quartz",   "label": "Quartz",   "language": "English",
     "region": "Australian",     "presentation": "Feminine",  "source": "Generated"},
    {"id": "ripple",   "label": "Ripple",   "language": "English",
     "region": "Australian",     "presentation": "Masculine", "source": "Natural"},
    {"id": "vesper",   "label": "Vesper",   "language": "English",
     "region": "British",        "presentation": "Masculine", "source": "Natural"},
    {"id": "willow",   "label": "Willow",   "language": "English",
     "region": "Irish",          "presentation": "Feminine",  "source": "Natural"},
    {"id": "stone",    "label": "Stone",    "language": "English",
     "region": "Irish",          "presentation": "Masculine", "source": "Natural"},
    {"id": "gleam",    "label": "Gleam",    "language": "English",
     "region": "North American", "presentation": "Feminine",  "source": "Natural"},
    {"id": "meridian", "label": "Meridian", "language": "English",
     "region": "North American", "presentation": "Masculine", "source": "Natural"},
    {"id": "bossa",    "label": "Bossa",    "language": "Portuguese",
     "region": "Brazilian",      "presentation": "Feminine",  "source": "Natural"},
    {"id": "tempo",    "label": "Tempo",    "language": "Portuguese",
     "region": "Brazilian",      "presentation": "Masculine", "source": "Natural"},
    {"id": "beacon",   "label": "Beacon",   "language": "English",
     "region": "Filipino",       "presentation": "Masculine", "source": "Generated"},
    {"id": "delta",    "label": "Delta",    "language": "English",
     "region": "Southern U.S.",  "presentation": "Feminine",  "source": "Generated"},
    {"id": "cinder",   "label": "Cinder",   "language": "English",
     "region": "Southern U.S.",  "presentation": "Masculine", "source": "Generated"},
)

VOICE_IDS = tuple(v["id"] for v in VOICES)

# The two Australian voices are offered first in the picker (this build targets
# an Australian English user). This is presentation only - it changes no
# behaviour.
SUGGESTED_VOICES = ("quartz", "ripple", "marin")


def is_known_voice(name: str) -> bool:
    """True for a documented voice id.

    A custom voice approved on the user's own account is also valid but cannot be
    enumerated from here, so the UI accepts an unknown name and warns rather
    than blocking it.
    """
    return str(name or "").strip().lower() in VOICE_IDS

# Millisecond tolerance when deciding whether transcripts have caught up with
# the audio we sent. End-of-speech handling needs a little slack.
SETTLE_TOLERANCE_MS = 450.0
DRAIN_POLL_S = 0.05

DICTATION_INSTRUCTIONS = (
    "You are in DICTATION mode. The user is dictating text that will be typed "
    "into another application. Never reply, never speak, never ask questions, "
    "and never call tools. Only listen."
)


@dataclass
class TranscriptAssembler:
    """Collect transcript fragments exactly as received, with their intervals.

    Doc: "Append fragments in order for each speaker, retaining start_ms and
    end_ms." Fragments can arrive out of order over the network, so we sort by
    start_ms for display while preserving every fragment verbatim.
    """

    fragments: list[tuple[float, float, str]] = field(default_factory=list)
    received_at: list[float] = field(default_factory=list)

    def add(self, delta: str, start_ms: float, end_ms: float) -> None:
        if not delta:
            return
        self.fragments.append((float(start_ms or 0.0), float(end_ms or 0.0), delta))
        self.received_at.append(time.monotonic())

    def text(self) -> str:
        if not self.fragments:
            return ""
        ordered = sorted(self.fragments, key=lambda f: f[0])
        return "".join(f[2] for f in ordered)

    @property
    def covered_ms(self) -> float:
        """Furthest point on the session timeline any fragment has covered."""
        return max((f[1] for f in self.fragments), default=0.0)

    @property
    def last_arrival(self) -> float:
        return self.received_at[-1] if self.received_at else 0.0

    def __len__(self) -> int:
        return len(self.fragments)


@dataclass
class CloseReport:
    usage_seconds: float = 0.0
    reason: str = ""
    session_id: str = ""
    complete: bool = False          # did we receive session.closed in time?
    detail: str = ""

    def as_dict(self) -> dict:
        return {"usage_seconds": round(self.usage_seconds, 2), "reason": self.reason,
                "session_id": self.session_id, "finalization_complete": self.complete,
                "detail": self.detail}


@dataclass
class LiveConfig:
    model: str = MODEL
    instructions: str = ""
    voice: str = DEFAULT_VOICE
    rate: int = DEFAULT_RATE
    delegation: Optional[dict] = None
    dictation: bool = False
    # audio format type from the documented set
    format_type: str = "audio/pcm"


class LiveSession:
    """One GPT-Live voice session."""

    def __init__(self, config: LiveConfig, api_key: Optional[str] = None,
                 on_event: Optional[Callable[[str, dict], None]] = None,
                 on_audio: Optional[Callable[[bytes], None]] = None):
        self.config = config
        self._api_key = api_key
        self.on_event = on_event
        self.on_audio = on_audio

        self.session_id: str = ""
        self.started = False
        self.closed = False
        self.input_transcript = TranscriptAssembler()
        self.output_transcript = TranscriptAssembler()
        self.usage_seconds: float = 0.0
        self.usage_final: bool = False
        self.last_error: Optional[dict] = None
        self.backend_usage: list[dict] = []

        self._ws: Any = None
        self._reader: Optional[asyncio.Task] = None
        self._pending_byte = b""            # carries an odd trailing byte
        self._bytes_sent = 0
        self._closed_event = asyncio.Event()
        self._started_event = asyncio.Event()
        self._dead = False
        self._closing = False

    # ------------------------------------------------------------- lifecycle
    def _key(self) -> str:
        key = self._api_key or secrets.get_api_key()
        if not key:
            raise errors.EngineError(
                "no OpenAI API key is stored yet. Enter it on the setup screen.",
                kind="auth", status=401)
        return key

    async def connect(self, open_timeout: float = 20.0) -> None:
        try:
            from websockets.asyncio.client import connect
        except ImportError as exc:  # pragma: no cover
            raise errors.EngineError(
                "the 'websockets' package is required for Live voice. "
                "Install it with: pip install websockets",
                kind="bad_request", detail=str(exc)) from exc

        headers = {"Authorization": f"Bearer {self._key()}"}
        try:
            self._ws = await connect(
                LIVE_WS_URL,
                additional_headers=headers,
                open_timeout=open_timeout,
                ping_interval=20,
                ping_timeout=20,
                max_size=16 * 1024 * 1024,
                max_queue=256,
            )
        except Exception as exc:
            raise errors.from_exception(exc, self.config.model) from exc

        self._reader = asyncio.create_task(self._read_loop())
        await self._send({"type": "session.start", "event_id": "event_start",
                          "session": self._session_payload()})

        try:
            await asyncio.wait_for(self._started_event.wait(), timeout=open_timeout)
        except asyncio.TimeoutError:
            await self.close(graceful=False)
            raise errors.EngineError(
                "the Live session never reported session.started within "
                f"{open_timeout:.0f}s. Nothing was left running.",
                kind="server", status=None, retryable=True)
        if not self.started:
            await self.close(graceful=False)
            raise errors.EngineError(
                f"the Live session was rejected: "
                f"{(self.last_error or {}).get('message', 'connection closed before startup')}",
                kind="bad_request")

    def _session_payload(self) -> dict:
        session: dict = {
            "model": self.config.model,
            "instructions": self.config.instructions or DICTATION_INSTRUCTIONS,
            "audio": {
                "format": {"type": self.config.format_type, "rate": self.config.rate},
                "output": {"voice": self.config.voice},
            },
        }
        if self.config.delegation:
            session["delegation"] = self.config.delegation
        return session

    async def _send(self, payload: dict) -> None:
        if not self._ws or self._dead:
            return
        try:
            await self._ws.send(json.dumps(payload))
        except Exception as exc:
            self._dead = True
            log.warning("live send failed: %s", redact(str(exc)))

    # ----------------------------------------------------------------- audio
    async def append_audio(self, pcm: bytes) -> None:
        """Send raw PCM. Chunks must contain complete 16-bit samples."""
        if not pcm or not self.started or self._closing or self._dead:
            return
        data = self._pending_byte + pcm
        if self.config.format_type == "audio/pcm":
            usable = len(data) - (len(data) % 2)
            self._pending_byte = data[usable:]
            data = data[:usable]
        else:
            self._pending_byte = b""
        if not data:
            return
        self._bytes_sent += len(data)
        await self._send({
            "type": "session.input_audio.append",
            "audio": base64.b64encode(data).decode("ascii"),
        })

    @property
    def audio_ms_sent(self) -> float:
        """Duration of audio we have actually pushed to the model."""
        if self.config.format_type == "audio/pcm":
            samples = self._bytes_sent / 2
        else:  # G.711 is one byte per sample
            samples = self._bytes_sent
        return samples / float(self.config.rate) * 1000.0

    # --------------------------------------------------------------- controls
    async def mute_input(self, event_id: str = "mute_1") -> None:
        await self._send({"type": "session.input_audio.mute", "event_id": event_id})

    async def unmute_input(self, event_id: str = "unmute_1") -> None:
        await self._send({"type": "session.input_audio.unmute", "event_id": event_id})

    async def append_instructions(self, content: str, event_id: str = "ctx_1") -> None:
        await self._send({"type": "session.instructions.append",
                          "event_id": event_id, "delegation_id": None,
                          "content": content})

    async def update_session(self, patch: dict) -> None:
        await self._send({"type": "session.update", "session": patch})

    # ------------------------------------------------------------------ read
    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                try:
                    event = json.loads(raw)
                except Exception:
                    continue
                self._handle(event)
        except Exception as exc:
            if not self._closing:
                self._dead = True
                log.warning("live read loop ended: %s", redact(str(exc)))
        finally:
            if not self.started:
                self._started_event.set()
            if not self.usage_final:
                self._dead = True
            self._closed_event.set()

    def _handle(self, event: dict) -> None:
        etype = event.get("type", "")
        if etype == "session.started":
            self.started = True
            sess = event.get("session") or {}
            self.session_id = sess.get("id") or self.session_id
            self._started_event.set()
        elif etype == "session.input_transcript.delta":
            self.input_transcript.add(event.get("delta", ""),
                                      event.get("start_ms", 0.0),
                                      event.get("end_ms", 0.0))
        elif etype == "session.output_transcript.delta":
            self.output_transcript.add(event.get("delta", ""),
                                       event.get("start_ms", 0.0),
                                       event.get("end_ms", 0.0))
        elif etype == "session.output_audio.delta":
            if self.on_audio and not self.config.dictation:
                try:
                    self.on_audio(base64.b64decode(event.get("delta", "")))
                except Exception as exc:
                    log.warning("audio callback failed: %s", exc)
        elif etype == "session.usage.updated":
            # CUMULATIVE SNAPSHOT - keep the max, never sum.
            secs = float(((event.get("usage") or {}).get("seconds")) or 0.0)
            self.usage_seconds = max(self.usage_seconds, secs)
        elif etype == "session.closed":
            usage = event.get("usage") or {}
            secs = float(usage.get("seconds") or 0.0)
            self.usage_seconds = max(self.usage_seconds, secs)
            self.usage_final = True
            self.closed = True
            self.closed_reason = str(usage.get("reason") or event.get("reason") or "")
            self._closed_event.set()
        elif etype == "error":
            err = event.get("error") or {}
            self.last_error = {"message": err.get("message", ""),
                               "code": err.get("code") or err.get("type", ""),
                               "client_event_id": err.get("client_event_id")}
            if not self.started:
                self._started_event.set()
            log.warning("live error event: %s",
                        redact(str(self.last_error.get("message"))))
        elif etype == "response.event":
            nested = event.get("event") or {}
            if nested.get("type"):
                self.backend_usage.append(nested)

        if self.on_event:
            try:
                self.on_event(etype, event)
            except Exception as exc:
                log.error("live event callback failed: %s", exc)

    # ----------------------------------------------------------- finalisation
    async def drain_until_settled(self, drain_ms: float = 1600.0) -> bool:
        """Wait, bounded, for transcripts to catch up with the audio we sent.

        This is the mitigation for "the final word is not cut off". There is no
        server-side turn-completed event to wait for, so we compare the furthest
        transcript interval against our own audio clock and keep draining while
        the transcript is still behind.

        Returns True when the transcript has caught up (or we deliberately
        stopped because nothing had been transcribed at all).
        """
        if not self.started:
            return False
        deadline = time.monotonic() + max(0.0, drain_ms) / 1000.0
        hard_deadline = time.monotonic() + max(drain_ms, 300.0) / 1000.0 * 2.0
        target = self.audio_ms_sent - SETTLE_TOLERANCE_MS
        while True:
            covered = self.input_transcript.covered_ms
            has_fragments = len(self.input_transcript) > 0
            if has_fragments and covered >= target:
                return True
            now = time.monotonic()
            if now >= deadline:
                # If fragments were still arriving right at the deadline, allow
                # one bounded extension - the model may just be behind.
                if has_fragments and (now - self.input_transcript.last_arrival) < 0.25 \
                        and now < hard_deadline:
                    await asyncio.sleep(DRAIN_POLL_S)
                    continue
                return not has_fragments or covered >= target
            await asyncio.sleep(DRAIN_POLL_S)

    async def close(self, graceful: bool = True, timeout: float = 15.0) -> CloseReport:
        """Follow the documented graceful-close lifecycle."""
        self._closing = True
        report = CloseReport(session_id=self.session_id)
        if not self._ws:
            report.detail = "no connection was open"
            return report

        if graceful and self.started and not self.closed:
            await self._send({"type": "session.close"})
            try:
                await asyncio.wait_for(self._closed_event.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                report.detail = (f"incomplete finalization: session.closed was not "
                                 f"received within {timeout:.0f}s, so the final "
                                 f"usage is unconfirmed")
                log.warning("%s", report.detail)
        else:
            report.detail = "closed without a graceful handshake"

        try:
            await self._ws.close()
        except Exception:
            pass
        if self._reader:
            try:
                await asyncio.wait_for(self._reader, timeout=2.0)
            except Exception:
                self._reader.cancel()

        report.usage_seconds = self.usage_seconds
        report.reason = getattr(self, "closed_reason", "")
        report.complete = bool(self.usage_final)
        self.closed = True
        self._ws = None
        return report

    # ------------------------------------------------------------ dictation
    async def run_dictation(self, audio_source, *, drain_ms: float = 1600.0,
                            close_timeout: float = 15.0,
                            max_seconds: float = 180.0,
                            speech_detected: Optional[bool] = None) -> dict:
        """Dictate one utterance.

        `audio_source` is an async iterator of PCM bytes that finishes when the
        user releases the button. Returns a structured result including explicit
        `incomplete_capture` and `likely_fabricated` flags - we never silently
        insert a transcript we know may be missing its tail or invented from
        silence.

        `speech_detected` comes from the local VAD on the capture buffer. When it
        is False but the model returned text, this is exactly the
        silent-audio-fabrication case, so the transcript is flagged and must be
        confirmed by the user rather than auto-inserted.
        """
        from ..core import pricing
        started = time.monotonic()
        try:
            await self.connect()
            async for chunk in audio_source:
                if time.monotonic() - started > max_seconds:
                    log.warning("dictation stopped at the %ss cap", max_seconds)
                    break
                await self.append_audio(chunk)
            # The button release is an APP-SIDE boundary, not server confirmation.
            settled = await self.drain_until_settled(drain_ms)
        finally:
            report = await self.close(graceful=True, timeout=close_timeout)

        text = self.input_transcript.text()
        covered = self.input_transcript.covered_ms
        sent = self.audio_ms_sent
        incomplete = False
        likely_fabricated = False
        notes: list[str] = []
        if not report.complete:
            notes.append("final usage unconfirmed")
        if sent > 400 and not text:
            incomplete = True
            notes.append("audio was captured but no transcript arrived")
        elif sent > 1500 and covered < sent - SETTLE_TOLERANCE_MS * 2:
            incomplete = True
            notes.append(f"transcript covers {covered:.0f}ms of {sent:.0f}ms of audio")
        elif not settled and text:
            incomplete = True
            notes.append("transcript may be missing the last word")

        # Silent-audio fabrication guard: the local VAD says no speech, yet the
        # model produced words. Observed for real during loopback testing, where
        # near-silence plus keyword hints produced "the user.".
        if speech_detected is False and text.strip():
            likely_fabricated = True
            notes.append("no speech energy was detected locally, so this text is "
                         "probably invented - confirm before using it")

        usd = pricing.live_seconds_to_usd(report.usage_seconds)
        result = {
            "ok": True,
            "text": text,
            "session_id": self.session_id,
            "usage_seconds": round(report.usage_seconds, 2),
            # NOT rounded: rounding is a display concern, and rounding here broke
            # an exact-cost assertion. The UI formats it.
            "usd": usd,
            "audio_ms_sent": round(sent, 1),
            "transcript_covered_ms": round(covered, 1),
            "fragments": len(self.input_transcript),
            "finalization_complete": report.complete,
            "incomplete_capture": incomplete,
            "likely_fabricated": likely_fabricated,
            "requires_confirmation": bool(incomplete or likely_fabricated),
            "notes": notes,
            "detail": "; ".join(notes),
        }
        log.info("live dictation: %d chars, %.1fs session, $%.5f, %d fragments, "
                 "incomplete=%s", len(text), report.usage_seconds, usd,
                 len(self.input_transcript), incomplete)
        return result


async def dictation_once(audio_source, *, instructions: str = DICTATION_INSTRUCTIONS,
                         drain_ms: float = 1600.0, api_key: Optional[str] = None,
                         config: Optional[LiveConfig] = None,
                         on_event=None) -> dict:
    """Convenience: one standalone dictation session."""
    cfg = config or LiveConfig(instructions=instructions, dictation=True)
    cfg.dictation = True
    session = LiveSession(cfg, api_key=api_key, on_event=on_event)
    try:
        return await session.run_dictation(audio_source, drain_ms=drain_ms)
    except errors.EngineError as exc:
        return exc.as_dict()
    except Exception as exc:
        return errors.from_exception(exc, MODEL).as_dict()
