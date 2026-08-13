import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type PlaySession } from "../lib/api";
import { takeTurn, type ClockState, type Mechanic, type PartyMember } from "../lib/play";

interface Entry {
  kind: "player" | "gm" | "mechanic" | "error";
  text: string;
  mechanic?: Mechanic;
}

const TIER_LABEL: Record<string, string> = {
  crit_success: "Critical success",
  success: "Success",
  partial: "Partial",
  failure: "Failure",
  crit_failure: "Critical failure",
};

/** Dice render distinctly from prose on purpose. Mechanics folded into
 *  narration are easy to miss, and missing a boon or a clock tick means
 *  missing the part the engine guaranteed would happen. */
function MechanicLine({ mechanic }: { mechanic: Mechanic }) {
  const r = mechanic.result as Record<string, string | number | boolean>;

  if (!mechanic.ok) {
    return <div className="mech mech--rejected">rejected · {String(r.error)}</div>;
  }
  if (mechanic.name === "roll_check") {
    const tier = String(r.tier);
    return (
      <div className={`mech mech--${tier}`}>
        <span className="mech__die">{r.natural === 0 ? "—" : String(r.natural)}</span>
        <span className="mech__tier">{TIER_LABEL[tier] ?? tier}</span>
        {r.override ? <span className="mech__flag">natural</span> : null}
        {r.locked ? <span className="mech__flag">already tried</span> : null}
        {r.boon ? <span className="mech__flag mech__flag--boon">boon</span> : null}
        {r.setback ? <span className="mech__flag mech__flag--setback">setback</span> : null}
      </div>
    );
  }
  return <div className="mech">{mechanic.name.replace(/_/g, " ")}</div>;
}

function Sheet({
  member,
  active,
  onSelect,
}: {
  member: PartyMember;
  active: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      className={`sheet ${active ? "sheet--active" : ""}`}
      onClick={onSelect}
      aria-pressed={active}
    >
      <span className="sheet__name">{member.name}</span>
      <span className="sheet__bar" aria-label={`${member.hp} of ${member.hp_max} health`}>
        <span
          className="sheet__fill"
          style={{ width: `${(member.hp / member.hp_max) * 100}%` }}
        />
      </span>
      <span className="sheet__stats">
        {member.hp}/{member.hp_max} · stress {member.stress}
      </span>
      {member.conditions.length > 0 && (
        <span className="sheet__conditions">{member.conditions.join(", ")}</span>
      )}
    </button>
  );
}

export function Play() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const [session, setSession] = useState<PlaySession | null>(null);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [party, setParty] = useState<Record<string, PartyMember>>({});
  const [clocks, setClocks] = useState<Record<string, ClockState>>({});
  const [actorId, setActorId] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  // Load the sheet and replay the log, so a refresh mid-session picks the
  // thread up rather than starting from a blank screen.
  useEffect(() => {
    if (!sessionId) return;
    void (async () => {
      const s = await api.sessions.get(sessionId);
      setSession(s);

      const characters = await api.characters.list(s.campaign_id);
      setParty(
        Object.fromEntries(
          characters
            .filter((c) => c.active)
            .map((c) => [
              c.id,
              {
                name: c.name,
                hp: c.sheet.hp,
                hp_max: c.sheet.hp_max,
                stress: c.sheet.stress,
                stress_max: c.sheet.stress_max,
                conditions: c.sheet.conditions.map((x) => x.spec_id),
                inventory: c.sheet.inventory,
              },
            ]),
        ),
      );

      const turns = await api.sessions.turns(sessionId);
      const replayed: Entry[] = [];
      for (const turn of turns) {
        replayed.push({ kind: "player", text: turn.player_input });
        for (const call of turn.tool_calls) {
          replayed.push({ kind: "mechanic", text: "", mechanic: call });
        }
        if (turn.narration) replayed.push({ kind: "gm", text: turn.narration });
      }
      setEntries(replayed);
    })().catch((e) =>
      setEntries((prev) => [...prev, { kind: "error", text: (e as Error).message }]),
    );
  }, [sessionId]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [entries]);

  const send = async () => {
    const text = input.trim();
    if (!text || busy || !sessionId) return;
    setInput("");
    setBusy(true);
    setEntries((e) => [...e, { kind: "player", text }]);

    await takeTurn(sessionId, text, actorId, {
      onMechanic: (m) =>
        setEntries((e) => [...e, { kind: "mechanic", text: "", mechanic: m }]),
      onNarration: (t) => setEntries((e) => [...e, { kind: "gm", text: t }]),
      onState: (s) => {
        setParty(s.party);
        setClocks(s.clocks);
      },
      onError: (m) => setEntries((e) => [...e, { kind: "error", text: m }]),
      onDone: () => setBusy(false),
    });
    setBusy(false);
  };

  const acting = actorId ? party[actorId]?.name : null;
  const multiple = Object.keys(party).length > 1;

  return (
    <div className="play">
      <aside className="play__party">
        {session && (
          <Link className="crumb" to={`/campaigns/${session.campaign_id}`}>
            Back to campaign
          </Link>
        )}

        <h2 className="play__heading">Party</h2>
        {Object.entries(party).map(([id, member]) => (
          <Sheet
            key={id}
            member={member}
            active={actorId === id}
            onSelect={() => setActorId(actorId === id ? null : id)}
          />
        ))}
        {multiple && (
          <button
            className={`sheet sheet--together ${actorId === null ? "sheet--active" : ""}`}
            onClick={() => setActorId(null)}
          >
            Act together
          </button>
        )}

        {Object.keys(clocks).length > 0 && (
          <>
            <h2 className="play__heading">Clocks</h2>
            {Object.entries(clocks).map(([id, clock]) => (
              <div key={id} className="clock">
                <span className="clock__label">{clock.label}</span>
                <span className="clock__segments">
                  {Array.from({ length: clock.size }, (_, i) => (
                    <span
                      key={i}
                      className={`clock__seg ${i < clock.filled ? "clock__seg--full" : ""}`}
                    />
                  ))}
                </span>
              </div>
            ))}
          </>
        )}
      </aside>

      <main className="play__log">
        {entries.length === 0 && (
          <p className="empty">
            Say what you do. Augur reads the signs from there.
          </p>
        )}
        {entries.map((entry, i) =>
          entry.kind === "mechanic" && entry.mechanic ? (
            <MechanicLine key={i} mechanic={entry.mechanic} />
          ) : (
            <p key={i} className={`entry entry--${entry.kind}`}>
              {entry.text}
            </p>
          ),
        )}
        {busy && <p className="entry entry--pending">reading the signs</p>}
        <div ref={endRef} />

        <div className="composer">
          <label className="composer__who">
            {acting ? `${acting} acts` : multiple ? "The party acts" : ""}
          </label>
          <textarea
            className="composer__input"
            value={input}
            placeholder="What do you do?"
            rows={2}
            disabled={busy}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send();
              }
            }}
          />
          <button className="button" onClick={() => void send()} disabled={busy}>
            {busy ? "…" : "Act"}
          </button>
        </div>
      </main>
    </div>
  );
}
