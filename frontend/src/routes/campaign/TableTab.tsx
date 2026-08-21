import { useEffect, useState } from "react";
import { useOutletContext } from "react-router-dom";
import {
  api,
  type CampaignSettings,
  type Invite,
  type Member,
  type Role,
} from "../../lib/api";
import type { WorkspaceContext } from "./Workspace";

const ROLES: { id: Role; label: string; note: string }[] = [
  { id: "gm", label: "Game master", note: "Sees every sheet and what the world is hiding." },
  { id: "player", label: "Player", note: "Runs their own characters. Sees their own sheet." },
  { id: "observer", label: "Observer", note: "Reads along. Cannot act." },
];

export function TableTab() {
  const { campaign } = useOutletContext<WorkspaceContext>();
  const [members, setMembers] = useState<Member[] | null>(null);
  const [invites, setInvites] = useState<Invite[]>([]);
  const [role, setRole] = useState<Role>("player");
  const [copied, setCopied] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    const m = await api.campaigns.members(campaign.id);
    setMembers(m);
    // Only the GM may list invites; a player gets a 403 and that is fine.
    try {
      setInvites(await api.campaigns.invites(campaign.id));
    } catch {
      setInvites([]);
    }
  };

  useEffect(() => {
    void load().catch((e) => setError((e as Error).message));
  }, [campaign.id]);

  const run = (p: Promise<unknown>) =>
    void p.then(load).catch((e) => setError((e as Error).message));

  const you = members?.find((m) => m.is_you);
  const canManage = you?.role === "owner" || you?.role === "gm";

  if (!members) return <div className="pane"><div className="pane__inner">Loading</div></div>;

  return (
    <div className="pane">
      <div className="pane__inner">
        <header className="pane__head">
          <div>
            <h1 className="pane__title">Table</h1>
            <span className="pane__sub">
              {members.length} {members.length === 1 ? "person" : "people"}
            </span>
          </div>
        </header>

        {error && <p className="notice notice--bad">{error}</p>}

        <GmControls campaignId={campaign.id} />

        <ul className="roster">
          {members.map((m) => (
            <li key={m.user_id} className="member">
              <span className="member__name">
                {m.display_name ?? m.email ?? "Someone"}
                {m.is_you && <span className="member__you">you</span>}
              </span>
              <span className="member__role">{m.role}</span>
              {you?.role === "owner" && !m.is_you && m.role !== "owner" && (
                <span className="member__actions">
                  {(["gm", "player", "observer"] as Role[])
                    .filter((r) => r !== m.role)
                    .map((r) => (
                      <button
                        key={r}
                        className="linkish"
                        onClick={() =>
                          run(api.campaigns.setRole(campaign.id, m.user_id, r))
                        }
                      >
                        make {r}
                      </button>
                    ))}
                  <button
                    className="linkish"
                    onClick={() =>
                      run(api.campaigns.removeMember(campaign.id, m.user_id))
                    }
                  >
                    remove
                  </button>
                </span>
              )}
            </li>
          ))}
        </ul>

        {canManage && (
          <div className="section">
            <div className="section__head">
              <h2 className="section__title">Invite</h2>
            </div>

            <div className="choices">
              {ROLES.map((r) => (
                <button
                  key={r.id}
                  className={`choice ${role === r.id ? "choice--on" : ""}`}
                  onClick={() => setRole(r.id)}
                >
                  <span className="choice__label">{r.label}</span>
                  <span className="choice__note">{r.note}</span>
                </button>
              ))}
            </div>

            <div className="actions">
              <button
                className="btn btn--go"
                onClick={() =>
                  run(api.campaigns.createInvite(campaign.id, { role }))
                }
              >
                Create code
              </button>
            </div>

            {invites.filter((i) => !i.spent).length > 0 && (
              <ul className="roster">
                {invites
                  .filter((i) => !i.spent)
                  .map((i) => (
                    <li key={i.code} className="invite">
                      {/* Codes get read aloud down a voice call, so they are
                          short and avoid characters that sound alike. */}
                      <code className="invite__code">{i.code}</code>
                      <span className="invite__meta">
                        {i.role} · {i.uses}/{i.max_uses} used
                      </span>
                      <span className="member__actions">
                        <button
                          className="linkish"
                          onClick={() => {
                            void navigator.clipboard.writeText(i.code);
                            setCopied(i.code);
                          }}
                        >
                          {copied === i.code ? "copied" : "copy"}
                        </button>
                        <button
                          className="linkish"
                          onClick={() =>
                            run(api.campaigns.revokeInvite(campaign.id, i.code))
                          }
                        >
                          revoke
                        </button>
                      </span>
                    </li>
                  ))}
              </ul>
            )}
          </div>
        )}
      </div>
    </div>
  );
}


