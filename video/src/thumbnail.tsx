import React from "react";
import { AbsoluteFill, Img, staticFile } from "remotion";
import { C, display, emoji, mono } from "./theme";

/** YouTube thumbnail 1280×720: messy chat → booked table, both real screenshots from the 30 Sep recording. */
const Shot: React.FC<{ src: string; rotate: number; left: number; top: number; h: number; crop: number }> = ({
  src,
  rotate,
  left,
  top,
  h,
  crop,
}) => {
  const w = h * crop; // frame aspect; the screenshot covers it from the top (keyboard cropped away)
  return (
    <div
      style={{
        position: "absolute",
        left,
        top,
        width: w + 16,
        height: h + 16,
        padding: 8,
        borderRadius: 34,
        background: "#050507",
        transform: `rotate(${rotate}deg)`,
        boxShadow: "0 30px 70px rgba(0,0,0,0.6), 0 0 0 2px #2b2833",
      }}
    >
      <div style={{ width: w, height: h, borderRadius: 26, overflow: "hidden" }}>
        <Img src={staticFile(src)} style={{ width: w, height: h, objectFit: "cover", objectPosition: "top" }} />
      </div>
    </div>
  );
};

export const Thumbnail: React.FC = () => (
  <AbsoluteFill style={{ background: `radial-gradient(circle at 78% 40%, #3a1d08 0%, ${C.bg} 55%)`, overflow: "hidden" }}>
    {/* left: headline */}
    <div style={{ position: "absolute", left: 56, top: 52, width: 640 }}>
      <div
        style={{
          display: "inline-block",
          fontFamily: mono,
          fontWeight: 700,
          fontSize: 24,
          letterSpacing: "0.12em",
          color: C.bg,
          background: C.orange,
          padding: "8px 16px",
          borderRadius: 10,
        }}
      >
        BUILT ON SWIGGY MCP
      </div>
      <div style={{ fontFamily: display, fontWeight: 800, fontSize: 112, lineHeight: 0.9, letterSpacing: "-0.045em", color: C.text, marginTop: 26 }}>
        Group chat
      </div>
      <div style={{ fontFamily: display, fontWeight: 800, fontSize: 112, lineHeight: 0.9, letterSpacing: "-0.045em", color: C.orange, marginTop: 6 }}>
        → table
        <br />
        booked.
      </div>
      <div style={{ fontFamily: display, fontWeight: 700, fontSize: 38, color: C.text, marginTop: 34, display: "flex", alignItems: "center", gap: 16 }}>
        <span style={{ fontFamily: emoji }}>🤖</span> AI bot · <span style={{ color: C.green }}>₹0 paid</span>
      </div>
    </div>

    {/* right: the two real screens */}
    <Shot src="thumb/chat.png" rotate={-8} left={712} top={150} h={470} crop={0.66} />
    <Shot src="thumb/booked.png" rotate={6} left={935} top={62} h={500} crop={0.6} />
    <div
      style={{
        position: "absolute",
        left: 880,
        top: 540,
        width: 96,
        height: 96,
        borderRadius: "50%",
        background: C.orange,
        display: "grid",
        placeItems: "center",
        fontFamily: display,
        fontWeight: 800,
        fontSize: 60,
        color: C.bg,
        boxShadow: "0 12px 40px rgba(252,128,25,0.55)",
      }}
    >
      →
    </div>
    <div
      style={{
        position: "absolute",
        right: 36,
        bottom: 30,
        fontFamily: display,
        fontWeight: 800,
        fontSize: 34,
        color: C.text,
        background: "rgba(12,11,16,0.85)",
        padding: "10px 20px",
        borderRadius: 14,
        border: `2px solid ${C.orange}`,
      }}
    >
      <span style={{ color: C.orange }}>●</span> Plan Bana
    </div>
  </AbsoluteFill>
);
