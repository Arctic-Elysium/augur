import { Component, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useNavigate, useOutletContext, useSearchParams } from "react-router-dom";
import { api, type CheckKind } from "../../lib/api";
import { splitOoc, toEntries, type Entry, type NewEntry } from "../../lib/log";
import { takeTurn, type ClockState, type PartyMember } from "../../lib/play";
import { estimateTurn, useWindowedLog } from "../../hooks/useWindowedLog";
import { DiceReadout } from "./DiceReadout";
import { PartyRail } from "./PartyRail";
import type { WorkspaceContext } from "./Workspace";

/** One turn of narration is a few paragraphs. Anything past this is a loop,
 *  and mounting hundreds of rows makes the page unusable before anyone can
 *  stop it. The server caps this too - this is the last line. */
const MAX_NARRATION_PARAGRAPHS = 12;

/** Turns in flight, keyed by session id, OUTSIDE React.
 *
 * Flipping to Inventory mid-turn unmounts this component. The stream keeps
 * running in its closure, the server persists the turn - and the remounted
 * tab, having refetched before the turn landed, shows a log that ate your
 *  message until you refresh. The registry lets a fresh mount notice the
 * in-flight turn, wait it out, and refetch once it is durable. */
const turnsInFlight = new Map<string, Promise<void>>();

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
  // Which narration block the GM is rewriting, and the text they are writing.
  const [amending, setAmending] = useState<{ turnId: string; text: string } | null>(
    null,
  );

  const logRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const narrationCount = useRef(0);
  // Live entries need unique ids. Deriving them through toEntries gave every
  // mechanic in a turn the same `t0-r0`, and duplicate React keys under a
  // windowed list is exactly the scroll-duplication and blank-screen failure:
  // the reconciler matches the wrong rows when the window shifts, then throws.
  const liveSeq = useRef(0);

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
    const id = session.id;
    let stale = false;
    const load = async () => {
      const pending = turnsInFlight.get(id);
      if (pending) {
        setBusy(true);
        await pending.catch(() => {});
        if (stale) return;
        setBusy(false);
      }
      const turns = await api.sessions.turns(id);
      if (stale) return;
      setEntries(toEntries(turns, kinds, names));
      requestAnimationFrame(toLatest);
    };
    void load().catch(() => {});
    return () => { stale = true; };
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

  /** Rewrite what Augur said. No model call: the record is the GM's, and the
   *  cheapest correction available is the one that just replaces the text. */
  const saveAmend = async () => {
    if (!amending || !session) return;
    const { turnId, text } = amending;
    setAmending(null);
    try {
      await api.sessions.amendTurn(turnId, text);
      setEntries(toEntries(await api.sessions.turns(session.id), kinds, names));
    } catch {
      /* the log still shows the old text; nothing was lost */
    }
  };

  /** Regenerate the prose. The dice, damage and locks all stand - a redo that
   *  re-rolled would be a retry-farm wearing a friendlier name. */
  const redo = async (turnId: string, note: string) => {
    if (!session) return;
    setBusy(true);
    try {
      await api.sessions.redoTurn(turnId, note);
      setEntries(toEntries(await api.sessions.turns(session.id), kinds, names));
    } finally {
      setBusy(false);
    }
  };

  const send = async () => {
    const text = input.trim();
    if (!text || busy || !session) return;
    const id = session.id;
    setInput("");
    setBusy(true);
    narrationCount.current = 0;
    append({
      id: `live-${liveSeq.current++}`,
      kind: "action",
      speaker: effectiveActor ? names[effectiveActor] ?? "" : "The party",
      text,
    });
    requestAnimationFrame(toLatest);

    const flight = takeTurn(id, text, effectiveActor, {
      onMechanic: (m) => {
        toEntries(
          [{ ordinal: 0, actor_id: null, player_input: "", narration: "", tool_calls: [m] }],
          kinds,
          names,
        )
          .filter((e) => e.kind !== "action")
          // Re-id: toEntries stamps every live mechanic `t0-r0`, and duplicate
          // keys under a windowed list is the scroll-duplication bug.
          .forEach((e) => append({ ...e, id: `live-${liveSeq.current++}` }));
        if (!atBottom) setUnread((n) => n + 1);
      },
      onNarration: (t) => {
        // Client-side ceiling. The server cleans this, but a stream that
        // arrives as hundreds of paragraph events would otherwise mount
        // hundreds of rows before anyone could stop it.
        narrationCount.current += 1;
        if (narrationCount.current > MAX_NARRATION_PARAGRAPHS) return;
        append({ id: `live-${liveSeq.current++}`, kind: "narration", text: t });
      },
      onState: (s) => {
        setParty(s.party);
        setClocks(s.clocks);
      },
      onError: (m) =>
        append({ id: `live-${liveSeq.current++}`, kind: "event", text: m, bad: true }),
      onDone: () => setBusy(false),
    });

    turnsInFlight.set(id, flight);
    try {
      await flight;
    } finally {
      turnsInFlight.delete(id);
    }

    // Reconcile with the durable log. The live entries were streamed guesses;
    // the server's rows are the truth, with canonical ids - swapping them in
    // clears any accumulated live state instead of letting it rot for a
    // whole session.
    try {
      const turns = await api.sessions.turns(id);
      setEntries(toEntries(turns, kinds, names));
    } catch {
      /* keep the live entries; the next mount refetches */
    }
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
          <LogBoundary>
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
                  <LogRow
                    key={entry.id}
                    entry={entry}
                    index={startIndex + i}
                    readOnly={readOnly}
                    amending={amending}
                    onAmendStart={(turnId, text) =>
                      setAmending({ turnId, text })
                    }
                    onAmendChange={(text) =>
                      setAmending((a) => (a ? { ...a, text } : a))
                    }
                    onAmendSave={() => void saveAmend()}
                    onAmendCancel={() => setAmending(null)}
                    onRedo={(turnId, note) => void redo(turnId, note)}
                  />
                ))}
              </div>
              <div className="log__pad" style={{ height: padBottom }} />
            </div>
          </LogBoundary>

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
function LogRow({
  entry,
  index,
  readOnly = false,
  amending = null,
  onAmendStart,
  onAmendChange,
  onAmendSave,
  onAmendCancel,
  onRedo,
}: {
  entry: Entry;
  index: number;
  readOnly?: boolean;
  amending?: { turnId: string; text: string } | null;
  onAmendStart?: (turnId: string, text: string) => void;
  onAmendChange?: (text: string) => void;
  onAmendSave?: () => void;
  onAmendCancel?: () => void;
  onRedo?: (turnId: string, note: string) => void;
}) {
  const turnId = entry.kind === "narration" ? entry.turnId ?? null : null;
  const editing = Boolean(turnId && amending?.turnId === turnId);

  return (
    <div className="turn" data-turn-index={index}>
      <div className="turn__time" />
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
            <p className="turn__action">
              {splitOoc(entry.text).map((seg, i) =>
                seg.ooc ? (
                  <span key={i} className="turn__ooc" title="Out of character">
                    (({seg.text}))
                  </span>
                ) : (
                  <span key={i}>{seg.text}</span>
                ),
              )}
            </p>
          </>
        )}
        {entry.kind === "narration" && !editing && (
          <>
            <p className="turn__prose">{entry.text}</p>
            {!readOnly && turnId && (
              <div className="turn__tools">
                <button
                  className="linkish"
                  onClick={() => onAmendStart?.(turnId, entry.text)}
                >
                  amend
                </button>
                <button
                  className="linkish"
                  onClick={() => {
                    const note = window.prompt(
                      "What was wrong with it? (optional — this is passed to Augur)",
                      "",
                    );
                    if (note !== null) onRedo?.(turnId, note);
                  }}
                >
                  redo
                </button>
              </div>
            )}
          </>
        )}
        {entry.kind === "narration" && editing && (
          <div className="turn__amend">
            <textarea
              className="field__input"
              rows={6}
              autoFocus
              value={amending?.text ?? ""}
              onChange={(e) => onAmendChange?.(e.target.value)}
            />
            <div className="actions">
              <button className="btn" onClick={() => onAmendCancel?.()}>
                Cancel
              </button>
              <button className="btn btn--go" onClick={() => onAmendSave?.()}>
                Save
              </button>
            </div>
          </div>
        )}
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

/** A render fault in one log row must cost the log, not the app.
 *
 * The blank-screen-until-refresh failure was a thrown reconciliation error
 * unmounting the whole tree because nothing caught it. The duplicate-key bug
 * that threw it is fixed; this is the backstop for whatever throws next. */
class LogBoundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (this.state.failed) {
      return (
        <div className="log">
          <p className="notice notice--bad">
            The log hit a rendering fault.{" "}
            <button
              className="linkish"
              onClick={() => window.location.reload()}
            >
              Reload
            </button>{" "}
            to pick the session back up — nothing was lost.
          </p>
        </div>
      );
    }
    return this.props.children;
  }
}
