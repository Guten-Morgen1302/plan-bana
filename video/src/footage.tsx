import React from "react";
import { interpolate, spring, useCurrentFrame } from "remotion";
import { Chip, Code, E, Footage, H, Kicker, P, Pop, Slam } from "./ui";
import { C, display, mono, s } from "./theme";

/* Clip times below are seconds inside each sped-up clip (see video/README.md for the source mapping). */

export const ChatScene: React.FC = () => (
  <Footage clip="clips/c1_chat.mp4" tag="30 SEP · 7:38 PM" step={0}>
    <Kicker>Real group chat · 2 people · no commands</Kicker>
    <H size={72} style={{ marginTop: 16 }}>
      No forms.
      <br />
      Just <span style={{ color: C.orange }}>chat.</span>
    </H>
    <div style={{ marginTop: 50, display: "flex", flexDirection: "column", gap: 20, alignItems: "flex-start" }}>
      <Pop at={1.0} from="left"><Chip><E>🌙</E> "aaj raat" → Wed 30 Sep, night</Chip></Pop>
      <Pop at={3.7} from="left"><Chip><E>🎭</E> "comedy show dekhte hai?" → comedy</Chip></Pop>
      <Pop at={6.3} from="left"><Chip><E>💸</E> Hemlata: "budget 800 max"</Chip></Pop>
      <Pop at={10.3} from="left">
        <Chip color={C.bg} bg={C.orange} style={{ border: "none" }}>
          <span style={{ textDecoration: "line-through", opacity: 0.6 }}>₹800</span> → ₹1000 · latest message wins
        </Chip>
      </Pop>
      <Pop at={11.7} from="left"><Chip><E>🥗</E> "me veg hu" → veg: Hemlata</Chip></Pop>
      <Pop at={13.0} from="left"><Chip><E>👥</E> "Aman bhi aayega" → 3 log (1 not in the group)</Chip></Pop>
    </div>
  </Footage>
);

export const SamjhaScene: React.FC = () => {
  const frame = useCurrentFrame();
  const flow = ["Group chat", "Gemini", "JSON facts", "Python rules", "Samjha"];
  return (
    <Footage clip="clips/c2_plan.mp4" tag="/plan" step={1}>
      <Kicker>What it understood</Kicker>
      <H size={72} style={{ marginTop: 16 }}>
        Gemini reads.
        <br />
        <span style={{ color: C.orange }}>Python decides.</span>
      </H>
      <div style={{ display: "flex", alignItems: "center", gap: 14, marginTop: 44 }}>
        {flow.map((name, i) => {
          const p = spring({ frame: frame - s(0.4 + i * 0.45), fps: 30, config: { damping: 13 } });
          const hot = i === 1 || i === 3;
          return (
            <React.Fragment key={name}>
              <div
                style={{
                  opacity: p,
                  transform: `scale(${interpolate(p, [0, 1], [0.7, 1])})`,
                  fontFamily: display,
                  fontWeight: 700,
                  fontSize: 25,
                  padding: "16px 18px",
                  borderRadius: 14,
                  color: hot ? C.bg : C.text,
                  background: hot ? (i === 1 ? C.tg : C.orange) : C.panel,
                  border: `1.5px solid ${hot ? "transparent" : C.line}`,
                  whiteSpace: "nowrap",
                }}
              >
                {name}
              </div>
              {i < flow.length - 1 ? <div style={{ opacity: p, fontFamily: mono, color: C.muted, fontSize: 26 }}>→</div> : null}
            </React.Fragment>
          );
        })}
      </div>
      <div style={{ marginTop: 36 }}>
        <Code
          at={2.4}
          step={0.5}
          size={23}
          lines={[
            { t: "# Gemini's ONLY job: who said what (sandboxed tool call)", c: C.muted },
            { t: '{"message_id": 23, "field": "budget", "value": "800"}' },
            { t: '{"message_id": 25, "field": "budget", "value": "1000"}' },
            { t: '{"message_id": 26, "field": "veg",    "value": "true"}' },
            { t: "# deterministic Python: latest wins, min budget binds", c: C.orange },
          ]}
        />
      </div>
      <Pop at={4.0}>
        <Chip style={{ marginTop: 28 }} size={26}>
          <E>❓</E> Something missing? It asks exactly <b style={{ color: C.orange }}>one</b> question: "kaunsa din?"
        </Chip>
      </Pop>
    </Footage>
  );
};

