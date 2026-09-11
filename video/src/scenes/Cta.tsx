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
import { Headline, Reveal, Sub } from "../components/Text";
import { color, font, glassPanel } from "../theme";

export const Cta: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const urlIn = spring({
    frame: frame - 26,
    fps,
    config: { damping: 16, stiffness: 100 },
  });

  const pillIn = spring({
    frame: frame - 96,
    fps,
    config: { damping: 200 },
  });

  return (
    <AbsoluteFill>
      <Backdrop mood="blue" />

      <AbsoluteFill
        style={{
          alignItems: "center",
          justifyContent: "center",
          flexDirection: "column",
          paddingBottom: 90,
        }}
      >
        <Reveal delay={2}>
          <Headline size={104} style={{ textAlign: "center" }}>
            Talk to your PC.
          </Headline>
        </Reveal>

        {/* ---- the URL, as the single most important object on screen ---- */}
        <div
          style={{
            marginTop: 42,
            opacity: urlIn,
            transform: `translateY(${(1 - urlIn) * 26}px) scale(${0.96 + 0.04 * urlIn})`,
          }}
        >
          <div
            style={{
              ...glassPanel,
              padding: "26px 44px",
              display: "flex",
              alignItems: "center",
              gap: 20,
              boxShadow: `0 0 70px ${color.accent}2e`,
              borderColor: `${color.accent}66`,
            }}
          >
            <div style={{ fontSize: 32 }}>★</div>
            <div
              style={{
                fontFamily: font.mono,
                fontSize: 46,
                fontWeight: 600,
                color: color.text,
                letterSpacing: "-0.01em",
              }}
            >
              github.com/RizN91/jarvis
            </div>
          </div>
        </div>

        <Reveal delay={52} style={{ marginTop: 34 }}>
          <div style={{ display: "flex", gap: 40, alignItems: "center" }}>
            {[
              ["MIT licensed", "do anything with it"],
              ["Windows 10 / 11", "Python 3.11+, one setup script"],
              ["15 languages", "wizard, settings and docs"],
            ].map(([a, b]) => (
              <div key={a} style={{ textAlign: "center" }}>
                <div
                  style={{
                    fontFamily: font.ui,
                    fontSize: 24,
                    fontWeight: 660,
                    color: color.text,
                  }}
                >
                  {a}
                </div>
                <div
                  style={{
                    fontFamily: font.ui,
                    fontSize: 19,
                    color: color.textFaint,
                    marginTop: 5,
                  }}
                >
                  {b}
                </div>
              </div>
            ))}
          </div>
        </Reveal>

        {/* ---- the one command ---- */}
        <Reveal delay={78} style={{ marginTop: 40 }}>
          <div
            style={{
              fontFamily: font.mono,
              fontSize: 25,
              color: color.teal,
              background: "rgba(0,0,0,0.42)",
              border: `1px solid ${color.glassEdge}`,
              borderRadius: 14,
              padding: "18px 30px",
            }}
          >
            irm https://raw.githubusercontent.com/RizN91/jarvis/main/install.ps1 | iex
          </div>
        </Reveal>

        <Sub size={23} style={{ marginTop: 26, opacity: 0.9 }}>
          Free, open source, and honest about what it can't do.
        </Sub>
      </AbsoluteFill>

      {/* The pill comes back to rest — the last thing you see is the product. */}
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "flex-end" }}>
        <div
          style={{
            marginBottom: 46,
            opacity: pillIn,
            transform: `translateY(${(1 - pillIn) * 26}px)`,
          }}
        >
          <FrameSequence dir="pill/idle" style={{ width: 760, height: 137 }} />
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
