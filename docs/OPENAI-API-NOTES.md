# OpenAI API notes — what this app relies on, and how it was verified

Jarvis talks to two OpenAI models. This file records the exact contract the app
was built against, so a future change can be checked against the source rather
than against memory.

Everything below was read from the official documentation pages (they are
available as markdown by appending `.md` to the page URL) and then **confirmed
with real API calls** from the development machine. Where the app's own
measurements disagree with the published docs, the difference is called out.

## Endpoints

| Model | Endpoint | Used for |
| --- | --- | --- |
| `gpt-live-1` | `wss://api.openai.com/v1/live/sessions` | the voice assistant, and Live-mode dictation |
| `gpt-transcribe` | `POST /v1/audio/transcriptions` | Economy-mode dictation |

Notes that matter:

- **`gpt-live-1` does not support `/v1/realtime`.** The model page's endpoint
  matrix lists `v1/live/sessions` and marks `v1/realtime` unsupported. Using the
  Realtime path against this model fails.
- The Live WebSocket takes **no query parameters**; the model is named inside the
  `session.start` payload instead. Authenticate with `Authorization: Bearer …`.
- `gpt-live-1` is rate-limited by **concurrent sessions**, not requests per
  minute, and is not available on the free tier.

## Live session: the events the app uses

Send `session.start` and wait for `session.started` before streaming audio.

| Event | Direction | Meaning |
| --- | --- | --- |
| `session.start` | out | opens the session; carries model, instructions, audio config, delegation |
| `session.started` | in | the session is live |
| `session.input_audio.append` | out | base64 PCM |
| `session.output_audio.delta` | in | base64 PCM to play |
| `session.input_transcript.delta` | in | what the user said |
| `session.output_transcript.delta` | in | what the model said |
| `session.instructions.append` | out | add context mid-session |
| `session.input_audio.mute` / `unmute` | out | used to stop the model hearing itself |
| `session.usage.updated` | in | **cumulative** seconds — a snapshot, not an increment |
| `session.close` → `session.closed` | out/in | graceful close; `session.closed` carries final usage |

### Payload shape

```json
{
  "model": "gpt-live-1",
  "instructions": "…conversation style and delegation guidance…",
  "audio": {
    "format": { "type": "audio/pcm", "rate": 24000 },
    "output": { "voice": "marin" }
  },
  "delegation": {
    "type": "responses",
    "responses": {
      "model": "gpt-5.6-luna",
      "instructions": "…business rules and tool-use instructions…",
      "tools": [{ "type": "web_search" }],
      "tool_choice": "auto",
      "parallel_tool_calls": false
    }
  }
}
```

The split between `instructions` and `delegation.responses.instructions` is
documented behaviour, not a style choice: conversation style and delegation
guidance belong to the voice session, while business rules and tool-use
instructions belong to the backend. Leaving the second one out means the backend
runs with no system prompt at all.

`parallel_tool_calls` is set to `false` because every function call raises its own
blocking, default-No approval dialog — the docs prescribe `false` "when calls must
run sequentially".

## Audio

- Mono signed 16-bit little-endian PCM. `{"type":"audio/pcm","rate":24000}` is the
  default; 16000 is also supported.
- **PCM chunks must contain complete 16-bit samples**, so their byte length must be
  even. If a chunk ends on an odd byte, carry that byte into the next chunk — the
  docs' own example does this and so does `jarvis/engines/live.py`.
- Changing the format setting does not convert your bytes. Resample yourself.
- The default voice is **`marin`**. An unrecognised voice name is rejected at
  session start, which is why the app never invents one.

## Usage and cost — the part that is easy to get wrong

- `session.usage.updated` reports **cumulative** voice duration. Summing these
  events multiplies your bill; the app takes the maximum instead.
- Observed on a real session: the event was **absent at 6 s and present at 22 s**.
  Do not depend on it arriving. Final usage comes from `session.closed`.
- `session.closed` confirms finalisation "even when the reason is a connection
  loss or safety termination". **Without it, final usage is unconfirmed** — the
  app records the spend as unconfirmed rather than guessing.
- A stored session can take longer to finalise; allow time in your close timeout.
- Billing is per second and never rounded up.

## Measured behaviour worth knowing

| Observation | Value |
| --- | --- |
| `gpt-transcribe` word error rate, synthetic fixture | **2.6 %** with vocabulary hints, 7.7 % without |
| `gpt-live-1` word error rate, same audio | **46 %** — much worse at verbatim text |
| Cost per minute | `gpt-transcribe` $0.0045, `gpt-live-1` $0.05 |

**The practical conclusion: use `gpt-transcribe` for dictation and `gpt-live-1`
for conversation.** Live is 11× the price and, on identical audio, wrote
`1,250` as "one thousand two hundred and fifty" and heard `Claude` as "cloud",
`Supabase` as "to car base" and `TypeScript` as "type script". That is why
Economy is the default dictation engine and why the onboarding wizard lets you
listen to both before choosing.

These are synthetic-speech numbers. Measure on your own voice with the built-in
calibration screen rather than trusting ours.

## Delegation: completing a function call

The Responses-delegation flow the app implements:

1. Read completed function calls from **nested `response.output_item.done`**
   events. The finished item carries `call_id`, `name` and `arguments`; an
   arguments-done event alone is not enough to identify the call.
2. Track the response id from the nested `response.created`.
3. **Forwarded lifecycle snapshots deliberately contain `response.output: []`** —
   including at `response.completed`. An empty terminal output list does **not**
   mean there are no pending calls. Use what you collected in step 1.
4. Submit **every** required result as `response.item.create` with a
   `function_call_output` item, then send `response.create` **with no body**.
   Appending a result does not by itself continue the response, and
   `response.item.create` has no standalone success acknowledgement.

## SDK caveat

The pinned `openai` SDK version has **no `client.live`**. The documentation's own
Live Python example therefore does not run against it, which is why
`jarvis/engines/live.py` speaks the WebSocket protocol directly. If a later SDK
adds Live support, that file is where to consolidate.
