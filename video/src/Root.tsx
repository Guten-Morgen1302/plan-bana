import React from "react";
import { AbsoluteFill, Composition, Series } from "remotion";
import { AppScene, CardScene, ChatScene, DecideScene, PartyScene, PlansScene, SamjhaScene, WhatsAppScene } from "./footage";
import { ColdOpen, Logo, Problem } from "./intro";
import { Chaining, End, Stats } from "./outro";
import { FPS, s } from "./theme";

// Footage scenes last exactly as long as their clip (video/public/clips, cut by video/cut_clips.sh).
const SCENES: { C: React.FC; d: number }[] = [
  { C: ColdOpen, d: s(6) },
  { C: Logo, d: s(4.5) },
  { C: Problem, d: s(8) },
  { C: ChatScene, d: s(14.0) },
  { C: SamjhaScene, d: s(10.0) },
  { C: PlansScene, d: s(8.2) },
  { C: Chaining, d: s(15) },
  { C: DecideScene, d: s(18.3) },
  { C: CardScene, d: s(9.0) },
  { C: AppScene, d: s(10.0) },
  { C: WhatsAppScene, d: s(8.7) },
  { C: PartyScene, d: s(7.4) },
  { C: Stats, d: s(10) },
  { C: End, d: s(9) },
];

export const TOTAL = SCENES.reduce((a, b) => a + b.d, 0);

const Main: React.FC = () => (
  <AbsoluteFill style={{ background: "#0c0b10" }}>
    <Series>
      {SCENES.map(({ C, d }, i) => (
        <Series.Sequence key={i} durationInFrames={d}>
          <C />
        </Series.Sequence>
      ))}
    </Series>
  </AbsoluteFill>
);

export const Root: React.FC = () => (
  <Composition id="PlanBana" component={Main} durationInFrames={TOTAL} fps={FPS} width={1920} height={1080} />
);
