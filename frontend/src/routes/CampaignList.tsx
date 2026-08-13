import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { api, type Campaign, type PlayMode } from "../lib/api";

const MODES: { value: PlayMode; label: string; note: string }[] = [
  { value: "solo", label: "Solo", note: "You run the party. Augur runs the world." },
  { value: "party", label: "Party", note: "Several players, Augur as GM." },
  { value: "table", label: "Table", note: "A human GM, Augur assisting." },
];

export function CampaignList() {
  const [campaigns, setCampaigns] = useState<Campaign[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [premise, setPremise] = useState("");
  const [mode, setMode] = useState<PlayMode>("solo");
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<Campaign | null>(null);

  const load = () =>
    api.campaigns.list().then(setCampaigns).catch((e) => setError(e.message));

  useEffect(() => {
    void load();
  }, []);

  const create = async () => {
    if (!name.trim() || busy) return;
    setBusy(true);
    try {
      await api.campaigns.create({
        name: name.trim(),
        premise: premise.trim() || undefined,
        play_mode: mode,
      });
      setName("");
      setPremise("");
      setCreating(false);
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (error)
    return (
      <div className="pane">
        <div className="pane__inner">
          <p className="notice notice--bad">{error}</p>
        </div>
      </div>
    );
  if (!campaigns)
    return (
      <div className="pane">
        <div className="pane__inner">
          <p className="notice">Loading campaigns</p>
        </div>
      </div>
    );

  return (
    <div className="pane">
      <div className="pane__inner">
      <header className="pane__head">
        <div>
          <h1 className="pane__title">Campaigns</h1>
          <span className="pane__sub">
            {campaigns.length} {campaigns.length === 1 ? "campaign" : "campaigns"}
          </span>
        </div>
        {!creating && (
          <button className="btn btn--go" onClick={() => setCreating(true)}>
            New campaign
          </button>
        )}
      </header>

      {creating && (
        <div className="panel">
          <label className="field">
            <span className="field__label">Name</span>
            <input
              className="field__input"
              value={name}
              autoFocus
              placeholder="Ashfell"
              onChange={(e) => setName(e.target.value)}
            />
          </label>

          <label className="field">
            <span className="field__label">Premise</span>
            <textarea
              className="field__input"
              rows={3}
              value={premise}
              placeholder="A mining town where the ore came up wrong, and the company is still paying for silence."
              onChange={(e) => setPremise(e.target.value)}
            />
            <span className="field__hint">
              Optional. Sets the tone Augur narrates in.
            </span>
          </label>

          <fieldset className="field">
            <legend className="field__label">Mode</legend>
            <div className="choices">
              {MODES.map((m) => (
                <button
                  key={m.value}
                  type="button"
                  className={`choice ${mode === m.value ? "choice--on" : ""}`}
                  onClick={() => setMode(m.value)}
                  aria-pressed={mode === m.value}
                >
                  <span className="choice__label">{m.label}</span>
                  <span className="choice__note">{m.note}</span>
                </button>
              ))}
            </div>
          </fieldset>

          <div className="actions">
            <button className="btn btn--go" onClick={() => void create()} disabled={busy}>
              {busy ? "Creating" : "Create campaign"}
            </button>
            <button className="btn" onClick={() => setCreating(false)}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {campaigns.length === 0 && !creating ? (
        <div className="empty">
          <h2>No campaigns yet</h2>
          <p>Start one and the world gets written as you walk into it.</p>
        </div>
      ) : (
        <ul className="cards">
          {campaigns.map((c) => (
            <li key={c.id} className="card">
              <div className="card__row">
                <Link
                  to={`/campaigns/${c.id}`}
                  style={{ display: "grid", gap: 5, textDecoration: "none", color: "inherit" }}
                >
                  <span className="card__eyebrow">{c.play_mode}</span>
                  <span className="card__title">{c.name}</span>
                  <span className="card__body">{c.premise ?? "No premise set."}</span>
                </Link>
                <button className="btn" onClick={() => setConfirm(c)}>
                  Delete
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {confirm && (
        <ConfirmDialog
          title={`Delete ${confirm.name}?`}
          body={
            "Every character, session, turn and codex entry in this campaign is " +
            "removed permanently. This cannot be undone."
          }
          label="Delete campaign"
          destructive
          run={() => {
            void api.campaigns
              .remove(confirm.id)
              .then(load)
              .catch((e) => setError((e as Error).message));
          }}
          onClose={() => setConfirm(null)}
        />
      )}
      </div>
    </div>
  );
}
