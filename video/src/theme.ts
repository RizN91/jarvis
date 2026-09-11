/**
 * The video's design tokens.
 *
 * These are lifted from the application's own dark theme so the video and the
 * product look like the same thing — a promo that uses different colours to the
 * app it is advertising reads as a mockup.
 */
export const color = {
  bg0: "#080a12",
  bg1: "#0d1220",
  bg2: "#111a2e",
  accent: "#3b8bfa",
  accentDeep: "#1d4ed8",
  teal: "#28cdc3",
  violet: "#7c6cf0",
  amber: "#fbbf24",
  danger: "#f26060",
  text: "#eaf0fb",
  textDim: "#a9b6cf",
  textFaint: "#6d7b95",
  glass: "rgba(255,255,255,0.045)",
  glassEdge: "rgba(255,255,255,0.13)",
} as const;

export const font = {
  // Matches the app: Windows 11's UI font, falling back sensibly elsewhere.
  ui: "'Segoe UI Variable Display', 'Segoe UI', system-ui, -apple-system, sans-serif",
  mono: "'Cascadia Code', 'Consolas', ui-monospace, monospace",
} as const;

export const glassPanel: React.CSSProperties = {
  background: color.glass,
  border: `1px solid ${color.glassEdge}`,
  borderRadius: 22,
  backdropFilter: "blur(18px)",
};

/** A soft coloured bloom, used behind the orb and behind headlines. */
export const bloom = (rgb: string, size: number, opacity = 0.5): React.CSSProperties => ({
  position: "absolute",
  width: size,
  height: size,
  borderRadius: "50%",
  background: `radial-gradient(circle, ${rgb} 0%, transparent 68%)`,
  opacity,
  filter: "blur(28px)",
  pointerEvents: "none",
});
