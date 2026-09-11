import React, { useMemo } from "react";
import {
  Img,
  getStaticFiles,
  staticFile,
  useCurrentFrame,
} from "remotion";

/**
 * Plays a folder of numbered PNGs as an animation.
 *
 * The file list is discovered from the public dir rather than hard-coded, so
 * re-running `tools/make_pill.py` (which may yield a different frame count)
 * cannot silently desynchronise the video from the app it is showing.
 */
export const FrameSequence: React.FC<{
  /** Folder under public/, e.g. "pill/dictating". */
  dir: string;
  /** Video frames to hold each image for. 1 = play at the source rate. */
  hold?: number;
  /** Play backwards from the end — used for the pill collapsing. */
  reverse?: boolean;
  /** Clamp to the first image before the sequence starts, rather than hiding. */
  startAt?: number;
  style?: React.CSSProperties;
}> = ({ dir, hold = 1, reverse = false, startAt = 0, style }) => {
  const frame = useCurrentFrame();

  const files = useMemo(() => {
    const prefix = `${dir}/`;
    return getStaticFiles()
      .filter((f) => f.name.startsWith(prefix) && f.name.endsWith(".png"))
      .map((f) => f.name)
      .sort();
  }, [dir]);

  if (files.length === 0) {
    // Loud in the studio, harmless in a render: better than a blank frame that
    // nobody notices until the video is published.
    return (
      <div
        style={{
          width: 940,
          height: 170,
          border: "2px dashed #f26060",
          color: "#f26060",
          fontFamily: "monospace",
          fontSize: 22,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          ...style,
        }}
      >
        missing frames: public/{dir}/
      </div>
    );
  }

  const local = Math.max(0, frame - startAt);
  let index = Math.floor(local / hold);
  if (reverse) {
    index = files.length - 1 - index;
  }
  index = Math.max(0, Math.min(files.length - 1, index));

  return <Img src={staticFile(files[index])} style={style} />;
};
