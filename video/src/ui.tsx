import React from "react";
import {
  AbsoluteFill,
  Easing,
  interpolate,
  OffthreadVideo,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { C, display, emoji, mono, s } from "./theme";

/** Spring-in from frame `at` (seconds, relative to the current Sequence). */
export const useIn = (at: number, damping = 14) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  return spring({ frame: frame - s(at), fps, config: { damping, stiffness: 140, mass: 0.7 } });
};

export const Pop: React.FC<{ at: number; children: React.ReactNode; from?: "up" | "left" | "scale"; style?: React.CSSProperties }> = ({
  at,
  children,
  from = "up",
  style,
}) => {
  const p = useIn(at);
  const t =
    from === "left"
      ? `translateX(${interpolate(p, [0, 1], [-60, 0])}px)`
      : from === "scale"
        ? `scale(${interpolate(p, [0, 1], [0.6, 1])})`
        : `translateY(${interpolate(p, [0, 1], [40, 0])}px)`;
  return <div style={{ opacity: Math.min(1, p * 1.4), transform: t, ...style }}>{children}</div>;
};

/** Slams in big then settles: for the punchy lines. */
export const Slam: React.FC<{ at: number; children: React.ReactNode; size?: number; color?: string; style?: React.CSSProperties }> = ({
  at,
  children,
  size = 120,
  color = C.text,
  style,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const p = spring({ frame: frame - s(at), fps, config: { damping: 11, stiffness: 220, mass: 0.6 } });
  const scale = interpolate(p, [0, 1], [2.4, 1]);
  return (
    <div
      style={{
        fontFamily: display,
        fontWeight: 800,
        fontSize: size,
        lineHeight: 0.95,
        letterSpacing: "-0.03em",
        color,
        opacity: p > 0.01 ? 1 : 0,
        transform: `scale(${scale})`,
        filter: `blur(${interpolate(p, [0, 0.6, 1], [14, 2, 0])}px)`,
        ...style,
      }}
    >
      {children}
    </div>
  );
};

export const Bg: React.FC = () => {
  const frame = useCurrentFrame();
  const a = interpolate(frame % 600, [0, 600], [0, 360]);
  return (
    <AbsoluteFill
      style={{
        background: `linear-gradient(${135 + Math.sin((a * Math.PI) / 180) * 8}deg, ${C.bg} 0%, ${C.bg2} 55%, ${C.bg} 100%)`,
      }}
    />
  );
};

export const Kicker: React.FC<{ children: React.ReactNode; color?: string }> = ({ children, color = C.orange }) => (
  <div
    style={{
      fontFamily: mono,
      fontSize: 22,
      letterSpacing: "0.14em",
      textTransform: "uppercase",
      color,
      fontWeight: 700,
    }}
  >
    {children}
  </div>
);

export const H: React.FC<{ children: React.ReactNode; size?: number; style?: React.CSSProperties }> = ({ children, size = 64, style }) => (
  <div style={{ fontFamily: display, fontWeight: 800, fontSize: size, lineHeight: 1.02, letterSpacing: "-0.025em", color: C.text, ...style }}>
    {children}
  </div>
);

export const P: React.FC<{ children: React.ReactNode; size?: number; style?: React.CSSProperties }> = ({ children, size = 30, style }) => (
  <div style={{ fontFamily: display, fontWeight: 400, fontSize: size, lineHeight: 1.3, color: C.muted, ...style }}>{children}</div>
);

export const E: React.FC<{ children: React.ReactNode }> = ({ children }) => <span style={{ fontFamily: emoji }}>{children}</span>;

export const Chip: React.FC<{ children: React.ReactNode; color?: string; bg?: string; size?: number; style?: React.CSSProperties }> = ({
  children,
  color = C.text,
  bg = C.panel,
  size = 28,
  style,
}) => (
  <div
    style={{
      display: "inline-flex",
      alignItems: "center",
      gap: 12,
      padding: "14px 22px",
      borderRadius: 16,
      background: bg,
      border: `1.5px solid ${C.line}`,
      fontFamily: display,
      fontWeight: 600,
      fontSize: size,
      color,
      ...style,
    }}
  >
    {children}
  </div>
);

export const Code: React.FC<{ lines: { t: string; c?: string }[]; at: number; step?: number; size?: number }> = ({ lines, at, step = 0.35, size = 24 }) => {
  const frame = useCurrentFrame();
  return (
    <div
      style={{
        fontFamily: mono,
        fontSize: size,
        lineHeight: 1.55,
        background: "#0f0e14",
        border: `1.5px solid ${C.line}`,
        borderRadius: 18,
        padding: "22px 26px",
        color: C.text,
        whiteSpace: "pre",
      }}
    >
      {lines.map((l, i) => {
        const start = s(at + i * step);
        const n = Math.max(0, Math.min(l.t.length, Math.floor((frame - start) * 2.2)));
        return (
          <div key={i} style={{ color: l.c ?? C.text, minHeight: size * 1.55 }}>
            {l.t.slice(0, n)}
            {n > 0 && n < l.t.length ? <span style={{ color: C.orange }}>▍</span> : null}
          </div>
        );
      })}
    </div>
  );
};

/** The user's real phone recording, in a phone frame. */
export const Phone: React.FC<{ src: string; height?: number }> = ({ src, height = 940 }) => {
  const width = (height * 576) / 1296;
  return (
    <div
      style={{
        width: width + 28,
        height: height + 28,
        borderRadius: 58,
        background: "#050507",
        padding: 14,
        boxShadow: `0 40px 90px rgba(0,0,0,0.55), 0 0 0 2px #2b2833`,
      }}
    >
      <div style={{ width, height, borderRadius: 46, overflow: "hidden", background: "#000" }}>
        <OffthreadVideo src={staticFile(src)} muted style={{ width, height, objectFit: "cover" }} />
      </div>
    </div>
  );
};

/** Left: the real recording. Right: what is happening under the hood. */
export const Footage: React.FC<{ clip: string; tag: string; step: number; children: React.ReactNode }> = ({ clip, tag, step, children }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();
  const inP = spring({ frame, fps, config: { damping: 16 } });
  const out = interpolate(frame, [durationInFrames - 8, durationInFrames], [1, 0], { extrapolateLeft: "clamp" });
  return (
    <AbsoluteFill style={{ opacity: out }}>
      <Bg />
      <div style={{ position: "absolute", left: 150, top: 46, transform: `translateX(${interpolate(inP, [0, 1], [-500, 0])}px) rotate(${interpolate(inP, [0, 1], [-6, 0])}deg)` }}>
        <Phone src={clip} />
        <div style={{ position: "absolute", top: -6, left: 0, right: 0, display: "flex", justifyContent: "center" }}>
          <Chip size={18} bg={C.red} color="#fff" style={{ padding: "6px 14px", borderRadius: 999, border: "none", fontFamily: mono, letterSpacing: "0.08em" }}>
            ● REC · {tag}
          </Chip>
        </div>
      </div>
      <div style={{ position: "absolute", left: 760, right: 110, top: 90, bottom: 150 }}>{children}</div>
      <Steps active={step} />
    </AbsoluteFill>
  );
};

const STEPS = ["Chat", "Samjha", "Plans", "Vote", "Book", "Real"];

export const Steps: React.FC<{ active: number }> = ({ active }) => (
  <div style={{ position: "absolute", left: 760, bottom: 60, display: "flex", gap: 14, alignItems: "center" }}>
    {STEPS.map((name, i) => (
      <React.Fragment key={name}>
        <div
          style={{
            fontFamily: mono,
            fontSize: 20,
            fontWeight: 700,
            letterSpacing: "0.08em",
            textTransform: "uppercase",
            color: i === active ? C.bg : i < active ? C.text : C.muted,
            background: i === active ? C.orange : "transparent",
            border: `1.5px solid ${i === active ? C.orange : C.line}`,
            padding: "8px 14px",
            borderRadius: 10,
          }}
        >
          {i + 1} {name}
        </div>
        {i < STEPS.length - 1 ? <div style={{ width: 18, height: 2, background: C.line }} /> : null}
      </React.Fragment>
    ))}
  </div>
);

export const ease = Easing.bezier(0.16, 1, 0.3, 1);
