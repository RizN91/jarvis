"""Economy dictation: gpt-transcribe via the documented file/committed-audio path.

VERIFIED CONTRACT (docs/VERIFIED_API.md)
----------------------------------------
  * Endpoint   : POST https://api.openai.com/v1/audio/transcriptions
  * Model      : `gpt-transcribe`  ($0.0045 / minute of audio)
  * File limit : 25 MB; formats mp3, mp4, mpeg, mpga, m4a, wav, webm
  * Context    : `prompt`, `keywords`, `languages` improve domain terms.
                 The official Python example passes `keywords` and `languages`
                 through `extra_body`, because the SDK's typed signature does
                 not include them yet - so we do exactly that rather than
                 inventing field names.
  * Response   : `text` (and `usage`/`duration` with verbose formats)

WHY THE SDK HERE AND RAW WEBSOCKETS FOR LIVE
--------------------------------------------
The installed SDK (openai 2.24.0) exposes `audio.transcriptions` but has NO
`client.live` attribute - it does not implement the GPT-Live API yet. So the SDK
is used where it is authoritative (file transcription) and the documented wire
protocol is used for Live.

COST
----
We measure the recorded duration ourselves rather than trusting a returned
field, so the spend meter is based on real audio we captured. The published
rate is applied in core/pricing.py.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from . import errors
from ..core import pricing
from ..logsetup import get as _log

log = _log("transcribe")

MODEL = "gpt-transcribe"


@dataclass
class TranscribeResult:
    text: str
    model: str
    audio_seconds: float
    usd: float
    request_id: Optional[str] = None
    detected_languages: Optional[list[str]] = None
    detail: str = ""

    def as_dict(self) -> dict:
        return {"ok": True, "text": self.text, "model": self.model,
                "audio_seconds": round(self.audio_seconds, 2),
                "usd": round(self.usd, 6),
                "request_id": self.request_id,
                "languages": self.detected_languages or [],
                "detail": self.detail}


class TranscribeEngine:
    """One-shot transcription of a completed recording."""

    def __init__(self, api_key: Optional[str] = None, timeout: float = 180.0):
        self._api_key = api_key
        self.timeout = timeout
        self._client = None

    # ---------------------------------------------------------------- client
    def _key(self) -> str:
        key = self._api_key
        if not key:
            from .. import secrets
            key = secrets.get_api_key()
        if not key:
            raise errors.EngineError(
                friendly_missing_key(), kind="auth", status=401)
        return key

    def _sdk(self):
        if self._client is None:
            try:
                from openai import OpenAI
            except ImportError as exc:  # pragma: no cover
                raise errors.EngineError(
                    "the 'openai' package is required for Economy dictation. "
                    "Install it with: pip install openai",
                    kind="bad_request", detail=str(exc)) from exc
            self._client = OpenAI(api_key=self._key(), timeout=self.timeout,
                                 max_retries=1)
        return self._client

    def reset(self) -> None:
        """Forget the cached client so a rotated key takes effect."""
        self._client = None

    # ------------------------------------------------------------ transcribe
    def transcribe_file(
        self,
        path: str,
        keywords: Optional[Sequence[str]] = None,
        prompt: Optional[str] = None,
        languages: Optional[Sequence[str]] = ("en",),
        audio_seconds: Optional[float] = None,
        model: str = MODEL,
    ) -> TranscribeResult:
        """Transcribe one completed audio file. Charged per minute of audio."""
        if not path or not os.path.exists(path):
            raise errors.EngineError("no recording was available to transcribe",
                                     kind="bad_request")
        size = os.path.getsize(path)
        if size > 25 * 1024 * 1024:
            raise errors.EngineError(
                f"the recording is {size/1048576:.1f} MB, above the 25 MB limit "
                f"for a single request", kind="too_large")
        if size == 0:
            raise errors.EngineError("the recording was empty", kind="bad_request")

        extra: dict = {}
        # `keywords` are hints, not required output. Keep each on one line and
        # free of <, >, CR or LF - the API rejects the request otherwise.
        cleaned_keywords = [k for k in _clean_keywords(keywords) if k]
        if cleaned_keywords:
            extra["keywords"] = cleaned_keywords
        if languages:
            extra["languages"] = [str(l) for l in languages if l]

        sdk = self._sdk()
        try:
            with open(path, "rb") as fh:
                kwargs: dict = {"model": model, "file": fh}
                if prompt:
                    kwargs["prompt"] = prompt[:2000]
                if extra:
                    kwargs["extra_body"] = extra
                result = sdk.audio.transcriptions.create(**kwargs)
        except Exception as exc:
            raise errors.from_exception(exc, model) from exc

        text = (getattr(result, "text", None) or "").strip()
        seconds = float(audio_seconds or 0.0) or _duration_of(result)
        usd = pricing.transcribe_seconds_to_usd(seconds)
        langs = None
        raw = getattr(result, "languages", None)
        if raw:
            langs = [getattr(l, "code", None) or str(l) for l in raw]
        request_id = None
        try:
            response = getattr(result, "_response", None) or getattr(result, "response", None)
            request_id = (response.headers.get("x-request-id")
                          if response is not None and hasattr(response, "headers")
                          else None)
        except Exception:
            request_id = None

        detail = ("transcribed with gpt-transcribe"
                  if text else "the model returned an empty transcript")
        log.info("transcribe ok: %.1fs audio, %d chars, $%.5f",
                 seconds, len(text), usd)
        return TranscribeResult(text=text, model=model, audio_seconds=seconds,
                               usd=usd, request_id=request_id,
                               detected_languages=langs, detail=detail)


def config_transcribe_keys() -> list[str]:
    """Keywords offered to the transcriber, from the user's vocabulary list."""
    try:
        from ..db import db
        return db().list_vocab()[:120]
    except Exception:
        from .. import config
        return list(config.DEFAULT_VOCABULARY)


