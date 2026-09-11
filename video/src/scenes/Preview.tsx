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
import { color, font } from "../theme";

/**
 * The short, silent clip that goes inline in the README.
 *
 * GitHub strips <video> from markdown, so the page needs an animated WebP to
 * show motion at all. That means no audio and roughly seven seconds — enough to
 * communicate "you talk, it types", and nothing else.
 */
export const Preview: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const pillIn = spring({
    frame: frame - 8,
    fps,
    config: { damping: 15, stiffness: 95 },
  });

  // Gentle breathing so the still moments are not actually still.
  const breathe = 1 + 0.012 * Math.sin(frame / 16);

  return (
    <AbsoluteFill>
      <Backdrop mood="blue" fadeIn={14} />

      <AbsoluteFill
        style={{
          alignItems: "center",
          justifyContent: "center",
          flexDirection: "column",
        }}
      >
        <div
          style={{
            fontFamily: font.ui,
            fontSize: 62,
            fontWeight: 700,
            letterSpacing: "-0.025em",
            color: color.text,
            opacity: interpolate(frame, [10, 32], [0, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
            }),
            transform: `translateY(${
              (1 -
                interpolate(frame, [10, 32], [0, 1], {
                  extrapolateLeft: "clamp",
                  extrapolateRight: "clamp",
                })) * 18
            }px)`,
          }}
        >
          Talk to your PC.
        </div>

        <div
          style={{
            marginTop: 14,
            fontFamily: font.ui,
            fontSize: 27,
            color: color.textDim,
            opacity: interpolate(frame, [22, 46], [0, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
            }),
          }}
        >
          It types what you say — into any app.
        </div>

        <div
          style={{
            marginTop: 46,
            position: "relative",
            opacity: pillIn,
            transform: `translateY(${(1 - pillIn) * 40}px) scale(${
              (0.9 + 0.1 * pillIn) * breathe
            })`,
          }}
        >
          <div
            style={{
              position: "absolute",
              inset: -70,
              background: `radial-gradient(50% 60% at 50% 50%, ${color.accent}30 0%, transparent 70%)`,
              filter: "blur(26px)",
            }}
          />
          <FrameSequence dir="pill/dictating" style={{ width: 900, height: 163 }} />
        </div>
      </AbsoluteFill>

      {/* Metadata line, so a viewer arriving cold knows what this is. */}
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "flex-end" }}>
        <div
          style={{
            marginBottom: 30,
            fontFamily: font.mono,
            fontSize: 17,
            color: color.textFaint,
            opacity: interpolate(frame, [40, 70], [0, 0.85], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
            }),
            letterSpacing: "0.05em",
          }}
        >
          Jarvis · open source · github.com/RizN91/jarvis
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
