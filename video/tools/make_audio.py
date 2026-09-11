"""Generate the demo video's soundtrack and sound effects from scratch.

Everything here is synthesised with numpy — no samples, no third-party audio —
so the result is original and carries no licensing obligation. Output is
44.1 kHz stereo WAV; the build script transcodes it to MP3 for the repo.

Musical sketch
--------------
82 BPM, swung 8ths, a four-bar ii-V-I-ish lofi progression looped with a little
human variation. Warm electric-piano chords, a soft round bass, brushed drums,
vinyl crackle and tape hiss, then a gentle low-pass so nothing is bright or
harsh. Dynamics are deliberately narrow: this sits UNDER narration, it is not a
track anyone should notice on its own.
"""
from __future__ import annotations

import math
import wave
from pathlib import Path

import numpy as np

SR = 44100
OUT = Path(__file__).resolve().parent.parent / "public" / "audio"
OUT.mkdir(parents=True, exist_ok=True)

BPM = 82.0
BEAT = 60.0 / BPM          # 0.7317 s
BAR = 4 * BEAT             # 2.9268 s
BARS = 28                  # ~82 s of music
TOTAL = BAR * BARS + 2.0

# ---------------------------------------------------------------- helpers

def t_axis(dur: float) -> np.ndarray:
    return np.linspace(0.0, dur, int(SR * dur), endpoint=False)


def env_ad(t: np.ndarray, attack: float, decay: float, curve: float = 3.0) -> np.ndarray:
    """Attack/decay envelope. `curve` shapes how percussive the decay feels."""
    a = np.clip(t / max(attack, 1e-6), 0.0, 1.0)
    d = np.exp(-np.clip((t - attack) / max(decay, 1e-6), 0.0, None) * curve)
    return a * d


def place(buf: np.ndarray, sig: np.ndarray, at: float, gain: float = 1.0) -> None:
    """Mix `sig` into `buf` starting at `at` seconds, clipping to the buffer."""
    i = int(round(at * SR))
    if i < 0:
        # A humanised offset can land fractionally before a section starts;
        # trim the overhang rather than silently dropping the whole hit.
        sig = sig[-i:]
        i = 0
    if i >= len(buf) or len(sig) == 0:
        return
    n = min(len(sig), len(buf) - i)
    buf[i:i + n] += sig[:n] * gain


def lp_fft(sig: np.ndarray, cutoff: float, order: int = 2) -> np.ndarray:
    """Zero-phase low-pass via FFT. Fine for a whole-mix tone shaping pass."""
    spec = np.fft.rfft(sig)
    freqs = np.fft.rfftfreq(len(sig), 1.0 / SR)
    rolloff = 1.0 / np.sqrt(1.0 + (freqs / cutoff) ** (2 * order))
    return np.fft.irfft(spec * rolloff, n=len(sig))


def hp_fft(sig: np.ndarray, cutoff: float, order: int = 2) -> np.ndarray:
    spec = np.fft.rfft(sig)
    freqs = np.fft.rfftfreq(len(sig), 1.0 / SR)
    rolloff = 1.0 / np.sqrt(1.0 + (cutoff / np.maximum(freqs, 1e-9)) ** (2 * order))
    return np.fft.irfft(spec * rolloff, n=len(sig))


def tilt_eq(sig: np.ndarray, curve: list[tuple[float, float]]) -> np.ndarray:
    """Apply a smooth EQ curve, given as (frequency Hz, linear gain) anchors.

    Interpolating in log-frequency is what makes the result sound like one broad
    tonal move rather than a series of shelves.
    """
    spec = np.fft.rfft(sig)
    freqs = np.fft.rfftfreq(len(sig), 1.0 / SR)
    anchors = np.asarray(curve, dtype=float)
    gain = np.interp(np.log10(np.maximum(freqs, 20.0)),
                     np.log10(anchors[:, 0]), anchors[:, 1])
    return np.fft.irfft(spec * gain, n=len(sig))


