"""Speaker output for GPT-Live audio.

Requirements (build spec sections 7, 11)
---------------------------------------
* "allow interruptions" - interrupting must be immediate, so `flush()` drops the
  pending buffer and restarts the stream at once.
* "Validate acoustic echo cancellation, speaker feedback, microphone handover
  and Bluetooth-device behaviour." We cannot perform AEC ourselves; what this
  module guarantees is that the app never feeds the model's own voice back into
  the microphone *through the app*: in dictation mode output audio is discarded
  entirely (see engines/live.py), so there is no local playback loop. Whether the
  physical room echoes is a hardware question, which the setup wizard's speaker
  test measures rather than assumes.
* "Interrupting voice does not itself cancel backend actions." Barge-in stops
  playback only; it never touches backend tasks.

Audio in is the documented `session.output_audio.delta`: mono PCM16 at the
session rate (no container header, and no timing fields - the docs note Live
emits no output-audio-done event, so we track our own buffer instead).
"""

from __future__ import annotations

import threading
import time
from typing import Optional

import numpy as np

from ..logsetup import get as _log
from .resample import StreamingResampler

log = _log("audio.playback")

DEFAULT_RATE = 24000


def _sd():
    import sounddevice as sd
    return sd


class Speaker:
    """A small streaming player with hard, immediate interruption."""

    def __init__(self, rate: int = DEFAULT_RATE, device: Optional[int] = None,
                 volume: float = 1.0):
        self.rate = int(rate)
        self._input_rate = int(rate)
        self._resampler = None
        self.device = device
        self.volume = float(volume)
        self._pending = bytearray()
        self._lock = threading.RLock()
        self._stream = None
        self._playing = False
        self._level = 0.0
        self._last_audio_at = 0.0
        self._channels = 1

    # ---------------------------------------------------------------- state
    @property
    def playing(self) -> bool:
        with self._lock:
            return self._playing and bool(self._pending)

    @property
    def level(self) -> float:
        return self._level

    @property
    def queued_bytes(self) -> int:
        with self._lock:
            return len(self._pending)

    # ------------------------------------------------------------- controls
    def start(self) -> None:
        with self._lock:
            if self._stream is not None:
                return
            sd = _sd()
            blocksize = max(64, int(self.rate * 20 / 1000))
            try:
                self._stream = sd.OutputStream(
                    samplerate=self.rate, channels=1, dtype="int16",
                    blocksize=blocksize, callback=self._callback, device=self.device)
                self._stream.start()
                self._channels = 1
            except Exception as exc:
                # Retry at the device's native rate; PortAudio converts for us.
                log.warning("output open at %d Hz failed (%s); trying native",
                            self.rate, exc)
                if self._stream is not None:
                    self._stream.close()
                    self._stream = None
                try:
                    info = sd.query_devices(self.device, "output")
                    native = int(info["default_samplerate"])
                    self._stream = sd.OutputStream(
                        samplerate=native, channels=1, dtype="int16",
                        blocksize=blocksize, callback=self._callback,
                        device=self.device)
                    self.rate = native
                    self._resampler = StreamingResampler(self._input_rate, native)
                    self._stream.start()
                    self._channels = 1
                except Exception as exc2:
                    log.error("speaker open failed: %s", exc2)
                    self._stream = None
                    raise
        log.info("speaker started at %d Hz (device=%s)", self.rate, self.device)

    def stop(self) -> None:
        with self._lock:
            stream = self._stream
            self._stream = None
            self._pending.clear()
        self._playing = False
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception as exc:
                log.debug("speaker close: %s", exc)

    def play(self, pcm: bytes) -> None:
        """Queue PCM16 for playback, opening the device on first use."""
        if not pcm:
            return
        if self._stream is None:
            try:
                self.start()
            except Exception as exc:
                log.warning("cannot play audio: %s", exc)
                return
        with self._lock:
            if self._resampler is not None:
                pcm = self._resampler.process(pcm)
            self._pending.extend(pcm)
            self._last_audio_at = time.monotonic()
            self._playing = True

    def flush(self, reason: str = "barge-in") -> int:
        """Stop playback immediately and drop anything pending.

        Returns the number of bytes discarded, so the caller can report what was
        actually skipped rather than claiming it was played.
        """
        with self._lock:
            dropped = len(self._pending)
            self._pending.clear()
            if self._resampler is not None:
                self._resampler = StreamingResampler(self._input_rate, self.rate)
        self._playing = False
        self._level = 0.0
        if self._stream is not None:
            try:
                # abort() drops the device's own internal buffer too, which is
                # what makes interruption feel instant rather than "after the
                # current block finishes".
                self._stream.abort()
                self._stream.start()
            except Exception as exc:
                log.debug("flush: %s", exc)
        log.info("playback flushed (%s), %d bytes discarded", reason, dropped)
        return dropped

    # -------------------------------------------------------------- callback
    def _callback(self, outdata, frames, time_info, status) -> None:  # noqa: ARG002
        """PortAudio thread: fill the output block, silence when idle.

        Must never raise - an exception here kills the audio thread.
        """
        need = frames * 2  # mono int16
        piece = b""
        try:
            with self._lock:
                if len(self._pending) >= need:
                    piece = bytes(self._pending[:need])
                    del self._pending[:need]
                elif self._pending:
                    piece = bytes(self._pending)
                    self._pending.clear()

            if piece and self.volume != 1.0:
                arr = np.frombuffer(piece, dtype="<i2").astype(np.float32) * self.volume
                piece = np.clip(arr, -32768, 32767).astype("<i2").tobytes()

            had_audio = bool(piece)
            if len(piece) < need:
                piece = piece + b"\x00" * (need - len(piece))

            arr = np.frombuffer(piece, dtype="<i2", count=frames)
            if outdata.ndim == 2:
                outdata[:, 0] = arr
            else:
                outdata[:] = arr

            if had_audio:
                nz = arr[arr != 0]
                self._level = (float(np.sqrt(np.mean(nz.astype(np.float32) ** 2)))
                               / 32768.0) if nz.size else 0.0
            else:
                self._level = 0.0
            self._playing = had_audio
        except Exception as exc:
            log.debug("playback callback error: %s", exc)
            try:
                if outdata.ndim == 2:
                    outdata[:, 0] = 0
                else:
                    outdata[:] = 0
            except Exception:
                pass


def speaker_echo_test(seconds: float = 1.2, device: Optional[int] = None,
                      rate: int = DEFAULT_RATE) -> dict:
    """Play a short faded tone so the wizard can confirm the output device.

    Reports real device information. It does NOT claim to measure echo, which
    depends on the physical setup (speaker placement, laptop mic, Bluetooth
    latency) and cannot be established from inside the app.
    """
    sd = _sd()
    try:
        info = sd.query_devices(device, "output")
        name = str(info["name"])
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    if seconds <= 0:
        return {"ok": True, "device": device, "name": name, "played": False}

    tone_hz = 440.0
    t = np.linspace(0, seconds, int(rate * seconds), endpoint=False)
    # Fade in/out so there is no click at the edges.
    env = np.clip(np.minimum(t / 0.05, (seconds - t) / 0.05), 0.0, 1.0)
    tone = np.sin(2 * np.pi * tone_hz * t) * 0.18 * env
    pcm = (tone * 32767).astype("<i2").tobytes()
    sp = Speaker(rate=rate, device=device)
    try:
        sp.start()
        sp.play(pcm)
        time.sleep(seconds + 0.3)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "device": device, "name": name}
    finally:
        sp.stop()
    return {"ok": True, "device": device, "name": name, "played": True,
            "seconds": seconds}
