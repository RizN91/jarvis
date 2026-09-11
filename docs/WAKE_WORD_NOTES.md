# Wake word (sherpa-onnx KWS) — implementation notes and raw evidence

Status: **working and verified on this machine** for file-based input. The microphone
path opens and runs against the real device; the *acoustic* round-trip (speaker →
microphone) could **not** be demonstrated here and is listed as a blocker below.
Nothing in this document is inferred where a measurement exists.

Author/agent run date: 2026-09-11. Host: Windows 11, Intel i7-11800H (16 logical
cores), Python 3.11.16, `sherpa_onnx` 1.13.4, numpy 2.4.3, sounddevice 0.5.5,
psutil 7.2.2.

## 1. Model

* Name / release asset / URL / sha256 / sizes: see `WAKE_MODEL_LICENCE.md`.
* Licence: **declared `Apache License 2.0`** in the tarball's own `README.md`
  front-matter (no LICENSE file ships). Training data (GigaSpeech XL) carries a
  research/education-only terms-of-access caveat — read `WAKE_MODEL_LICENCE.md`
  before commercial use.
* Installed with (from a bash shell; MSYS path translation is off on this host, and
  GNU tar needs `--force-local` for `C:\…` archive paths):

```bash
curl -sL -o kws-gigaspeech.tar.bz2 \
  https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01.tar.bz2
sha256sum kws-gigaspeech.tar.bz2      # f170013b... = official checksum.txt value
tar --force-local -xf kws-gigaspeech.tar.bz2 -C "%LOCALAPPDATA%/Jarvis/models/kws"
```

* On disk: `%LOCALAPPDATA%\Jarvis\models\kws\sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01\`
  — 20,126,725 bytes (19.2 MiB) total. `wake.py` picks the int8 encoder + int8 joiner
  + fp32 decoder (the combination the sherpa-onnx docs recommend); that is
  `encoder-…int8.onnx` 4,807,159 B, `joiner-…int8.onnx` 163,380 B,
  `decoder-….onnx` 1,063,189 B, `tokens.txt` 5,006 B, `bpe.model` 244,837 B.

## 2. Code

| file | what it is |
| --- | --- |
| `jarvis/audio/__init__.py` | package marker (new) |
| `jarvis/audio/wake.py` | `WakeWordDetector` — the whole feature (new, ~660 lines) |
| `docs/wake_tests/` | the verification harness and its captured raw output (new) |

Public interface, exactly as specified: `__init__(phrases, model_dir, threshold=0.22,
sample_rate=16000, on_detect=None, logger=None)`, `is_available(model_dir=None)`
(static), `start()`, `stop()`, `pause()`, `resume()`, `running`. Two additions:
`start(microphone=True)` (pass `False` to drive the detector from a file/virtual
source instead of PortAudio) and `push_audio(samples, sample_rate=None)`.

Design points that matter:

* **Bounded ring buffer.** A fixed numpy ring (`ring_seconds` worth of float32, 3 s
  default ≈ 192 KB) is allocated once. PortAudio's callback does nothing but copy into
  it. If decoding falls behind, the **oldest** audio is discarded and an overflow
  counter is bumped — memory cannot grow. Measured `overflows=0` in 12 s of continuous
  decode.
* **No audio ever touches disk.** The detector reads from the device and hands samples
  to the ONNX graph; the only file it writes is the tiny tokenised keywords list
  (`keywords.app.txt`) inside the model directory.
* **`on_detect(keyword, score)` runs on the decode worker thread**, mid-loop. It must
  not block: a slow callback stalls decoding and the ring starts dropping audio. This
  is documented in the module docstring and on the call site.
* **`stop()` releases the microphone in 100 ms measured** (spec: ~1 s).
* **Missing model ⇒ `is_available()` False, `start()` raises `RuntimeError`** with a
  message that includes the exact download commands. The app can therefore stay
  push-to-talk-only; nothing else in the app needs to change.

### The keywords file format (this is where an evening goes if you guess)

Verified against <https://k2-fsa.github.io/sherpa/onnx/kws/index.html> and against the
model's own `keywords.txt` / `test_wavs/test_keywords.txt`:

```
<bpe tokens, space separated> :<boosting score> #<trigger threshold> @<original>
▁HE Y ▁JA R VI S :2.00 #0.2200 @hey_jarvis
```

Only the tokens are mandatory. `:` is the boosting score (larger = easier to trigger),
`#` is the per-keyword trigger threshold (0–1, larger = harder), `@` is the surface form
the model reports back — we set it (with spaces replaced by `_`) so `get_result()` hands
us `hey_jarvis`, which we map back to the configured phrase `hey jarvis`.

