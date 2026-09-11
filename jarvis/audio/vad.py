"""Energy-based voice activity detection.

WHY THIS EXISTS (a real bug this module fixes)
----------------------------------------------
During loopback testing the microphone captured 11 seconds of near-silence
(peak -40 dBFS) while the app was configured with the user's vocabulary as keyword
hints. `gpt-transcribe` returned "the user." - a confident, completely
fabricated transcript produced from no speech at all. Strong keyword hints make
that worse, because they bias the model toward the supplied terms.

The build spec requires: "Silence/background speech produces no fabricated
dictation." Prompting the model to behave is not enforcement, so we refuse to
send silent audio in the first place, and we treat a transcript that appears over
silent audio as suspect rather than inserting it.

This is intentionally a cheap energy gate, not a speech-recognition model: it
answers "was there any speech-level energy at all?", which is exactly the
question needed to avoid paying for and believing a hallucination. It runs
locally, adds no dependency, and never uploads anything.
"""

from __future__ import annotations

import numpy as np

# dBFS thresholds. Typical speech at a normal distance sits well above -40 dBFS;
# a quiet room's noise floor is usually below -55 dBFS.
SILENCE_DB = -52.0        # below this a frame is treated as silence
SPEECH_DB = -42.0         # above this a frame counts as speech energy
FRAME_MS = 20


def _frames(pcm: bytes, rate: int, frame_ms: int = FRAME_MS) -> np.ndarray:
    """Return an (n_frames, frame_len) float array in [-1, 1]."""
    if not pcm:
        return np.zeros((0, 1), dtype=np.float32)
    usable = len(pcm) - (len(pcm) % 2)
    x = np.frombuffer(pcm[:usable], dtype="<i2").astype(np.float32) / 32768.0
    n = max(1, int(rate * frame_ms / 1000))
    if x.size < n:
        return x.reshape(1, -1) if x.size else np.zeros((0, 1), dtype=np.float32)
    trimmed = x[: (x.size // n) * n]
    return trimmed.reshape(-1, n)


def frame_db(pcm: bytes, rate: int, frame_ms: int = FRAME_MS) -> np.ndarray:
    """Per-frame RMS level in dBFS."""
    f = _frames(pcm, rate, frame_ms)
    if f.size == 0:
        return np.zeros(0, dtype=np.float32)
    rms = np.sqrt(np.mean(f.astype(np.float64) ** 2, axis=1))
    return 20.0 * np.log10(np.maximum(rms, 1e-9))


def analyze(pcm: bytes, rate: int) -> dict:
    """Describe the energy content of a captured buffer."""
    if not pcm:
        return {"duration": 0.0, "peak_db": -120.0, "rms_db": -120.0,
                "speech_ratio": 0.0, "has_speech": False, "clipped": False}
    usable = len(pcm) - (len(pcm) % 2)
    x = np.frombuffer(pcm[:usable], dtype="<i2").astype(np.float32) / 32768.0
    duration = x.size / float(rate or 1)
    if x.size == 0:
        return {"duration": 0.0, "peak_db": -120.0, "rms_db": -120.0,
                "speech_ratio": 0.0, "has_speech": False, "clipped": False}
    peak = float(np.max(np.abs(x)))
    rms = float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))
    db = frame_db(pcm, rate)
    speech_frames = int(np.count_nonzero(db >= SPEECH_DB))
    ratio = speech_frames / max(1, db.size)
    return {
        "duration": round(duration, 3),
        "peak_db": round(20 * np.log10(max(peak, 1e-9)), 1),
        "rms_db": round(20 * np.log10(max(rms, 1e-9)), 1),
        "speech_ratio": round(ratio, 4),
        "has_speech": bool(peak > 0 and ratio >= 0.06),
        "clipped": bool(peak >= 0.995),
    }


def has_speech(pcm: bytes, rate: int, min_ratio: float = 0.06) -> bool:
    """True when enough frames carry speech-level energy.

    Conservative in the direction of NOT fabricating: a false "no speech" costs
    the user one retry, whereas a false "speech" can insert invented text.
    """
    if not pcm:
        return False
    db = frame_db(pcm, rate)
    if db.size == 0:
        return False
    return float(np.count_nonzero(db >= SPEECH_DB)) / db.size >= min_ratio


def trim_silence(pcm: bytes, rate: int, pad_ms: int = 150,
                 threshold_db: float = SILENCE_DB) -> bytes:
    """Remove leading/trailing silence, keeping `pad_ms` of margin.

    Sending a tighter buffer reduces billed audio and removes the long silent
    lead-in that encourages hallucination.
    """
    if not pcm:
        return b""
    db = frame_db(pcm, rate)
    if db.size == 0:
        return b""
    loud = np.nonzero(db >= threshold_db)[0]
    if loud.size == 0:
        return b""
    frame_len = max(1, int(rate * FRAME_MS / 1000)) * 2   # bytes per frame
    pad_frames = max(1, int(pad_ms / FRAME_MS))
    start = max(0, int(loud[0]) - pad_frames)
    end = min(db.size, int(loud[-1]) + 1 + pad_frames)
    return pcm[start * frame_len:end * frame_len]
