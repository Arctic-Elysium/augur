import { useNavigate, useOutletContext } from "react-router-dom";
import { useState } from "react";
import { api } from "../../lib/api";
import type { WorkspaceContext } from "./Workspace";
import { Play } from "../Play";

/** Play is either the live session or the door into one. Keeping both here
 *  means the Play tab is never a dead end. */
export function PlayTab() {
  const { campaign, characters, sessions, reload } =
    useOutletContext<WorkspaceContext>();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const active = sessions.find((s) => s.status === "active");
  const roster = characters.filter((c) => c.active);

  if (active) return <Play sessionId={active.id} />;

  const start = async () => {
    setBusy(true);
    try {
      await api.sessions.start(campaign.id);
      await reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack">
      {roster.length === 0 ? (
        <div className="empty">
          <h2>No one to play</h2>
          <p>
            Build at least one character first. In solo mode you can run several
            at once, and each gets their own attempt at the same locked door.
          </p>
          <button className="button" onClick={() => navigate("party")}>
            Go to Party
          </button>
        </div>
      ) : (
        <div className="empty">
          <h2>No session running</h2>
          <p>
            {roster.length === 1
              ? `${roster[0]!.name} is ready.`
              : `${roster.length} characters ready.`}
          </p>
          <button className="button" onClick={() => void start()} disabled={busy}>
            {busy ? "Starting" : `Start session ${sessions.length + 1}`}
          </button>
        </div>
      )}

      {error && <p className="notice notice--bad">{error}</p>}

      {sessions.length > 0 && (
        <div className="section">
          <div className="section__head">
            <h2 className="section__title">Past sessions</h2>
          </div>
          <ul className="past">
            {sessions
              .filter((s) => s.status === "ended")
              .map((s) => (
                <li key={s.id}>
                  <button
                    className="past__link"
                    onClick={() => navigate(`/play/${s.id}`)}
                  >
                    Session {s.number}
                  </button>
                </li>
              ))}
          </ul>
        </div>
      )}
    </div>
  );
}