Two hard-won facts:

1. **This BPE vocabulary is UPPERCASE-only.** `bpe.model` encodes `hey jarvis` as
   `['▁','hey','▁','jarvis']` — `hey` and `jarvis` are *not* in `tokens.txt`. sherpa-onnx
   then prints `Cannot find ID for token hey … Encode keywords failed` and the process
   dies. `wake.py` therefore upper-cases every phrase **and** validates every BPE piece
   against `tokens.txt` before the file is handed to sherpa-onnx, skipping (with a
   logged warning) any phrase that cannot be tokenised. `HEY JARVIS` →
   `['▁HE','Y','▁JA','R','VI','S']`, all present in the vocab.
2. **`sherpa-onnx-cli text2token` is unusable for the BPE path here** because
   `sherpa_onnx.utils.text2token` imports `pypinyin` unconditionally (used only by the
   pinyin token types) and `pypinyin` is not installed. `wake.py` therefore calls
   `sentencepiece` directly (`spm.SentencePieceProcessor(model_file=bpe.model).encode_as_pieces(phrase.upper())`),
   which is exactly what the BPE branch of `text2token` does. `sentencepiece` is already
   present as a dependency of `sherpa-onnx`. **No new packages were pip-installed.**

### The `score` argument is not a confidence — read this

sherpa-onnx 1.13.4 exposes **no per-detection acoustic confidence**. The C struct
`SherpaOnnxKeywordResult` carries only `keyword`, `tokens`, `tokens_arr`, `count`,
`timestamps`, `start_time`, `json`; the Python object
(`sherpa_onnx.lib._sherpa_onnx.KeywordResult`) exposes only `keyword`, `tokens`,
`timestamps`. The trigger is a boolean decision inside the decoder (acoustic probability
vs. the per-keyword `#` threshold).

Rather than fabricate a probability, `wake.py` passes **the trigger threshold that the
decoder had to beat** as `score`, and sets the module constant
`SCORE_IS_TRIGGER_THRESHOLD = True` so the caller can tell. With the app's default
`wake_threshold = 0.22` that is why every detection logs `score=0.22`. Code that gates
on `score >= wake_threshold` behaves correctly; code that treats 0.22 as "weak
confidence" is misreading it. If a genuine confidence is ever required, it has to come
from a fork of sherpa-onnx that surfaces the decoder probability.

### Timestamps after a reset

`sherpa_onnx.KeywordSpotter.get_result()` **consumes** the result (a second call returns
an empty keyword), so `wake.py` calls the raw binding
(`spotter.keyword_spotter.get_result(stream)`) exactly once per decode step and reads
`.keyword` from it. After `reset_stream()` the decoder's timestamp epoch restarts, so the
derived latency is only reported for the **first** detection in a stream
(`latency=None` afterwards, and the raw values are still logged).

## 3. Verification evidence

Harness: `docs/wake_tests/verify_files.py` (file replay), `verify_mic.py` (mic, latency,
CPU/RSS), `decide_latency.py`, `dup_check.py`, `dup_check2.py`, `acoustic_route2.py`.
Raw output: `docs/wake_tests/run1.txt`, `run2_final.txt`, `run_latency.txt`,
`run_decide.txt`, `run_cpu.txt`, `run_mic.txt`.

File replay does **not** bypass the production path: the only thing replaced is the
capture device — samples go through `push_audio()` into the same ring buffer PortAudio
writes into, and then through the real keywords file, real `KeywordSpotter`, real decode
loop, real cooldown and the real `on_detect` callback.

### 3.1 `is_available()`

| argument | result |
| --- | --- |
| `None` (→ default model dir) | `True` |
| explicit model dir | `True` |
| `C:/nope/not/here` | `False` |
| `jarvis/docs` (exists, no model) | `False` |
| a temp dir with no model | `False` |

### 3.2 Detections from real audio files

Detector A: phrases `['hey jarvis','hey gpt']`, threshold 0.22, generated keywords file:

