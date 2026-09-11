# Third-party notices

Jarvis itself is [MIT licensed](LICENSE). It depends on other people's work,
which keeps its own licence. Nothing here changes your rights to Jarvis; this
file exists so the obligations that come with the dependencies are written down
in one place.

## Libraries

| Component | Licence | Notes |
| --- | --- | --- |
| OpenAI Python SDK, `websockets`, `numpy`, `sounddevice`, `pillow`, `pywebview` | MIT / BSD | permissive, no obligations beyond attribution |
| `pystray` | LGPL-3.0 | used unmodified as a library |
| `sherpa-onnx` | Apache-2.0 | the local wake-word engine |

## The wake-word model — read this before commercial use

| Component | Licence | Notes |
| --- | --- | --- |
| `sherpa-onnx-kws-zipformer-gigaspeech-3.3M` | Apache-2.0 declared | **the distributed tarball ships no `LICENSE` file, and the GigaSpeech audio corpus it was trained on carries non-commercial research terms.** |

This is a genuine gap, not a formality, and it is recorded here rather than
glossed over. The model is downloaded by the app on first use, not vendored in
this repository. **Read the model's own terms before redistributing it or using
it commercially.** `docs/WAKE_MODEL_LICENCE.md` records exactly what was checked
and what could not be confirmed.

## The OpenAI API

Commercial terms, and your own responsibility. You bring your own API key and
pay OpenAI directly. Jarvis is not affiliated with, endorsed by, or sponsored by
OpenAI.

## Documentation

The OpenAI documentation pages used to verify the API contract are **not**
redistributed in this repository. `docs/OPENAI-API-NOTES.md` records what was
checked and links to the source pages.
