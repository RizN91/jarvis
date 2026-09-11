import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { Backdrop } from "../components/Backdrop";
import { FrameSequence } from "../components/FrameSequence";
import { Headline, Kicker, Reveal, Sub } from "../components/Text";
import { color, font } from "../theme";

/**
 * The opening beat: make the promise, then show the thing that makes it — the
 * pill arriving with the orb already awake.
 */
export const Hook: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const pillIn = spring({
    frame: frame - 52,
    fps,
    config: { damping: 13, stiffness: 90, mass: 0.9 },
  });

  const orbPulse = 1 + 0.02 * Math.sin(frame / 13);

  return (
    <AbsoluteFill>
      <Backdrop mood="blue" />

      <AbsoluteFill
        style={{
          alignItems: "center",
          justifyContent: "center",
          flexDirection: "column",
          paddingBottom: 96,
        }}
      >
        <Reveal delay={4} from={16}>
          <Kicker>Jarvis · open source</Kicker>
        </Reveal>

        <Reveal delay={16} style={{ marginTop: 26 }}>
          <Headline size={132}>
            Talk to your PC.
          </Headline>
        </Reveal>

        <Reveal delay={34} style={{ marginTop: 30 }}>
          <Sub size={37} style={{ textAlign: "center", maxWidth: 1240 }}>
            It types what you say, into any app — then it does what you ask.
          </Sub>
        </Reveal>
      </AbsoluteFill>

      {/* The pill lands under the headline and keeps breathing. */}
      <AbsoluteFill
        style={{
          alignItems: "center",
          justifyContent: "center",
          paddingTop: 392,
        }}
      >
        <div
          style={{
            position: "relative",
            transform: `translateY(${(1 - pillIn) * 70}px) scale(${
              (0.96 + 0.14 * pillIn) * orbPulse
            })`,
            opacity: pillIn,
          }}
        >
          <div
            style={{
              position: "absolute",
              inset: -90,
              background: `radial-gradient(50% 60% at 50% 50%, ${color.accent}33 0%, transparent 70%)`,
              filter: "blur(30px)",
              opacity: 0.9,
            }}
          />
          <FrameSequence
            dir="pill/idle"
            style={{ width: 940, height: 170 }}
          />
        </div>
      </AbsoluteFill>

      {/* A quiet strapline that resolves once the pill has landed. */}
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "flex-end" }}>
        <div
          style={{
            marginBottom: 74,
            fontFamily: font.mono,
            fontSize: 23,
            color: color.textFaint,
            opacity: interpolate(frame, [92, 118], [0, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
            }),
            letterSpacing: "0.04em",
          }}
        >
          Windows 10 / 11 &nbsp;·&nbsp; from $0.0045 per minute
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
