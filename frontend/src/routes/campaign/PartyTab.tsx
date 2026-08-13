import { useState } from "react";
import { useOutletContext } from "react-router-dom";
import { api, type Character } from "../../lib/api";
import type { WorkspaceContext } from "./Workspace";
import { Builder } from "./Builder";

const HOOK_LABEL: Record<string, string> = {
  bond: "Bond", debt: "Debt", goal: "Goal",
  flaw: "Flaw", fear: "Fear", secret: "Secret",
};

function SheetView({ character, onChanged }: { character: Character; onChanged: () => void }) {
  const [editing, setEditing] = useState(false);
  const [backstory, setBackstory] = useState(character.backstory ?? "");
  const sheet = character.sheet;
  const attrs = Object.entries(sheet.attributes);
  const skills = Object.entries(sheet.skills).filter(([, v]) => v > 0);

  const save = async () => {
    await api.characters.update(character.id, { backstory });
    setEditing(false);
    onChanged();
  };

  return (
    <div className="pane">
      <div className="pane__inner">
            <article className="sheetcard">
              <header className="sheetcard__head">
                <h3 className="sheetcard__name">{character.name}</h3>
                <span className="sheetcard__level">Level {sheet.level}</span>
              </header>

              <div className="vitals">
                <div className="vital">
                  <span className="vital__label">Health</span>
                  <span className="vital__track">
                    <span
                      className="vital__fill"
                      style={{ width: `${(sheet.hp / sheet.hp_max) * 100}%` }}
                    />
                  </span>
                  <span className="vital__value">
                    {sheet.hp}/{sheet.hp_max}
                  </span>
                </div>
                <div className="vital">
                  <span className="vital__label">Stress</span>
                  <span className="vital__track">
                    <span
                      className="vital__fill vital__fill--stress"
                      style={{ width: `${(sheet.stress / sheet.stress_max) * 100}%` }}
                    />
                  </span>
                  <span className="vital__value">
                    {sheet.stress}/{sheet.stress_max}
                  </span>
                </div>
              </div>

              <div className="statline">
                {attrs.map(([id, value]) => {
                  const mod = Math.floor((value - 10) / 2);
                  return (
                    <div key={id} className="stat">
                      <span className="stat__name">{id.slice(0, 3)}</span>
                      <span className="stat__mod">{mod >= 0 ? `+${mod}` : mod}</span>
                      <span className="stat__raw">{value}</span>
                    </div>
                  );
                })}
              </div>

              {skills.length > 0 && (
                <div className="sheetcard__block">
                  <span className="field__label">Trained</span>
                  <span className="taglist">
                    {skills.map(([id, rank]) => (
                      <span key={id} className="tag">
                        {id.replace(/_/g, " ")} {"·".repeat(rank)}
                      </span>
                    ))}
                  </span>
                </div>
              )}

              {sheet.conditions.length > 0 && (
                <div className="sheetcard__block">
                  <span className="field__label">Conditions</span>
                  <span className="taglist">
                    {sheet.conditions.map((c) => (
                      <span key={c.spec_id} className="tag tag--warn">
                        {c.spec_id}
                      </span>
                    ))}
                  </span>
                </div>
              )}

              {character.hooks.length > 0 && (
                <div className="sheetcard__block">
                  <span className="field__label">Threads</span>
                  <ul className="threads">
                    {character.hooks.map((h, i) => (
                      <li key={i} className="thread">
                        <span className="thread__kind">{HOOK_LABEL[h.kind] ?? h.kind}</span>
                        <span className="thread__subject">{h.subject}</span>
                        {h.detail && <span className="thread__detail">{h.detail}</span>}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              <div className="sheetcard__block">
                <span className="field__label">Who they are</span>
                {editing ? (
                  <>
                    <textarea
                      className="field__input"
                      rows={5}
                      value={backstory}
                      onChange={(e) => setBackstory(e.target.value)}
                    />
                    <div className="actions">
                      <button className="btn btn--go" onClick={() => void save()}>
                        Save
                      </button>
                      <button className="btn" onClick={() => setEditing(false)}>
                        Cancel
                      </button>
                    </div>
                  </>
                ) : (
                  <>
                    <p className="prose">
                      {character.backstory || "Nothing written yet."}
                    </p>
                    <button className="btn" onClick={() => setEditing(true)}>
                      Edit
                    </button>
                  </>
                )}
              </div>
            </article>
      </div>
    </div>
  );
}

export function PartyTab() {
  const { campaign, characters, reload } = useOutletContext<WorkspaceContext>();
  const [building, setBuilding] = useState(false);
  const roster = characters.filter((c) => c.active);

  // Both branches need their own scroll container: the shell is
  // height:100dvh with overflow:hidden, so anything taller than the viewport
  // is unreachable without one. The builder is always taller.
  if (building) {
    return (
      <div className="pane">
        <div className="pane__inner">
          <Builder
            campaignId={campaign.id}
            onDone={() => {
              setBuilding(false);
              void reload();
            }}
            onCancel={() => setBuilding(false)}
          />
        </div>
      </div>
    );
  }

  return (
    <div className="pane">
      <div className="pane__inner">
        <header className="pane__head">
          <div>
            <h1 className="pane__title">Party</h1>
            <span className="pane__sub">
              {roster.length} {roster.length === 1 ? "character" : "characters"}
            </span>
          </div>
          <button className="btn btn--go" onClick={() => setBuilding(true)}>
            Build a character
          </button>
        </header>

        {roster.length === 0 ? (
          <div className="empty">
            <h2>Nobody here yet</h2>
            <p>
              Build someone. In solo mode you can run several at once — each
              takes their own turns and gets their own attempt at the same
              locked door.
            </p>
          </div>
        ) : (
          <div className="sheets">
            {roster.map((c) => (
              <SheetView key={c.id} character={c} onChanged={() => void reload()} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
