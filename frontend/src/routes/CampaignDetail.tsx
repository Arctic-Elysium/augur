import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  api,
  type Campaign,
  type Character,
  type PlaySession,
} from "../lib/api";

const ATTRIBUTES = [
  "might",
  "agility",
  "endurance",
  "wits",
  "insight",
  "presence",
] as const;

/** Standard array. Assign as you like; the sum is fixed so no character is
 *  simply better than another. */
const ARRAY = [15, 14, 13, 12, 10, 9];

function blankSheet(): Record<string, number> {
  return Object.fromEntries(ATTRIBUTES.map((a, i) => [a, ARRAY[i]!]));
}

function CharacterForm({
  campaignId,
  onDone,
  onCancel,
}: {
  campaignId: string;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState("");
  const [attributes, setAttributes] = useState(blankSheet);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const spent = Object.values(attributes).reduce((a, b) => a + b, 0);
  const budget = ARRAY.reduce((a, b) => a + b, 0);

  const submit = async () => {
    if (!name.trim() || busy) return;
    setBusy(true);
    try {
      await api.characters.create({
        campaign_id: campaignId,
        name: name.trim(),
        attributes,
      });
      onDone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="panel">
      <label className="field">
        <span className="field__label">Name</span>
        <input
          className="field__input"
          value={name}
          autoFocus
          placeholder="Vessa"
          onChange={(e) => setName(e.target.value)}
        />
      </label>

      <fieldset className="field">
        <legend className="field__label">
          Attributes
          <span className={`budget ${spent === budget ? "" : "budget--off"}`}>
            {spent} / {budget}
          </span>
        </legend>
        <div className="attrs">
          {ATTRIBUTES.map((attr) => {
            const value = attributes[attr]!;
            const mod = Math.floor((value - 10) / 2);
            return (
              <label key={attr} className="attr">
                <span className="attr__name">{attr}</span>
                <input
                  className="attr__input"
                  type="number"
                  min={3}
                  max={18}
                  value={value}
                  onChange={(e) =>
                    setAttributes({
                      ...attributes,
                      [attr]: Number(e.target.value),
                    })
                  }
                />
                <span className="attr__mod">
                  {mod >= 0 ? `+${mod}` : mod}
                </span>
              </label>
            );
          })}
        </div>
        <span className="field__hint">
          Endurance sets starting health. Scores run 3–18; the modifier is what
          gets added to a roll.
        </span>
      </fieldset>

      {error && <p className="notice notice--bad">{error}</p>}

      <div className="actions">
        <button className="button" onClick={() => void submit()} disabled={busy}>
          {busy ? "Creating" : "Create character"}
        </button>
        <button className="button button--quiet" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}

export function CampaignDetail() {
  const { campaignId } = useParams<{ campaignId: string }>();
  const navigate = useNavigate();
  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [characters, setCharacters] = useState<Character[]>([]);
  const [sessions, setSessions] = useState<PlaySession[]>([]);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    if (!campaignId) return;
    try {
      const [c, chars, sess] = await Promise.all([
        api.campaigns.get(campaignId),
        api.characters.list(campaignId),
        api.sessions.list(campaignId),
      ]);
      setCampaign(c);
      setCharacters(chars);
      setSessions(sess);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  useEffect(() => {
    void load();
  }, [campaignId]);

  const active = sessions.find((s) => s.status === "active");
  const roster = characters.filter((c) => c.active);

  const startSession = async () => {
    if (!campaignId || busy) return;
    setBusy(true);
    try {
      const session = await api.sessions.start(campaignId);
      navigate(`/play/${session.id}`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };

  if (error) return <p className="notice notice--bad">{error}</p>;
  if (!campaign) return <p className="notice">Loading</p>;

  return (
    <section className="stack">
      <header className="pagehead">
        <div>
          <Link className="crumb" to="/">
            Campaigns
          </Link>
          <h1 className="pagehead__title">{campaign.name}</h1>
          {campaign.premise && <p className="lede">{campaign.premise}</p>}
        </div>
      </header>

      <div className="section">
        <div className="section__head">
          <h2 className="section__title">Party</h2>
          {!adding && (
            <button className="button button--quiet" onClick={() => setAdding(true)}>
              Add character
            </button>
          )}
        </div>

        {adding && campaignId && (
          <CharacterForm
            campaignId={campaignId}
            onDone={() => {
              setAdding(false);
              void load();
            }}
            onCancel={() => setAdding(false)}
          />
        )}

        {roster.length === 0 && !adding ? (
          <p className="empty">
            No one to play yet. Add at least one character — in solo mode you
            can run several at once.
          </p>
        ) : (
          <ul className="roster">
            {roster.map((c) => (
              <li key={c.id} className="rostered">
                <span className="rostered__name">{c.name}</span>
                <span className="rostered__stats">
                  {c.sheet.hp}/{c.sheet.hp_max} health
                  {c.sheet.conditions.length > 0 &&
                    ` · ${c.sheet.conditions.map((x) => x.spec_id).join(", ")}`}
                </span>
                <span className="rostered__attrs">
                  {ATTRIBUTES.map((a) => (
                    <span key={a} className="tick">
                      <span className="tick__name">{a.slice(0, 3)}</span>
                      <span className="tick__value">{c.sheet.attributes[a]}</span>
                    </span>
                  ))}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="section">
        <div className="section__head">
          <h2 className="section__title">Sessions</h2>
        </div>

        {active ? (
          <Link className="button" to={`/play/${active.id}`}>
            Resume session {active.number}
          </Link>
        ) : (
          <button
            className="button"
            onClick={() => void startSession()}
            disabled={busy || roster.length === 0}
          >
            {busy ? "Starting" : `Start session ${sessions.length + 1}`}
          </button>
        )}

        {roster.length === 0 && (
          <p className="field__hint">Add a character first.</p>
        )}

        {sessions.filter((s) => s.status === "ended").length > 0 && (
          <ul className="past">
            {sessions
              .filter((s) => s.status === "ended")
              .map((s) => (
                <li key={s.id}>
                  <Link className="past__link" to={`/play/${s.id}`}>
                    Session {s.number}
                  </Link>
                </li>
              ))}
          </ul>
        )}
      </div>
    </section>
  );
}
