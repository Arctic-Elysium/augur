import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, type AdminCampaign } from "../lib/api";
import { useSession } from "../lib/session";

/** Every campaign on the deployment, not just yours.
 *
 * Deliberately its own page rather than a toggle on the campaign list. The
 * page where you delete your own campaigns should not also be the page
 * showing other people's — those two things being one keystroke apart is how
 * somebody loses a campaign.
 *
 * The nav link that reaches here is hidden for non-admins, but that is a
 * courtesy, not the control: the endpoint re-reads the group claim from the
 * verified token on every request, so a hand-typed URL gets a 403.
 */
export function AdminCampaigns() {
  const session = useSession();
  const [rows, setRows] = useState<AdminCampaign[] | null>(null);
  const [archived, setArchived] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setRows(null);
    void api.campaigns
      .adminAll(archived)
      .then(setRows)
      .catch((e) => setError((e as Error).message));
  }, [archived]);

  if (session.status !== "signedIn" || !session.me.isAdmin) {
    return (
      <div className="pane">
        <div className="pane__inner">
          <div className="empty">
            <h2>Not for you</h2>
            <p>This view is for platform administrators.</p>
          </div>
        </div>
      </div>
    );
  }

  const theirs = (rows ?? []).filter((c) => !c.is_member).length;

  return (
    <div className="pane">
      <div className="pane__inner">
        <header className="pane__head">
          <div>
            <h1 className="pane__title">All campaigns</h1>
            <span className="pane__sub">
              {rows
                ? `${rows.length} total · ${theirs} you are not a member of`
                : "Loading"}
            </span>
          </div>
          <label className="checkline">
            <input
              type="checkbox"
              checked={archived}
              onChange={(e) => setArchived(e.target.checked)}
            />
            <span>Include archived</span>
          </label>
        </header>

        {error && <p className="notice notice--bad">{error}</p>}

        {rows && rows.length === 0 && (
          <div className="empty">
            <h2>Nothing here</h2>
            <p>No campaigns exist on this deployment yet.</p>
          </div>
        )}

        {rows && rows.length > 0 && (
          <table className="grid">
            <thead>
              <tr>
                <th>Campaign</th>
                <th>Owner</th>
                <th className="num">Sessions</th>
                <th className="num">Turns</th>
                <th className="num">Members</th>
                <th>Last played</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => (
                <tr key={c.id} className={c.is_member ? "" : "row--foreign"}>
                  <td>
                    <span className="grid__name">{c.name}</span>
                    {c.status !== "active" && (
                      <span className="grid__tag">{c.status}</span>
                    )}
                    {c.premise && <span className="grid__sub">{c.premise}</span>}
                  </td>
                  <td>
                    {c.is_member ? (
                      <span className="label label--lit">you</span>
                    ) : (
                      <span title={c.owner_subject}>
                        {c.owner_display_name ?? c.owner_subject}
                      </span>
                    )}
                  </td>
                  {/* Play volume is what actually separates a real campaign
                      from the seven test ones. A name never does. */}
                  <td className="num">{c.sessions}</td>
                  <td className="num">{c.turns.toLocaleString()}</td>
                  <td className="num">{c.members}</td>
                  <td className="num num--left">{when(c.last_played_at)}</td>
                  <td>
                    <Link className="btn" to={`/campaigns/${c.id}`}>
                      Open
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <p className="field__hint">
          Opening a campaign you do not belong to gives you game master rights
          in it without joining, so the member list stays honest. Private
          journals stay private — nobody reads those, admins included.
        </p>
      </div>
    </div>
  );
}

function when(iso: string | null) {
  if (!iso) return "never";
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000);
  if (days === 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 30) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}
