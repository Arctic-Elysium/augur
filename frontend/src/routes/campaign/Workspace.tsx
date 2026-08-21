import { useEffect, useState } from "react";
import { NavLink, Outlet, useNavigate, useParams } from "react-router-dom";
import { api, type Campaign, type Character, type PlaySession } from "../../lib/api";

export interface WorkspaceContext {
  campaign: Campaign;
  characters: Character[];
  sessions: PlaySession[];
  activeSession: PlaySession | null;
  reload: () => Promise<void>;
}

/** The campaign row: identity on the left, live-session tabs in the middle,
 *  campaign-level pages on the right. Sessions sits apart from the five because
 *  it is about the campaign rather than about the turn you are taking. */
export function Workspace() {
  const { campaignId } = useParams<{ campaignId: string }>();
  const navigate = useNavigate();
  const [context, setContext] = useState<WorkspaceContext | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    if (!campaignId) return;
    const [campaign, characters, sessions] = await Promise.all([
      api.campaigns.get(campaignId),
      api.characters.list(campaignId),
      api.sessions.list(campaignId),
    ]);
    setContext({
      campaign,
      characters,
      sessions,
      activeSession: sessions.find((s) => s.status === "active") ?? null,
      reload: load,
    });
  };

  useEffect(() => {
    void load().catch((e) => setError((e as Error).message));
  }, [campaignId]);

  // Alt+1..5 across the tabs, Alt+6 to sessions. Reachable mid-session without
  // taking your hands off the composer.
  useEffect(() => {
    if (!campaignId) return;
    const routes = ["", "party", "inventory", "journal", "codex", "table", "sessions"];
    const onKey = (e: KeyboardEvent) => {
      if (!e.altKey) return;
      const n = Number(e.key);
      if (n >= 1 && n <= routes.length) {
        e.preventDefault();
        navigate(`/campaigns/${campaignId}/${routes[n - 1]}`);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [campaignId, navigate]);

  if (error) return <div className="pane"><p className="notice notice--bad">{error}</p></div>;
  if (!context) return <div className="pane"><p className="notice">Loading campaign</p></div>;

  const base = `/campaigns/${campaignId}`;
  const roster = context.characters.filter((c) => c.active);
  const items = roster.reduce((n, c) => n + c.sheet.inventory.length, 0);

  const TABS = [
    { to: "", label: "Play", end: true },
    { to: "party", label: "Party", count: roster.length },
    { to: "inventory", label: "Inventory", count: items },
    { to: "journal", label: "Journal" },
    { to: "codex", label: "Codex" },
  ];

  return (
    <>
      <div className="tabbar">
        <div className="tabbar__identity">
          <span className="tabbar__name">{context.campaign.name}</span>
          <span className="label">
            {roster.length}{" "}
            {roster.length === 1 ? "character" : "characters"}
          </span>
        </div>

        <nav className="tabbar__tabs" aria-label="Live session">
          {TABS.map((t) => (
            <NavLink key={t.label} to={`${base}/${t.to}`} end={t.end} className="tab">
              {t.label}
              {t.count != null && <span className="tab__count">{t.count}</span>}
            </NavLink>
          ))}
        </nav>

        <div className="tabbar__pages">
          <span className="label">
            {context.activeSession
              ? `Session ${context.activeSession.number} live`
              : "No session"}
          </span>
          <span className="tabbar__sep" />
          <NavLink to={`${base}/table`} className="tab tab--page">
            Table
          </NavLink>
          <NavLink to={`${base}/sessions`} className="tab tab--page">
            Sessions
          </NavLink>
        </div>
      </div>

      <Outlet context={context} />
    </>
  );
}
