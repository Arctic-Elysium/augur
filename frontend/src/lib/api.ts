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
    // Deliberately NOT redirecting to /api/auth/login here.
    //
    // Signing out clears Augur's cookie but not the identity provider's, so an
    // automatic redirect bounces straight back through a still-valid IdP
    // session and signs you back in - which looks exactly like sign-out being
    // broken. Surfacing the 401 lets the session provider show the signed-out
    // screen, where signing in is a deliberate click.
    throw new ApiError(401, "unauthenticated", "Not signed in");
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    // FastAPI's own validation errors arrive as {detail: [{loc, msg}, ...]}
    // rather than the app's {code, message}. Without this the client shows
    // "Unprocessable Entity" and the actual field problem is invisible.
    const fromDetail = Array.isArray(body.detail)
      ? body.detail
          .map(
            (d: { loc?: (string | number)[]; msg?: string }) =>
              `${(d.loc ?? []).filter((p) => p !== "body").join(".")}: ${d.msg}`,
          )
          .join("; ")
      : null;
    throw new ApiError(
      response.status,
      body.code ?? "error",
      body.message ?? fromDetail ?? response.statusText,
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
  ruleset_id?: string;
}

export interface CharacterSheet {
  attributes: Record<string, number>;
  skills: Record<string, number>;
  hp: number;
  hp_max: number;
  conditions: { spec_id: string }[];
  inventory: string[];
  level: number;
}

export interface Hook {
  kind: string;
  subject: string;
  detail: string;
}

export interface Character {
  id: string;
  campaign_id: string;
  name: string;
  controller: "player" | "ai";
  active: boolean;
  archived_reason: "dead" | "retired" | "missing" | null;
  epitaph: string | null;
  sheet: CharacterSheet;
  backstory: string | null;
  hooks: Hook[];
}

export interface BuildRules {
  method: string;
  attributes: { id: string; label: string; description: string }[];
  budget: number;
  base: number;
  min: number;
  max: number;
  costs: Record<string, number>;
  skills: { id: string; label: string; attribute: string }[];
  skill_points: number;
  skill_max: number;
  derived: Record<string, string>;
}

