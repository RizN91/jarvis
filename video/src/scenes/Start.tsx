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
import { color, font, glassPanel } from "../theme";

/**
 * How you actually start it.
 *
 * This beat carries the two features people ask about first — the wake word and
 * the mouse button — and it carries them with the real pill: the left card is
 * `jarvis/win/overlay.py` in its `listening` state, the same frames the app
 * draws when it is waiting for "Hey Jarvis".
 */
const SHORTCUTS: Array<[string, string]> = [
  ["Mouse side button", "hold to dictate"],
  ["F8", "toggle dictation"],
  ["Ctrl + Alt + Space", "voice conversation"],
  ["Ctrl + Alt + Pause", "emergency stop"],
  ["Esc", "cancel"],
];

export const Start: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const cardsIn = spring({
    frame: frame - 26,
    fps,
    config: { damping: 200 },
  });

  return (
    <AbsoluteFill>
      <Backdrop mood="teal" />

      <AbsoluteFill
        style={{
          padding: "40px 120px",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <Reveal delay={2}>
          <Kicker tint={color.teal}>Two ways to start it</Kicker>
        </Reveal>
        <Reveal delay={8} style={{ marginTop: 14 }}>
          <Headline size={68}>You never have to click anything.</Headline>
        </Reveal>

        {/* ---- the two cards ---- */}
        <div
          style={{
            marginTop: 34,
            display: "flex",
            gap: 28,
            opacity: cardsIn,
            transform: `translateY(${(1 - cardsIn) * 26}px)`,
          }}
        >
          {/* wake word */}
          <div
            style={{
              ...glassPanel,
              width: 560,
              padding: "26px 28px",
              borderLeft: `3px solid ${color.teal}`,
            }}
          >
            <div
              style={{
                fontFamily: font.ui,
                fontSize: 30,
                fontWeight: 680,
                color: color.text,
              }}
            >
              Say “Hey Jarvis”
            </div>
            <div
              style={{
                fontFamily: font.ui,
                fontSize: 21,
                color: color.textDim,
                marginTop: 10,
                lineHeight: 1.45,
                minHeight: 92,
              }}
            >
              Turn the wake word on and just start talking. It is detected
              <span style={{ color: color.teal, fontWeight: 600 }}>
                {" "}
                on your machine{" "}
              </span>
              by a small local model — so while it is listening for the phrase,
              no audio leaves your computer at all.
            </div>
            <div style={{ marginTop: 14, display: "flex", justifyContent: "center" }}>
              <PillZoom dir="pill/listening" />
            </div>
          </div>

          {/* mouse button */}
          <div
            style={{
              ...glassPanel,
              width: 560,
              padding: "26px 28px",
              borderLeft: `3px solid ${color.accent}`,
            }}
          >
            <div
              style={{
                fontFamily: font.ui,
                fontSize: 30,
                fontWeight: 680,
                color: color.text,
              }}
            >
              Or hold a button
            </div>
            <div
              style={{
                fontFamily: font.ui,
                fontSize: 21,
                color: color.textDim,
                marginTop: 10,
                lineHeight: 1.45,
                minHeight: 92,
              }}
            >
              The default is your mouse's{" "}
              <span style={{ color: color.accent, fontWeight: 600 }}>
                side button
              </span>
              : hold it, speak, release — the words are already at your cursor.
              Prefer the keyboard? Tap F8.
            </div>
            <div
              style={{
                marginTop: 14,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                gap: 18,
                height: 90,
              }}
            >
              <MouseGlyph />
            </div>
          </div>
        </div>

        {/* ---- the shortcut table ---- */}
        <div
          style={{
            marginTop: 30,
            display: "flex",
            gap: 14,
            flexWrap: "wrap",
            justifyContent: "center",
            maxWidth: 1400,
          }}
        >
          {SHORTCUTS.map(([key, what], i) => (
            <div
              key={key}
              style={{
                ...glassPanel,
                borderRadius: 14,
                padding: "13px 20px",
                display: "flex",
                alignItems: "center",
                gap: 12,
                opacity: interpolate(
                  frame,
                  [96 + i * 7, 112 + i * 7],
                  [0, 1],
                  { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
                ),
                transform: `translateY(${interpolate(
                  frame,
                  [96 + i * 7, 112 + i * 7],
                  [10, 0],
                  { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
                )}px)`,
              }}
            >
              <span
                style={{
                  fontFamily: font.mono,
                  fontSize: 20,
                  color: color.text,
                  fontWeight: 600,
                }}
              >
                {key}
              </span>
              <span
                style={{
                  fontFamily: font.ui,
                  fontSize: 18,
                  color: color.textFaint,
                }}
              >
                {what}
              </span>
            </div>
          ))}
        </div>

        <Sub
          size={22}
          style={{
            marginTop: 24,
            opacity: interpolate(frame, [140, 168], [0, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
            }),
          }}
        >
          Every binding is rebindable in the wizard — a different key, a different
          mouse button, or nothing at all.
        </Sub>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

/**
 * Shows the pill at a readable size.
 *
 * The pill's canvas is 940px wide, but at rest the island occupies only the
 * middle ~317px of it, so scaling the whole canvas down leaves a tiny pill
 * floating in transparency. This centres the canvas in a clipped box at a
 * larger scale, so what you see is the island — at a size you can actually read.
 */
const PillZoom: React.FC<{ dir: string; width?: number; height?: number }> = ({
  dir,
  width = 504,
  height = 150,
}) => (
  <div
    style={{
      position: "relative",
      width,
      height,
      overflow: "hidden",
    }}
  >
    <div
      style={{
        position: "absolute",
        left: "50%",
        top: "50%",
        transform: "translate(-50%, -50%)",
      }}
    >
      <FrameSequence dir={dir} style={{ width: 1180, height: 213, display: "block" }} />
    </div>
  </div>
);

/** A small mouse with its side button lit — drawn, so it needs no asset. */
const MouseGlyph: React.FC = () => {
  const frame = useCurrentFrame();
  // A slow press-and-release, so the button reads as the thing you hold.
  const held = 0.5 + 0.5 * Math.sin(frame / 11);
  return (
    <svg width="72" height="112" viewBox="0 0 72 112">
      <defs>
        <linearGradient id="mg" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="rgba(255,255,255,0.16)" />
          <stop offset="100%" stopColor="rgba(255,255,255,0.05)" />
        </linearGradient>
      </defs>
      <rect
        x="6" y="4" width="60" height="104" rx="30"
        fill="url(#mg)" stroke={color.glassEdge} strokeWidth="2"
      />
      <line x1="36" y1="4" x2="36" y2="44" stroke={color.glassEdge} strokeWidth="1.5" />
      {/* the side button, on the left flank, glowing as it is held */}
      <rect
        x="2" y="34" width="10" height="26" rx="5"
        fill={color.accent}
        opacity={0.45 + 0.55 * held}
      />
      <circle
        cx="36" cy="26" r="5"
        fill="rgba(255,255,255,0.22)"
      />
    </svg>
  );
};
