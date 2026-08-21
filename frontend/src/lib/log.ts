/** Turns -> log entries.
 *
 * The API stores one row per exchange: what the player typed, what the engine
 * resolved, what the model narrated. The log reads better as a stream of
 * distinct beats - an action, then the dice, then the prose - because that is
 * the order they happened in and each wants different treatment on screen.
 *
 * Flattening here rather than in the component keeps the windowing hook working
 * on a flat indexable array, which is what makes scene jumps exact.
 */

import type { CheckKind, TurnRecord } from "./api";

export type Outcome =
  | "critical-success"
  | "success"
  | "partial"
  | "miss"
  | "critical-failure";

export interface Roll {
  label: string;
  natural: number;
  margin: number;
  dc: number | null;
  outcome: Outcome;
  override: boolean;
  locked: boolean;
  boon: string | null;
  setback: string | null;
  notRolled: string | null;
}

/** Distributes over the union - a bare Omit<Entry, "index"> collapses to the
 *  keys every member shares, which is just id and kind. */
export type NewEntry<T = Entry> = T extends Entry ? Omit<T, "index"> : never;

export type Entry =
  | { id: string; kind: "action"; index: number; speaker: string; text: string }
  // `turnId` is what amend and redo address. Absent on live-streamed
  // narration: the turn row does not exist yet, and there is nothing to
  // rewrite until it is durable. It arrives on the next refetch.
  | {
      id: string;
      kind: "narration";
      index: number;
      text: string;
      turnId?: string;
    }
  | { id: string; kind: "roll"; index: number; roll: Roll }
  | { id: string; kind: "event"; index: number; text: string; bad?: boolean }
  | { id: string; kind: "scene"; index: number; text: string };

/** Tools the model uses to look things up rather than to change them.
 *
 * These are the GM checking their notes. Surfacing them turns the log into a
 * debug trace - "query character" appearing when the player said "I put it in
 * my pack" tells them nothing and breaks the fiction. Failures still show,
 * because a refused lookup explains narration that would otherwise look
 * unmotivated. */
const SILENT_TOOLS = new Set([
  "query_character",
  "list_available_checks",
  "list_clocks",
]);

const TIER_TO_OUTCOME: Record<string, Outcome> = {
  crit_success: "critical-success",
  success: "success",
  partial: "partial",
  failure: "miss",
  crit_failure: "critical-failure",
};

function labelFor(kindId: string, kinds: CheckKind[]): string {
  return kinds.find((k) => k.id === kindId)?.label ?? kindId.replace(/_/g, " ");
}

export function toEntries(
  turns: TurnRecord[],
  kinds: CheckKind[] = [],
  names: Record<string, string> = {},
): Entry[] {
  const entries: Entry[] = [];
  let index = 0;
  const push = (e: NewEntry) => entries.push({ ...e, index: index++ } as Entry);

  for (const turn of turns) {
    // The opening scene is recorded with empty player input. An empty action
    // row above it reads as a rendering bug, not a beat.
    if (turn.player_input.trim()) {
      push({
        id: `t${turn.ordinal}-in`,
        kind: "action",
        // Name whoever acted. "The party" for a one-character game reads as a
        // bug, and with several characters it hides which one is doing the thing.
        speaker: turn.actor_id ? names[turn.actor_id] ?? "" : "The party",
        text: turn.player_input,
      });
    }

    for (const [i, call] of turn.tool_calls.entries()) {
      const r = call.result as Record<string, unknown>;

      if (call.ok && SILENT_TOOLS.has(call.name)) continue;

      if (!call.ok) {
        // Surfaced rather than hidden: a rejected call means the engine
        // refused something, and silently dropping it makes the narration
        // that follows look unmotivated.
        push({
          id: `t${turn.ordinal}-r${i}`,
          kind: "event",
          text: `refused · ${String(r.error ?? "invalid")}`,
          bad: true,
        });
        continue;
      }

      if (call.name === "roll_check") {
        push({
          id: `t${turn.ordinal}-r${i}`,
          kind: "roll",
          roll: {
            label: labelFor(String(call.arguments.kind_id ?? ""), kinds),
            natural: Number(r.natural ?? 0),
            margin: Number(r.margin ?? 0),
            dc: r.dc == null ? null : Number(r.dc),
            outcome: TIER_TO_OUTCOME[String(r.tier)] ?? "success",
            override: Boolean(r.override),
            locked: Boolean(r.locked),
            boon: r.boon ? String((r.boon as Record<string, string>).kind) : null,
            setback: r.setback
              ? String((r.setback as Record<string, string>).kind)
              : null,
            notRolled: r.not_rolled ? String(r.not_rolled) : null,
          },
        });
        continue;
      }

      push({
        id: `t${turn.ordinal}-r${i}`,
        kind: "event",
        text: describeEvent(call.name, call.arguments, r),
      });
    }

    if (turn.narration) {
      for (const [i, para] of turn.narration.split("\n\n").entries()) {
        if (para.trim()) {
          push({
            id: `t${turn.ordinal}-n${i}`,
            kind: "narration",
            text: para.trim(),
            turnId: turn.id,
          });
        }
      }
    }
  }

  return entries;
}

function describeEvent(
  name: string,
  args: Record<string, unknown>,
  result: Record<string, unknown>,
): string {
  switch (name) {
    case "apply_damage":
      return `${args.amount} damage${args.source ? ` · ${args.source}` : ""}`;
    case "heal":
      return `healed ${args.amount}`;
    case "add_condition":
      return `condition · ${args.condition_id}`;
    case "remove_condition":
      return `cleared · ${args.condition_id}`;
    case "give_item":
      return `gained · ${args.item}`;
    case "take_item":
      return `lost · ${args.item}`;
    case "create_clock":
      return `clock started · ${args.label}`;
    case "advance_clock":
      return `clock · ${result.label ?? args.clock_id} ${result.filled}/${result.size}`;
    default:
      return name.replace(/_/g, " ");
  }
}

/** Split player input into fiction and ((table talk)) segments for display.
 *
 * The backend does the authoritative split before the model sees anything;
 * this one only styles the log so table talk reads as what it was. The two
 * must agree on the delimiter and nothing else.
 */
export function splitOoc(text: string): { text: string; ooc: boolean }[] {
  const segments: { text: string; ooc: boolean }[] = [];
  const pattern = /\(\((.+?)\)\)/gs;
  let cursor = 0;
  for (const match of text.matchAll(pattern)) {
    const at = match.index ?? 0;
    if (at > cursor) segments.push({ text: text.slice(cursor, at), ooc: false });
    segments.push({ text: match[1] ?? "", ooc: true });
    cursor = at + match[0].length;
  }
  if (cursor < text.length) segments.push({ text: text.slice(cursor), ooc: false });
  return segments.length ? segments : [{ text, ooc: false }];
}
