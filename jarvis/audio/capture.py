"""Microphone capture.

DESIGN CONSTRAINTS (build spec sections 4, 5, 11)
-------------------------------------------------
* "While asleep, keep audio processing local... Keep only a short bounded
  in-memory buffer; forward only the intended utterance after activation, not
  background conversation preceding it. Discard buffers on cancel."
  -> `WakePreRoll` is a fixed-size ring buffer that is cleared on cancel, and
     the dictation buffer is bounded by `max_seconds`.

* "Disable raw-audio storage by default." -> nothing here writes audio to disk.
  `save_wav` exists but is only called when config `retain_audio_files` is true,
  and it is used for the Economy path's temporary upload file, which is deleted
  after the request unless retention is explicitly enabled.

* "On lock, suspend, microphone disable, or quit, stop capture."
  -> `on_error`/`on_lost` let the app react to a device disappearing, and
     `stop()` releases the device promptly.

* Level metering is exposed for the setup wizard's microphone test.

Audio is requested at the engine's rate and let PortAudio/WASAPI convert when
the device cannot open at that exact rate. `StreamingResampler` is the fallback
if a device refuses.
"""

from __future__ import annotations

import threading
import time
import wave
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from ..logsetup import get as _log
from .resample import StreamingResampler

log = _log("audio.capture")

DEFAULT_RATE = 24000
BLOCK_MS = 20
# Bound on a single utterance so a stuck key cannot fill memory or open an
# unbounded billed session.
MAX_UTTERANCE_SECONDS = 180.0


def _sd():
    import sounddevice as sd
    return sd


@dataclass
class DeviceInfo:
    index: int
    name: str
    channels: int
    default: bool = False
    host_api: str = ""

    def as_dict(self) -> dict:
        return {"index": self.index, "name": self.name, "channels": self.channels,
                "default": self.default, "host_api": self.host_api}


#: Host APIs in the order we prefer them. WASAPI is the modern Windows API: it
#: lists each physical device ONCE, with its full name. PortAudio reports every
#: API, so the same microphone appears again under MME, DirectSound and WDM-KS -
#: which is why a machine with two microphones was showing sixteen, and why the
#: MME copies arrive truncated to 31 characters
#: ("Microphone (BlackShark V3 X USB" with no closing bracket).
_HOSTAPI_PREFERENCE = ("Windows WASAPI", "Windows DirectSound", "MME",
                       "Windows WDM-KS")

#: Entries PortAudio synthesises that are not real hardware. "Microsoft Sound
#: Mapper" and "Primary Sound Capture Driver" are aliases, and WDM-KS exposes
#: outputs as capture endpoints, so "PC Speaker" turns up in the input list.
_ABSTRACT_NAMES = ("microsoft sound mapper", "primary sound capture driver",
                   "primary sound driver", "sound mapper", "pc speaker",
                   "default audio device")


def _looks_abstract(name: str) -> bool:
    low = name.strip().lower()
    return any(a in low for a in _ABSTRACT_NAMES)


