# The demo video

`docs/media/jarvis-demo.mp4` is built from this folder with [Remotion](https://remotion.dev).
It is not a mockup: it is assembled from captures of the real application, and
the generators that produce those captures live in `tools/`.

## Why it is built this way

The alternative — hand-cropped screenshots pasted into a video editor — goes
stale the moment the UI changes, and quietly lies once it does. Here, three
scripts regenerate every asset from the shipped code, so re-running them is all
it takes for the video to catch up with the product:

| Asset | Generator | What it actually is |
|---|---|---|
| `public/pill/**` | `tools/make_pill.py` | Imports `jarvis.win.overlay`, drives its real spring animation with `_advance()` and calls its real `_compose()`. Every pixel the pill shows is drawn by the shipped renderer. |
| `public/live/**` | `tools/make_shots.py` | Launches Notepad, focuses it, calls the shipped `insert_text()` to type the sentence, shows the shipped `Overlay` window, and photographs the actual desktop while it happens. |
| `public/shots/**` | `tools/make_shots.py` | The same screenshots the README publishes, copied from `docs/images/`. |
| `public/audio/**` | `tools/make_audio.py` | The music and the sound effects, synthesised from scratch with numpy. No samples, so there is nothing to license. |

`make_shots.py` **verifies its own take**: it reads the document back through
the clipboard after typing and discards the footage if what landed is not
exactly what was dictated. A stray keypress, or another program typing into the
foreground window, fails the check loudly instead of quietly shipping a corrupt
clip.

## Rebuilding

```bash
cd video
npm install
python tools/make_audio.py     # music + sfx
python tools/make_pill.py      # the pill, drawn by the app
python tools/make_shots.py     # real dictation footage + repo screenshots
npm run render                 # -> out/jarvis-demo.mp4
npm run render:preview         # -> the looping clip that goes inline in the README
```

`make_shots.py` opens and closes Notepad on the machine it runs on. Run it on a
desktop you are willing to have briefly disturbed, with nothing else typing.

## Where the music comes from

`tools/make_audio.py` synthesises an 84-second lofi bed — electric piano, bass,
brushed drums, vinyl crackle and tape hiss — plus four sound effects. It is
mixed deliberately quiet (about −21 LUFS) because it sits under the visuals
rather than in front of them. The tonal tilt is worth knowing about if you edit
it: an early version put 63% of its energy between 120–300 Hz, which sounded
like mud. Cutting that band and lifting 2–8 kHz is what made the chords audible.

## Layout

```
video/
  src/
    Root.tsx              composition + the music bed and cue sheet
    timing.ts            scene boundaries; one source of truth for durations
    theme.ts             colours lifted from the app's own dark theme
    components/          Backdrop, FrameSequence, Text primitives
    scenes/              one file per scene
  tools/                 the asset generators described above
  public/                generated assets (gitignored except the audio)
```

`FrameSequence` discovers its frames from the public directory rather than
having a count hard-coded, so changing a generator's frame count cannot
silently desynchronise the video from the app.
