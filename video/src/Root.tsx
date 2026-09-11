import React from "react";
import {
  AbsoluteFill,
  Audio,
  Composition,
  Sequence,
  interpolate,
  staticFile,
  useCurrentFrame,
} from "remotion";
import { Assistant } from "./scenes/Assistant";
import { Cost } from "./scenes/Cost";
import { Cta } from "./scenes/Cta";
import { Dictation } from "./scenes/Dictation";
import { Gallery } from "./scenes/Gallery";
import { Hook } from "./scenes/Hook";
import { Preview } from "./scenes/Preview";
import { Trust } from "./scenes/Trust";
import { SCENES, TOTAL_FRAMES, at } from "./timing";

/**
 * Fades a scene out over its last frames.
 *
 * Every scene already fades in from black via the backdrop, so letting them go
 * the same way turns what would be a hard cut between scenes into a breath —
 * which is the difference between "seven clips" and "one film".
 */
const SceneFade: React.FC<{
  duration: number;
  children: React.ReactNode;
}> = ({ duration, children }) => {
  const frame = useCurrentFrame();
  const out = interpolate(frame, [duration - 13, duration], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return <AbsoluteFill style={{ opacity: out }}>{children}</AbsoluteFill>;
};

const Scene: React.FC<{
  from: number;
  duration: number;
  children: React.ReactNode;
}> = ({ from, duration, children }) => (
  <Sequence from={from} durationInFrames={duration} name={undefined}>
    <SceneFade duration={duration}>{children}</SceneFade>
  </Sequence>
);

/** A one-shot sound at an absolute frame. */
const Cue: React.FC<{ from: number; file: string; volume?: number; dur?: number }> = ({
  from,
  file,
  volume = 1,
  dur = 60,
}) => (
  <Sequence from={from} durationInFrames={dur}>
    <Audio src={staticFile(`audio/${file}`)} volume={volume} />
  </Sequence>
);

export const JarvisDemo: React.FC = () => {
  const boundaries = [
    SCENES.dictation.from,
    SCENES.assistant.from,
    SCENES.gallery.from,
    SCENES.cost.from,
    SCENES.trust.from,
    SCENES.cta.from,
  ];

  return (
    <AbsoluteFill style={{ backgroundColor: "#080a12" }}>
      {/* ---- picture ---- */}
      <Scene from={SCENES.hook.from} duration={SCENES.hook.dur}>
        <Hook />
      </Scene>
      <Scene from={SCENES.dictation.from} duration={SCENES.dictation.dur}>
        <Dictation />
      </Scene>
      <Scene from={SCENES.assistant.from} duration={SCENES.assistant.dur}>
        <Assistant />
      </Scene>
      <Scene from={SCENES.gallery.from} duration={SCENES.gallery.dur}>
        <Gallery />
      </Scene>
      <Scene from={SCENES.cost.from} duration={SCENES.cost.dur}>
        <Cost />
      </Scene>
      <Scene from={SCENES.trust.from} duration={SCENES.trust.dur}>
        <Trust />
      </Scene>
      <Scene from={SCENES.cta.from} duration={SCENES.cta.dur}>
        <Cta />
      </Scene>

      {/* ---- the music bed ----
          Deliberately quiet and constant. It is there to stop the video feeling
          like a screen recording with the sound off, not to be listened to. */}
      <Audio
        src={staticFile("audio/music.mp3")}
        volume={(f) =>
          interpolate(
            f,
            [0, 45, TOTAL_FRAMES - 90, TOTAL_FRAMES - 18],
            [0, 0.42, 0.42, 0],
            { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
          )
        }
      />

      {/* ---- transitions, so a cut reads as intentional ---- */}
      {boundaries.map((b) => (
        <Cue key={`w${b}`} from={b - 14} file="whoosh.mp3" volume={0.5} dur={34} />
      ))}

      {/* ---- a thin punctuation layer ---- */}
      <Cue from={at("hook", 1.7)} file="pop.mp3" volume={0.45} dur={12} />
      <Cue from={at("assistant", 2.0)} file="pop.mp3" volume={0.35} dur={12} />
      <Cue from={at("assistant", 5.2)} file="pop.mp3" volume={0.35} dur={12} />
      <Cue from={at("assistant", 8.4)} file="pop.mp3" volume={0.35} dur={12} />
      <Cue from={at("assistant", 11.6)} file="pop.mp3" volume={0.35} dur={12} />
      <Cue from={at("gallery", 10.0)} file="pop.mp3" volume={0.4} dur={12} />
      <Cue from={at("cost", 5.6)} file="pop.mp3" volume={0.45} dur={12} />
      <Cue from={at("cta", 0.9)} file="chime.mp3" volume={0.6} dur={70} />
    </AbsoluteFill>
  );
};

export const RemotionRoot: React.FC = () => (
  <>
    <Composition
      id="JarvisDemo"
      component={JarvisDemo}
      durationInFrames={TOTAL_FRAMES}
      fps={30}
      width={1920}
      height={1080}
    />
    {/* The looping clip that goes inline in the README: GitHub will not play an
        embedded <video>, so the page gets an animated WebP instead. */}
    <Composition
      id="JarvisPreview"
      component={Preview}
      durationInFrames={210}
      fps={30}
      width={1200}
      height={675}
    />
  </>
);