def _list_devices(want_input: bool) -> list[DeviceInfo]:
    """Real devices for one direction, from a single host API.

    The full PortAudio list is not what a person means by "my microphones": it
    is every API's view of them. Picking the most capable API that has anything
    to offer gives one row per physical device with its real name.
    """
    sd = _sd()
    key = "max_input_channels" if want_input else "max_output_channels"
    default_slot = 0 if want_input else 1

    try:
        devices = list(sd.query_devices())
    except Exception:
        return []
    try:
        hostapis = list(sd.query_hostapis())
    except Exception:
        hostapis = []

    def api_name(d) -> str:
        try:
            return str(hostapis[int(d["hostapi"])]["name"])
        except Exception:
            return ""

    by_api: dict[str, list[int]] = {}
    for i, d in enumerate(devices):
        try:
            if int(d.get(key, 0)) <= 0:
                continue
        except Exception:
            continue
        if _looks_abstract(str(d.get("name", ""))):
            continue
        by_api.setdefault(api_name(d), []).append(i)

    if not by_api:
        return []

    chosen = next((a for a in _HOSTAPI_PREFERENCE if by_api.get(a)), None)
    if chosen is None:
        chosen = max(by_api, key=lambda a: len(by_api[a]))

    # PortAudio's default index lives in the global numbering and may belong to
    # a different API than the one we kept, so match it by name instead.
    default_name = ""
    try:
        default_index = int(sd.default.device[default_slot])
        if 0 <= default_index < len(devices):
            default_name = str(devices[default_index].get("name", "")).strip().lower()
    except Exception:
        pass

    out: list[DeviceInfo] = []
    seen: set[str] = set()
    default_norm = _norm(default_name)
    for i in by_api[chosen]:
        name = str(devices[i].get("name", "")).strip()
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        is_default = bool(default_norm) and _norm(name) == default_norm
        out.append(DeviceInfo(i, name, int(devices[i][key]), is_default, chosen))

    # The default may live under an API we did not keep, so if nothing matched
    # by name the first entry is the closest thing to a sensible default.
    if out and not any(d.default for d in out):
        out[0].default = True
    return out


def _norm(name: str) -> str:
    """Normalise a device name so names from different APIs can be compared.

    MME truncates to 31 characters, so the same microphone is
    "Microphone (BlackShark V3 X USB" there and "...USB)" under WASAPI. Dropping
    brackets, trailing punctuation and case makes the two comparable.
    """
    s = str(name or "").strip().lower()
    for ch in "()[]{}":
        s = s.replace(ch, " ")
    return " ".join(s.split()).rstrip(",.")


def resolve_device(value, want_input: bool = True) -> Optional[int]:
    """Turn a configured device into a real index, or None for the default.

    Accepts an index, a full name, or the truncated name an older build saved
    (which is what MME reports). A name that no longer exists - the headset got
    unplugged - resolves to None rather than being handed to PortAudio, where it
    would raise and take the whole dictation path down with it.
    """
    devices = list_input_devices() if want_input else list_output_devices()
    if value is None or value == "":
        return None

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if any(d.index == value for d in devices) else None
    if isinstance(value, float) and float(value).is_integer():
        return resolve_device(int(value), want_input)

    wanted = _norm(value)
    if not wanted:
        return None

    for d in devices:                       # exact, once normalised
        if _norm(d.name) == wanted:
            return d.index
    for d in devices:                       # one is a prefix of the other
        got = _norm(d.name)
        if got.startswith(wanted) or wanted.startswith(got):
            return d.index
    return None


def list_input_devices() -> list[DeviceInfo]:
    return _list_devices(want_input=True)


def list_output_devices() -> list[DeviceInfo]:
    return _list_devices(want_input=False)


class WakePreRoll:
    """Fixed-size ring of recent audio, kept ONLY for wake-word detection.

    Cleared on cancel, and never forwarded wholesale: after activation the app
    forwards the intended utterance, not the ambient conversation that preceded
    it. This type exists so that "keep only a short bounded in-memory buffer" is
    a property of the code rather than a promise.
    """

    def __init__(self, seconds: float = 1.2, rate: int = 16000):
        self.capacity = max(1, int(seconds * rate))
        self._buf = np.zeros(0, dtype=np.int16)
        self._lock = threading.Lock()

    def push(self, samples: np.ndarray) -> None:
        with self._lock:
            self._buf = np.concatenate([self._buf, samples])[-self.capacity:]

    def snapshot(self) -> np.ndarray:
        with self._lock:
            return self._buf.copy()

    def clear(self) -> None:
        with self._lock:
            self._buf = np.zeros(0, dtype=np.int16)

    def __len__(self) -> int:
        with self._lock:
            return int(self._buf.size)