def note(name: str) -> float:
    """'F3', 'A#4', 'C5' -> frequency in Hz."""
    names = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5,
             "F#": 6, "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11}
    pitch, octave = name[:-1], int(name[-1])
    midi = 12 * (octave + 1) + names[pitch]
    return 440.0 * 2 ** ((midi - 69) / 12.0)


# ---------------------------------------------------------------- instruments

def epiano(freq: float, dur: float, vel: float = 0.5) -> np.ndarray:
    """A tine-ish electric piano voice: warm fundamental, fast bright tine."""
    t = t_axis(dur)
    sig = np.zeros_like(t)
    # body — the harmonics you hear as the 'note'
    for harmonic, amp, decay in ((1, 1.00, 1.7), (2, 0.34, 1.1), (3, 0.13, 0.75)):
        sig += amp * np.sin(2 * np.pi * freq * harmonic * t) * np.exp(-t / decay)
    # tine — the bright attack that makes it read as 'electric' piano
    for harmonic, amp, decay in ((5, 0.16, 0.16), (7, 0.09, 0.10), (10, 0.05, 0.07)):
        sig += amp * np.sin(2 * np.pi * freq * harmonic * t) * np.exp(-t / decay)
    # a detuned twin, a few cents sharp, sells the tape-wobble warmth
    sig += 0.30 * np.sin(2 * np.pi * freq * 1.0016 * t) * np.exp(-t / 1.5)
    out = sig * env_ad(t, 0.006, dur, curve=2.0)
    # Keep the piano out of the bass's lane — this is the single biggest
    # difference between 'warm' and 'muddy'.
    out = hp_fft(out, 210.0, order=1)
    # gentle tremolo, like a real amp
    return out * (1.0 - 0.06 * np.sin(2 * np.pi * 4.3 * t)) * vel


def bass(freq: float, dur: float, vel: float = 0.5) -> np.ndarray:
    t = t_axis(dur)
    sig = (np.sin(2 * np.pi * freq * t)
           + 0.18 * np.sin(2 * np.pi * freq * 2 * t)
           + 0.05 * np.sin(2 * np.pi * freq * 3 * t))
    return sig * env_ad(t, 0.020, dur, curve=1.6) * vel


def pad(freqs: list[float], dur: float, vel: float = 0.3) -> np.ndarray:
    """Soft sustained chord swell for the sections that need air."""
    t = t_axis(dur)
    sig = np.zeros_like(t)
    for f in freqs:
        for det in (0.997, 1.0, 1.003):
            sig += np.sin(2 * np.pi * f * det * t) / len(freqs)
    return sig * env_ad(t, dur * 0.35, dur * 0.5, curve=1.2) * vel


def kick(vel: float = 1.0) -> np.ndarray:
    t = t_axis(0.34)
    f = 112 * np.exp(-t / 0.045) + 44           # pitch drop
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.13)
    click = np.random.default_rng(7).normal(0, 1, len(t)) * np.exp(-t / 0.004) * 0.25
    return (body + click) * vel


def snare(vel: float = 0.5) -> np.ndarray:
    rng = np.random.default_rng(11)
    t = t_axis(0.22)
    noise = rng.normal(0, 1, len(t))
    noise = hp_fft(noise, 1200.0) * np.exp(-t / 0.055)
    tone = (np.sin(2 * np.pi * 186 * t) + 0.6 * np.sin(2 * np.pi * 278 * t)) * np.exp(-t / 0.045)
    return (noise * 0.55 + tone * 0.35) * vel


def hat(open_: bool = False, vel: float = 0.2) -> np.ndarray:
    rng = np.random.default_rng(13)
    dur = 0.20 if open_ else 0.045
    t = t_axis(dur)
    noise = rng.normal(0, 1, len(t))
    noise = hp_fft(noise, 6500.0, order=3) * np.exp(-t / (0.075 if open_ else 0.012))
    return noise * vel


