"""Audio sample-rate conversion.

Live voice requires a fixed rate (24 kHz PCM16 by default, per the documented
audio formats), while the capture device may not offer it. PortAudio/WASAPI will
usually convert for us, but not on every host, so we can also convert ourselves.

Two implementations, because the two cases have different constraints:

  * `resample_offline` - FFT-based, high quality, for a complete recording.
    Used when we must convert a finished utterance.

  * `StreamingResampler` - 4-point cubic Hermite interpolation with carried
    state, for real-time audio where we cannot look ahead. Cheap enough to run
    inside the audio callback.

Both are pure numpy and produce little-endian signed 16-bit PCM, matching the
`audio/pcm` format the Live API documents.
"""

from __future__ import annotations

import numpy as np

PCM16 = "<i2"


def _to_float(data: bytes) -> np.ndarray:
    if not data:
        return np.zeros(0, dtype=np.float32)
    usable = len(data) - (len(data) % 2)
    if usable <= 0:
        return np.zeros(0, dtype=np.float32)
    return np.frombuffer(data[:usable], dtype=PCM16).astype(np.float32) / 32768.0


def _to_pcm16(samples: np.ndarray) -> bytes:
    clipped = np.clip(samples, -1.0, 1.0)
    return (clipped * 32767.0).astype(PCM16).tobytes()


def resample_offline(data: bytes, src_rate: int, dst_rate: int) -> bytes:
    """High-quality resample of a complete PCM16 buffer (FFT method).

    FFT resampling assumes the signal is periodic over the buffer, so we
    taper the edges to avoid a click at the seam. For a short dictation
    utterance this is inaudible and keeps the spectrum clean.
    """
    if src_rate == dst_rate or not data:
        return data
    x = _to_float(data)
    if x.size == 0:
        return b""
    n_out = max(1, int(round(x.size * dst_rate / float(src_rate))))

    taper = min(x.size, 64)
    if taper > 1:
        ramp = np.linspace(0.0, 1.0, taper, dtype=np.float32)
        x = x.copy()
        x[:taper] *= ramp
        x[-taper:] *= ramp[::-1]

    spec = np.fft.rfft(x)
    new_len = n_out // 2 + 1
    if new_len <= 1:
        return _to_pcm16(np.zeros(n_out, dtype=np.float32))
    # Rate conversion keeps the signal's DURATION, so a given frequency lands on
    # the SAME bin in both spectra: bin = freq * samples / rate, and both the
    # sample count and the rate scale by the same factor, so samples/rate (the
    # duration) is unchanged. We therefore map new bin j -> old bin j, exactly,
    # and leave any new bins above the source Nyquist as zero (there is no
    # content up there to preserve). Scaling by the length ratio keeps the
    # time-domain amplitude constant.
    idx = np.arange(new_len, dtype=np.float64)
    old_bins = np.arange(spec.size, dtype=np.float64)
    real = np.interp(idx, old_bins, spec.real, left=0.0, right=0.0)
    imag = np.interp(idx, old_bins, spec.imag, left=0.0, right=0.0)
    new_spec = (real + 1j * imag) * (n_out / float(x.size))
    y = np.fft.irfft(new_spec, n=n_out)
    return _to_pcm16(y.astype(np.float32))


class StreamingResampler:
    """Real-time rate conversion with cubic Hermite interpolation."""

    def __init__(self, src_rate: int, dst_rate: int):
        self.src_rate = int(src_rate)
        self.dst_rate = int(dst_rate)
        self.ratio = self.src_rate / float(self.dst_rate) if dst_rate else 1.0
        self._tail = np.zeros(0, dtype=np.float32)
        self._phase = 0.0
        self._carry = b""

    @property
    def passthrough(self) -> bool:
        return self.src_rate == self.dst_rate

    def process(self, data: bytes) -> bytes:
        """Feed PCM16 bytes, get converted PCM16 bytes back (may be delayed)."""
        if self.passthrough or not data:
            return data
        # Re-attach a byte held over from the previous odd-length chunk, else the
        # sample stream would drop a byte and lose 16-bit alignment.
        if self._carry:
            data = self._carry + data
            self._carry = b""
        if len(data) % 2:
            data, self._carry = data[:-1], data[-1:]
        x = _to_float(data)
        if x.size == 0:
            return b""
        buf = np.concatenate([self._tail, x])
        # Need 2 samples either side for cubic interpolation.
        if buf.size < 4:
            self._tail = buf
            return b""

        positions: list[float] = []
        p = self._phase
        limit = buf.size - 3.0
        while p < limit:
            positions.append(p)
            p += self.ratio
        self._phase = p - (buf.size - 3.0) if p > limit else p
        if p > limit:
            # Advance the buffer past fully consumed samples.
            consumed = int(np.floor(limit))
            consumed = max(0, min(consumed, buf.size - 3))
            self._tail = buf[consumed:]
            self._phase = p - consumed
        else:
            self._tail = buf

        if not positions:
            return b""

        pos = np.array(positions, dtype=np.float32)
        i0 = np.floor(pos).astype(np.int32)
        t = (pos - i0).astype(np.float32)
        y0 = buf[i0]
        y1 = buf[i0 + 1]
        y2 = buf[i0 + 2]
        y3 = buf[i0 + 3]
        # Cubic Hermite basis.
        a = -0.5 * y0 + 1.5 * y1 - 1.5 * y2 + 0.5 * y3
        b = y0 - 2.5 * y1 + 2.0 * y2 - 0.5 * y3
        c = -0.5 * y0 + 0.5 * y2
        d = y1
        out = ((a * t + b) * t + c) * t + d
        return _to_pcm16(out.astype(np.float32))
