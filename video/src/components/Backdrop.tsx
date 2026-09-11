import React from "react";
import { AbsoluteFill, interpolate, useCurrentFrame } from "remotion";
import { color } from "../theme";

/**
 * The room the whole video sits in.
 *
 * Two very large, very soft colour blooms drift against a dark gradient — the
 * same relationship the app's own window has, so a scene change never feels
 * like leaving the product. Everything is CSS gradients, so it costs a render
 * almost nothing.
 */
export const Backdrop: React.FC<{
  /** Shifts the palette per scene: "blue" | "teal" | "violet" | "amber". */
  mood?: "blue" | "teal" | "violet" | "amber";
  fadeIn?: number;
}> = ({ mood = "blue", fadeIn = 18 }) => {
  const frame = useCurrentFrame();

  const tint = {
    blue: [color.accent, color.violet],
    teal: [color.teal, color.accent],
    violet: [color.violet, color.accent],
    amber: [color.amber, color.accent],
  }[mood];

  // Slow, non-looping drift so no two moments look identical.
  const x1 = 22 + 9 * Math.sin(frame / 210);
  const y1 = 26 + 7 * Math.cos(frame / 260);
  const x2 = 78 + 8 * Math.cos(frame / 230);
  const y2 = 74 + 6 * Math.sin(frame / 300);

  const opacity = interpolate(frame, [0, fadeIn], [0, 1], {
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill
      style={{
        backgroundColor: color.bg0,
        backgroundImage: `linear-gradient(155deg, ${color.bg1} 0%, ${color.bg0} 46%, ${color.bg2} 100%)`,
        opacity,
      }}
    >
      <AbsoluteFill
        style={{
          background: `radial-gradient(46% 52% at ${x1}% ${y1}%, ${tint[0]}33 0%, transparent 62%)`,
        }}
      />
      <AbsoluteFill
        style={{
          background: `radial-gradient(42% 48% at ${x2}% ${y2}%, ${tint[1]}26 0%, transparent 60%)`,
        }}
      />
      {/* A faint technical grid: gives the flat areas something to sit on. */}
      <AbsoluteFill
        style={{
          backgroundImage:
            "linear-gradient(rgba(255,255,255,0.028) 1px, transparent 1px)," +
            "linear-gradient(90deg, rgba(255,255,255,0.028) 1px, transparent 1px)",
          backgroundSize: "68px 68px",
          maskImage:
            "radial-gradient(70% 60% at 50% 45%, black 20%, transparent 78%)",
          WebkitMaskImage:
            "radial-gradient(70% 60% at 50% 45%, black 20%, transparent 78%)",
        }}
      />
      {/* Vignette, so the corners never compete with the subject. */}
      <AbsoluteFill
        style={{
          background:
            "radial-gradient(120% 100% at 50% 50%, transparent 40%, rgba(0,0,0,0.55) 100%)",
        }}
      />
    </AbsoluteFill>
  );
};
