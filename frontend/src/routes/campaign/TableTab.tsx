import { useEffect, useState } from "react";
import { useOutletContext } from "react-router-dom";
import { api, type Invite, type Member, type Role } from "../../lib/api";
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