@dataclass
class CaptureTranscribeResult:
    """Outcome of the full Economy path: captured PCM -> transcript."""

    ok: bool
    text: str = ""
    reason: str = ""
    kind: str = ""
    audio_seconds: float = 0.0
    usd: float = 0.0
    vad: dict = None            # type: ignore[assignment]
    model: str = MODEL
    uploaded: bool = False

    def as_dict(self) -> dict:
        return {"ok": self.ok, "text": self.text, "reason": self.reason,
                "kind": self.kind, "audio_seconds": round(self.audio_seconds, 2),
                "usd": self.usd, "vad": self.vad or {}, "model": self.model,
                "uploaded": self.uploaded}


def transcribe_captured(
    self,
    pcm: bytes,
    rate: int,
    keywords: Optional[Sequence[str]] = None,
    prompt: Optional[str] = None,
    languages: Optional[Sequence[str]] = ("en",),
    retain_audio: Optional[bool] = None,
) -> CaptureTranscribeResult:
    """The Economy dictation path: capture buffer -> verified transcript.

    Two guards that the integration tests showed are necessary:

    1. SILENCE IS NEVER UPLOADED. On 11 seconds of near-silence with strong
       keyword hints, gpt-transcribe returned "the user." - a fabricated
       transcript. We check for speech energy first, so silent audio costs
       nothing and can never produce invented text.
    2. SILENCE IS TRIMMED. Removing the silent lead-in/tail reduces billed audio
       and removes the long quiet stretch that encourages hallucination.

    Raw audio is written to a temporary file only because the documented path is
    a file upload; it is deleted immediately unless retention is enabled.
    """
    from .. import config
    from ..audio import vad

    info = vad.analyze(pcm, rate)
    if not pcm:
        return CaptureTranscribeResult(False, reason="nothing was recorded",
                                       kind="empty", vad=info)
    if not vad.has_speech(pcm, rate):
        log.info("economy dictation refused: no speech energy (%s)", info)
        return CaptureTranscribeResult(
            False, kind="no_speech", vad=info,
            reason=("no speech was detected, so nothing was uploaded "
                    "(this prevents a made-up transcript and costs nothing)"))

    trimmed = vad.trim_silence(pcm, rate)
    seconds = len(trimmed) / 2 / float(rate or 1)
    if seconds < 0.25:
        return CaptureTranscribeResult(
            False, kind="too_short", vad=info,
            reason="that was too short to transcribe")

    retain = config.get("retain_audio_files", False) if retain_audio is None else retain_audio
    import tempfile
    import wave
    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as fh:
            tmp_path = fh.name
        with wave.open(tmp_path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(trimmed)

        result = self.transcribe_file(tmp_path, keywords=keywords, prompt=prompt,
                                     languages=languages, audio_seconds=seconds)
    except errors.EngineError as exc:
        return CaptureTranscribeResult(False, reason=str(exc), kind=exc.kind,
                                       vad=info, audio_seconds=seconds)
    finally:
        if tmp_path and not retain:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    if not result.text.strip():
        return CaptureTranscribeResult(
            False, kind="no_speech", vad=info, audio_seconds=seconds,
            usd=result.usd, model=result.model,
            reason="the transcriber returned nothing - say it again, a little closer")

    return CaptureTranscribeResult(True, text=result.text, vad=info,
                                   audio_seconds=seconds, usd=result.usd,
                                   model=result.model, uploaded=True,
                                   reason="transcribed")


# Bind the helper as a method so it reads naturally at call sites.
TranscribeEngine.transcribe_captured = transcribe_captured  # type: ignore[attr-defined]


def _clean_keywords(keywords: Optional[Iterable[str]]) -> list[str]:
    """Strip characters the API rejects inside a keyword."""
    out: list[str] = []
    for k in keywords or []:
        s = str(k).replace("<", "").replace(">", "")
        s = s.replace("\r", " ").replace("\n", " ").strip()
        if s:
            out.append(s)
    return out


def _duration_of(result) -> float:
    for attr in ("duration", "audio_duration"):
        val = getattr(result, attr, None)
        if isinstance(val, (int, float)) and val > 0:
            return float(val)
    usage = getattr(result, "usage", None)
    if usage is not None:
        val = getattr(usage, "seconds", None) or getattr(usage, "duration", None)
        if isinstance(val, (int, float)) and val > 0:
            return float(val)
    return 0.0


def friendly_missing_key() -> str:
    return ("no OpenAI API key is stored yet. Open the app and enter it on the "
            "setup screen (it is kept in Windows Credential Manager, never in "
            "a file or a log).")


@dataclass
class ProgressState:
    stage: str = "idle"      # idle | capturing | uploading | transcribing | done
    detail: str = ""

    def as_dict(self) -> dict:
        return {"stage": self.stage, "detail": self.detail}
