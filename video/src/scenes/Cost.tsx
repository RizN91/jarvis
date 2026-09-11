import React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { Backdrop } from "../components/Backdrop";
import { Headline, Kicker, Reveal, Sub } from "../components/Text";
import { color, font, glassPanel } from "../theme";

const ENGINES = [
  {
    name: "Economy",
    model: "gpt-transcribe",
    price: "$0.0045",
    per: "/ minute",
    note: "The default, and the most accurate for plain dictation.",
    tint: color.accent,
    best: true,
  },
  {
    name: "Live",
    model: "gpt-live-1",
    price: "$0.05",
    per: "/ minute",
    note: "A conversation you can talk over, and tools that act.",
    tint: color.teal,
    best: false,
  },
];

export const Cost: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const punch = spring({
    frame: frame - 168,
    fps,
    config: { damping: 15, stiffness: 110 },
  });

  return (
    <AbsoluteFill>
      <Backdrop mood="amber" />

      <AbsoluteFill
        style={{
          padding: "66px 120px",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <Reveal delay={2}>
          <Kicker tint={color.amber}>What it costs</Kicker>
        </Reveal>
        <Reveal delay={8} style={{ marginTop: 16 }}>
          <Headline size={68}>Bring your own key. That's it.</Headline>
        </Reveal>
        <Reveal delay={18} style={{ marginTop: 14 }}>
          <Sub size={27}>
            No subscription, no account, no markup. You pay OpenAI directly, at
            cost.
          </Sub>
        </Reveal>

        {/* ---- the two engines ---- */}
        <div style={{ marginTop: 40, display: "flex", gap: 30 }}>
          {ENGINES.map((e, i) => (
            <Reveal key={e.name} delay={30 + i * 12} from={30}>
              <div
                style={{
                  ...glassPanel,
                  width: 470,
                  padding: "28px 30px",
                  borderColor: e.best ? `${e.tint}77` : color.glassEdge,
                  boxShadow: e.best ? `0 0 50px ${e.tint}1f` : "none",
                }}
              >
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "baseline",
                  }}
                >
                  <div
                    style={{
                      fontFamily: font.ui,
                      fontSize: 24,
                      fontWeight: 680,
                      color: e.tint,
                    }}
                  >
                    {e.name}
                  </div>
                  {e.best && (
                    <div
                      style={{
                        fontFamily: font.ui,
                        fontSize: 15,
                        fontWeight: 700,
                        letterSpacing: "0.12em",
                        color: "#04101f",
                        background: e.tint,
                        padding: "5px 11px",
                        borderRadius: 999,
                      }}
                    >
                      DEFAULT
                    </div>
                  )}
                </div>

                <div
                  style={{
                    marginTop: 16,
                    display: "flex",
                    alignItems: "baseline",
                    gap: 6,
                  }}
                >
                  <div
                    style={{
                      fontFamily: font.ui,
                      fontSize: 62,
                      fontWeight: 720,
                      color: color.text,
                      letterSpacing: "-0.03em",
                    }}
                  >
                    {e.price}
                  </div>
                  <div
                    style={{
                      fontFamily: font.ui,
                      fontSize: 22,
                      color: color.textFaint,
                    }}
                  >
                    {e.per}
                  </div>
                </div>

                <div
                  style={{
                    fontFamily: font.mono,
                    fontSize: 18,
                    color: color.textFaint,
                    marginTop: 4,
                  }}
                >
                  {e.model}
                </div>

                <div
                  style={{
                    fontFamily: font.ui,
                    fontSize: 20,
                    color: color.textDim,
                    marginTop: 16,
                    lineHeight: 1.42,
                  }}
                >
                  {e.note}
                </div>
              </div>
            </Reveal>
          ))}
        </div>

        {/* ---- the number that actually lands ---- */}
        <div
          style={{
            marginTop: 44,
            opacity: punch,
            transform: `scale(${0.94 + 0.06 * punch})`,
            display: "flex",
            alignItems: "center",
            gap: 26,
          }}
        >
          <div
            style={{
              fontFamily: font.ui,
              fontSize: 84,
              fontWeight: 740,
              color: color.amber,
              letterSpacing: "-0.035em",
              textShadow: `0 0 60px ${color.amber}55`,
            }}
          >
            45¢
          </div>
          <div style={{ maxWidth: 620 }}>
            <div
              style={{
                fontFamily: font.ui,
                fontSize: 27,
                fontWeight: 620,
                color: color.text,
                lineHeight: 1.3,
              }}
            >
              a month, at twenty minutes of dictation a day.
            </div>
            <Sub size={21} style={{ marginTop: 8 }}>
              Measured, not guessed: a full build-and-test session of this app
              cost under ten cents.
            </Sub>
          </div>
        </div>

        {/* ---- the honest bit, which is also the trustworthy bit ---- */}
        <div
          style={{
            marginTop: 34,
            fontFamily: font.ui,
            fontSize: 20,
            color: color.textFaint,
            opacity: interpolate(frame, [210, 240], [0, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
            }),
            maxWidth: 1180,
            textAlign: "center",
            lineHeight: 1.5,
          }}
        >
          Those are estimates from published rates, not invoices — the app can
          enforce its own spending ceilings, but it cannot read your OpenAI
          account budget, and it never pretends to.
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