# ---------------------------------------------------------------- arrangement
# Fmaj9 · Em7 · Dm7 · Cmaj7 — familiar, warm, unobtrusive.
# The chord voicings sit in the 350-800 Hz 'presence' band on purpose: voiced
# any lower they pile into the same 120-300 Hz region as the bass and the whole
# mix turns to mud. `low` fills the body underneath, `bass` stays out of the way.
PROG = [
    {"chord": ["F4", "A4", "C5", "G5"], "low": ["F3", "C4"], "bass": "F2"},
    {"chord": ["E4", "G4", "B4", "D5"], "low": ["E3", "B3"], "bass": "E2"},
    {"chord": ["D4", "F4", "A4", "C5"], "low": ["D3", "A3"], "bass": "D2"},
    {"chord": ["C4", "E4", "G4", "B4"], "low": ["C3", "G3"], "bass": "C2"},
]

rng = np.random.default_rng(2026)
N = int(TOTAL * SR)
music = np.zeros(N, dtype=np.float64)

for bar in range(BARS):
    bar_start = bar * BAR
    step = PROG[bar % len(PROG)]
    # ---- energy map: sparse intro, full middle, thinning outro
    if bar < 2:
        level = 0.45
    elif bar < 4:
        level = 0.75
    else:
        level = 1.0
    if bar >= BARS - 3:
        level = 0.55

    # ---- chords: laid back, slightly behind the beat (the lofi feel)
    for i, name in enumerate(step["chord"]):
        at = bar_start + (0.04 if i == 0 else 0.0) + rng.uniform(-0.012, 0.012)
        dur = BAR * rng.uniform(0.85, 1.0)
        place(music, epiano(note(name), dur, vel=0.26 * level), at)

    # ---- body notes an octave down, quiet: warmth without the mud
    for name in step["low"]:
        place(music, epiano(note(name), BAR * 1.05, vel=0.11 * level),
              bar_start + rng.uniform(-0.010, 0.010))

    # ---- bass: root on 1, a fifth-ish pickup on the 'and' of 3
    place(music, bass(note(step["bass"]), BEAT * 1.6, 0.30 * level), bar_start)
    place(music, bass(note(step["bass"]), BEAT * 0.9, 0.19 * level),
          bar_start + BEAT * 2.5)

    # ---- pad: only where we want air
    if bar >= 4 and bar < BARS - 2 and bar % 4 in (1, 3):
        place(music, pad([note(n) for n in step["low"]], BAR * 1.1, 0.13),
              bar_start)

    # ---- drums
    place(music, kick(0.78 * level), bar_start)
    if rng.random() < 0.45:
        place(music, kick(0.38 * level), bar_start + BEAT * 2.5)
    place(music, snare(0.34 * level), bar_start + BEAT * 1)
    place(music, snare(0.36 * level), bar_start + BEAT * 3)
    # swung 8th hats — the second hit is late, which is most of the groove.
    # These carry nearly all of the track's top end, so they are mixed high.
    for eighth in range(8):
        at = bar_start + eighth * BEAT / 2
        if eighth % 2 == 1:
            at += BEAT * 0.09
        if eighth % 2 == 0 or rng.random() < 0.8:
            place(music, hat(open_=(eighth == 7 and rng.random() < 0.5),
                             vel=(0.30 if eighth % 2 == 0 else 0.19) * level), at)

# ---- vinyl crackle + tape hiss: quiet, but they are why it sounds 'lofi'
crackle = np.zeros(N)
pops = rng.integers(0, N, size=int(TOTAL * 34))
crackle[pops] = rng.uniform(0.10, 0.55, size=len(pops))
crackle = hp_fft(lp_fft(crackle, 5200.0), 900.0) * 0.5
hiss = rng.normal(0, 1, N)
hiss = lp_fft(hp_fft(hiss, 700.0), 9000.0) * 0.0075
music = music + crackle + hiss

# ---- mix: a broad tonal tilt. Cutting 150-260 Hz is what removes the 'mud';
#      the lift from 2-8 kHz is what gives the track air without harshness.
TILT = [
    (20, 0.75), (55, 0.95), (150, 0.70), (260, 0.66), (420, 0.88),
    (900, 0.96), (1800, 1.28), (3500, 1.50), (6500, 1.70), (9500, 1.45),
    (13000, 1.10), (18000, 0.80),
]
music = tilt_eq(music, TILT)
music = hp_fft(music, 45.0, order=2)

