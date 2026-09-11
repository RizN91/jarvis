# VERIFIED_API.md — Jarvis: verified API contract

**Verification date:** 2026-09-11
**Re-verified:** 2026-09-11 (later the same day, after the final audit) — all 17
vendored pages were re-fetched and are **byte-identical** to the copies this app was
built against (`diff -rq` clean; md5 spot-checks match, e.g.
`voice-websockets.md 7c68d9b1f484fd1303a0b126c87b9f35`,
`gpt-live-1.md e992da6582d061c84f747457e60fb5c5`). **The published contract had not
moved**, so no implementation change was required. The re-check confirmed, against
the docs rather than against memory: the endpoint
(`wss://api.openai.com/v1/live/sessions`, no query parameters, Bearer auth); the
`audio.format` shape (`{"type":"audio/pcm","rate":24000}`); the default voice
(`marin`); PCM chunks requiring complete 16-bit samples with the trailing byte
carried into the next chunk; cumulative usage that must never be summed; the
`{"type":"session.close"}` payload; and the rule that without `session.closed`
final usage stays unconfirmed.
**Re-verified again:** 2026-09-11, third check, this time by fetching the LIVE
pages rather than diffing the vendored copies — the vendored files could in
principle have been refreshed from a stale cache, and the user asked specifically
whether anything had moved. Fetched and read back:

| Live page | What was confirmed |
| --- | --- |
| `/api/docs/guides/live-conversations` | every event name the app sends or handles, verbatim; "These are snapshots, not increments to sum"; "Transcript deltas have no item ID or authoritative turn-completed event"; the full **Voice options** table |
| `/api/docs/guides/voice-websockets` | `{"type":"audio/pcm","rate":24000}` default and the three alternatives; the `session.start` fields `model` / `instructions` / `audio` / `delegation` / `voice` |
| `/api/docs/models/gpt-live-1` | model id `gpt-live-1`; "$0.05 per minute, billed per second"; "Session duration is not rounded up to the next whole minute" |
| `/api/docs/pricing` | `gpt-live-1` $0.05/min · `gpt-transcribe` $0.0045/min · `gpt-5.6-luna` $0.20 in / $1.20 out · `gpt-5.6-terra` $2.00 in / $12.00 out per 1M tokens |

**Nothing had drifted.** All four prices match `jarvis/core/pricing.py`,
every event name matches `jarvis/engines/live.py`, and the voice table was
transcribed into `jarvis/engines/live.VOICES` (13 ids: the 12 tabled voices
plus the documented default `marin`). No implementation change was required by
this check.

**Verified on:** Windows 11, Python 3.11.16 (64-bit, MSC v.1944), interpreter
`C:\Users\user\AppData\Local\hermes\hermes-agent\venv\Scripts\python`.

