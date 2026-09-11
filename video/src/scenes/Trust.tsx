import React from "react";
import {
  AbsoluteFill,
  interpolate,
  useCurrentFrame,
} from "remotion";
import { Backdrop } from "../components/Backdrop";
import { Headline, Kicker, Reveal, Sub } from "../components/Text";
import { color, font, glassPanel } from "../theme";

/**
 * Trust, argued from architecture rather than asserted as marketing. Every line
 * here corresponds to something the code actually does, which is the only kind
 * of privacy claim worth putting in a video.
 */
const FACTS = [
  {
    glyph: "🔑",
    title: "The key never leaves the vault",
    body: "Stored once in Windows Credential Manager, DPAPI-wrapped, and read from memory. Never in config, logs, the database, the command line or the UI — with a redaction filter scrubbing key-shaped strings from every log line.",
    tint: color.accent,
  },
  {
    glyph: "🔌",
    title: "There is no server",
    body: "No backend, no account, and no localhost port. The tray app and the settings window share a folder, which removes the entire attack surface a listening socket would add.",
    tint: color.teal,
  },
  {
    glyph: "🎙",
    title: "Wake words run on your machine",
    body: "“Hey Jarvis” is detected locally by a small on-device model. While it is listening for the phrase, no audio leaves your computer at all.",
    tint: color.violet,
  },
  {
    glyph: "📉",
    title: "No telemetry. Ever.",
    body: "History, vocabulary and spend are SQLite on your own disk. Nothing is synced, phoned home, or counted. Spend ceilings are enforced by the app itself.",
    tint: color.amber,
  },
];

export const Trust: React.FC = () => {
  const frame = useCurrentFrame();

  return (
    <AbsoluteFill>
      <Backdrop mood="teal" />

      <AbsoluteFill
        style={{
          padding: "64px 120px",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <Reveal delay={2}>
          <Kicker tint={color.teal}>Why it's safe to run</Kicker>
        </Reveal>
        <Reveal delay={8} style={{ marginTop: 16 }}>
          <Headline size={66}>A voice tool has your microphone. It earned it.</Headline>
        </Reveal>

        <div
          style={{
            marginTop: 44,
            display: "grid",
            gridTemplateColumns: "1fr 1fr",
            gap: 24,
            width: 1500,
          }}
        >
          {FACTS.map((f, i) => (
            <Reveal key={f.title} delay={22 + i * 14} from={26}>
              <div
                style={{
                  ...glassPanel,
                  padding: "26px 28px",
                  height: 208,
                  borderLeft: `3px solid ${f.tint}`,
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
                  <div style={{ fontSize: 30 }}>{f.glyph}</div>
                  <div
                    style={{
                      fontFamily: font.ui,
                      fontSize: 26,
                      fontWeight: 660,
                      color: color.text,
                    }}
                  >
                    {f.title}
                  </div>
                </div>
                <div
                  style={{
                    fontFamily: font.ui,
                    fontSize: 21,
                    lineHeight: 1.5,
                    color: color.textDim,
                    marginTop: 14,
                  }}
                >
                  {f.body}
                </div>
              </div>
            </Reveal>
          ))}
        </div>

        <Reveal delay={92} style={{ marginTop: 36 }}>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 20,
              padding: "18px 30px",
              borderRadius: 999,
              background: "rgba(255,255,255,0.04)",
              border: `1px solid ${color.glassEdge}`,
            }}
          >
            <div
              style={{
                fontFamily: font.mono,
                fontSize: 22,
                color: color.teal,
                fontWeight: 600,
              }}
            >
              489 assertions · 13 suites · 0 failures
            </div>
            <div
              style={{
                width: 1,
                height: 26,
                background: color.glassEdge,
              }}
            />
            <Sub size={21}>
              the tests type into real applications, not mocks
            </Sub>
          </div>
        </Reveal>

        <Sub
          size={19}
          style={{
            marginTop: 22,
            opacity: interpolate(frame, [150, 180], [0, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
            }),
            maxWidth: 1100,
            textAlign: "center",
          }}
        >
          Read the code before you trust it. That is the point of it being open
          source.
        </Sub>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