const CHECKS = [
  "Every ID came from Swiggy's own reply",
  "FREE Dineout table (never a paid deal)",
  "Inside the group's time window",
  "Table after the show + travel buffer",
  "Budget: ticket + ½ cost for two",
  "Veg-friendly if anyone is veg",
];

export const PlansScene: React.FC = () => {
  const frame = useCurrentFrame();
  return (
    <Footage clip="clips/c3_plans.mp4" tag="~40 s LATER" step={2}>
      <Kicker>Real Swiggy inventory, checked in code</Kicker>
      <H size={66} style={{ marginTop: 16 }}>
        3 plans. <span style={{ color: C.orange }}>6 checks each.</span>
      </H>
      <div style={{ marginTop: 40, display: "flex", flexDirection: "column", gap: 16 }}>
        {CHECKS.map((c, i) => {
          const p = spring({ frame: frame - s(0.8 + i * 0.55), fps: 30, config: { damping: 13 } });
          return (
            <div key={c} style={{ display: "flex", alignItems: "center", gap: 18, opacity: p, transform: `translateX(${(1 - p) * -40}px)` }}>
              <div style={{ width: 44, height: 44, borderRadius: 12, background: C.green, color: C.bg, display: "grid", placeItems: "center", fontFamily: display, fontWeight: 800, fontSize: 28 }}>✓</div>
              <div style={{ fontFamily: display, fontSize: 32, fontWeight: 600, color: C.text }}>{c}</div>
            </div>
          );
        })}
      </div>
      <Pop at={4.6}>
        <P size={28} style={{ marginTop: 34 }}>
          A plan that fails any check is <b style={{ color: C.red }}>dropped</b>, never shown. The model can't invent a restaurant, a time or a price.
        </P>
      </Pop>
    </Footage>
  );
};

const STATES = ["VOTING", "RSVP", "BOOK_PENDING", "BOOKING", "BOOKED"];

export const DecideScene: React.FC = () => {
  const frame = useCurrentFrame();
  const t = frame / 30;
  // Clip seconds when each state is reached in the recording (144 s + 2.4× speed).
  const at = [0, 2.5, 8.7, 13.0, 17.4];
  const active = at.filter((x) => t >= x).length - 1;
  const party = t < 3.7 ? "1" : t < 5.0 ? "1 + 1 guest" : "1 + 2 guests";
  const party2 = t >= 10 ? "2 + 2 guests = 4" : `${party}`;
  return (
    <Footage clip="clips/c4_decide.mp4" tag="VOTE → BOOK" step={t < 8.7 ? 3 : 4}>
      <Kicker>Chaos → agreement</Kicker>
      <H size={66} style={{ marginTop: 16 }}>
        Group votes. <span style={{ color: C.orange }}>Organizer books.</span>
      </H>
      <div style={{ display: "flex", gap: 12, marginTop: 44, flexWrap: "wrap" }}>
        {STATES.map((st, i) => {
          const on = i === active;
          const done = i < active;
          return (
            <div
              key={st}
              style={{
                fontFamily: mono,
                fontWeight: 700,
                fontSize: 24,
                padding: "14px 18px",
                borderRadius: 12,
                color: on ? C.bg : done ? C.text : C.muted,
                background: on ? (st === "BOOKED" ? C.green : C.orange) : "transparent",
                border: `2px solid ${on ? "transparent" : done ? C.text : C.line}`,
                transform: `scale(${on ? 1.06 : 1})`,
              }}
            >
              {st === "BOOKING" ? "🔒 " : ""}
              {st}
            </div>
          );
        })}
      </div>
      <div style={{ marginTop: 30, display: "flex", gap: 16, flexWrap: "wrap" }}>
        <Chip size={26}><E>🗳</E> anyone in the group can vote</Chip>
        <Chip size={26}><E>👥</E> table size: {party2}</Chip>
      </div>
      <div style={{ marginTop: 34 }}>
        <Code
          at={9.2}
          step={0.55}
          size={22}
          lines={[
            { t: "recheck(): list_event_shows + get_available_slots", c: C.muted },
            { t: "  slot 8:00 PM still FREE ✓   (live, at tap time)", c: C.green },
            { t: "cas_state(round, 'BOOK_PENDING' → 'BOOKING')  🔒", c: C.orange },
            { t: "  2nd tap / crashed worker → NEEDS_REVIEW, never re-books", c: C.muted },
            { t: "place_order('book_table', guestCount=4) → BOOKED ✓", c: C.green },
          ]}
        />
      </div>
      {t > 17.4 ? (
        <div style={{ position: "absolute", right: 0, top: -30 }}>
          <Slam at={17.4} size={64} color={C.green}>BOOKED ✓</Slam>
        </div>
      ) : null}
    </Footage>
  );
};