/** The GM's dials: the long arc, standing corrections, and the debug switches.
 *
 * Lives on the Table tab because that is already the who-runs-what page, and
 * because everything here is GM-only — the server strips these fields for
 * players, so a player simply sees nothing. */
function GmControls({ campaignId }: { campaignId: string }) {
  const [settings, setSettings] = useState<CampaignSettings | null>(null);
  const [arc, setArc] = useState("");
  const [directive, setDirective] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    void api.settings
      .get(campaignId)
      .then((s) => {
        setSettings(s);
        setArc(s.arc ?? "");
      })
      .catch(() => setSettings(null));
  }, [campaignId]);

  const save = async (patch: Partial<CampaignSettings>) => {
    setBusy(true);
    try {
      const next = await api.settings.update(campaignId, patch);
      setSettings(next);
    } finally {
      setBusy(false);
    }
  };

  // A player gets no settings back at all, so there is nothing to show them.
  if (!settings) return null;
  const directives = settings.directives ?? [];

  return (
    <section className="panel stack">
      <label className="field">
        <span className="field__label">Campaign arc</span>
        <textarea
          className="field__input"
          rows={3}
          value={arc}
          placeholder="Where the whole thing is heading. Sessions hang off this; nothing should foreclose it."
          onChange={(e) => setArc(e.target.value)}
        />
        <div className="actions">
          <button
            className="btn"
            disabled={busy || arc === (settings.arc ?? "")}
            onClick={() => void save({ arc })}
          >
            Save arc
          </button>
        </div>
      </label>

      <div className="field">
        <span className="field__label">Standing corrections</span>
        <span className="field__hint">
          Pinned into every turn, above everything else Augur is told. For drift
          that keeps coming back — “Borveld is dead, never voice him”, “stop
          opening scenes with the weather”. For a one-off, amend the turn
          instead.
        </span>
        {directives.length > 0 && (
          <ul className="items">
            {directives.map((d, i) => (
              <li key={`${d}-${i}`} className="item">
                <span className="item__name">{d}</span>
                <button
                  className="step"
                  aria-label={`Remove: ${d}`}
                  disabled={busy}
                  onClick={() =>
                    void save({
                      directives: directives.filter((_, j) => j !== i),
                    })
                  }
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        )}
        <div className="additem">
          <input
            className="field__input"
            value={directive}
            placeholder="Add a standing correction"
            disabled={busy}
            onChange={(e) => setDirective(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && directive.trim()) {
                e.preventDefault();
                void save({ directives: [...directives, directive.trim()] });
                setDirective("");
              }
            }}
          />
          <button
            className="btn"
            disabled={busy || !directive.trim()}
            onClick={() => {
              void save({ directives: [...directives, directive.trim()] });
              setDirective("");
            }}
          >
            Add
          </button>
        </div>
      </div>

      <div className="field">
        <span className="field__label">Table rules</span>
        <label className="checkline">
          <input
            type="checkbox"
            checked={settings.allow_player_grants !== false}
            disabled={busy}
            onChange={(e) =>
              void save({ allow_player_grants: e.target.checked })
            }
          />
          <span>
            Players may name items into their own inventory. Off means the
            engine refuses anything canon has never heard of.
          </span>
        </label>
        <label className="checkline">
          <input
            type="checkbox"
            checked={Boolean(settings.debug_prompts)}
            disabled={busy}
            onChange={(e) => void save({ debug_prompts: e.target.checked })}
          />
          <span>
            Capture the assembled context on every turn, downloadable from
            Sessions. Not retroactive.
          </span>
        </label>
      </div>
    </section>
  );
}