# ---- slow level drift so it never feels like a loop
drift = 1.0 + 0.04 * np.sin(2 * np.pi * np.arange(N) / (SR * 21.0))
music *= drift

# ---- stereo: haas-ish width, keep the low end centred
mono = music.copy()
left = music + 0.14 * np.roll(mono, int(SR * 0.011))
right = music + 0.14 * np.roll(mono, int(SR * 0.019))
low = lp_fft(music, 170.0)
left = left - 0.5 * low + 0.5 * low
right = right - 0.5 * low + 0.5 * low
stereo = np.stack([left, right], axis=1)

# ---- fades
fade = int(SR * 2.5)
stereo[:fade] *= np.linspace(0, 1, fade)[:, None] ** 1.3
stereo[-fade:] *= np.linspace(1, 0, fade)[:, None] ** 1.3

# ---- normalise to a mix bus level, then a soft limiter
peak = np.max(np.abs(stereo))
stereo = stereo / peak * 0.88
stereo = np.tanh(stereo * 1.12) / np.tanh(1.12)


def write_wav(path: Path, data: np.ndarray) -> None:
    pcm = np.clip(data, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2 if pcm.ndim > 1 else 1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    print(f"  {path.name:<22} {path.stat().st_size/1024:8.1f} KB  "
          f"{len(pcm)/SR:5.1f} s")


print("music:")
write_wav(OUT / "music.wav", stereo)

# ---------------------------------------------------------------- sound effects
print("sfx:")


def sfx_whoosh() -> np.ndarray:
    """Soft airy transition. Filters open then close."""
    rng_ = np.random.default_rng(31)
    dur = 0.55
    t = t_axis(dur)
    noise = rng_.normal(0, 1, len(t))
    shaped = noise * np.sin(np.pi * t / dur) ** 1.6
    out = lp_fft(hp_fft(shaped, 320.0), 2600.0)
    return out / np.max(np.abs(out)) * 0.5


def sfx_pop() -> np.ndarray:
    """A short, soft click for UI reveals. Not a beep."""
    t = t_axis(0.16)
    f = 880 * np.exp(-t / 0.03) + 260
    sig = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / 0.035)
    tick = np.random.default_rng(37).normal(0, 1, len(t)) * np.exp(-t / 0.0025)
    out = sig * 0.6 + tick * 0.25
    return out / np.max(np.abs(out)) * 0.42


def sfx_tick() -> np.ndarray:
    """Single keypress. Deliberately dry and very short."""
    rng_ = np.random.default_rng(41)
    t = t_axis(0.035)
    noise = rng_.normal(0, 1, len(t)) * np.exp(-t / 0.006)
    out = hp_fft(lp_fft(noise, 4200.0), 900.0)
    return out / max(np.max(np.abs(out)), 1e-9) * 0.20


def sfx_chime() -> np.ndarray:
    """Warm two-note resolve for the closing card."""
    dur = 1.9
    out = np.zeros(int(SR * dur))
    for freq, at, gain in ((note("C5"), 0.00, 0.5),
                           (note("G5"), 0.13, 0.42),
                           (note("E5"), 0.26, 0.30)):
        t = t_axis(dur - at)
        sig = (np.sin(2 * np.pi * freq * t)
               + 0.30 * np.sin(2 * np.pi * freq * 2 * t)
               + 0.10 * np.sin(2 * np.pi * freq * 3 * t))
        sig *= np.exp(-t / 0.75)
        place(out, sig, at, gain)
    out = lp_fft(out, 6000.0)
    return out / np.max(np.abs(out)) * 0.34


for name, fn in (("whoosh", sfx_whoosh), ("pop", sfx_pop),
                 ("tick", sfx_tick), ("chime", sfx_chime)):
    sig = fn()
    write_wav(OUT / f"{name}.wav", np.stack([sig, sig], axis=1))

print(f"\ndone -> {OUT}")
