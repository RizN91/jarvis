import React from "react";
import { interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { color, font } from "../theme";

/** Fade + rise. The default entrance for anything that is not the headline. */
export const Reveal: React.FC<{
  delay?: number;
  children: React.ReactNode;
  /** Movement in px; negative comes down from above. */
  from?: number;
  style?: React.CSSProperties;
}> = ({ delay = 0, from = 26, children, style }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = spring({ frame: frame - delay, fps, config: { damping: 200 } });
  return (
    <div
      style={{
        opacity: t,
        transform: `translateY(${(1 - t) * from}px)`,
        ...style,
      }}
    >
      {children}
    </div>
  );
};

export const Headline: React.FC<{
  children: React.ReactNode;
  size?: number;
  weight?: number;
  colorOverride?: string;
  style?: React.CSSProperties;
}> = ({ children, size = 96, weight = 680, colorOverride, style }) => (
  <div
    style={{
      fontFamily: font.ui,
      fontSize: size,
      fontWeight: weight,
      lineHeight: 1.08,
      letterSpacing: "-0.022em",
      color: colorOverride ?? color.text,
      ...style,
    }}
  >
    {children}
  </div>
);

export const Sub: React.FC<{
  children: React.ReactNode;
  size?: number;
  style?: React.CSSProperties;
}> = ({ children, size = 30, style }) => (
  <div
    style={{
      fontFamily: font.ui,
      fontSize: size,
      fontWeight: 420,
      lineHeight: 1.45,
      color: color.textDim,
      letterSpacing: "-0.006em",
      ...style,
    }}
  >
    {children}
  </div>
);

/** Small uppercase label, e.g. a section tag. */
export const Kicker: React.FC<{ children: React.ReactNode; tint?: string }> = ({
  children,
  tint = color.accent,
}) => (
  <div
    style={{
      fontFamily: font.ui,
      fontSize: 20,
      fontWeight: 660,
      letterSpacing: "0.20em",
      textTransform: "uppercase",
      color: tint,
    }}
  >
    {children}
  </div>
);

/**
 * A highlight that sweeps on behind the text, then holds.
 * `progress` is driven by the caller so it can be timed to a word.
 */
export const Sweep: React.FC<{
  at: number;
  dur?: number;
  children: React.ReactNode;
  tint?: string;
}> = ({ at, dur = 22, children, tint = color.accent }) => {
  const frame = useCurrentFrame();
  const p = interpolate(frame, [at, at + dur], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return (
    <span style={{ position: "relative", display: "inline-block" }}>
      <span
        style={{
          position: "absolute",
          left: -4,
          right: -4,
          bottom: 0.06,
          height: "0.62em",
          background: tint,
          opacity: 0.26,
          borderRadius: 6,
          transform: `scaleX(${p})`,
          transformOrigin: "left center",
        }}
      />
      <span style={{ position: "relative" }}>{children}</span>
    </span>
  );
};

/** A wide, quiet rule used to separate beats inside a scene. */
export const Rule: React.FC<{ width?: number; tint?: string }> = ({
  width = 90,
  tint = color.accent,
}) => (
  <div
    style={{
      width,
      height: 4,
      borderRadius: 2,
      background: `linear-gradient(90deg, ${tint}, ${tint}00)`,
    }}
  />
);