class MicrophoneCapture:
    """Streams mono PCM16 from the microphone.

    Two consumption modes, usable simultaneously:
      * `on_chunk(bytes)` callback - for streaming straight into a Live session
      * `take()` - returns and clears everything buffered since the last call
        (used by the Economy path, which uploads a completed recording)
    """

    def __init__(self, rate: int = DEFAULT_RATE, device: Optional[int] = None,
                 gain: float = 1.0,
                 on_chunk: Optional[Callable[[bytes], None]] = None,
                 on_level: Optional[Callable[[float], None]] = None,
                 on_lost: Optional[Callable[[str], None]] = None,
                 max_seconds: float = MAX_UTTERANCE_SECONDS,
                 preroll: Optional[WakePreRoll] = None):
        self.rate = int(rate)
        self.device = device
        self.gain = float(gain)
        self.on_chunk = on_chunk
        self.on_level = on_level
        self.on_lost = on_lost
        self.max_seconds = float(max_seconds)
        self.preroll = preroll

        self._stream = None
        self._lock = threading.RLock()
        self._buf = bytearray()
        self._running = False
        self._paused = False
        self._level = 0.0
        self._peak = 0.0
        self._started_at = 0.0
        self._frames_total = 0
        self._speech_frames = 0
        self._frame_count = 0
        self._resampler: Optional[StreamingResampler] = None
        self._actual_rate = self.rate
        self._overflow = False
        self._last_speech_at = 0.0

    # ------------------------------------------------------------- properties
    @property
    def running(self) -> bool:
        return self._running

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def level(self) -> float:
        return self._level

    @property
    def peak(self) -> float:
        return self._peak

    @property
    def seconds_captured(self) -> float:
        return self._frames_total / float(self.rate or 1)

    @property
    def overflowed(self) -> bool:
        """True if we hit the utterance cap and stopped accepting audio."""
        return self._overflow

    @property
    def speech_detected(self) -> bool:
        """Whether any speech-level energy was seen during this capture.

        The dictation controller uses this to refuse to upload silent audio and
        to distrust a transcript that appears over silence - see audio/vad.py
        for the fabricated-transcript bug this closes.
        """
        if self._speech_frames == 0 or self._frame_count == 0:
            return False
        return (self._speech_frames / self._frame_count) >= 0.06

    @property
    def speech_ratio(self) -> float:
        if not self._frame_count:
            return 0.0
        return self._speech_frames / self._frame_count

    @property
    def silence_seconds(self) -> float:
        """Seconds since the last speech-level frame, or 0.0 before any speech.

        Zero means "not armed yet", NOT "no silence": hands-free auto-stop must
        never end a capture that has not heard anything, because the user may
        still be drawing breath after pressing the key.
        """
        with self._lock:
            if not self._last_speech_at:
                return 0.0
            return max(0.0, time.monotonic() - self._last_speech_at)

    # ---------------------------------------------------------------- control
    def start(self) -> None:
        sd = _sd()
        with self._lock:
            if self._running:
                return
            self._buf = bytearray()
            self._frames_total = 0
            self._speech_frames = 0
            self._frame_count = 0
            self._overflow = False
            self._peak = 0.0
            self._last_speech_at = 0.0
            self._started_at = time.monotonic()

            blocksize = max(64, int(self.rate * BLOCK_MS / 1000))
            kwargs = dict(samplerate=self.rate, channels=1, dtype="int16",
                          blocksize=blocksize, callback=self._callback,
                          device=self.device)
            try:
                self._stream = sd.InputStream(**kwargs)
                self._actual_rate = int(self._stream.samplerate)
                self._stream.start()
            except Exception as exc:
                # Device may refuse our rate; capture native and convert.
                log.warning("could not open capture at %d Hz (%s); retrying native",
                            self.rate, exc)
                try:
                    info = sd.query_devices(self.device, "input")
                    native = int(info["default_samplerate"])
                    self._stream = sd.InputStream(samplerate=native, channels=1,
                                                  dtype="int16", blocksize=blocksize,
                                                  callback=self._callback,
                                                  device=self.device)
                    self._actual_rate = native
                    self._resampler = StreamingResampler(native, self.rate)
                    self._stream.start()
                except Exception as exc2:
                    log.error("microphone open failed: %s", exc2)
                    self._running = False
                    if self.on_lost:
                        self.on_lost(str(exc2))
                    raise
            self._running = True
            log.info("capture started: device=%s rate=%d -> %d",
                     self.device, self._actual_rate, self.rate)

    def stop(self) -> None:
        with self._lock:
            self._running = False
            stream = self._stream
            self._stream = None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception as exc:
                log.debug("capture close: %s", exc)
        # Clear the pre-roll: nothing from a finished utterance should linger.
        if self.preroll is not None:
            self.preroll.clear()
        log.info("capture stopped")

    def pause(self) -> None:
        """Suspend forwarding without releasing the device."""
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def take(self) -> bytes:
        with self._lock:
            data = bytes(self._buf)
            self._buf = bytearray()
        return data

    def reset_peak(self) -> None:
        self._peak = 0.0

    # ----------------------------------------------------------------- callback
    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ARG002
        """PortAudio thread. Must stay fast and must not raise."""
        if status:
            # input overflow means we could not keep up; note it rather than
            # silently dropping audio.
            log.debug("capture status: %s", status)
        try:
            pcm = bytes(indata)
            if self._resampler is not None:
                pcm = self._resampler.process(pcm)
            if not pcm:
                return
            if self.gain != 1.0:
                arr = np.frombuffer(pcm, dtype="<i2").astype(np.float32) * self.gain
                pcm = np.clip(arr, -32768, 32767).astype("<i2").tobytes()

            if self.preroll is not None:
                self.preroll.push(np.frombuffer(pcm, dtype="<i2"))

            if self._paused:
                return

            with self._lock:
                if self._frames_total / self.rate >= self.max_seconds:
                    if not self._overflow:
                        self._overflow = True
                        log.warning("utterance cap reached (%.0fs); no longer "
                                    "buffering", self.max_seconds)
                    return
                self._frames_total += len(pcm) // 2
                self._buf.extend(pcm)

            chunk = pcm
            if self.on_chunk:
                self.on_chunk(chunk)

            # Level for the wizard's meter, throttled to keep the callback cheap.
            samples = np.frombuffer(pcm, dtype="<i2")
            if samples.size:
                rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2))) / 32768.0
                peak = float(np.max(np.abs(samples))) / 32768.0
                self._level = rms
                if peak > self._peak:
                    self._peak = peak
                # Count speech-level frames so the controller can refuse to
                # upload silence (see audio/vad.py).
                self._frame_count += 1
                if rms > 0 and (20.0 * float(np.log10(max(rms, 1e-9)))) >= -42.0:
                    self._speech_frames += 1
                    self._last_speech_at = time.monotonic()
                if self.on_level:
                    self.on_level(rms)
        except Exception as exc:  # never let this escape into PortAudio
            log.debug("capture callback error: %s", exc)

    # -------------------------------------------------------------------- files
    def write_wav(self, path: str, pcm: Optional[bytes] = None) -> float:
        """Write buffered PCM to a WAV file. Returns the duration in seconds.

        Only used for the Economy upload path (or explicit retention). The
        caller is responsible for deleting the file when retention is off.
        """
        data = self.take() if pcm is None else pcm
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.rate)
            w.writeframes(data)
        return len(data) / 2 / float(self.rate or 1)


def measure_input_level(seconds: float = 1.0, device: Optional[int] = None,
                        rate: int = DEFAULT_RATE) -> dict:
    """Blocking microphone test for the setup wizard. Returns real measurements."""
    cap = MicrophoneCapture(rate=rate, device=device)
    try:
        cap.start()
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    peak = 0.0
    rms_sum = 0.0
    n = 0
    deadline = time.monotonic() + seconds
    try:
        while time.monotonic() < deadline:
            time.sleep(0.05)
            peak = max(peak, cap.peak)
            rms_sum += cap.level
            n += 1
    finally:
        cap.stop()
    rms = rms_sum / max(1, n)
    return {"ok": True, "peak": round(peak, 4), "rms": round(rms, 5),
            "seconds": seconds,
            "verdict": ("signal detected" if peak > 0.02 else
                        "almost silent - check the microphone is not muted"),
            "device": device}
