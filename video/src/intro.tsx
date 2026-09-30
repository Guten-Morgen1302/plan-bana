import React from "react";
import { AbsoluteFill, interpolate, random, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { Bg, E, H, Kicker, P, Pop, Slam } from "./ui";
import { C, display, mono, s } from "./theme";

const CHAOS = [
  "kab?", "kal?", "sat raat?", "kuch bhi chalega", "budget?", "mujhe nahi pata", "tum batao", "comedy?",
  "nahi yaar music", "main veg hu", "kitne log?", "800 max", "door hai", "kal pakka", "sun better",
  "aaj nahi ho payega", "koi toh decide karo", "?", "??", "chal theek hai", "kaun book karega", "seen ✓✓",
  "haan", "dekhte hai", "baad mein batata", "sab free ho?", "pizza?", "biryani!", "phir se 10 baje?",
];

/** 0–6 s: the group chat nobody can finish. */
export const ColdOpen: React.FC = () => {
  const frame = useCurrentFrame();
  const freeze = s(3.6);
  const f = Math.min(frame, freeze);
  const count = Math.min(99, Math.floor(interpolate(f, [0, freeze], [3, 99])));
  const shake = frame < freeze ? (random(`sh${frame}`) - 0.5) * 10 : 0;
  const dim = interpolate(frame, [freeze, freeze + 10], [1, 0.18], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return (
    <AbsoluteFill style={{ overflow: "hidden" }}>
      <Bg />
      <AbsoluteFill style={{ opacity: dim, transform: `translate(${shake}px, ${-shake / 2}px)` }}>
        {Array.from({ length: 70 }).map((_, i) => {
          const born = i * 1.6;
          if (f < born) return null;
          const left = random(`l${i}`) > 0.5;
          const x = left ? 60 + random(`x${i}`) * 650 : 1180 + random(`x${i}`) * 560;
          const y = 1080 - (f - born) * 9 - random(`y${i}`) * 80;
          const text = CHAOS[i % CHAOS.length];
          const p = Math.min(1, (f - born) / 5);
          return (
            <div
              key={i}
              style={{
                position: "absolute",
                left: x,
                top: y,
                transform: `scale(${0.6 + p * 0.4}) rotate(${(random(`r${i}`) - 0.5) * 6}deg)`,
                opacity: p,
                fontFamily: display,
                fontSize: 30 + random(`s${i}`) * 16,
                fontWeight: 600,
                color: left ? C.text : "#fff",
                background: left ? "#262330" : "#5b3fa8",
                padding: "12px 22px",
                borderRadius: left ? "22px 22px 22px 6px" : "22px 22px 6px 22px",
                whiteSpace: "nowrap",
              }}
            >
              {text}
            </div>
          );
        })}
        <div
          style={{
            position: "absolute",
            right: 70,
            top: 50,
            fontFamily: mono,
            fontWeight: 700,
            fontSize: 44,
            color: "#fff",
            background: C.red,
            borderRadius: 999,
            padding: "10px 26px",
          }}
        >
          {count === 99 ? "99+" : count}
        </div>
      </AbsoluteFill>
      <AbsoluteFill style={{ justifyContent: "center", alignItems: "center", gap: 10 }}>
        <Slam at={3.7} size={150}>50 messages.</Slam>
        <Slam at={4.4} size={150} color={C.orange}>
          0 plans.
        </Slam>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};

/** 6–11 s: everything collapses into one orange dot → Plan Bana. */
export const Logo: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const dot = spring({ frame, fps, config: { damping: 9, stiffness: 120 } });
  const grow = spring({ frame: frame - 14, fps, config: { damping: 14 } });
  const size = interpolate(dot, [0, 1], [900, 46]);
  const letters = "Plan Bana".split("");
  return (
    <AbsoluteFill style={{ justifyContent: "center", alignItems: "center" }}>
      <Bg />
      <div
        style={{
          position: "absolute",
          width: size,
          height: size,
          borderRadius: "50%",
          background: C.orange,
          opacity: interpolate(grow, [0, 1], [1, 0]),
        }}
      />
      <div style={{ display: "flex", alignItems: "center", gap: 28 }}>
        <div style={{ width: 70 * grow, height: 70 * grow, borderRadius: "50%", background: C.orange }} />
        <div style={{ display: "flex" }}>
          {letters.map((ch, i) => {
            const p = spring({ frame: frame - 16 - i * 2, fps, config: { damping: 12 } });
            return (
              <span
                key={i}
                style={{
                  fontFamily: display,
                  fontWeight: 800,
                  fontSize: 200,
                  letterSpacing: "-0.04em",
                  color: C.text,
                  whiteSpace: "pre",
                  opacity: p,
                  transform: `translateY(${interpolate(p, [0, 1], [80, 0])}px)`,
                  display: "inline-block",
                }}
              >
                {ch}
              </span>
            );
          })}
        </div>
      </div>
      <div style={{ position: "absolute", top: 700 }}>
        <Pop at={1.4}>
          <P size={40} style={{ color: C.text, textAlign: "center" }}>
            Swiggy <b style={{ color: C.orange }}>Scenes</b> + <b style={{ color: C.orange }}>Dineout</b>, inside your group chat
          </P>
        </Pop>
      </div>
    </AbsoluteFill>
  );
};

/** 11–19 s: single-user search bar vs. a group decision. */
export const Problem: React.FC = () => {
  const frame = useCurrentFrame();
  const typed = "comedy near me".slice(0, Math.max(0, Math.floor((frame - 12) / 3)));
  const cursorOn = Math.floor(frame / 8) % 2 === 0;
  const merge = spring({ frame: frame - s(4.2), fps: 30, config: { damping: 14 } });
  const faces = [
    { n: "H", say: "kab?", c: "#5b3fa8" },
    { n: "R", say: "budget 800!", c: "#1f7a5c" },
    { n: "A", say: "main veg hu", c: "#a8553f" },
  ];
  return (
    <AbsoluteFill>
      <Bg />
      <div style={{ position: "absolute", left: 130, top: 150, width: 720 }}>
        <Kicker color={C.muted}>Every app today</Kicker>
        <H size={62} style={{ marginTop: 14 }}>1 person. 1 search bar.</H>
        <Pop at={0.3}>
          <div
            style={{
              marginTop: 60,
              display: "flex",
              alignItems: "center",
              gap: 18,
              background: "#fff",
              borderRadius: 20,
              padding: "26px 30px",
              fontFamily: display,
              fontSize: 38,
              color: "#222",
              width: 620,
            }}
          >
            <E>🔍</E>
            <span>{typed}</span>
            <span style={{ color: C.orange, opacity: cursorOn ? 1 : 0 }}>|</span>
          </div>
        </Pop>
        <Pop at={1.2}>
          <P size={32} style={{ marginTop: 34 }}>
            Great for ordering dinner <E>🍔</E>
          </P>
        </Pop>
      </div>
      <div style={{ position: "absolute", left: 1040, top: 150, width: 760 }}>
        <Kicker>But going out</Kicker>
        <H size={62} style={{ marginTop: 14 }}>
          is a <span style={{ color: C.orange }}>group</span> decision.
        </H>
        <div style={{ position: "relative", height: 420, marginTop: 40 }}>
          {faces.map((f, i) => {
            const p = spring({ frame: frame - s(1.6 + i * 0.5), fps: 30, config: { damping: 12 } });
            const y0 = 20 + i * 130;
            const y = interpolate(merge, [0, 1], [y0, 150]);
            const x = interpolate(merge, [0, 1], [0, 180]);
            return (
              <div key={f.n} style={{ position: "absolute", left: x, top: y, display: "flex", gap: 18, alignItems: "center", opacity: p * interpolate(merge, [0, 1], [1, 0]) }}>
                <div style={{ width: 84, height: 84, borderRadius: "50%", background: f.c, display: "grid", placeItems: "center", fontFamily: display, fontWeight: 800, fontSize: 38, color: "#fff" }}>{f.n}</div>
                <div style={{ fontFamily: display, fontSize: 34, fontWeight: 600, color: C.text, background: "#262330", padding: "12px 22px", borderRadius: "22px 22px 22px 6px" }}>{f.say}</div>
              </div>
            );
          })}
          <div style={{ position: "absolute", left: 150, top: 120, opacity: merge, transform: `scale(${interpolate(merge, [0, 1], [0.5, 1])})` }}>
            <div style={{ fontFamily: display, fontWeight: 800, fontSize: 48, color: C.bg, background: C.orange, padding: "24px 36px", borderRadius: 22 }}>
              <E>✅</E> 1 plan. Booked.
            </div>
          </div>
        </div>
      </div>
    </AbsoluteFill>
  );
};