```
▁HE Y ▁JA R VI S :2.00 #0.2200 @hey_jarvis
▁HE Y ▁G P T :2.00 #0.2200 @hey_gpt
```

Speech used: Windows SAPI TTS (`Microsoft Zira Desktop` en-US, `Microsoft Hazel Desktop`
en-GB) rendered to 16 kHz mono WAV — synthesised speech, not a human recording.

| wav | audio | detections | keyword | score |
| --- | --- | --- | --- | --- |
| `tts_jarvis_hazel16k.wav` ("Hey Jarvis") | 1.85 s | **1** | `hey jarvis` | 0.22 |
| `tts_jarvis_zira16k.wav` ("Hey Jarvis" ×2) | 4.82 s | **2** | `hey jarvis`, `hey jarvis` | 0.22, 0.22 |
| `tts_jarvis_gpt_zira16k.wav` ("Hey GPT" ×2) | 3.20 s | **2** | `hey gpt`, `hey gpt` | 0.22, 0.22 |

Detector B: phrases `['light up','lovely child','forever']` against **the model's own
`test_wavs`**, i.e. the files the sherpa-onnx docs use:

| wav | detections | keyword | expected per sherpa-onnx docs | score |
| --- | --- | --- | --- | --- |
| `test_wavs/0.wav` | **1** | `light up` | `LIGHT UP` | 0.22 |
| `test_wavs/1.wav` | **2** | `lovely child`, `forever` | `LOVELY CHILD`, `FOREVER` | 0.22, 0.22 |

Token-by-token, this reproduces the published demo output: our `0.wav` hit carries
tokens `[' ', 'L', 'IGHT', ' UP']` with last timestamp 3.16 s (docs: `[3.04, 3.08,
3.12, 3.16]`), and our `1.wav` `lovely child` hit carries `[' LOVE','LY',' CHI','L','D']`
ending at 6.04 s (docs: `[5.44, 5.56, 5.84, 6.00, 6.04]`). Same keywords, same
timestamps — independent confirmation that the pipeline is wired correctly.

### 3.3 Negatives (what did *not* trigger)

| input | detector | detections |
| --- | --- | --- |
| `test_wavs/0.wav`, `test_wavs/1.wav` (English audiobook speech) | A (`hey jarvis`/`hey gpt`) | 0, 0 |
| 6 s of digital silence | A | 0 |
| the three "hey jarvis / hey gpt" TTS files | B (`light up`/`lovely child`/`forever`) | 0 |
| 12 s of live microphone audio (quiet room) | A, real mic | 0 |

No false trigger was observed in any negative case, including 12 s of real room audio.

### 3.4 Threshold behaviour

`test_wavs/0.wav`, phrase `light up`, re-tokenised per threshold:

| `#` threshold | detections |
| --- | --- |
| 0.05 | 1 |
| 0.22 (app default) | 1 |
| 0.60 | 0 |
| 0.95 | 0 |

So the acoustic probability the decoder saw for this utterance sits between 0.22 and
0.60. The app default (0.22) is permissive; raising `wake_threshold` genuinely tightens
the gate. (This is the one place we can *bracket* the real probability, which is why the
`score` caveat in §2 is not merely pedantry.)

### 3.5 Latency (end of keyword → callback), first detection in a fresh stream

Measured by feeding 100 ms blocks in real time and comparing the callback wall-clock
instant against the wall-clock instant at which the audio covering the keyword's last
token had been fed. The 100 ms block quantisation in the harness mirrors the production
mic path (PortAudio blocksize = 100 ms).

| audio | keyword | word end in stream | latency |
| --- | --- | --- | --- |
| `tts_jarvis_hazel16k.wav` | hey jarvis | 0.96 s | **448 ms** |
| `tts_jarvis_zira16k.wav` | hey jarvis | 0.96 s | **453 ms** |
| `tts_jarvis_gpt_zira16k.wav` | hey gpt | 1.16 s | **249 ms** |
| `test_wavs/0.wav` | light up | 3.16 s | **450 ms** |
| `test_wavs/1.wav` | lovely child | 6.04 s | **471 ms** |

Median ≈ 450 ms. The sherpa-onnx docs state the `chunk-16` model has ~320 ms latency
(`chunk-8` would be ~160 ms); our figure is 320 ms of model latency plus up to 100 ms of
block quantisation and finalisation. Detection is a *streaming* decision — it fires while
audio keeps arriving, so this is the delay added to a wake-up, not a batch time.

