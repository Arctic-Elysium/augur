import { useState } from "react";
import { useNavigate, useOutletContext } from "react-router-dom";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { api } from "../../lib/api";
import type { WorkspaceContext } from "./Workspace";

export function SessionsTab() {
  const { campaign, sessions, activeSession, reload } =
    useOutletContext<WorkspaceContext>();
  const navigate = useNavigate();
  const [confirm, setConfirm] = useState<null | {
    title: string;
    body: string;
    label: string;
    destructive?: boolean;
    run: () => void;
  }>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const run = async (fn: () => Promise<unknown>) => {
    try {
      await fn();
      await reload();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const totalTurns = sessions.reduce((n, s) => n + (s.turn_count ?? 0), 0);

  return (
    <div className="pane">
      <div className="pane__inner">
        <header className="pane__head">
          <div>
            <h1 className="pane__title">Sessions</h1>
            <span className="pane__sub">
              {sessions.length} {sessions.length === 1 ? "session" : "sessions"} ·{" "}
              {totalTurns.toLocaleString()} turns
            </span>
          </div>
          {activeSession ? (
            <button
              className="btn btn--go"
              onClick={() =>
                setConfirm({
                  title: `End session ${activeSession.number}?`,
                  body:
                    "The log is sealed and becomes part of the campaign's memory. " +
                    "The party keeps its conditions, items and clocks into the next session.",
                  label: "End session",
                  run: () => void run(() => api.sessions.end(activeSession.id)),
                })
              }
            >
              End session {activeSession.number}
            </button>
          ) : (
            <button
              className="btn btn--go"
              onClick={() => void run(() => api.sessions.start(campaign.id))}
            >
              Start session {sessions.length + 1}
            </button>
          )}
        </header>

        {error && <p className="notice notice--bad">{error}</p>}

        {sessions.length === 0 ? (
          <div className="empty">
            <h2>Nothing played yet</h2>
            <p>Sessions appear here once you start one.</p>
          </div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th style={{ width: 56 }}>No.</th>
                <th style={{ width: 150 }}>Started</th>
                <th style={{ width: 80, textAlign: "right" }}>Turns</th>
                <th>Title</th>
                <th style={{ width: 250, textAlign: "right" }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {sessions.map((s) => {
                const live = s.status === "active";
                return (
                  <tr key={s.id} className={live ? "table__row--live" : ""}>
                    <td className={`num ${live ? "num--live" : ""}`}>{s.number}</td>
                    <td className="num num--left">{formatDate(s.created_at)}</td>
                    <td className="num">{(s.turn_count ?? 0).toLocaleString()}</td>
                    <td>
                      {renaming === s.id ? (
                        <RenameField
                          value={s.title ?? ""}
                          onCommit={(title) => {
                            setRenaming(null);
                            void run(() => api.sessions.rename(s.id, title));
                          }}
                          onCancel={() => setRenaming(null)}
                        />
                      ) : (
                        <>
                          <span className="table__title">
                            {s.title || (live ? "In progress" : `Session ${s.number}`)}
                          </span>
                          {s.summary && (
                            <span className="table__summary">{s.summary}</span>
                          )}
                        </>
                      )}
                    </td>
                    <td>
                      <div className="table__actions">
                        {live ? (
                          <button className="btn" onClick={() => navigate("..")}>
                            Resume
                          </button>
                        ) : (
                          <button
                            className="btn"
                            onClick={() => navigate(`..?session=${s.id}`)}
                          >
                            Open
                          </button>
                        )}
                        <button className="btn" onClick={() => setRenaming(s.id)}>
                          Rename
                        </button>
                        {/* Includes the mechanics, not just the prose - drift
                            is usually visible in what the engine did versus
                            what the narration claimed. */}
                        <a
                          className="btn"
                          href={api.sessions.exportUrl(s.id, "md")}
                          download
                        >
                          Export
                        </a>
                        {!live && (
                          <button
                            className="btn"
                            onClick={() =>
                              setConfirm({
                                title: `Delete session ${s.number}?`,
                                body:
                                  `Its ${(s.turn_count ?? 0).toLocaleString()} turns are ` +
                                  "removed permanently. Journal entries you wrote during it " +
                                  "stay — those are yours. This cannot be undone.",
                                label: "Delete session",
                                destructive: true,
                                run: () => void run(() => api.sessions.remove(s.id)),
                              })
                            }
                          >
                            Delete
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {confirm && <ConfirmDialog {...confirm} onClose={() => setConfirm(null)} />}
    </div>
  );
}

function formatDate(iso: string | null | undefined) {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

/** Commits on Enter, abandons on Escape. */
function RenameField({
  value,
  onCommit,
  onCancel,
}: {
  value: string;
  onCommit: (v: string) => void;
  onCancel: () => void;
}) {
  const [draft, setDraft] = useState(value);
  return (
    <input
      className="field__input"
      value={draft}
      autoFocus
      placeholder="The night the bridge went"
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => onCommit(draft)}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          onCommit(draft);
        }
        if (e.key === "Escape") onCancel();
      }}
    />
  );
}
