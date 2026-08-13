import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useParams } from "react-router-dom";
import { api, type Campaign, type Character, type PlaySession } from "../../lib/api";

/** Everything about a campaign lives under one workspace.
 *
 * The tabs are not decoration - they are the app's actual nouns. A campaign is
 * a party, a set of things they carry, what they have learned, and the session
 * in progress. Flat routes made those feel unrelated. */
const TABS = [
  { to: "", label: "Play", end: true },
  { to: "party", label: "Party" },
  { to: "inventory", label: "Inventory" },
  { to: "journal", label: "Journal" },
  { to: "codex", label: "Codex" },
];

export interface WorkspaceContext {
  campaign: Campaign;
  characters: Character[];
  sessions: PlaySession[];
  reload: () => Promise<void>;
}

export function Workspace() {
  const { campaignId } = useParams<{ campaignId: string }>();
  const [context, setContext] = useState<WorkspaceContext | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    if (!campaignId) return;
    const [campaign, characters, sessions] = await Promise.all([
      api.campaigns.get(campaignId),
      api.characters.list(campaignId),
      api.sessions.list(campaignId),
    ]);
    setContext({ campaign, characters, sessions, reload: load });
  };

  useEffect(() => {
    void load().catch((e) => setError((e as Error).message));
  }, [campaignId]);

  if (error) return <p className="notice notice--bad">{error}</p>;
  if (!context) return <p className="notice">Loading campaign</p>;

  const active = context.sessions.find((s) => s.status === "active");

  return (
    <div className="workspace">
      <header className="workspace__head">
        <div>
          <Link className="crumb" to="/">
            Campaigns
          </Link>
          <h1 className="workspace__title">{context.campaign.name}</h1>
        </div>
        <span className="workspace__state">
          {active ? `Session ${active.number} in progress` : "No session running"}
        </span>
      </header>

      <nav className="tabs">
        {TABS.map((tab) => (
          <NavLink
            key={tab.label}
            to={tab.to}
            end={tab.end}
            className={({ isActive }) => `tab ${isActive ? "tab--on" : ""}`}
          >
            {tab.label}
          </NavLink>
        ))}
      </nav>

      <div className="workspace__body">
        <Outlet context={context} />
      </div>
    </div>
  );
}
