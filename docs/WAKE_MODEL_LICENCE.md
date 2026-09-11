# Wake-word model: licence and provenance

Reviewed 2026-09-11 by the agent that installed the model. Everything below was read
from the artifact on disk or from the upstream project itself; where something could
not be established, that is stated explicitly instead of guessed.

## What is installed

| item | value |
| --- | --- |
| Model | `sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01` |
| Release asset | `sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01.tar.bz2` |
| Exact URL | `https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01.tar.bz2` |
| Tarball size | 17,626,723 bytes (16.8 MiB) |
| Tarball sha256 | `f170013b4716e41b62b9bfd809687c207cef798ef9bc6534d524e17af9b6561a` |
| Checksum source | `https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/checksum.txt` — our sha256 **matches the published value exactly** |
| Installed at | `%LOCALAPPDATA%\Jarvis\models\kws\sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01\` |
| Size on disk | 20,126,725 bytes (19.2 MiB), including the generated `keywords.app.txt` |
| Release tag | GitHub release `kws-models` in `k2-fsa/sherpa-onnx` |
| Docs reference | <https://k2-fsa.github.io/sherpa/onnx/kws/pretrained_models/index.html> |
| Publisher | `pkufool` (k2-fsa / icefall contributor); the same tarball is mirrored on ModelScope at `pkufool/sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01` |

Files that ship inside the tarball:

```
244837  bpe.model
277985  decoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx
1063189 decoder-epoch-12-avg-2-chunk-16-left-64.onnx
4807159 encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx
12174219 encoder-epoch-12-avg-2-chunk-16-left-64.onnx
163380  joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx
642462  joiner-epoch-12-avg-2-chunk-16-left-64.onnx
184     keywords.txt          <- the publisher's own example keywords file
102     keywords_raw.txt
726     README.md
5006    tokens.txt
        test_wavs/0.wav (212044), test_wavs/1.wav (534924),
        test_wavs/test_keywords.txt (52), test_wavs/trans.txt (367)
```

## Licence actually present in the artifact

The tarball ships **no `LICENSE`, `COPYING` or `NOTICE` file**. Verified with

```bash
find . -iname "*licen*" -o -iname "*copying*" -o -iname "*notice*"   # -> no matches
```

The only licence statement in the artifact is the YAML front-matter of the shipped
`README.md`, which is ModelScope model-card metadata:

```yaml
---
frameworks:
- 其他
license: Apache License 2.0
tasks:
- keyword-spotting
---
```

So: **the model's own README declares `license: Apache License 2.0`.** There is no
full licence text, copyright line, attribution notice, or warranty disclaimer inside
the tarball — just that one declaration. We did **not** assume the model inherits
sherpa-onnx's Apache-2.0; the declaration above stands on its own, and it happens to
agree with it.

## Provenance and the caveat that actually matters

The same README states (Chinese, verbatim summary): a custom wake-word model for
sherpa, *trained on GigaSpeech XL (10,000 hours)*, ≈3.3 M parameters, BPE modelling
units, trained with `icefall`, converted to ONNX for the `sherpa-onnx` inference
engine. Architecture: zipformer streaming transducer with a keyword-constrained
decoder.

GigaSpeech is a third-party corpus, and its terms are **not** the same as the model's
declared licence:

* The GigaSpeech GitHub project (`SpeechColab/GigaSpeech`) and its Hugging Face dataset
  card declare `license: apache-2.0`.
* The **same** Hugging Face dataset card carries an explicit access statement that the
  Apache-2.0 metadata does not override:

  > SpeechColab does not own the copyright of the audio files. For researchers and
  > educators who wish to use the audio files for non-commercial research and/or
  > educational purposes, we can provide access through the Hub under certain
  > conditions and terms.
  >
  > Terms of Access: … Researcher shall use the Database only for non-commercial
  > research and educational purposes. … The SpeechColab team and Tsinghua University
  > make no representations or warranties regarding the Database …

  (<https://huggingface.co/datasets/speechcolab/gigaspeech>)

Conclusion to act on, stated plainly:

1. **Model weights: declared `Apache License 2.0`** by their publisher. No other
   licence text ships with them.
2. **Training audio: research/education-only in spirit.** The corpus owner does not own
   the source audio (audiobooks, podcasts, YouTube) and publishes a non-commercial
   terms-of-access notice. A trained model is a derived work of that audio in the
   ordinary sense; whether that restriction travels to the weights is a legal question
   this review cannot answer. For the user's own private, local use there is nothing to
   worry about. **Before shipping Jarvis as a commercial product, get the
   GigaSpeech terms reviewed**, or swap in a wake model whose training data is
   unambiguously permissive.
3. **`test_wavs/0.wav` and `test_wavs/1.wav`** ship with no licence statement at all.
   They are audiobook readings (see `test_wavs/trans.txt`). Treat them as test fixtures
   only — do not redistribute them, and do not ship them with the app.
4. No warranty of any kind is offered by the model publisher or by GigaSpeech. There is
   no indemnity.
5. The runtime that consumes this model is `sherpa-onnx` (Apache-2.0, k2-fsa); that is a
   separate work from the model weights. `icefall` (used to train it) is also
   Apache-2.0.

Not a substitute for legal advice; this is a record of what the files say.
