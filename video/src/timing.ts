/**
 * Scene boundaries, in frames at 30 fps.
 *
 * Kept in one place so the narration audio, the transitions and the music bed
 * all stay in step: a scene never hard-codes its own length.
 */
export const FPS = 30;
export const WIDTH = 1920;
export const HEIGHT = 1080;

export const SCENES = {
  /** The promise, and the pill appearing for the first time. */
  hook: { from: 0, dur: 150 },
  /** Real footage: dictation landing in a real application. */
  dictation: { from: 150, dur: 540 },
  /** The other half: a voice conversation that can act. */
  assistant: { from: 690, dur: 480 },
  /** Settings, themes and the translated UI. */
  gallery: { from: 1170, dur: 420 },
  /** What it costs, honestly. */
  cost: { from: 1590, dur: 300 },
  /** Why it is safe to run. */
  trust: { from: 1890, dur: 300 },
  /** The ask. */
  cta: { from: 2190, dur: 240 },
} as const;

export type SceneName = keyof typeof SCENES;

export const TOTAL_FRAMES =
  SCENES.cta.from + SCENES.cta.dur; // 2430 frames = 81 s

/** Absolute frame for a point `offset` seconds into a scene. */
export const at = (scene: SceneName, seconds: number): number =>
  SCENES[scene].from + Math.round(seconds * FPS);
