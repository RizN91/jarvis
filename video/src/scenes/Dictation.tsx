import React from "react";
import {
  AbsoluteFill,
  Audio,
  Sequence,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { Backdrop } from "../components/Backdrop";
import { FrameSequence } from "../components/FrameSequence";
import { Headline, Kicker, Reveal, Sub } from "../components/Text";
import { color, font, glassPanel } from "../theme";

/**
 * Dictation, shown rather than described.
 *
 * The frames are a real session: the shipped insert path typed the sentence
 * into Notepad while the shipped overlay drew the pill above it. The crop keeps
 * the pill and the document and drops the wallpaper, so the eye lands on the
 * product rather than on the desktop.
 */
const CROP = { left: 195, top: 0, width: 1110, height: 748 } as const;
const SOURCE = { width: 1500, height: 820 } as const;
const CARD_W = 1040;

/** How long each captured frame is held. ~half real speed: readable, still live. */
const HOLD = 7;
const CAPTURE_FRAMES = 32;
const TYPING_AT = 112;

export const Dictation: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const cardIn = spring({
    frame: frame - 58,
    fps,
    config: { damping: 200 },
  });

  const cardH = (CROP.height * CARD_W) / CROP.width + 47; // +47 = window chrome
  const scale = CARD_W / CROP.width;

  return (
    <AbsoluteFill>
      <Backdrop mood="blue" />

      <AbsoluteFill
        style={{
          padding: "58px 120px 0",
          flexDirection: "column",
          alignItems: "center",
        }}
      >
        <Reveal delay={2}>
          <Kicker>Dictation</Kicker>
        </Reveal>
        <Reveal delay={9} style={{ marginTop: 14 }}>
          <Headline size={72}>Hold a button. Speak. It's there.</Headline>
        </Reveal>

        {/* ---- the real capture, framed as a window ---- */}
        <div
          style={{
            marginTop: 30,
            width: CARD_W,
            opacity: cardIn,
            transform: `translateY(${(1 - cardIn) * 30}px) scale(${
              0.975 + 0.025 * cardIn
            })`,
          }}
        >
          <div
            style={{
              ...glassPanel,
              padding: 0,
              overflow: "hidden",
              boxShadow: "0 40px 90px rgba(0,0,0,0.6)",
            }}
          >
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 9,
                padding: "13px 18px",
                borderBottom: `1px solid ${color.glassEdge}`,
                background: "rgba(255,255,255,0.03)",
              }}
            >
              {["#ff5f57", "#febc2e", "#28c840"].map((c) => (
                <div
                  key={c}
                  style={{
                    width: 12,
                    height: 12,
                    borderRadius: "50%",
                    background: c,
                    opacity: 0.85,
                  }}
                />
              ))}
              <div
                style={{
                  marginLeft: 12,
                  fontFamily: font.ui,
                  fontSize: 16,
                  color: color.textFaint,
                }}
              >
                A real session — Jarvis typing into Notepad
              </div>
            </div>

            {/* the crop window, scaled from the 1500x820 capture */}
            <div
              style={{
                position: "relative",
                width: "100%",
                height: cardH - 47,
                overflow: "hidden",
                background: "#000",
              }}
            >
              <div
                style={{
                  position: "absolute",
                  left: -CROP.left * scale,
                  top: -CROP.top * scale,
                  width: SOURCE.width * scale,
                }}
              >
                <FrameSequence
                  dir="live"
                  hold={HOLD}
                  startAt={TYPING_AT}
                  style={{ width: "100%", display: "block" }}
                />
              </div>
            </div>
          </div>
        </div>

        {/* ---- said plainly, because the frame cannot show it ---- */}
        <div
          style={{
            marginTop: 26,
            display: "flex",
            gap: 40,
            opacity: interpolate(
              frame,
              [TYPING_AT + CAPTURE_FRAMES * HOLD - 60, TYPING_AT + CAPTURE_FRAMES * HOLD - 20],
              [0, 1],
              { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
            ),
          }}
        >
          {[
            ["Never presses Enter", "no half-finished message sends itself"],
            ["Refuses password fields", "and holds text for review in terminals"],
            ["15 languages", "right-to-left done properly"],
          ].map(([title, body]) => (
            <div key={title} style={{ width: 340 }}>
              <div
                style={{
                  fontFamily: font.ui,
                  fontSize: 22,
                  fontWeight: 640,
                  color: color.text,
                }}
              >
                {title}
              </div>
              <div
                style={{
                  fontFamily: font.ui,
                  fontSize: 18,
                  color: color.textFaint,
                  marginTop: 5,
                  lineHeight: 1.35,
                }}
              >
                {body}
              </div>
            </div>
          ))}
        </div>
      </AbsoluteFill>

      {/* Keystrokes, one per captured frame — texture, not clicks. */}
      {Array.from({ length: CAPTURE_FRAMES }, (_, i) => (
        <Sequence key={i} from={TYPING_AT + i * HOLD} durationInFrames={4}>
          <Audio src={staticFile("audio/tick.mp3")} volume={0.3} />
        </Sequence>
      ))}
    </AbsoluteFill>
  );
};