export interface PlaySession {
  id: string;
  campaign_id: string;
  number: number;
  status: "active" | "ended";
  scene_id: string;
  active_character_id: string | null;
  title: string | null;
  summary: string | null;
  created_at: string | null;
  ended_at: string | null;
  turn_count: number;
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

export type Role = "owner" | "gm" | "player" | "observer";

export interface Member {
  user_id: string;
  role: Role;
  display_name: string | null;
  email: string | null;
  is_you: boolean;
}

export interface Invite {
  code: string;
  role: string;
  uses: number;
  max_uses: number;
  expires_at: string;
  spent: boolean;
}

export interface CodexFact {
  subject_ref: string;
  predicate: string;
  object_text: string;
  session_number: number | null;
}

export interface CodexEntity {
  ref: string;
  kind: string;
  name: string;
  summary: string;
  mentions: number;
  first_seen_session: number | null;
  state: Record<string, unknown>;
  facts: CodexFact[];
}

export interface Note {
  id: string;
  title: string;
  body: string;
  pinned: boolean;
  session_number: number | null;
}

export const api = {
  me: () => request<Me>("/auth/me"),
  logout: () => request<void>("/auth/logout", { method: "POST" }),

  campaigns: {
    list: () => request<Campaign[]>("/campaigns"),
    get: (id: string) => request<Campaign>(`/campaigns/${id}`),
    create: (body: {
      name: string;
      premise?: string;
      play_mode?: PlayMode;
      primer?: string;
      tone?: string;
    }) =>
      request<Campaign>("/campaigns", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    remove: (id: string) =>
      request<void>(`/campaigns/${id}`, { method: "DELETE" }),
    members: (id: string) => request<Member[]>(`/campaigns/${id}/members`),
    invites: (id: string) => request<Invite[]>(`/campaigns/${id}/invites`),
    createInvite: (id: string, body: { role: Role; max_uses?: number }) =>
      request<Invite>(`/campaigns/${id}/invites`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    revokeInvite: (id: string, code: string) =>
      request<void>(`/campaigns/${id}/invites/${code}`, { method: "DELETE" }),
    join: (code: string) =>
      request<Campaign>("/campaigns/join", {
        method: "POST",
        body: JSON.stringify({ code }),
      }),
    setRole: (id: string, userId: string, role: Role) =>
      request<Member>(`/campaigns/${id}/members/${userId}`, {
        method: "PATCH",
        body: JSON.stringify({ role }),
      }),
    removeMember: (id: string, userId: string) =>
      request<void>(`/campaigns/${id}/members/${userId}`, { method: "DELETE" }),
  },

  characters: {
    list: (campaignId: string) =>
      request<Character[]>(`/characters?campaign_id=${campaignId}`),
    create: (body: {
      campaign_id: string;
      name: string;
      attributes: Record<string, number>;
      skills?: Record<string, number>;
      backstory?: string;
      hooks?: Hook[];
    }) =>
      request<Character>("/characters", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    update: (
      id: string,
      body: {
        name?: string;
        backstory?: string;
        hooks?: Hook[];
        inventory?: string[];
      },
    ) =>
      request<Character>(`/characters/${id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    archive: (id: string, reason: "dead" | "retired" | "missing", epitaph?: string) =>
      request<Character>(`/characters/${id}/archive`, {
        method: "POST",
        body: JSON.stringify({ reason, epitaph }),
      }),
    restore: (id: string) =>
      request<Character>(`/characters/${id}/restore`, { method: "POST" }),
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
    rename: (id: string, title: string) =>
      request<PlaySession>(`/sessions/${id}`, {
        method: "PATCH",
        body: JSON.stringify({ title }),
      }),
    remove: (id: string) =>
      request<void>(`/sessions/${id}`, { method: "DELETE" }),
    // Not through request(): this returns a file, not JSON.
    exportUrl: (id: string, format: "md" | "json" = "md") =>
      `/api/sessions/${id}/export?format=${format}`,
    spotlight: (id: string, characterId: string | null) =>
      request<PlaySession>(`/sessions/${id}/spotlight`, {
        method: "POST",
        body: JSON.stringify({ character_id: characterId }),
      }),
  },

  memory: {
    codex: (campaignId: string) =>
      request<{ entities: CodexEntity[]; unattached_facts: CodexFact[] }>(
        `/memory/codex?campaign_id=${campaignId}`,
      ),
    updateEntity: (
      campaignId: string,
      ref: string,
      body: { name?: string; kind?: string; summary?: string; known_to_players?: boolean },
    ) =>
      request<{ ref: string; name: string; kind: string }>(
        `/memory/entities/${ref}?campaign_id=${campaignId}`,
        { method: "PATCH", body: JSON.stringify(body) },
      ),
    deleteEntity: (campaignId: string, ref: string) =>
      request<void>(`/memory/entities/${ref}?campaign_id=${campaignId}`, {
        method: "DELETE",
      }),
    mergeEntity: (campaignId: string, ref: string, intoRef: string) =>
      request<{ ref: string; mentions: number }>(
        `/memory/entities/${ref}/merge?campaign_id=${campaignId}`,
        { method: "POST", body: JSON.stringify({ into_ref: intoRef }) },
      ),
    notes: (campaignId: string) =>
      request<Note[]>(`/memory/notes?campaign_id=${campaignId}`),
    createNote: (campaignId: string, body: Partial<Note>) =>
      request<Note>(`/memory/notes?campaign_id=${campaignId}`, {
        method: "POST",
        body: JSON.stringify(body),
      }),
    updateNote: (id: string, body: Partial<Note>) =>
      request<Note>(`/memory/notes/${id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    deleteNote: (id: string) =>
      request<void>(`/memory/notes/${id}`, { method: "DELETE" }),
  },

  rules: {
    checks: (rulesetId = "d20") =>
      request<CheckKind[]>(`/rules/${rulesetId}/checks`),
    build: (rulesetId = "d20") =>
      request<BuildRules>(`/rules/${rulesetId}/build`),
  },
};
