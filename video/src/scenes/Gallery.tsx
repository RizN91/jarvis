import React, { useMemo } from "react";
import {
  AbsoluteFill,
  Img,
  getStaticFiles,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { Backdrop } from "../components/Backdrop";
import { Headline, Kicker, Reveal, Sub } from "../components/Text";
import { color, font, glassPanel } from "../theme";

/** The fifteen languages the UI actually ships. */
const LANGS = [
  "English", "Español", "Français", "Deutsch", "Português", "Italiano",
  "Nederlands", "Polski", "Türkçe", "Русский", "العربية", "हिन्दी",
  "日本語", "한국어", "简体中文",
];

export const Gallery: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  // Every Settings page the app has, in nav order.
  const shots = useMemo(
    () =>
      getStaticFiles()
        .filter((f) => f.name.startsWith("shots/s-") && f.name.endsWith(".webp"))
        .map((f) => f.name)
        .sort(),
    [],
  );

  const CARD_W = 430;
  const GAP = 34;
  const stride = CARD_W + GAP;

  // A long, slow pan left-to-right. Enough travel to feel like a tour, slow
  // enough that no individual page flashes past unread.
  const pan = interpolate(frame, [0, 420], [110, -(shots.length * stride - 1400)], {
    extrapolateRight: "clamp",
  });

  // The RTL reveal lands as its own beat near the end.
  const rtlIn = spring({
    frame: frame - 300,
    fps,
    config: { damping: 200 },
  });

  return (
    <AbsoluteFill>
      <Backdrop mood="blue" />

      <AbsoluteFill style={{ padding: "60px 0 0 0", flexDirection: "column" }}>
        <div style={{ padding: "0 120px" }}>
          <Reveal delay={2}>
            <Kicker>Every screen</Kicker>
          </Reveal>
          <Reveal delay={8} style={{ marginTop: 16 }}>
            <Headline size={70}>
              Nothing is half-built. Settings included.
            </Headline>
          </Reveal>
        </div>

        {/* ---- the shelf ---- */}
        <div
          style={{
            marginTop: 44,
            height: 560,
            perspective: 2000,
            opacity: interpolate(frame, [10, 34], [0, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
            }),
          }}
        >
          <div
            style={{
              display: "flex",
              gap: GAP,
              transform: `translateX(${pan}px) rotateY(-9deg) rotateX(3deg)`,
              transformOrigin: "center center",
            }}
          >
            {shots.map((name, i) => {
              const appear = interpolate(
                frame,
                [i * 3, i * 3 + 22],
                [0, 1],
                { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
              );
              return (
                <div
                  key={name}
                  style={{
                    width: CARD_W,
                    flex: "0 0 auto",
                    opacity: appear,
                    transform: `translateY(${(1 - appear) * 26}px)`,
                  }}
                >
                  <div
                    style={{
                      ...glassPanel,
                      padding: 8,
                      overflow: "hidden",
                      boxShadow: "0 26px 60px rgba(0,0,0,0.5)",
                    }}
                  >
                    <Img
                      src={staticFile(name)}
                      style={{
                        width: "100%",
                        display: "block",
                        borderRadius: 15,
                      }}
                    />
                  </div>
                  <div
                    style={{
                      marginTop: 14,
                      fontFamily: font.ui,
                      fontSize: 19,
                      color: color.textFaint,
                      textAlign: "center",
                      letterSpacing: "0.03em",
                    }}
                  >
                    {name.replace("shots/s-", "").replace(".webp", "")}
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* ---- language chips ---- */}
        <div
          style={{
            padding: "0 120px",
            marginTop: 6,
            display: "flex",
            flexWrap: "wrap",
            gap: 12,
            maxWidth: 1500,
            opacity: interpolate(frame, [40, 70], [0, 1], {
              extrapolateLeft: "clamp",
              extrapolateRight: "clamp",
            }),
          }}
        >
          {LANGS.map((l, i) => (
            <div
              key={l}
              style={{
                fontFamily: font.ui,
                fontSize: 21,
                color: color.textDim,
                padding: "8px 18px",
                borderRadius: 999,
                border: `1px solid ${color.glassEdge}`,
                background: "rgba(255,255,255,0.035)",
                transform: `scale(${interpolate(
                  frame,
                  [70 + i * 4, 84 + i * 4],
                  [0.9, 1],
                  { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
                )})`,
                opacity: interpolate(
                  frame,
                  [70 + i * 4, 88 + i * 4],
                  [0, 1],
                  { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
                ),
              }}
            >
              {l}
            </div>
          ))}
        </div>
      </AbsoluteFill>

      {/* ---- RTL finale: proof, not a claim ---- */}
      <AbsoluteFill
        style={{
          alignItems: "center",
          justifyContent: "flex-end",
          paddingBottom: 54,
          opacity: rtlIn,
          transform: `translateY(${(1 - rtlIn) * 40}px)`,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 40 }}>
          <div style={{ ...glassPanel, padding: 9, boxShadow: "0 30px 70px rgba(0,0,0,0.6)" }}>
            <Img
              src={staticFile("shots/i18n-ar-settings.webp")}
              style={{ width: 620, display: "block", borderRadius: 14 }}
            />
          </div>
          <div style={{ width: 560 }}>
            <div
              style={{
                fontFamily: font.ui,
                fontSize: 34,
                fontWeight: 660,
                color: color.text,
                lineHeight: 1.25,
              }}
            >
              Right-to-left isn't a flag.
            </div>
            <Sub size={24} style={{ marginTop: 14 }}>
              Arabic mirrors the whole layout — sidebar, meters, controls. The
              same fifteen languages are in the setup wizard and the settings
              window, and none of them overflow.
            </Sub>
          </div>
        </div>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
