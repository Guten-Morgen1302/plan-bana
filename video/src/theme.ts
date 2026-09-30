import { loadFont as loadDisplay } from "@remotion/google-fonts/BricolageGrotesque";
import { loadFont as loadMono } from "@remotion/google-fonts/JetBrainsMono";

export const display = loadDisplay("normal", { weights: ["400", "600", "800"], subsets: ["latin"] }).fontFamily;
export const mono = loadMono("normal", { weights: ["400", "700"], subsets: ["latin"] }).fontFamily;
export const emoji = '"Segoe UI Emoji", "Apple Color Emoji", "Noto Color Emoji"';

export const C = {
  bg: "#0c0b10",
  bg2: "#15121c",
  panel: "#18161f",
  line: "#2c2935",
  text: "#f6f3ee",
  muted: "#9d99a9",
  orange: "#fc8019", // Swiggy orange: the one accent
  orangeSoft: "#ff9e4d",
  green: "#3ddc84",
  red: "#ff5c61",
  tg: "#2aabee",
};

export const FPS = 30;
export const s = (seconds: number) => Math.round(seconds * FPS);
