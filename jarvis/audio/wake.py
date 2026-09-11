"""Local wake-word detection for Jarvis.

Design notes and hard facts (verified on this machine, sherpa-onnx 1.13.4):

* Backend is sherpa-onnx *keyword spotting* (KWS): a streaming transducer whose
  decoder is constrained to a keyword list. Nothing leaves the machine and no
  raw audio is ever written to disk by this module.
* The bundled English model is ``sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01``
  (the tarball's README declares Apache-2.0; its GigaSpeech training audio carries a
  research-only terms-of-access caveat - read docs/WAKE_MODEL_LICENCE.md before any
  commercial distribution). It is a BPE model, so every keyword must be tokenised with
  the model's own ``bpe.model``.
* The model's BPE vocabulary is UPPERCASE-only. Encoding a lowercase phrase
  yields pieces like ``['▁', 'hey', '▁', 'jarvis']`` which are *not* in
  ``tokens.txt``; sherpa-onnx then prints "Encode keywords failed" and dies.
  We therefore upper-case phrases before encoding and validate every piece
  against ``tokens.txt`` before we hand the file to sherpa-onnx.
* Keywords file line format (see
  https://k2-fsa.github.io/sherpa/onnx/kws/index.html):
  ``<bpe tokens separated by spaces> :<boosting score> #<trigger threshold> @<original>``
  Only the tokens are mandatory; ``:`` and ``#`` are optional per line and
  ``@`` records the surface form the model reports back to us.

Threading contract
------------------
* ``sounddevice`` calls :meth:`WakeWordDetector._audio_callback` on PortAudio's
  own high-priority thread. That callback does nothing but copy samples into a
  fixed-size ring buffer, so it never blocks and can never grow.
* Decoding happens on one worker thread (:meth:`WakeWordDetector._decode_loop`),
  which pulls whole blocks out of the ring, feeds the sherpa-onnx stream and
  emits detections.
* ``on_detect(keyword, score)`` is invoked **on the decode worker thread**, in
  the middle of the decode loop. It MUST NOT block: a slow callback stalls
  decoding, the bounded ring starts dropping the oldest audio, and wake-word
  responsiveness degrades (by design memory is never allowed to grow instead).
  Push work onto a queue/thread of your own if it can take longer than a few
  milliseconds.

Score caveat (please read before using ``score``)
-------------------------------------------------
sherpa-onnx 1.13.4 exposes **no per-detection acoustic confidence**. The C
struct ``SherpaOnnxKeywordResult`` carries only ``keyword``, ``tokens``,
``timestamps``, ``start_time`` and ``json``; the Python binding
``sherpa_onnx.lib._sherpa_onnx.KeywordResult`` likewise exposes only
``keyword``, ``tokens`` and ``timestamps``. The trigger is a boolean decision
inside the decoder (acoustic probability vs. the per-keyword ``#`` threshold).

Rather than invent a probability, ``score`` is the **trigger threshold the
decoder had to beat for this keyword** (i.e. the ``#`` value in the keywords
file). It is a truthful "how strict was the gate" number in [0, 1], not a
measurement of this utterance. Code that gates on ``score >= wake_threshold``
still behaves correctly; code that treats ``score`` as "confidence 0.22 is
weak" is misreading it. See ``SCORE_IS_TRIGGER_THRESHOLD``.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Callable, Optional, Sequence

__all__ = [
    "WakeWordDetector",
    "MODEL_NAME",
    "MODEL_URL",
    "SCORE_IS_TRIGGER_THRESHOLD",
    "default_model_dir",
    "ensure_model",
    "model_help",
]

#: Canonical English KWS model used by the app.
MODEL_NAME = "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01"

#: Download URL of the tarball this module expects on disk.
MODEL_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/"
    f"{MODEL_NAME}.tar.bz2"
)

#: True when ``score`` passed to ``on_detect`` is the keyword's trigger
#: threshold rather than an acoustic probability (see module docstring).
SCORE_IS_TRIGGER_THRESHOLD = True

#: Files that must exist for the model directory to count as usable.
_REQUIRED = ("tokens.txt", "bpe.model")

#: Blocks the decode worker feeds at a time. 100 ms at 16 kHz: small enough
#: to keep detection latency near the model's own 320 ms chunk-16 latency,
#: large enough that the ONNX graph is not called per-sample.
_DECODE_BLOCK_SAMPLES = 1600


def _base_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return Path(base) / "Jarvis"


def default_model_dir() -> Path:
    """Where the KWS model is expected when the caller passes ``model_dir=None``.

    Honours the ``config`` key ``wake_model_dir`` when it is set, otherwise
    ``%LOCALAPPDATA%\\Jarvis\\models\\kws\\<MODEL_NAME>``.
    """
    try:  # optional: keep this module importable without the rest of the app
        from .. import config as _config  # type: ignore

        configured = _config.get("wake_model_dir")
        if configured:
            return Path(configured).expanduser()
    except Exception:
        pass
    return _base_dir() / "models" / "kws" / MODEL_NAME


def model_help() -> str:
    return (
        f"Wake-word model '{MODEL_NAME}' is not installed.\n"
        f"Download it with:\n"
        f'  curl -L -o {MODEL_NAME}.tar.bz2 "{MODEL_URL}"\n'
        f"  tar xf {MODEL_NAME}.tar.bz2 -C {default_model_dir().parent}\n"
    )


def _required_files_present(root: Path) -> bool:
    if not root.is_dir():
        return False
    if not all((root / name).is_file() for name in _REQUIRED):
        return False
    # The encoder/joiner pair is what actually drives inference; a half-extracted
    # directory that happens to contain tokens.txt is not usable.
    return any(root.glob("*encoder*.onnx")) and any(root.glob("*joiner*.onnx"))


def ensure_model(model_dir: Optional[str] = None, *,
                 progress=None, timeout: float = 300.0) -> tuple[bool, str]:
    """Make sure the KWS model is on disk, downloading it if it is not.

    Returns ``(ok, detail)``. The tarball is ~20 MB and is deliberately **not**
    vendored in the repository, so a fresh install has to fetch it once before
    the wake word can work at all. Without this function the only route was a
    documented ``curl`` command, which meant a user could switch the wake word
    on, say the phrase, get nothing, and have no idea why.

    ``progress`` is called as ``progress(fraction, detail)`` if given.

    Downloads to a temporary directory and only moves the result into place once
    the required files are present, so an interrupted download can never leave a
    broken model behind that later reads as "installed but unusable".
    """
    # Imported here, not at module scope: the app imports this module on every
    # start, and almost every start has no downloading to do.
    import shutil
    import tarfile
    import tempfile
    import urllib.error
    import urllib.request

    target = Path(model_dir).expanduser() if model_dir else default_model_dir()

    if _required_files_present(target):
        return True, f"already installed at {target}"

    if target.exists() and not _required_files_present(target):
        return False, (f"{target} exists but is not a usable model "
                       f"(missing {', '.join(_REQUIRED)} or the onnx files). "
                       f"Delete it and try again.")

    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="jarvis-kws-", dir=str(target.parent)))
    archive = staging / f"{MODEL_NAME}.tar.bz2"

    try:
        if progress:
            progress(0.02, "contacting github.com")
        request = urllib.request.Request(
            MODEL_URL, headers={"User-Agent": "jarvis-wake-setup/1.0"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            total = int(response.headers.get("Content-Length") or 0)
            done = 0
            with open(archive, "wb") as fh:
                while True:
                    chunk = response.read(262144)
                    if not chunk:
                        break
                    fh.write(chunk)
                    done += len(chunk)
                    if progress:
                        frac = (done / total) if total else 0.0
                        progress(0.05 + 0.75 * min(frac, 1.0),
                                 f"{done/1048576:.1f} MB"
                                 + (f" of {total/1048576:.1f} MB" if total else ""))

        if progress:
            progress(0.82, "unpacking")
        with tarfile.open(archive, "r:bz2") as tar:
            # Refuse absolute paths and '..' — a release archive should not be
            # able to write outside the staging directory.
            for member in tar.getmembers():
                name = member.name.replace("\\", "/")
                if name.startswith("/") or ".." in name.split("/"):
                    return False, f"refused an unsafe path in the archive: {name!r}"
            tar.extractall(staging)

        # The archive contains a single top-level directory named MODEL_NAME.
        candidates = [staging / MODEL_NAME]
        candidates += [p for p in staging.iterdir()
                       if p.is_dir() and p.name != MODEL_NAME]
        source = next((c for c in candidates if _required_files_present(c)), None)
        if source is None:
            return False, ("the downloaded archive did not contain a usable "
                           f"model (looked for {', '.join(_REQUIRED)} and the "
                           "onnx files)")

        if progress:
            progress(0.92, "installing")
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        shutil.move(str(source), str(target))

        if not _required_files_present(target):
            return False, f"installed to {target} but it still looks incomplete"

        size_mb = sum(f.stat().st_size for f in target.rglob("*")
                      if f.is_file()) / 1048576
        if progress:
            progress(1.0, "done")
        return True, f"installed {MODEL_NAME} ({size_mb:.1f} MB) to {target}"

    except urllib.error.HTTPError as exc:
        return False, f"download failed: HTTP {exc.code} from {MODEL_URL}"
    except urllib.error.URLError as exc:
        return False, f"download failed: {exc.reason} (is there a network?)"
    except Exception as exc:  # noqa: BLE001 - surfaced to the user verbatim
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _pick(model_dir: Path, stem: str, prefer_int8: bool) -> Optional[Path]:
    """Pick encoder/decoder/joiner for ``stem``, preferring int8 or fp32."""
    candidates = sorted(model_dir.glob(f"{stem}-*.onnx"))
    if not candidates:
        return None
    int8 = [p for p in candidates if p.name.endswith(".int8.onnx")]
    fp32 = [p for p in candidates if not p.name.endswith(".int8.onnx")]
    order = (int8 + fp32) if prefer_int8 else (fp32 + int8)
    return order[0]


class _Ring:
    """Fixed-capacity sample ring.

    Memory is allocated once (``capacity`` float32 samples) and never grows:
    when the writer outruns the reader the *oldest* samples are dropped and
    ``overflows`` is incremented. Safe for one writer + one reader.
    """

    def __init__(self, capacity: int) -> None:
        import numpy as np

        self.capacity = int(capacity)
        self.buf = np.zeros(self.capacity, dtype=np.float32)
        self.head = 0
        self.size = 0
        self.lock = threading.Lock()
        self.overflows = 0

    def write(self, samples) -> None:
        import numpy as np

        if not isinstance(samples, np.ndarray):
            samples = np.asarray(samples, dtype=np.float32)
        n = int(samples.size)
        if n == 0:
            return
        if n >= self.capacity:
            samples = samples[-self.capacity:]
            n = self.capacity
        with self.lock:
            if self.size + n > self.capacity:
                drop = self.size + n - self.capacity
                self.head = (self.head + drop) % self.capacity
                self.size -= drop
                self.overflows += 1
            end = (self.head + self.size) % self.capacity
            first = min(n, self.capacity - end)
            self.buf[end:end + first] = samples[:first]
            if n > first:
                self.buf[:n - first] = samples[first:]
            self.size += n

    def read(self, want: int):
        """Return up to ``want`` samples (FIFO), or ``None`` when starved."""
        import numpy as np

        with self.lock:
            n = min(int(want), self.size)
            if n <= 0:
                return None
            out = np.empty(n, dtype=np.float32)
            first = min(n, self.capacity - self.head)
            out[:first] = self.buf[self.head:self.head + first]
            if n > first:
                out[first:] = self.buf[:n - first]
            self.head = (self.head + n) % self.capacity
            self.size -= n
            return out

    def clear(self) -> None:
        with self.lock:
            self.head = 0
            self.size = 0


class WakeWordDetector:
    """Always-on local keyword spotter.

    Typical use::

        det = WakeWordDetector(cfg["wake_phrases"], cfg["wake_model_dir"],
                               threshold=cfg["wake_threshold"],
                               on_detect=lambda kw, score: ...)
        if WakeWordDetector.is_available(cfg["wake_model_dir"]):
            det.start()      # opens the mic
        ...
        det.pause()          # while dictating
        det.resume()
        det.stop()           # releases the mic within ~1 s

    ``on_detect`` runs on the detector's decode thread and MUST NOT block.
    """

    MODEL_NAME = MODEL_NAME

    def __init__(
        self,
        phrases: Sequence[str],
        model_dir: Optional[str],
        threshold: float = 0.22,
        sample_rate: int = 16000,
        on_detect: Optional[Callable[[str, float], None]] = None,
        logger: Optional[logging.Logger] = None,
        *,
        boosting_score: float = 2.0,
        num_threads: int = 1,
        provider: str = "cpu",
        device: Optional[int] = None,
        prefer_int8: bool = True,
        max_active_paths: int = 4,
        num_trailing_blanks: int = 1,
        cooldown_s: float = 0.25,
        blocksize_ms: int = 100,
        ring_seconds: float = 3.0,
    ) -> None:
        self._phrases = [str(p).strip() for p in (phrases or []) if str(p).strip()]
        self._model_dir = Path(model_dir).expanduser() if model_dir else default_model_dir()
        self.threshold = float(threshold)
        self.sample_rate = int(sample_rate)
        self.on_detect = on_detect
        self.device = device
        self.cooldown_s = float(cooldown_s)

        self._boosting_score = float(boosting_score)
        self._num_threads = max(1, int(num_threads))
        self._provider = str(provider or "cpu")
        self._prefer_int8 = bool(prefer_int8)
        self._max_active_paths = int(max_active_paths)
        self._num_trailing_blanks = int(num_trailing_blanks)

        if logger is not None:
            self._log = logger
        else:
            try:
                from .. import logsetup  # type: ignore

                self._log = logsetup.get("wake")
            except Exception:
                self._log = logging.getLogger("jarvis.wake")

        block = max(1, int(self.sample_rate * max(50, int(blocksize_ms)) / 1000))
        self._block_samples = block
        self._decode_block = max(_DECODE_BLOCK_SAMPLES, block)
        self._ring = _Ring(max(self._decode_block * 4, int(ring_seconds * self.sample_rate)))

        self._spotter = None            # sherpa_onnx.KeywordSpotter
        self._stream = None             # sherpa_onnx OnlineStream
        self._sd_stream = None          # sounddevice.InputStream
        self._stream_rate = float(self.sample_rate)
        self._worker: Optional[threading.Thread] = None
        self._run = threading.Event()
        self._paused = threading.Event()
        self._lock = threading.RLock()

        self._keywords_file: Optional[Path] = None
        self._keyword_lookup: dict[str, str] = {}   # model keyword -> app phrase
        self._keyword_threshold: dict[str, float] = {}

        self._samples_fed = 0
        self.last_detect_at: Optional[float] = None
        self.last_detect_phrase: Optional[str] = None
        self.last_detect_latency_s: Optional[float] = None
        self.last_detect_info: Optional[dict] = None
        self.detect_count = 0

    # ------------------------------------------------------------ probes

    @staticmethod
    def _parts(model_dir: Path, prefer_int8: bool = True) -> Optional[dict]:
        if not model_dir.is_dir():
            return None
        for name in _REQUIRED:
            if not (model_dir / name).is_file():
                return None
        parts = {
            "tokens": model_dir / "tokens.txt",
            "bpe_model": model_dir / "bpe.model",
            "encoder": _pick(model_dir, "encoder", prefer_int8),
            "decoder": _pick(model_dir, "decoder", prefer_int8=False),
            "joiner": _pick(model_dir, "joiner", prefer_int8),
        }
        if not all(parts.get(k) for k in ("encoder", "decoder", "joiner")):
            return None
        return parts

    @staticmethod
    def is_available(model_dir: Optional[str] = None) -> bool:
        """True when a complete KWS model is on disk at ``model_dir``.

        Never raises: any problem (missing directory, missing tokens/bpe, no
        onnx files, unreadable path) simply reports False, which is what keeps
        push-to-talk-only mode working.
        """
        try:
            d = Path(model_dir).expanduser() if model_dir else default_model_dir()
            return WakeWordDetector._parts(Path(d)) is not None
        except Exception:
            return False

    @property
    def running(self) -> bool:
        """True while the decode thread is live (microphone or pushed audio)."""
        return bool(
            self._run.is_set()
            and self._worker is not None
            and self._worker.is_alive()
        )

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    @property
    def keywords_path(self) -> Optional[Path]:
        return self._keywords_file

    # ------------------------------------------------- keyword tokenising

    @staticmethod
    def _load_token_table(tokens_path: Path) -> set:
        table = set()
        with open(tokens_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    table.add(line.split()[0])
        return table

    def _build_keywords_file(self, parts: dict) -> tuple[Path, dict, dict]:
        """Tokenise ``self._phrases`` into a sherpa-onnx keywords file.

        Returns ``(path, {model_keyword: app_phrase}, {model_keyword: threshold})``.
        Raises RuntimeError when nothing usable can be produced.
        """
        if not self._phrases:
            raise RuntimeError("wake phrases: none configured")

        try:
            import sentencepiece as spm  # dependency of sherpa-onnx itself
        except Exception as exc:  # pragma: no cover - environment problem
            raise RuntimeError(
                "sentencepiece is required to tokenise BPE wake phrases "
                "(pip install sentencepiece)"
            ) from exc

        spp = spm.SentencePieceProcessor(model_file=str(parts["bpe_model"]))
        vocab = self._load_token_table(parts["tokens"])

        lines: list[str] = []
        lookup: dict[str, str] = {}
        thresholds: dict[str, float] = {}
        skipped: list[str] = []

        for phrase in self._phrases:
            # The BPE vocabulary of this model is upper-case only.
            pieces = spp.encode_as_pieces(phrase.upper())
            missing = [p for p in pieces if p not in vocab]
            if missing:
                skipped.append(f"{phrase!r} (tokens not in tokens.txt: {missing})")
                continue
            original = phrase.replace(" ", "_")
            lines.append(
                " ".join(pieces)
                + f" :{self._boosting_score:.2f}"
                + f" #{self.threshold:.4f}"
                + f" @{original}"
            )
            lookup[original] = phrase
            thresholds[original] = self.threshold

        if skipped:
            self._log.warning("wake: skipping untokenisable phrase(s): %s", "; ".join(skipped))
        if not lines:
            raise RuntimeError(
                "wake phrases could not be tokenised by the model BPE "
                f"(tried: {self._phrases}); see docs/WAKE_WORD_NOTES.md"
            )

        target = self._model_dir / "keywords.app.txt"
        body = "\n".join(lines) + "\n"
        try:
            if not target.is_file() or target.read_text(encoding="utf-8") != body:
                tmp = target.with_suffix(".txt.tmp")
                tmp.write_text(body, encoding="utf-8")
                os.replace(tmp, target)
        except OSError as exc:
            raise RuntimeError(f"cannot write keywords file {target}: {exc}") from exc

        self._log.info(
            "wake: keywords file %s (%d phrase(s)):\n%s", target, len(lines), body.rstrip()
        )
        return target, lookup, thresholds

    # ------------------------------------------------------------ lifecycle

    def _ensure_spotter(self) -> None:
        if self._spotter is not None:
            return
        parts = self._parts(self._model_dir, self._prefer_int8)
        if parts is None:
            raise RuntimeError(
                f"wake word model not usable at '{self._model_dir}'. {model_help()}"
            )
        try:
            import sherpa_onnx
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(
                "sherpa-onnx is required for local wake words (pip install sherpa-onnx)"
            ) from exc

        kw_file, lookup, thresholds = self._build_keywords_file(parts)
        kwargs = dict(
            tokens=str(parts["tokens"]),
            encoder=str(parts["encoder"]),
            decoder=str(parts["decoder"]),
            joiner=str(parts["joiner"]),
            keywords_file=str(kw_file),
            num_threads=self._num_threads,
            sample_rate=int(self.sample_rate),
            max_active_paths=self._max_active_paths,
            keywords_score=self._boosting_score,
            keywords_threshold=self.threshold,
            num_trailing_blanks=self._num_trailing_blanks,
            provider=self._provider,
        )
        if self.device is not None:
            kwargs["device"] = int(self.device)
        try:
            self._spotter = sherpa_onnx.KeywordSpotter(**kwargs)
        except Exception as exc:
            raise RuntimeError(f"could not create KeywordSpotter: {exc}") from exc
        self._keywords_file = kw_file
        self._keyword_lookup = lookup
        self._keyword_threshold = thresholds
        self._log.info(
            "wake: model ready dir=%s encoder=%s fmt=%s",
            self._model_dir, parts["encoder"].name,
            "int8" if parts["encoder"].name.endswith(".int8.onnx") else "fp32",
        )

    def start(self, microphone: bool = True) -> None:
        """Begin detecting. Raises RuntimeError when the model is unusable.

        With the default ``microphone=True`` the default input device is opened
        and PortAudio pushes audio in. ``microphone=False`` skips PortAudio
        entirely (no sounddevice import, no device access) and instead expects
        audio from :meth:`push_audio` - used for replaying files or feeding a
        virtual device. ``start()`` with no arguments behaves exactly as
        specified for the app.
        """
        with self._lock:
            if self.running:
                self._log.debug("wake: start() ignored, already running")
                return
            self._ensure_spotter()

            sd = None
            if microphone:
                try:
                    import sounddevice as sd
                except Exception as exc:
                    raise RuntimeError(
                        "sounddevice is required to open the microphone "
                        "(pip install sounddevice)"
                    ) from exc

            self._ring.clear()
            self._stream = self._spotter.create_stream()
            self._sd_stream = None
            if microphone:
                self._sd_stream = self._open_input(sd)
            else:
                # File/virtual-source mode: samples arrive via push_audio().
                self._stream_rate = float(self.sample_rate)
                self._decode_block = max(_DECODE_BLOCK_SAMPLES, self._block_samples)

            self._paused.clear()
            self._run.set()
            self._worker = threading.Thread(
                target=self._decode_loop, name="jarvis-wake", daemon=True
            )
            self._worker.start()
            self._log.info(
                "wake: listening (%d Hz in -> %d Hz model, phrases=%s, mic=%s)",
                int(self._stream_rate), self.sample_rate, self._phrases, microphone,
            )

    def push_audio(self, samples, sample_rate: Optional[int] = None) -> None:
        """Feed audio as if it had come from the capture device.

        ``samples`` must be a 1-D mono array (float32 in [-1, 1], or int16, which
        is converted). ``sample_rate`` defaults to :attr:`sample_rate`; if it
        differs from the rate the stream is currently being fed at, the audio is
        resampled by linear interpolation so detection timings stay correct.

        Only valid while the detector is running. Nothing is written to disk.
        """
        import numpy as np

        if not self.running:
            raise RuntimeError("push_audio() requires a running detector")
        x = np.asarray(samples)
        if x.ndim > 1:
            x = x[:, 0]
        if x.dtype == np.int16:
            x = x.astype(np.float32) / 32768.0
        elif x.dtype != np.float32:
            x = x.astype(np.float32)
        rate = float(sample_rate or self.sample_rate)
        if rate != float(self._stream_rate) and x.size > 1:
            n_out = max(1, int(round(x.size * float(self._stream_rate) / rate)))
            src = np.arange(x.size, dtype=np.float64)
            dst = np.linspace(0.0, x.size - 1.0, n_out)
            x = np.interp(dst, src, x).astype(np.float32)
        self._ring.write(x)

    def _open_input(self, sd):
        """Open a mono float32 input stream, falling back to the device rate.

        The sherpa-onnx OnlineStream resamples whatever rate we feed it to the
        model rate, so opening at the device's native rate is safe and avoids
        driver-level sampling-rate conversion failures.
        """
        err: Optional[Exception] = None
        for rate in (self.sample_rate, None):
            try:
                if rate is None:
                    info = sd.query_devices(self.device, "input")
                    rate = int(info["default_samplerate"])
                stream = sd.InputStream(
                    samplerate=rate,
                    blocksize=max(1, int(rate * 0.1)),
                    channels=1,
                    dtype="float32",
                    device=self.device,
                    callback=self._audio_callback,
                )
                stream.start()
                self._stream_rate = float(rate)
                self._decode_block = max(_DECODE_BLOCK_SAMPLES, int(rate * 0.1))
                return stream
            except Exception as exc:  # try the device's own rate next
                err = exc
                self._log.debug("wake: input at %s Hz failed: %s", rate, exc)
        raise RuntimeError(f"cannot open the default microphone: {err}")

    def _audio_callback(self, indata, frames, time_info, status) -> None:  # PortAudio thread
        if status:
            self._log.debug("wake: sounddevice status %s", status)
        # Copy only; never block this thread.
        self._ring.write(indata[:, 0])

    def _decode_loop(self) -> None:
        import numpy as np

        spotter = self._spotter
        stream = self._stream
        last_fire = 0.0
        fed = 0
        # Latency can only be measured against the stream's own timeline. After
        # the first reset_stream() the decoder's timestamp epoch restarts, so we
        # only report the derived latency for the first detection of a stream.
        first_detection = True
        try:
            while self._run.is_set():
                if self._paused.is_set():
                    self._ring.clear()
                    time.sleep(0.02)
                    continue
                block = self._ring.read(self._decode_block)
                if block is None or len(block) == 0:
                    # ~5 ms idle sleep keeps the core near-zero while silent.
                    time.sleep(0.005)
                    continue
                if block.dtype != np.float32:
                    block = block.astype(np.float32, copy=False)
                stream.accept_waveform(self._stream_rate, block)
                fed += len(block)
                while spotter.is_ready(stream):
                    spotter.decode_stream(stream)
                    # NOTE: the raw binding must be read exactly once per
                    # result - the python wrapper's get_result() consumes it,
                    # so calling it twice returns an empty keyword.
                    res = spotter.keyword_spotter.get_result(stream)
                    key = (res.keyword or "").strip()
                    if not key:
                        continue
                    spotter.reset_stream(stream)
                    now = time.perf_counter()
                    if now - last_fire < self.cooldown_s:
                        self._log.debug("wake: suppressed duplicate %s", key)
                        continue
                    last_fire = now
                    latency = None
                    if first_detection:
                        try:
                            if res.timestamps:
                                latency = max(
                                    0.0, fed / self.sample_rate - float(res.timestamps[-1])
                                )
                        except Exception:
                            latency = None
                    first_detection = False
                    self._fire(key, res, latency, now, fed / self.sample_rate)
        except Exception:
            self._log.exception("wake: decode loop crashed; detector stopped")
            self._run.clear()
        finally:
            if self._ring.overflows:
                self._log.warning(
                    "wake: ring overflowed %d time(s) while decoding was busy",
                    self._ring.overflows,
                )

    def _fire(
        self,
        model_keyword: str,
        res,
        latency: Optional[float],
        now: float,
        fed_s: float,
    ) -> None:
        phrase = self._keyword_lookup.get(model_keyword, model_keyword)
        score = self._keyword_threshold.get(model_keyword, self.threshold)
        self.detect_count += 1
        self.last_detect_at = now
        self.last_detect_phrase = phrase
        self.last_detect_latency_s = latency
        self.last_detect_info = {
            "keyword": phrase,
            "model_keyword": model_keyword,
            "score": score,
            "tokens": list(getattr(res, "tokens", []) or []),
            "timestamps": list(getattr(res, "timestamps", []) or []),
            "fed_seconds": fed_s,
            "latency_s": latency,
        }
        self._log.info(
            "wake: detected %r (model=%r tokens=%s word_end=%.2fs fed=%.2fs latency=%s)",
            phrase, model_keyword, list(getattr(res, "tokens", []) or []),
            self.last_detect_info["timestamps"][-1] if self.last_detect_info["timestamps"] else -1.0,
            fed_s,
            "n/a" if latency is None else f"{latency*1000:.0f}ms",
        )
        cb = self.on_detect
        if cb is None:
            return
        try:
            cb(phrase, score)
        except Exception:
            self._log.exception("wake: on_detect callback raised; ignored")

    def pause(self) -> None:
        """Suspend detection without releasing the microphone."""
        self._paused.set()
        self._ring.clear()
        self._log.debug("wake: paused")

    def resume(self) -> None:
        """Resume detection after :meth:`pause`."""
        if not self._paused.is_set():
            return
        self._paused.clear()
        self._ring.clear()
        if self._stream is not None:
            try:
                self._spotter.reset_stream(self._stream)
            except Exception:
                self._log.debug("wake: reset_stream on resume failed", exc_info=True)
        self._log.debug("wake: resumed")

    def stop(self, timeout: float = 1.0) -> None:
        """Stop detection and release the microphone (target: < ~1 s)."""
        with self._lock:
            self._run.clear()
            self._paused.clear()
            sd_stream, self._sd_stream = self._sd_stream, None
            if sd_stream is not None:
                try:
                    sd_stream.stop()
                except Exception:
                    self._log.debug("wake: stop() on stream failed", exc_info=True)
                try:
                    sd_stream.close()
                except Exception:
                    self._log.debug("wake: close() on stream failed", exc_info=True)
            worker, self._worker = self._worker, None
            if worker is not None and worker.is_alive():
                worker.join(timeout=timeout)
                if worker.is_alive():
                    self._log.warning("wake: decode thread did not exit within %.1fs", timeout)
            self._stream = None
            self._ring.clear()
            self._log.info("wake: stopped")

    def __enter__(self) -> "WakeWordDetector":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()
