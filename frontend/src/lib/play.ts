/** SSE turn client.
 *
 * A turn takes seconds of tool calls before any prose exists, so the server
 * emits mechanics as they resolve and narration after. Watching dice land
 * feels very different from watching a spinner.
 */

export interface Mechanic {
  name: string;
  arguments: Record<string, unknown>;
  ok: boolean;
  result: Record<string, unknown>;
}

export interface PartyMember {
  name: string;
  hp: number;
  hp_max: number;
  stress: number;
  stress_max: number;
  conditions: string[];
  inventory: string[];
}

export interface ClockState {
  label: string;
  filled: number;
  size: number;
}

export interface TurnHandlers {
  onMechanic?: (m: Mechanic) => void;
  onNarration?: (text: string) => void;
  onState?: (s: { party: Record<string, PartyMember>; clocks: Record<string, ClockState> }) => void;
  onError?: (message: string) => void;
  onDone?: () => void;
}

export async function takeTurn(
  sessionId: string,
  text: string,
  actorId: string | null,
  handlers: TurnHandlers,
): Promise<void> {
  const response = await fetch(`/api/sessions/${sessionId}/turn`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, actor_id: actorId }),
  });

  if (!response.ok || !response.body) {
    handlers.onError?.(`Turn failed (${response.status})`);
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // SSE frames are separated by a blank line; a partial frame stays buffered.
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";

    for (const frame of frames) {
      const eventLine = frame.split("\n").find((l) => l.startsWith("event: "));
      const dataLine = frame.split("\n").find((l) => l.startsWith("data: "));
      if (!eventLine || !dataLine) continue;

      const event = eventLine.slice(7);
      const data = JSON.parse(dataLine.slice(6));

      if (event === "mechanic") handlers.onMechanic?.(data as Mechanic);
      else if (event === "narration") handlers.onNarration?.(data.text);
      else if (event === "state") handlers.onState?.(data);
      else if (event === "error") handlers.onError?.(data.message);
      else if (event === "done") handlers.onDone?.();
    }
  }
}
