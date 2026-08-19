import { useCallback, useLayoutEffect, useRef, useState } from "react";
import type { RefObject } from "react";

type Options = { overscan?: number; estimate?: (t: unknown) => number };

/**
 * Windowed log — no dependencies.
 *
 * A four-hour session is ~1,500 turns and a campaign is tens of thousands, so only the
 * turns in the viewport (plus `overscan` px each way) are mounted. Two spacer divs carry
 * the height of everything above and below, which keeps three things true:
 *   - the scrollbar is honest (its length reflects the whole session, not the window);
 *   - scrollTop maps to an absolute turn index, so scene jumps and deep links are exact;
 *   - scroll position never shifts when older turns mount, because the height they occupy
 *     was already reserved by the top spacer.
 *
 * Heights start as per-kind estimates and are replaced with measured heights as rows pass
 * through the viewport (turns range from a one-line dice readout to a full paragraph, so a
 * fixed row height is not an option). Measuring only corrects rows at or below the window
 * start, so no compensating scroll adjustment is needed.
 */
export function useWindowedLog<T>(
  turns: T[],
  ref: RefObject<HTMLElement | null>,
  estimate: (t: T) => number,
  { overscan = 700 }: Options = {},
) {
  const heights = useRef<number[]>([]);
  const prefix = useRef<number[]>([0]);
  const [win, setWin] = useState({ start: Math.max(0, turns.length - 60), end: turns.length });
  const [atBottom, setAtBottom] = useState(true);
  const [unread, setUnread] = useState(0);

  const rebuild = useCallback(() => {
    const h = heights.current, p = new Array(h.length + 1);
    p[0] = 0;
    for (let i = 0; i < h.length; i++) p[i + 1] = (p[i] ?? 0) + (h[i] ?? 0);
    prefix.current = p;
  }, []);

  // grow (append-only log) or reset (different session loaded, or the live
  // entries were reconciled against the durable log)
  if (heights.current.length !== turns.length) {
    if (heights.current.length > turns.length) {
      heights.current = turns.map(estimate);
      // The old window can point past the end of the new array; left alone it
      // renders an empty log until the next scroll event resyncs it.
      setWin({ start: Math.max(0, turns.length - 60), end: turns.length });
    } else {
      for (let i = heights.current.length; i < turns.length; i++) heights.current.push(estimate(turns[i]!));
    }
    rebuild();
  }

  const indexAt = useCallback((y: number) => {
    const p = prefix.current;
    let lo = 0, hi = p.length - 2;
    while (lo < hi) { const m = (lo + hi) >> 1; if ((p[m + 1] ?? 0) <= y) lo = m + 1; else hi = m; }
    return Math.max(0, lo);
  }, []);

  const sync = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const start = indexAt(Math.max(0, el.scrollTop - overscan));
    const end = Math.min(turns.length, indexAt(el.scrollTop + el.clientHeight + overscan) + 2);
    setWin((w) => (w.start === start && w.end === end ? w : { start, end }));
  }, [ref, indexAt, overscan, turns.length]);

  const toBottom = useCallback(() => { const el = ref.current; if (el) el.scrollTop = el.scrollHeight; }, [ref]);

  // measure what is mounted, then re-pin the bottom if we were following the session
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    let changed = false;
    el.querySelectorAll<HTMLElement>("[data-turn-index]").forEach((row) => {
      const i = Number(row.dataset.turnIndex);
      const h = row.getBoundingClientRect().height;
      if (h > 0 && Math.abs(h - (heights.current[i] ?? 0)) > 0.5) { heights.current[i] = h; changed = true; }
    });
    if (changed) { rebuild(); if (atBottom) toBottom(); }
    sync();
  });

  const onScroll = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const bottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
    setAtBottom(bottom);
    if (bottom) setUnread(0);
    sync();
  }, [ref, sync]);

  const toLatest = useCallback(() => {
    setWin({ start: Math.max(0, turns.length - 60), end: turns.length });
    setUnread(0);
    requestAnimationFrame(toBottom);
  }, [turns.length, toBottom]);

  /** Absolute jump: scene index, journal backlink, deep link. */
  const toIndex = useCallback((i: number) => {
    setWin({ start: Math.max(0, i - 3), end: Math.min(turns.length, i + 45) });
    requestAnimationFrame(() => {
      const el = ref.current;
      if (el) el.scrollTop = Math.max(0, (prefix.current[i] ?? 0) - 10);
      // never scrollIntoView — it would scroll the shell's container too
    });
  }, [ref, turns.length]);

  return {
    visible: turns.slice(win.start, win.end),
    startIndex: win.start,
    padTop: prefix.current[win.start] ?? 0,
    padBottom: Math.max(0, (prefix.current[turns.length] ?? 0) - (prefix.current[win.end] ?? 0)),
    onScroll, atBottom, unread, setUnread, toLatest, toIndex, toBottom,
  };
}

/** Per-kind first-paint estimates; replaced by measurement on first sight. */
export const estimateTurn = (t: { kind: string }) =>
  t.kind === "scene" ? 74 : t.kind === "action" ? 118 : t.kind === "roll" ? 62 : t.kind === "event" ? 46 : 112;
