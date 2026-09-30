import React from "react";
import { AbsoluteFill, interpolate, spring, useCurrentFrame } from "remotion";
import { Bg, Chip, Code, E, H, Kicker, P, Pop, Slam } from "./ui";
import { C, display, mono, s } from "./theme";

/* Real run on 8 Oct (scripts/try_plan.py, 2026-09-30): Comedy Cartel At Glocal 7:00–8:30 PM, ₹500,
   FREE tables 9:00 PM at China Gate 0.4 km, Boba Bhai 0.6 km, CocoBroma 0.8 km from the venue. */

const T0 = 18.75; // 6:45 PM
const T1 = 22.25; // 10:15 PM
const X0 = 150;
const X1 = 1770;
const hx = (h: number) => X0 + ((h - T0) / (T1 - T0)) * (X1 - X0);

export const Chaining: React.FC = () => {
  const frame = useCurrentFrame();
  const sp = (at: number, d = 14) => spring({ frame: frame - s(at), fps: 30, config: { damping: d } });
  const show = sp(1.6);
  const buffer = sp(3.0);
  const table = sp(4.2, 10);
  const ring = sp(6.2, 18);
  const dots = [
    { name: "China Gate", km: 0.4, a: -50 },
    { name: "Boba Bhai", km: 0.6, a: 15 },
    { name: "CocoBroma", km: 0.8, a: 80 },
  ];
  const R = 250; // px for 5 km
  return (
    <AbsoluteFill>
      <Bg />
      <div style={{ position: "absolute", left: 150, top: 70, right: 150 }}>
        <Kicker>Multi-inventory chaining</Kicker>
        <H size={64} style={{ marginTop: 12 }}>
          Scenes show <span style={{ color: C.muted }}>→</span> travel buffer <span style={{ color: C.muted }}>→</span>{" "}
          <span style={{ color: C.orange }}>Dineout table</span>
        </H>
        <Pop at={0.5}>
          <P size={25} style={{ marginTop: 14 }}>
            At 7:39 PM, tonight's nearby shows were sold out or too far to reach, so the bot honestly planned dinner. Same engine, a date with a show (real run, 8 Oct):
          </P>
        </Pop>
      </div>

      {/* timeline */}
      <div style={{ position: "absolute", left: 0, top: 360, width: 1920, height: 200 }}>
        <div style={{ position: "absolute", left: X0, width: X1 - X0, top: 120, height: 3, background: C.line }} />
        {[19, 20, 21, 22].map((h) => (
          <div key={h} style={{ position: "absolute", left: hx(h) - 40, top: 136, width: 80, textAlign: "center", fontFamily: mono, fontSize: 20, color: C.muted }}>
            {h - 12} PM
          </div>
        ))}
        <div
          style={{
            position: "absolute",
            left: hx(19),
            top: 40,
            height: 70,
            width: (hx(20.5) - hx(19)) * show,
            background: C.orange,
            borderRadius: 14,
            overflow: "hidden",
            display: "flex",
            alignItems: "center",
            paddingLeft: 20,
            fontFamily: display,
            fontWeight: 800,
            fontSize: 26,
            color: C.bg,
            whiteSpace: "nowrap",
          }}
        >
          <E>🎭</E>&nbsp;Comedy Cartel @ Glocal · 7:00 → 8:30 PM
        </div>
        <div style={{ position: "absolute", left: hx(19), top: -40, opacity: show, fontFamily: mono, fontSize: 20, color: C.orangeSoft }}>
          Scenes: real end time from list_event_shows · ₹500 ticket
        </div>
        <div
          style={{
            position: "absolute",
            left: hx(20.5) + 6,
            top: 52,
            height: 46,
            width: (hx(20.5 + 20 / 60) - hx(20.5) - 6) * buffer,
            borderRadius: 10,
            border: `2px dashed ${C.muted}`,
          }}
        />
        <div style={{ position: "absolute", left: hx(20.5) + 8, top: 4, opacity: buffer, fontFamily: mono, fontSize: 20, color: C.text }}>+20 min</div>
        <div
          style={{
            position: "absolute",
            left: hx(21) - 10,
            top: 30,
            opacity: table,
            transform: `scale(${interpolate(table, [0, 1], [0.4, 1])})`,
            transformOrigin: "left center",
            background: C.green,
            color: C.bg,
            borderRadius: 14,
            padding: "20px 22px",
            fontFamily: display,
            fontWeight: 800,
            fontSize: 26,
            whiteSpace: "nowrap",
          }}
        >
          <E>🍽</E> 9:00 PM · FREE table · China Gate
        </div>
      </div>

      {/* map */}
      <div style={{ position: "absolute", left: 230, top: 600, width: 620, height: 420 }}>
        <div
          style={{
            position: "absolute",
            left: 310 - R * ring,
            top: 210 - R * ring,
            width: 2 * R * ring,
            height: 2 * R * ring,
            borderRadius: "50%",
            border: `2px solid ${C.orange}`,
            background: "rgba(252,128,25,0.07)",
          }}
        />
        <div style={{ position: "absolute", left: 310 + R * 0.72 * ring, top: 210 - R * 0.72 * ring, opacity: ring, fontFamily: mono, fontSize: 20, color: C.orange }}>≤ 5 km</div>
        <div style={{ position: "absolute", left: 290, top: 180, fontSize: 44, opacity: ring }}>
          <E>📍</E>
        </div>
        <div style={{ position: "absolute", left: 180, top: 196, opacity: ring, fontFamily: display, fontWeight: 700, fontSize: 24, color: C.orange }}>Glocal</div>
        {dots.map((d, i) => {
          const p = sp(7.0 + i * 0.35, 11);
          const r = (d.km / 5) * R * 2.2;
          const x = 310 + Math.cos((d.a * Math.PI) / 180) * r;
          const y = 210 + Math.sin((d.a * Math.PI) / 180) * r;
          return (
            <div key={d.name} style={{ position: "absolute", left: x - 9, top: y - 9, opacity: p, transform: `scale(${p})` }}>
              <div style={{ width: 18, height: 18, borderRadius: "50%", background: C.green }} />
              <div style={{ position: "absolute", left: 26, top: -8, fontFamily: display, fontWeight: 600, fontSize: 21, color: C.text, whiteSpace: "nowrap" }}>
                {d.name} · {d.km} km
              </div>
            </div>
          );
        })}
      </div>

      {/* the actual check code */}
      <div style={{ position: "absolute", left: 960, top: 640, width: 800 }}>
        <Code
          at={8.4}
          step={0.6}
          size={21}
          lines={[
            { t: "# plan_bana/checks.py: pure, deterministic", c: C.muted },
            { t: "if known and dist > MAX_VENUE_TO_TABLE_KM:  # 5 km" },
            { t: "if rtime < event.end + gap * 60:            # +20/45 min" },
            { t: "if c.budget and per_head > c.budget:        # ticket + food" },
            { t: "if any(p.veg for p in people) and not vegf:" },
            { t: "    → plan dropped, reason shown in Hinglish", c: C.orange },
          ]}
        />
      </div>
    </AbsoluteFill>
  );
};

