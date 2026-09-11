import { Config } from "@remotion/cli/config";

Config.setEntryPoint("src/index.ts");

// The scenes are flat UI on a near-flat gradient, so a high-quality JPEG frame
// is visually identical to PNG here and renders considerably faster.
Config.setVideoImageFormat("jpeg");
Config.setJpegQuality(95);

Config.setCodec("h264");
// 22 keeps small UI text crisp while cutting roughly a third off the file size
// versus 19 — worth it for a clip that lives in a git repository.
Config.setCrf(22);
Config.setPixelFormat("yuv420p");

// Upward-only mixing keeps the music bed under the SFX instead of ducking it.
Config.setConcurrency(4);

Config.overrideWebpackConfig((cfg) => cfg);