Separately, `start()` cost 973–1494 ms cold (that includes loading the ONNX graphs) and
**108 ms** on a warm re-start (spotter cached). `stop()` took **100 ms**.

### 3.6 CPU / RSS (a process running only the detector)

| state | CPU (% of one core) | RSS |
| --- | --- | --- |
| after importing numpy + sherpa-onnx | — | 40.9 MB |
| model loaded, mic open (`start()`) | — | 93.1 MB |
| idle, live microphone, quiet room, 15 s | **3.1 %** | 99.8 MB |
| continuous 16k decode (pushed silence), 12 s | **2.7 %** | 130.3 MB |

On a 16-core machine 3.1 % of one core is ≈ 0.2 % of total CPU. The larger RSS in the
continuous-decode row is ONNX Runtime arena behaviour, not a leak (the ring is fixed
size and reported `overflows=0`). If low idle cost matters more than wake latency, swap
the encoder for `chunk-8` (4.4 MB int8) — not tested here.

### 3.7 Real microphone

* Default input device: `[1] Microphone Array (Realtek(R) Audio)`, 4 channels, native
  44100 Hz. The detector asked for 16000 Hz mono and **the device accepted it**
  (`listening (16000 Hz in -> 16000 Hz model)`); the code also has a fallback that opens
  at the device's native rate and lets sherpa-onnx resample, so a picky driver cannot
  break startup.
* `start()` → `running=True`; 12 s of live room audio → **0 detections** (no false
  trigger); `stop()` → **100 ms**, `running=False`; a second `stop()` is a silent no-op;
  `start()` again worked in 108 ms.
* **Acoustic round-trip is NOT verified.** Playing the "Hey Jarvis" TTS through the
  machine's speaker outputs while the detector listened produced **0 detections through
  the air** (2 attempts, output device 19 `Speakers 2 (Realtek HD Audio output with SST)`).
  A follow-up measurement across three outputs (19, 4 `Speakers (Realtek(R) Audio)`,
  3 `Headphones (Logi Z407)`) shows the microphone does not register the playback at
  all: mic RMS during full-scale playback was 0.0019–0.0069, *below* the 0.0114 RMS
  measured in the "quiet" room, i.e. no playback energy reached the mic (likely
  muted/beam-formed mic array, disconnected speakers, or system volume). Output 11
  (`Speakers (Realtek(R) Audio)`) refused to open at 16 kHz at all; the others accepted
  it. **A human speaking "Hey Jarvis" near this laptop has not been tested.**

## 4. Blockers / unverified

1. **No verified acoustic end-to-end detection.** Everything above is file-replay
   through the production decode path; the mic opens and streams but the speaker→mic
   loop could not be established on this box (§3.7). Please confirm with a real voice:
   run `python docs/wake_tests/verify_mic.py mic` and speak, or listen for the
   `wake: detected` log line.
2. **No real confidence score** is available from sherpa-onnx 1.13.4; `score` is the
   trigger threshold (§2). Anything in the UI that displays it must say so.
3. **False-trigger rate on real speech is unmeasured** — only negatives of silence,
   audiobook English and TTS were tested. There is no ROC curve here.
4. Post-`reset_stream()` timestamps are epoch-relative, so per-detection latency is only
   reported for the first detection of a stream. Successive detections are still emitted
   correctly (two utterances of a two-utterance file produce two detections; slices
   containing one utterance produce exactly one).
5. `chunk-8` variants and the `kw` model's `num_trailing_blanks`/boosting interaction
   were not tuned; `boosting_score = 2.0` is a choice, not a measurement.

## 5. Repro

```bash
python docs/wake_tests/verify_files.py          # file replay, is_available, negatives, thresholds
python docs/wake_tests/verify_mic.py cpu        # RSS / CPU of a detector-only process
python docs/wake_tests/verify_mic.py mic        # real microphone, start/stop timing, room audio
```

The audio files used by the harness are the model's own `test_wavs` plus three WAVs
rendered with Windows SAPI TTS into `%LOCALAPPDATA%\Temp\bvkw\` (`tts_jarvis_*16k.wav`);
regenerate with `System.Speech.Synthesis.SpeechSynthesizer` at
`SpeechAudioFormatInfo(16000, Sixteen, Mono)` if the scratch directory was cleared.
No package was installed for any of this.
