/** Thin fetch wrapper. Cookies are HttpOnly, so credentials must be included. */

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api${path}`, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...init.headers },
    ...init,
  });

  if (response.status === 401) {
    window.location.href = "/api/auth/login";
    throw new ApiError(401, "unauthenticated", "Redirecting to sign in");
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(
      response.status,
      body.code ?? "error",
      body.message ?? response.statusText,
    );
  }
  return response.status === 204 ? (undefined as T) : response.json();
}

export type PlayMode = "solo" | "party" | "table";

export interface Me {
  subject: string;
  email: string | null;
  displayName: string | null;
  groups: string[];
  isAdmin: boolean;
}

export interface Campaign {
  id: string;
  name: string;
  premise: string | null;
  play_mode: PlayMode;
  status: string;
}

export interface CharacterSheet {
  attributes: Record<string, number>;
  skills: Record<string, number>;
  hp: number;
  hp_max: number;
  stress: number;
  stress_max: number;
  conditions: { spec_id: string }[];
  inventory: string[];
  level: number;
}

export interface Character {
  id: string;
  campaign_id: string;
  name: string;
  controller: "player" | "ai";
  active: boolean;
  sheet: CharacterSheet;
}

export interface PlaySession {
  id: string;
  campaign_id: string;
  number: number;
  status: "active" | "ended";
  scene_id: string;
  active_character_id: string | null;
}

export interface TurnRecord {
  ordinal: number;
  actor_id: string | null;
  player_input: string;
  narration: string;
  tool_calls: {
    name: string;
    arguments: Record<string, unknown>;
    ok: boolean;
    result: Record<string, unknown>;
  }[];
}

export interface CheckKind {
  id: string;
  label: string;
  attribute: string;
  lock_policy: string;
}

export const api = {
  me: () => request<Me>("/auth/me"),
  logout: () => request<void>("/auth/logout", { method: "POST" }),

  campaigns: {
    list: () => request<Campaign[]>("/campaigns"),
    get: (id: string) => request<Campaign>(`/campaigns/${id}`),
    create: (body: { name: string; premise?: string; play_mode?: PlayMode }) =>
      request<Campaign>("/campaigns", {
        method: "POST",
        body: JSON.stringify(body),
      }),
  },

  characters: {
    list: (campaignId: string) =>
      request<Character[]>(`/characters?campaign_id=${campaignId}`),
    create: (body: {
      campaign_id: string;
      name: string;
      attributes: Record<string, number>;
      skills?: Record<string, number>;
    }) =>
      request<Character>("/characters", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    retire: (id: string) =>
      request<Character>(`/characters/${id}/retire`, { method: "POST" }),
  },

  sessions: {
    list: (campaignId: string) =>
      request<PlaySession[]>(`/sessions?campaign_id=${campaignId}`),
    get: (id: string) => request<PlaySession>(`/sessions/${id}`),
    start: (campaignId: string) =>
      request<PlaySession>("/sessions", {
        method: "POST",
        body: JSON.stringify({ campaign_id: campaignId }),
      }),
    end: (id: string) =>
      request<PlaySession>(`/sessions/${id}/end`, { method: "POST" }),
    turns: (id: string) => request<TurnRecord[]>(`/sessions/${id}/turns`),
    spotlight: (id: string, characterId: string | null) =>
      request<PlaySession>(`/sessions/${id}/spotlight`, {
        method: "POST",
        body: JSON.stringify({ character_id: characterId }),
      }),
  },

  rules: {
    checks: (rulesetId = "d20") =>
      request<CheckKind[]>(`/rules/${rulesetId}/checks`),
  },
};
