import { useState } from "react";
import { useOutletContext } from "react-router-dom";
import { api, type Character } from "../../lib/api";
import type { WorkspaceContext } from "./Workspace";
import { Builder } from "./Builder";

const HOOK_LABEL: Record<string, string> = {
  bond: "Bond", debt: "Debt", goal: "Goal",
  flaw: "Flaw", fear: "Fear", secret: "Secret",
};

const REASONS = [
  { id: "dead", label: "Died", note: "The game master can speak of them, and of what is owed." },
  { id: "retired", label: "Retired", note: "Set aside. Still out there somewhere." },
  { id: "missing", label: "Missing", note: "Unaccounted for. Could come back." },
] as const;

function SheetView({
  character,
  onChanged,
  onArchive,
}: {
  character: Character;
  onChanged: () => void;
  onArchive: () => void;
}) {
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

      {sheet.inventory.length > 0 && (
        <div className="sheetcard__block">
          <span className="field__label">Carrying</span>
          <span className="taglist">
            {sheet.inventory.map((item, i) => (
              <span key={`${item}-${i}`} className="tag">
                {item}
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
            <p className="prose">{character.backstory || "Nothing written yet."}</p>
            <div className="actions">
              <button className="btn" onClick={() => setEditing(true)}>
                Edit
              </button>
              <button className="btn" onClick={onArchive}>
                Archive
              </button>
            </div>
          </>
        )}
      </div>
    </article>
  );
}

/** Archiving is never deletion.
 *
 * A dead character is still part of what happened - still owed things, still
 * worth avenging - and the game master needs to be able to refer to them. The
 * reason is written to canon so it can. */
function ArchiveDialog({
  character,
  onDone,
  onCancel,
}: {
  character: Character;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [reason, setReason] = useState<"dead" | "retired" | "missing">("dead");
  const [epitaph, setEpitaph] = useState("");
  const [busy, setBusy] = useState(false);

  return (
    <div className="panel">
      <h3 className="field__label">Archive {character.name}</h3>
      <div className="choices">
        {REASONS.map((r) => (
          <button
            key={r.id}
            className={`choice ${reason === r.id ? "choice--on" : ""}`}
            onClick={() => setReason(r.id)}
          >
            <span className="choice__label">{r.label}</span>
            <span className="choice__note">{r.note}</span>
          </button>
        ))}
      </div>
      <label className="field">
        <span className="field__label">How it happened</span>
        <textarea
          className="field__input"
          rows={2}
          value={epitaph}
          placeholder="Fell at the east gate covering the retreat."
          onChange={(e) => setEpitaph(e.target.value)}
        />
        <span className="field__hint">
          Recorded as canon. The game master will know, and can bring it up.
        </span>
      </label>
      <div className="actions">
        <button
          className="btn btn--go"
          disabled={busy}
          onClick={() => {
            setBusy(true);
            void api.characters
              .archive(character.id, reason, epitaph || undefined)
              .then(onDone)
              .finally(() => setBusy(false));
          }}
        >
          Archive
        </button>
        <button className="btn" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}

export function PartyTab() {
  const { campaign, characters, reload } = useOutletContext<WorkspaceContext>();
  const [building, setBuilding] = useState(false);
  const [archiving, setArchiving] = useState<Character | null>(null);

  const roster = characters.filter((c) => c.active);
  const archived = characters.filter((c) => !c.active);

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

        {archiving && (
          <ArchiveDialog
            character={archiving}
            onDone={() => {
              setArchiving(null);
              void reload();
            }}
            onCancel={() => setArchiving(null)}
          />
        )}

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
              <SheetView
                key={c.id}
                character={c}
                onChanged={() => void reload()}
                onArchive={() => setArchiving(c)}
              />
            ))}
          </div>
        )}

        {archived.length > 0 && (
          <div className="section">
            <div className="section__head">
              <h2 className="section__title">No longer with the party</h2>
            </div>
            <ul className="roster">
              {archived.map((c) => (
                <li key={c.id} className="gone">
                  <span className="gone__name">{c.name}</span>
                  <span className="gone__reason">
                    {c.archived_reason === "dead"
                      ? "Died"
                      : c.archived_reason === "missing"
                        ? "Missing"
                        : "Retired"}
                    {c.epitaph ? ` — ${c.epitaph}` : ""}
                  </span>
                  <button
                    className="linkish"
                    onClick={() => void api.characters.restore(c.id).then(reload)}
                  >
                    Restore
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