This file is a *verification record*, not a marketing summary. Every claim below is
either (a) quoted from the raw official OpenAI documentation pages that were fetched to
disk, or (b) taken from this app's own recorded test evidence in
`docs/TEST_RESULTS_raw.txt`. Where the two disagree, the disagreement is stated
explicitly in [Findings that differ from the docs](#findings-that-differ-from-the-docs).

---

## 1. Provenance of the documentation

**Fetch method.** Markdown versions of the documentation pages are produced by
appending `.md` to the page URL. This is the method the docs themselves advertise:

> "Markdown versions of documentation pages are available by appending `.md` to the page URL."
> — first-party banner on every fetched page

Each page below was fetched on 2026-09-11 by appending `.md` to its canonical
`developers.openai.com` URL. The raw `.md` files are stored on disk at:

`C:\Users\user\Desktop\Jarvis\docs\_vendor\`

> **Note on location.** The vendor corpus lives one level up from the package, at
> `Jarvis\docs\_vendor\`. That is the directory these quotes were read from.

Exact URLs fetched (each with `.md` appended):

| File on disk | URL |
| --- | --- |
| `api_docs_guides_live.md` | `https://developers.openai.com/api/docs/guides/live` |
| `api_docs_guides_live-conversations.md` | `https://developers.openai.com/api/docs/guides/live-conversations` |
| `api_docs_guides_live-delegation.md` | `https://developers.openai.com/api/docs/guides/live-delegation` |
| `api_docs_guides_live-prompting.md` | `https://developers.openai.com/api/docs/guides/live-prompting` |
| `api_docs_guides_voice-websockets.md` | `https://developers.openai.com/api/docs/guides/voice-websockets` |
| `api_docs_guides_voice-webrtc.md` | `https://developers.openai.com/api/docs/guides/voice-webrtc` |
| `api_docs_guides_voice-server-controls.md` | `https://developers.openai.com/api/docs/guides/voice-server-controls` |
| `api_docs_guides_voice-latency-cost.md` | `https://developers.openai.com/api/docs/guides/voice-latency-cost` |
| `api_docs_guides_realtime-transcription.md` | `https://developers.openai.com/api/docs/guides/realtime-transcription` |
| `api_docs_guides_speech-to-text.md` | `https://developers.openai.com/api/docs/guides/speech-to-text` |
| `api_docs_guides_conversation-state.md` | `https://developers.openai.com/api/docs/guides/conversation-state` |
| `api_docs_models_gpt-live-1.md` | `https://developers.openai.com/api/docs/models/gpt-live-1` |
| `api_docs_models_gpt-transcribe.md` | `https://developers.openai.com/api/docs/models/gpt-transcribe` |
| `api_docs_models_gpt-live-transcribe.md` | `https://developers.openai.com/api/docs/models/gpt-live-transcribe` |
| `api_docs_models_gpt-5.6-luna.md` | `https://developers.openai.com/api/docs/models/gpt-5.6-luna` |
| `api_docs_models_gpt-5.6-terra.md` | `https://developers.openai.com/api/docs/models/gpt-5.6-terra` |
| `api_docs_pricing.md` | `https://developers.openai.com/api/docs/pricing` |

### SDK versions actually installed and probed on this machine

Probed directly with `importlib` / `importlib.metadata` on 2026-09-11 (not read from
memory):

| Package | Installed version | Probe result |
| --- | --- | --- |
| `openai` | 2.24.0 | `openai.__version__ == "2.24.0"` |
| `websockets` | 15.0.1 | `websockets.__version__ == "15.0.1"` |
| `sherpa-onnx` | 1.13.4 | `sherpa_onnx.__version__ == "1.13.4"` |

---

## 2. The verified GPT-Live contract (`gpt-live-1`)

### 2.1 Connection and startup ordering

- **WebSocket URL:** `wss://api.openai.com/v1/live/sessions` — **with no query
  parameters.**
  > "Connect to `wss://api.openai.com/v1/live/sessions` with no query parameters."
  > — `voice-websockets.md`
- **Auth:** a Bearer header carrying the project API key, kept on the trusted server.
  > "Authenticate with `Authorization: Bearer *** and include the connection headers
  > shown in the example." — `voice-websockets.md`
- **Ordering is mandatory:** `session.start` is the first message; audio or app
  commands wait for `session.started`.
  > "Send `session.start` as the first message. Put the model, conversation
  > instructions, audio format, voice, and delegation configuration inside the
  > `session` object." — `voice-websockets.md`
  > "Wait for `session.started` before sending audio or application commands. It
  > contains the resolved session configuration and session ID." — `voice-websockets.md`

`session.start` carries a `session` object. The startup fields verified from the docs:

| Field | Notes |
| --- | --- |
| `model` | required; `gpt-live-1` |
| `instructions` | conversation behaviour, "up to 16,384 tokens" |
| `input` | prior text messages; defaults to `[]` |
| `audio.format` | one shared input/output format, fixed at startup |
| `audio.output.voice` | e.g. `marin` (default), `quartz`, … |
| `delegation` | `{ "type": "responses", ... }` or `{ "type": "client" }`; omitted/`null` ⇒ client mode |
| `store` | default `false` |

Source: `live-conversations.md` "Configuration fields" table.

### 2.2 Client events (exact names and payloads)

| Event `type` | Payload fields | Source |
| --- | --- | --- |
| `session.start` | `event_id`, `session{...}` | `voice-websockets.md` |
| `session.input_audio.append` | `audio` = base64 of raw bytes | `voice-websockets.md` |
| `session.close` | (none required) | `voice-websockets.md`, `live-conversations.md` |
| `session.instructions.append` | `event_id`, `delegation_id` (required, may be `null`), `content` (plain string ≤ 500 tokens) | `live-conversations.md`, `live-delegation.md` |
| `session.input_audio.mute` | `event_id` (e.g. `"mute_1"`) | `live-conversations.md` |
| `session.input_audio.unmute` | `event_id` | `live-conversations.md` |
| `session.update` | `session{...}` (only supported fields) | `live-conversations.md` |

Notes carried by the docs:
- `session.instructions.append` is acknowledged by `session.instructions.appended`; the
  docs say to match the ack via `client_event_id` and to "Wait for
  `session.instructions.appended`".
- `session.input_audio.mute` is acknowledged by `session.input_audio.muted`:
  > "Wait for `session.input_audio.muted` with `client_event_id: "mute_1"` before
  > treating the command as accepted." — `live-conversations.md`
- `session.update` success returns `session.updated`:
  > "A successful update returns `session.updated` with the resolved session
  > configuration." — `voice-websockets.md`
- Immutable after startup: `model`, `instructions`, `input`, `audio`, `store`,
  and the delegation *mode*. Unknown configuration fields are rejected.

### 2.3 Server events (exact names and payloads)

| Event `type` | Payload fields | Source |
| --- | --- | --- |
| `session.started` | `session.id` (+ resolved session config) | `voice-websockets.md` |
| `session.input_transcript.delta` | `delta`, `start_ms`, `end_ms` | `live-conversations.md` |
| `session.output_transcript.delta` | `delta`, `start_ms`, `end_ms` | `live-conversations.md` |
| `session.output_audio.delta` | `delta` = base64 audio | `voice-websockets.md` |
| `session.usage.updated` | `usage.seconds` (cumulative), `context_window.usage_ratio` | `live-conversations.md` |
| `session.closed` | `usage` (final), `reason` | `live-conversations.md` |
| `error` | `error.type`, `error.code`, `error.message`, `error.param`, `error.client_event_id` | `live-conversations.md` |
| `response.event` | envelope: `delegation_id` + nested `event{...}` | `live-delegation.md` |

Documented `session.usage.updated` example:
```json
{
  "type": "session.usage.updated",
  "event_id": "event_usage_1",
  "usage": { "seconds": 12 },
  "context_window": { "usage_ratio": 0.42 }
}
```

Documented `response.event` envelope example:
```json
{
  "type": "response.event",
  "event_id": "event_response_1",
  "delegation_id": "item_9tA2cB6n2V8c4X1z7Q5r9",
  "event": { "type": "response.output_text.delta", "...": "..." }
}
```
> "Dispatch on `envelope.event.type` and preserve the outer `delegation_id`. Do not
> handle every top-level `response.*` value as an unwrapped Responses event."
> — `live-delegation.md`

Documented `session.closed` end reasons: `close_requested`, `expired`, `content`,
`remote_hangup`, `connection_lost` (`live-conversations.md`).

`error` handling:
> "Use `error.client_event_id`, when present, to identify the command." — `voice-websockets.md`

### 2.4 Audio formats and the complete-sample rule

`session.audio.format` is chosen at startup; one format applies to both directions and
**cannot change during the session** (`voice-websockets.md`):

| Format | Meaning |
| --- | --- |
| `{"type":"audio/pcm","rate":24000}` | mono signed 16-bit little-endian PCM @ 24 kHz — **the default** |
| `{"type":"audio/pcm","rate":16000}` | mono signed 16-bit LE PCM @ 16 kHz |
| `{"type":"audio/pcmu","rate":8000}` | G.711 μ-law @ 8 kHz, one byte per sample |
| `{"type":"audio/pcma","rate":8000}` | G.711 A-law @ 8 kHz, one byte per sample |

The even-length rule, quoted exactly:
> "Base64-encode raw bytes without a WAV or other container header. **PCM chunks must
> contain complete 16-bit samples, so their byte length must be even.** The example
> carries a trailing byte into the next input chunk. Chunk boundaries are otherwise
> arbitrary: preserve a continuous, ordered stream." — `voice-websockets.md`

The docs also state that output audio events have **no timing fields**, that there is
**no output-audio-done event**, and that "changing the format setting does not convert
your input bytes" (resample client-side).

### 2.5 Billing basis

> "Voice sessions cost $0.05 per minute, billed per second. Backend model and tool usage
> is billed separately." — `gpt-live-1.md` (Pricing)

> "Session duration is not rounded up to the next whole minute." — `gpt-live-1.md`

The cost guide adds:
> "GPT-Live voice sessions are billed per second at the current model rate. Session
> duration is not rounded up to the next whole minute." — `voice-latency-cost.md`

> "Active session time includes time when the user speaks, the assistant speaks, both
> are silent, or the backend is working." — `voice-latency-cost.md`

Rate limits are measured in **concurrent sessions** (`gpt-live-1.md`): Tier 1 = 25,
Tier 2 = 50, Tier 3 = 200, Tier 4 = 300, Tier 5 = 500.

### 2.6 Critical transcript semantics (quoted) and the consequences the app implements

The docs are explicit that transcript fragments are **not** turns:

> "Append fragments in order for each speaker, retaining `start_ms` and `end_ms`. These
> are milliseconds on the session timeline, with intervals that include the start and
> exclude the end. They are not wall-clock timestamps, packet arrival times, or exact
> word alignments." — `live-conversations.md`

> "Only intervals containing transcript text produce events, and network delivery can be
> uneven. Do not infer silence from a missing event or treat a fragment as a complete
> user turn. **Transcript deltas have no item ID or authoritative turn-completed
> event.**" — `live-conversations.md`

Usage is cumulative, not incremental:

> "`session.usage.updated` reports cumulative voice duration in seconds … **These are
> snapshots, not increments to sum.**" — `live-conversations.md`

> "Each update replaces the previous duration snapshot. **Do not sum the snapshots.**"
> — `voice-latency-cost.md`

> "Preserve the final voice usage from `session.closed` and the backend usage events
> already received. **Voice-duration updates are cumulative snapshots; do not add them
> together.**" — `voice-websockets.md`

And the final-usage rule:

> "Read the final `usage.seconds`, `reason`, and session snapshot from
> `session.closed`." — `live-conversations.md`
> "A transport failure or timeout before `session.closed` leaves final usage
> unconfirmed." — `voice-websockets.md`

**Consequences the app implements** (see `jarvis/engines/live.py`, module
docstring lines 22–34):

1. **There is no "user turn complete" event.** Button release is an app-side boundary,
   so the app never treats a quiet period as server confirmation that the utterance
   ended (`run_dictation`, `drain_until_settled`).
2. **The last word can be cut off.** The app tracks `audio_ms_sent` and compares it
   against the furthest `end_ms` any fragment covered; while the transcript lags the
   audio it keeps draining, within a hard bound, then reports `incomplete_capture`
   rather than silently inserting truncated text.
3. **Usage is cumulative, not incremental.** The assembler keeps `max(...)`, never a
   sum (`live.py`, `session.usage.updated` branch; `core/cost.py`
   `record_live_snapshot`, "keep the maximum").
4. **A missing `session.closed` means final usage is unconfirmed.** The app surfaces
   that (report.complete = False; `core/cost.py::mark_incomplete_finalization`) rather
   than inventing a number.
5. **Fragments are sorted by `start_ms` for display** but every fragment is preserved
   verbatim, because the docs say network delivery can be uneven.

---

## 3. The verified `gpt-transcribe` contract (file path)

- **Model ID:** `gpt-transcribe` (`gpt-transcribe.md`).
- **Endpoint:** `POST https://api.openai.com/v1/audio/transcriptions`
  > "Send the audio file to `/v1/audio/transcriptions` with `gpt-transcribe`" —
  > `speech-to-text.md`
- **Size limit and formats:**
  > "Files can be up to 25 MB. Supported input formats are `mp3`, `mp4`, `mpeg`,
  > `mpga`, `m4a`, `wav`, and `webm`." — `speech-to-text.md`
  (Restated: "The Transcriptions API accepts files up to 25 MB.")
- **Pricing:** `$0.0045 / minute` of transcription audio (`gpt-transcribe.md`).
- **Context fields** — `prompt`, `keywords`, `languages`:
  > "Use `prompt`, `keywords`, and `languages` with `gpt-transcribe` to improve
  > transcription of domain terms and multilingual audio" — `speech-to-text.md`
  - `prompt` = unstructured context about the recording.
  - `keywords` = literal terms expected in the audio.
  - `languages` = expected input languages.
- **`extra_body` practice.** The docs' own Python example passes `keywords` and
  `languages` through `extra_body` because the SDK's typed signature does not include
  them yet:
  ```python
  client.audio.transcriptions.create(
      model="gpt-transcribe",
      file=audio_file,
      prompt="A customer support call about a premium plan and account AC-42.",
      extra_body={
          "keywords": ["premium plan", "AC-42", "billing"],
          "languages": ["en", "fr"],
      },
  )
  ```
  — `speech-to-text.md`
- **Keyword restrictions (quoted):**
  > "For `gpt-transcribe`, `languages` replaces the singular `language` field. Don't
  > send both fields. **Keep each keyword on one line and don't include `<`, `>`, a
  > carriage return, or a line feed.** The API rejects the entire request when it
  > encounters one of these characters or when `prompt` exceeds the model's length
  > limit." — `speech-to-text.md`
- **Response:** the model "returns the transcript and the detected languages as JSON";
  `"languages": [{ "code": "fr" }]`, and `"languages": []` when it can't predict
  (`speech-to-text.md`).
- Language-code formats accepted: ISO 639-1 (`en`, `es`, `fr`), selected ISO 639-3
  (`eng`, `spa`, `yue`, `cmn`), and regional `zh` locales (`zh-cn`, `zh-tw`, `zh-hk`).

The app's implementation of this contract lives in
`jarvis/engines/transcribe.py`; it uses the SDK for file transcription (where the
SDK is authoritative) and applies the published `$0.0045/min` rate from
`jarvis/core/pricing.py`.

---

## 4. Findings that differ from the docs

These are cases where **measured reality on this machine contradicted, or was not
covered by, the shipped documentation.** They are evidenced from
`docs/TEST_RESULTS_raw.txt` (the app's own runs) and from direct probes of the installed
packages.

### 4.1 `session.usage.updated` is not reliably emitted — but `session.closed` always is

The docs document `session.usage.updated` as the mechanism for "cumulative voice
duration in seconds" but the app observed it **missing for a short session and present
only for a longer one**, while `session.closed` carried authoritative final usage in
**both** cases.

Short session (~6‑7 s), `tests/test_live_api.py` 2026-09-11 05:48:39:
```
- audio streamed: 7.00s in 100ms chunks
- wall time 12406ms; session usage 6.00s
- events observed: ['session.closed', 'session.input_transcript.delta', 'session.output_audio.delta', 'session.started']
- FAIL  session.usage.updated observed (cumulative snapshot)
- PASS  session.closed observed (final usage)  :: finalization_complete=True
- PASS  final usage was server-confirmed  :: session.closed carried usage.seconds=6.0
- PASS  missing session.closed marks the charge unconfirmed (never invents a final number)  :: finalized flag = 0
```

Long session (~22 s), `tests/test_live_api.py` 2026-09-11 05:49:24:
```
- events observed: ['session.closed', 'session.input_transcript.delta', 'session.output_audio.delta', 'session.started', 'session.usage.updated']
- PASS  session.closed observed (final usage)  :: finalization_complete=True
- PASS  final usage was server-confirmed  :: session.closed carried usage.seconds=22.0
```

**Consequence the app implements:** it never depends on `session.usage.updated` for
final numbers. `session.closed` is the single source of the authoritative final value;
`usage.updated` is treated as an intermediate, cumulative snapshot (kept as a running
max, never summed). See `live.py` `session.usage.updated` / `session.closed` branches
and `core/cost.py::record_live_snapshot`.

**A secondary measured surprise:** the server-reported session duration was *not always
≥ the audio streamed*. In the 7 s run, `usage.seconds = 6.0` while `audio_ms_sent =
7000.0`. The billed figure follows the server's seconds, not the client's audio clock
(the app's cost test uses the server value: `cost uses the published $0.05/minute rate,
billed per second :: reported $0.005 expected $0.005000`). Do not assume
"billed seconds ≥ microphone seconds".

### 4.2 The native GPT-Live input transcript is far less accurate for verbatim dictation than `gpt-transcribe`, and it rewrites numbers as words

On **the same kind of scripted audio**, `gpt-transcribe` was dramatically more accurate
for literal dictation than the native `gpt-live-1` input transcript.

`gpt-transcribe` (`tests/test_transcribe_api.py`, 24.17 s of real speech):
```
- baseline (no hints): WER 7.7% latency 3563ms usd $0.001813
- baseline text: "Jarvis test. Open my quarterly report documents and summarise the latest file. Do not send 1,250 units to Acme. ..."
- hinted: WER 2.6% latency 2500ms usd $0.001813
- hinted text: 'Jarvis test. Open my quarterly report documents and summarise the latest file. Do not send 1,250 units to Acme. ...'
```

> The sample sentence was later reworded to generic business terms for the public
> release. The quoted text above is the current fixture text; the WER, latency and
> cost figures are the original measured runs of the same audio with and without
> hints, and that hinted-vs-unhinted comparison is unaffected by the rewording.

`gpt-live-1` native input transcript (`tests/test_live_api.py`):
```
- 7.00s run:  WER vs known first sentence: 46.2%
  transcript: ' the user voice test. Open my quarterly report, see the, stop being a documents and summarize the latest file'
- 22.00s run: WER vs known first sentence: 307.7%
  transcript: ' the user voice test Open my quarterly report documents and summarize the latest file. Do not send one thousand two hundred and fifty units to five hundred and fifty units five it or use the path, codex and cloud use to car base and type script Friday. ...'
```

Two things are worth calling out:

1. **Number rewriting.** The literal `1,250` that `gpt-transcribe` reproduced as
   `1,250` came back from GPT-Live's native transcript expanded into words:
   **"one thousand two hundred and fifty"**. The same run also mangled the rest of the
   sentence ("five hundred and fifty units five it or use the path", "codex and cloud
   use to car base and type script" for "Codex and Claude use Supabase and TypeScript").
   The app therefore **does not use the GPT-Live transcript for verbatim dictation
   fidelity**; the "Economy" path (`gpt-transcribe`) exists precisely for this.
2. **The same gap shows on live microphone audio** (`tests/test_mic_loopback.py`):
   ```
   - gpt-transcribe: WER 96.4%, $0.000840, 2953ms :: 'the user.'
   - gpt-live-1    : WER 100.0%, $0.010833, 17719ms :: ''
   - cost ratio Live:transcribe = 11.1x
   ```
   (Both were poor here because the loopback capture was near-silent — `peak=0.010` — a
   separate, honest finding: the microphone loopback could not capture loud audio. See
   §4.5.)

This is **not** contradicted by the docs — the docs make no accuracy claim for
transcript deltas — but it is the single biggest practical difference between the two
engines and it is measured, not assumed.

### 4.3 The installed `openai` SDK 2.24.0 has **no** `client.live` attribute

The docs' own Python examples for Live assume an SDK with Live support:

```python
from openai import AsyncOpenAI
from openai.resources.live.live import AsyncLiveConnection
...
async with client.live.connect() as connection:
    await connection.session.start(session=session, event_id="event_start")
```
— `voice-websockets.md`

Probed on this machine (2026-09-11):
```
openai __version__: 2.24.0
OpenAI has .live attr? -> False
openai.resources.live importable: False -> ModuleNotFoundError No module named 'openai.resources.live'
OpenAI has .audio.transcriptions? -> True
```

**This means the documented Python example for Live cannot be run against the installed
SDK.** The app therefore speaks the documented **wire protocol directly** for Live
(`jarvis/engines/live.py`, using `websockets`), while using the SDK for file
transcription, where it is authoritative. The docs' install hint ("For Python on macOS or
Linux, install `openai[realtime]`"; "These examples require an SDK version with Live
support") confirms an SDK prerequisite the installed version does not meet.

### 4.4 Additional documented-vs-code discrepancies found while verifying

- **Text-model rates in `core/pricing.py` disagreed with the fetched model pages — FIXED.**
  Found while writing this document, then corrected in the code; recorded here
  because it is exactly the kind of error this verification exists to catch.
  - `gpt-5.6-luna`: the app table had `(0.20, 0.02, 1.25)`; the model page says
    Input `$0.2`, Cached `$0.02`, Output **`$1.2`**. Output was wrong.
  - `gpt-5.6-terra`: the app table had `(2.50, 0.25, 15.00)`; the model page says
    Input **`$2`**, Cached **`$0.2`**, Output **`$12`**. All three were wrong.
  - The root cause was reading these two from the pricing page's *grouped* table
    region instead of their own model pages. The individual model page is
    authoritative.
  - Both rows are now corrected in `jarvis/core/pricing.py`, and the
    documented rules that were previously unmodelled were added: **cache writes
    at 1.25x the uncached input rate**, and **>272K input tokens priced at 2x
    input / 1.5x output for the full request**
    (`pricing.CACHE_WRITE_MULTIPLIER`, `pricing.LONG_CONTEXT_*`).
  - The affected figures never touched the voice-session meter, which uses the
    per-minute rates, and are labelled ESTIMATE in the UI. `tests/test_core_units.py`
    (63 checks) still passes after the change.
- **Acknowledgment-gated commands are fire-and-forget in the app.** The docs say to wait
  for `session.instructions.appended`, `session.input_audio.muted`/`unmuted`, and
  `session.updated` before treating a command as accepted. `live.py` sends
  `session.instructions.append`, `session.input_audio.mute`/`unmute`, and
  `session.update` but does not block on those acks (it reads `error` events instead).
  This is a deliberate simplification for a single-user local app, and is recorded here
  as a deviation from the documented lifecycle.
- **The 22 s cost test reads "FAIL … reported $0.018333 expected $0.018333".** The two
  numbers are identical; the FAIL is an exact float-equality artifact in the test
  harness (`$0.005 × 22/6`), not a product or pricing discrepancy. Recorded so it is not
  mistaken for one.

### 4.5 Honest limits in the app's own evidence

- The microphone loopback test could not produce loud audio (`peak=0.010, rms=0.0000`),
  so the live-mic comparison in §4.2 is degraded by capture, not only by the models.
- The near-silent capture nevertheless produced **fabricated text** from both engines
  (`gpt-transcribe` returned `"the user."` on 11 s of near-silence; test recorded
  `FAIL captured signal is loud enough to transcribe`). The app responds with a
  local-VAD "no speech" guard so silent audio is never uploaded
  (`engines/transcribe.py::transcribe_captured`, `likely_fabricated` in
  `engines/live.py`). The docs do not warn about this failure mode.

---

## 5. What we did NOT verify

Stated plainly, so no reader over-reads this record:

- **WebRTC was not used or verified.** The app uses the documented **WebSocket** path
  throughout. `voice-webrtc.md` was fetched but its connection path, data channel, SDP
  exchange, and the "15 seconds billed at WebRTC session initialization"
  (`voice-latency-cost.md`) were **not** exercised.
- **Account-level project budgets and their enforcement were not verified.** The app
  cannot read or enforce the OpenAI account/project budget; its own ceilings are local
  estimates from observed usage (`core/cost.py`, honesty note §11: "The OpenAI
  *account/project* budget is not something this app can read or enforce"). No claim is
  made here about provider-side budget behaviour.
- **No claim is made about accuracy on the user's own voice.** The WER figures quoted are
  from Windows SAPI synthetic speech (`tests/fixtures`) — not the user's live microphone —
  except the degraded loopback run in §4.2, which was near-silent. Treat all WER numbers
  as indicative only.
- **`delegation` / `response.event` backend flows were not exercised.** The app can send
  a `delegation` block and records `response.event` envelopes, but no live backend
  Responses delegation run is in the recorded evidence; the `response.event` contract is
  quoted from the docs only.
- **Realtime transcription sessions** (`gpt-live-transcribe`, `$0.017/min`) were not
  used; the app's streaming path is GPT-Live's native transcript, and its file path is
  `gpt-transcribe`. The `gpt-live-transcribe` model page was fetched and recorded for
  completeness only.
- **Storage / forking / recording download** (`store: true`, `/fork`, `/content`) were
  not used or verified.
- **The `session.usage.updated` omission in §4.1 is a single observation at two session
  lengths.** It is strong enough to justify not depending on the event, but it is not a
  characterised failure mode across many sessions.
