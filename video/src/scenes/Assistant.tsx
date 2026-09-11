import React from "react";
import {
  AbsoluteFill,
  Sequence,
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
 * The second half of the pitch: the same button also starts a conversation that
 * can act. The pill walks through its real states — listen, think, work, speak —
 * so the viewer learns the visual language the app uses before they install it.
 */
const STEPS = [
  {
    at: 60,
    dir: "pill/listening",
    label: "Listening",
    text: "“Hey Jarvis — what's the latest on the Vue 3.6 release?”",
    tint: color.accent,
  },
  {
    at: 156,
    dir: "pill/thinking",
    label: "Thinking",
    text: "It decides it needs the web, not its own memory.",
    tint: color.violet,
  },
  {
    at: 252,
    dir: "pill/working",
    label: "Working",
    text: "It runs a real tool — and stops for your approval first.",
    tint: color.teal,
  },
  {
    at: 348,
    dir: "pill/speaking",
    label: "Speaking",
    text: "Then answers out loud, in a conversation you can talk over.",
    tint: color.teal,
  },
] as const;

export const Assistant: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const pillIn = spring({
    frame: frame - 14,
    fps,
    config: { damping: 200 },
  });

  const active = STEPS.reduce((acc, s) => (frame >= s.at ? s : acc), STEPS[0]);

  return (
    <AbsoluteFill>
      <Backdrop mood="violet" />

      <AbsoluteFill
        style={{
          padding: "66px 120px",
          flexDirection: "column",
          alignItems: "center",
        }}
      >
        <Reveal delay={2}>
          <Kicker tint={color.violet}>Assistant</Kicker>
        </Reveal>
        <Reveal delay={8} style={{ marginTop: 16 }}>
          <Headline size={74}>Or ask it for something.</Headline>
        </Reveal>

        {/* ---- the pill, changing state for real ---- */}
        <div
          style={{
            marginTop: 34,
            opacity: pillIn,
            transform: `scale(${0.94 + 0.06 * pillIn})`,
            height: 176,
            display: "flex",
            alignItems: "center",
          }}
        >
          {STEPS.map((s, i) => {
            const next = STEPS[i + 1];
            const end = next ? next.at : 480;
            return (
              <Sequence key={s.dir} from={s.at} durationInFrames={end - s.at}>
                <FrameSequence
                  dir={s.dir}
                  style={{ width: 940, height: 170 }}
                />
              </Sequence>
            );
          })}
        </div>

        {/* ---- the state timeline ---- */}
        <div
          style={{
            marginTop: 12,
            display: "flex",
            gap: 22,
            alignItems: "stretch",
          }}
        >
          {STEPS.map((s) => {
            const on = frame >= s.at;
            const isActive = active.dir === s.dir;
            return (
              <div
                key={s.label}
                style={{
                  ...glassPanel,
                  borderRadius: 18,
                  padding: "22px 24px",
                  width: 322,
                  opacity: on ? 1 : 0.34,
                  borderColor: isActive ? `${s.tint}88` : color.glassEdge,
                  boxShadow: isActive ? `0 0 40px ${s.tint}22` : "none",
                  transform: `translateY(${on ? 0 : 12}px)`,
                  transition: "none",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 10,
                    marginBottom: 12,
                  }}
                >
                  <div
                    style={{
                      width: 11,
                      height: 11,
                      borderRadius: "50%",
                      background: on ? s.tint : color.textFaint,
                      boxShadow: on ? `0 0 14px ${s.tint}` : "none",
                    }}
                  />
                  <div
                    style={{
                      fontFamily: font.ui,
                      fontSize: 19,
                      fontWeight: 660,
                      letterSpacing: "0.06em",
                      textTransform: "uppercase",
                      color: on ? s.tint : color.textFaint,
                    }}
                  >
                    {s.label}
                  </div>
                </div>
                <div
                  style={{
                    fontFamily: font.ui,
                    fontSize: 21,
                    lineHeight: 1.42,
                    color: on ? color.textDim : color.textFaint,
                  }}
                >
                  {s.text}
                </div>
              </div>
            );
          })}
        </div>

        <Reveal delay={430} style={{ marginTop: 34 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
            <div
              style={{
                fontFamily: font.ui,
                fontSize: 27,
                fontWeight: 620,
                color: color.amber,
              }}
            >
              Nothing runs without your yes.
            </div>
            <Sub size={25}>
              every tool call is a one-time, expiring approval — and Esc stops
              everything mid-flight
            </Sub>
          </div>
        </Reveal>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