export const CardScene: React.FC = () => (
  <Footage clip="clips/c5_map.mp4" tag="PINNED FOR THE GROUP" step={4}>
    <Kicker>The final card</Kicker>
    <H size={70} style={{ marginTop: 16 }}>
      <E>🎉</E> Wed ka plan
      <br />
      <span style={{ color: C.orange }}>pakka!</span>
    </H>
    <div style={{ marginTop: 44, display: "flex", flexDirection: "column", gap: 18, alignItems: "flex-start" }}>
      <Pop at={0.6} from="left"><Chip><E>📌</E> Auto-pinned in the group</Chip></Pop>
      <Pop at={1.4} from="left"><Chip><E>🍽</E> Vee Elevated Evenings · 8 PM · table for 4</Chip></Pop>
      <Pop at={2.2} from="left"><Chip><E>👥</E> Who's coming + guests + ₹/head</Chip></Pop>
      <Pop at={4.2} from="left"><Chip><E>📍</E> One tap → Google Maps</Chip></Pop>
    </div>
  </Footage>
);

export const AppScene: React.FC = () => (
  <Footage clip="clips/c6_app.mp4" tag="SWIGGY APP" step={5}>
    <Kicker>Not a mock-up</Kicker>
    <div style={{ marginTop: 30 }}>
      <Slam at={0.4} size={130}>Real booking.</Slam>
      <Slam at={1.3} size={130} color={C.orange} style={{ marginTop: 10 }}>
        ₹0 paid.
      </Slam>
    </div>
    <Pop at={3.0}>
      <P size={34} style={{ marginTop: 50, color: C.text }}>
        Swiggy Dineout → My Bookings: <b>Confirmed</b> · Today 8:00 PM · 4 guests
      </P>
    </Pop>
    <Pop at={4.5}>
      <P size={28} style={{ marginTop: 22 }}>
        The bot only books <b style={{ color: C.text }}>FREE</b> reservations. It has no payment tool at all.
      </P>
    </Pop>
  </Footage>
);

export const WhatsAppScene: React.FC = () => (
  <Footage clip="clips/c7_whatsapp.mp4" tag="WHATSAPP" step={5}>
    <Kicker>Swiggy confirms it too</Kicker>
    <H size={76} style={{ marginTop: 16 }}>
      Dineout on <span style={{ color: C.green }}>WhatsApp:</span>
      <br />
      "Reservation confirmed, Harsh! <E>🎉</E>"
    </H>
    <Pop at={2.0}>
      <P size={32} style={{ marginTop: 40 }}>
        From a messy chat to a confirmed table: two people, about two minutes of typing.
      </P>
    </Pop>
  </Footage>
);

export const PartyScene: React.FC = () => (
  <Footage clip="clips/c8_party.mp4" tag="DONE" step={5}>
    <div style={{ marginTop: 120 }}>
      <Slam at={0.3} size={140}>Chaos</Slam>
      <Slam at={0.9} size={140} color={C.orange} style={{ marginTop: 8 }}>
        → agreement.
      </Slam>
      <Pop at={2.2}>
        <P size={36} style={{ marginTop: 44, color: C.text }}>
          "Doneee party <E>🥳</E>"
        </P>
      </Pop>
    </div>
  </Footage>
);
