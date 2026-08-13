import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useOutletContext, useSearchParams } from "react-router-dom";
import { api, type CheckKind } from "../../lib/api";
import { toEntries, type Entry, type NewEntry } from "../../lib/log";
import { takeTurn, type ClockState, type PartyMember } from "../../lib/play";
import { estimateTurn, useWindowedLog } from "../../hooks/useWindowedLog";
import { DiceReadout } from "./DiceReadout";
import { PartyRail } from "./PartyRail";
import type { WorkspaceContext } from "./Workspace";

/** One turn of narration is a few paragraphs. Anything past this is a loop,
 *  and mounting hundreds of rows makes the page unusable before anyone can
 *  stop it. The server caps this too - this is the last line. */
const MAX_NARRATION_PARAGRAPHS = 12;

export function PlayTab() {
  const { campaign, characters, sessions, activeSession, reload } =
    useOutletContext<WorkspaceContext>();
  const navigate = useNavigate();
  const [params] = useSearchParams();

  // A read-only deep link opens a finished session without offering a composer.
  const readParam = params.get("session");
  const session = readParam
    ? sessions.find((s) => s.id === readParam) ?? null
    : activeSession;
  const readOnly = Boolean(readParam) || session?.status === "ended";

  const [entries, setEntries] = useState<Entry[]>([]);
  const [kinds, setKinds] = useState<CheckKind[]>([]);
  const [party, setParty] = useState<Record<string, PartyMember>>({});
  const [clocks, setClocks] = useState<Record<string, ClockState>>({});
  const [spotlight, setSpotlight] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  // Off by default: the log is the page, and an index that opens itself makes
  // the prose look secondary to its own table of contents.
  const [showScenes, setShowScenes] = useState(false);

  const logRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const narrationCount = useRef(0);

  const {
    visible, startIndex, padTop, padBottom,
    onScroll, atBottom, unread, setUnread, toLatest, toIndex,
  } = useWindowedLog(entries, logRef, estimateTurn);

  const roster = useMemo(() => characters.filter((c) => c.active), [characters]);
  const names = useMemo(
    () => Object.fromEntries(roster.map((c) => [c.id, c.name])),
    [roster],
  );
  // With one character there is no "party" to address, and no spotlight to
  // pick - they are always the one acting.
  const effectiveActor = spotlight ?? (roster.length === 1 ? roster[0]!.id : null);

  useEffect(() => {
    void api.rules.checks(campaign.ruleset_id ?? "d20").then(setKinds).catch(() => {});
  }, [campaign.ruleset_id]);

  useEffect(() => {
    if (!session) return;
    void api.sessions
      .turns(session.id)
      .then((turns) => {
        setEntries(toEntries(turns, kinds, names));
        requestAnimationFrame(toLatest);
      })
      .catch(() => {});
  }, [session?.id, kinds.length, roster.length]);


  // The rail reads live state during a turn and persisted state otherwise, so
  // damage shows immediately rather than after a refetch.
  const railCharacters = useMemo(
    () =>
      roster.map((c) => {
        const live = party[c.id];
        return live
          ? {
              ...c,
              sheet: {
                ...c.sheet,
                hp: live.hp,
                hp_max: live.hp_max,
                stress: live.stress,
                stress_max: live.stress_max,
                conditions: live.conditions.map((spec_id) => ({ spec_id })),
                inventory: live.inventory,
              },
            }
          : c;
      }),
    [roster, party],
  );

  const scenes = useMemo(
    () =>
      entries
        .filter((e) => e.kind === "action")
        .slice(-40)
        .map((e) => ({
          name: (e as { text: string }).text.slice(0, 46),
          startIndex: e.index,
        })),
    [entries],
  );

  const append = (entry: NewEntry) =>
    setEntries((prev) => [...prev, { ...entry, index: prev.length } as Entry]);

  const send = async () => {
    const text = input.trim();
    if (!text || busy || !session) return;
    setInput("");
    setBusy(true);
    narrationCount.current = 0;
    append({
      id: `live-${Date.now()}`,
      kind: "action",
      speaker: effectiveActor ? names[effectiveActor] ?? "" : "The party",
      text,
    });
    requestAnimationFrame(toLatest);

    await takeTurn(session.id, text, effectiveActor, {
      onMechanic: (m) => {
        toEntries(
          [{ ordinal: 0, actor_id: null, player_input: "", narration: "", tool_calls: [m] }],
          kinds,
          names,
        )
          .filter((e) => e.kind !== "action")
          .forEach((e) => append(e));
        if (!atBottom) setUnread((n) => n + 1);
      },
      onNarration: (t) => {
        // Client-side ceiling. The server cleans this, but a stream that
        // arrives as hundreds of paragraph events would otherwise mount
        // hundreds of rows before anyone could stop it.
        narrationCount.current += 1;
        if (narrationCount.current > MAX_NARRATION_PARAGRAPHS) return;
        append({ id: `live-n-${Date.now()}-${narrationCount.current}`, kind: "narration", text: t });
      },
      onState: (s) => {
        setParty(s.party);
        setClocks(s.clocks);
      },
      onError: (m) => append({ id: `live-e-${Date.now()}`, kind: "event", text: m, bad: true }),
      onDone: () => setBusy(false),
    });
    setBusy(false);
    if (atBottom) requestAnimationFrame(toLatest);
  };

  if (!session) {
    return (
      <div className="pane">
        <div className="pane__inner">
          <div className="empty">
            <h2>No session running</h2>
            <p>
              {roster.length === 0
                ? "Build a character first — the Party tab is where that happens."
                : `${roster.length} ready. Start a session to begin.`}
            </p>
            <button
              className="btn btn--go"
              disabled={roster.length === 0}
              onClick={async () => {
                await api.sessions.start(campaign.id);
                await reload();
              }}
            >
              Start session {sessions.length + 1}
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="play">
      <div className="play__main">
        {readOnly && (
          <div className="banner">
            <span className="label label--lit">Read only</span>
            <span>
              Session {session.number} has ended. You are reading the log.
            </span>
            <button className="linkish" onClick={() => navigate("../sessions")}>
              All sessions
            </button>
          </div>
        )}

        <div className="play__head">
          <span className="label label--lit">Session {session.number}</span>
          <span className="play__scene">
            {campaign.premise ?? campaign.name}
          </span>
          <span className="play__meta">
            {entries.length.toLocaleString()} entries
            {scenes.length > 3 && (
              <>
                {" · "}
                <button
                  className="linkish"
                  onClick={() => setShowScenes((v) => !v)}
                  aria-pressed={showScenes}
                >
                  {showScenes ? "hide index" : "index"}
                </button>
              </>
            )}
          </span>
        </div>

        <div className="play__mid">
          <div
            className="log"
            ref={logRef}
            onScroll={onScroll}
            tabIndex={0}
            aria-label="Session log"
          >
            <div className="log__pad" style={{ height: padTop }} />
            <div className="log__inner">
              {entries.length === 0 && (
                <p className="empty">
                  Say what you do. Augur reads the signs from there.
                </p>
              )}
              {visible.map((entry, i) => (
                <LogRow key={entry.id} entry={entry} index={startIndex + i} />
              ))}
            </div>
            <div className="log__pad" style={{ height: padBottom }} />
          </div>

          {showScenes && scenes.length > 3 && (
            <nav className="scenes" aria-label="Jump to a moment">
              <div className="label scenes__head">This session</div>
              {scenes.map((s) => (
                <button
                  key={s.startIndex}
                  className="scenes__item"
                  onClick={() => toIndex(s.startIndex)}
                >
                  {s.name}
                </button>
              ))}
            </nav>
          )}

          {!atBottom && (
            <button
              className="jump"
              onClick={() => {
                toLatest();
                inputRef.current?.focus();
              }}
            >
              {unread > 0 ? `${unread} new · latest` : "Latest"}{" "}
              <span aria-hidden>↓</span>
            </button>
          )}
        </div>

        {!readOnly && (
          <div className="composer">
            <label className="composer__who label label--lit">
              {effectiveActor
              ? `${names[effectiveActor]} acts`
              : roster.length > 1
                ? "The party acts"
                : ""}
            </label>
            <textarea
              ref={inputRef}
              className="composer__input"
              value={input}
              rows={2}
              placeholder="What do you do?"
              disabled={busy}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void send();
                }
              }}
            />
            <button
              className="btn btn--go"
              onClick={() => void send()}
              disabled={busy}
            >
              {busy ? "Reading" : "Act"}
            </button>
          </div>
        )}
      </div>

      <PartyRail
        characters={railCharacters}
        spotlightId={spotlight}
        onSpotlight={setSpotlight}
        clocks={clocks}
      />
    </div>
  );
}

/** One row per entry. `data-turn-index` is what the windowing hook measures. */
function LogRow({ entry, index }: { entry: Entry; index: number }) {
  return (
    <div className="turn" data-turn-index={index}>
      <div className="turn__time">{entry.kind === "roll" ? "" : ""}</div>
      <div className="turn__body">
        {entry.kind === "scene" && (
          <div className="turn__scene">
            <span className="label label--lit">{entry.text}</span>
            <hr className="rule" />
          </div>
        )}
        {entry.kind === "action" && (
          <>
            {entry.speaker && <span className="label label--lit">{entry.speaker}</span>}
            <p className="turn__action">{entry.text}</p>
          </>
        )}
        {entry.kind === "narration" && <p className="turn__prose">{entry.text}</p>}
        {entry.kind === "roll" && <DiceReadout roll={entry.roll} />}
        {entry.kind === "event" && (
          <span className={`eventline ${entry.bad ? "eventline--bad" : ""}`}>
            {entry.text}
          </span>
        )}
      </div>
    </div>
  );
}