const STATS = [
  { n: 174, label: "automated tests", suffix: "" },
  { n: 8, label: "real-Gemini evals: Hinglish, हिंदी, sarcasm", suffix: "/8" },
  { n: 6, label: "code checks on every plan", suffix: "" },
  { n: 4, label: "bot messages per plan, edited in place", suffix: "" },
  { n: 0, label: "payments. FREE tables only", suffix: "₹" },
];

export const Stats: React.FC = () => {
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill>
      <Bg />
      <div style={{ position: "absolute", left: 150, top: 90 }}>
        <Kicker>Engineering, not AI slop</Kicker>
        <H size={64} style={{ marginTop: 12 }}>Built like it will run in production.</H>
      </div>
      <div style={{ position: "absolute", left: 150, top: 300, display: "grid", gridTemplateColumns: "1fr 1fr", columnGap: 90, rowGap: 38, width: 1620 }}>
        {STATS.map((st, i) => {
          const p = spring({ frame: frame - s(0.5 + i * 0.5), fps: 30, config: { damping: 12 } });
          const val = Math.round(interpolate(p, [0, 1], [0, st.n]));
          const shown = st.suffix === "₹" ? `₹${val}` : `${val}${st.suffix}`;
          return (
            <div key={st.label} style={{ display: "flex", alignItems: "baseline", gap: 26, opacity: Math.min(1, p * 1.5) }}>
              <div style={{ fontFamily: display, fontWeight: 800, fontSize: 110, color: i === 4 ? C.orange : C.text, minWidth: 250, letterSpacing: "-0.04em" }}>{shown}</div>
              <div style={{ fontFamily: display, fontSize: 32, color: C.muted, maxWidth: 480 }}>{st.label}</div>
            </div>
          );
        })}
      </div>
      <div style={{ position: "absolute", left: 150, bottom: 90, display: "flex", gap: 16, flexWrap: "wrap", width: 1620 }}>
        {["Compare-and-set state machine", "🔒 BOOKING lock: never books twice", "Live re-fetch before booking", "DRY_RUN hard lock", "Gemini sandboxed to extraction"].map((t, i) => (
          <Pop key={t} at={3.2 + i * 0.3}>
            <Chip size={24}>{t}</Chip>
          </Pop>
        ))}
      </div>
    </AbsoluteFill>
  );
};

export const End: React.FC = () => {
  const frame = useCurrentFrame();
  const fade = interpolate(frame, [s(7.5), s(9)], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  return (
    <AbsoluteFill style={{ opacity: fade }}>
      <Bg />
      <AbsoluteFill style={{ justifyContent: "center", alignItems: "center", gap: 20 }}>
        <Slam at={0.2} size={150}>Chaos in.</Slam>
        <Slam at={0.8} size={150} color={C.orange}>
          Plans out.
        </Slam>
        <Pop at={2.0}>
          <div style={{ display: "flex", alignItems: "center", gap: 20, marginTop: 40 }}>
            <div style={{ width: 38, height: 38, borderRadius: "50%", background: C.orange }} />
            <div style={{ fontFamily: display, fontWeight: 800, fontSize: 64, color: C.text }}>Plan Bana</div>
          </div>
        </Pop>
        <Pop at={2.6}>
          <P size={32} style={{ color: C.text, textAlign: "center" }}>
            Harsh Patil · built on Swiggy Builders Club MCP (Scenes + Dineout)
          </P>
        </Pop>
        <Pop at={3.1}>
          <div style={{ fontFamily: mono, fontSize: 28, color: C.orangeSoft, marginTop: 8 }}>github.com/Guten-Morgen1302/plan-bana</div>
        </Pop>
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
