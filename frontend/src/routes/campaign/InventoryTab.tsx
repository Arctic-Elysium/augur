import { useState } from "react";
import { useOutletContext } from "react-router-dom";
import { api, type Character } from "../../lib/api";
import type { WorkspaceContext } from "./Workspace";

/** Inventory is per character, not a party pool.
 *
 * Who is carrying the rope matters when the party splits, and it is the kind
 * of thing the game master will be asked to adjudicate. A shared pile hides
 * exactly the information that makes the question interesting. */
function Carried({
  character,
  onChanged,
}: {
  character: Character;
  onChanged: () => void;
}) {
  const [adding, setAdding] = useState("");
  const [busy, setBusy] = useState(false);
  const items = character.sheet.inventory;

  const save = async (next: string[]) => {
    setBusy(true);
    try {
      await api.characters.update(character.id, { inventory: next });
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  const add = () => {
    const item = adding.trim();
    if (!item) return;
    setAdding("");
    void save([...items, item]);
  };

  return (
    <article className="carried">
      <header className="carried__head">
        <h3 className="carried__name">{character.name}</h3>
        <span className="carried__count">
          {items.length} {items.length === 1 ? "item" : "items"}
        </span>
      </header>

      {items.length === 0 ? (
        <p className="carried__empty">Carrying nothing.</p>
      ) : (
        <ul className="items">
          {items.map((item, i) => (
            <li key={`${item}-${i}`} className="item">
              <span className="item__name">{item}</span>
              <button
                className="step"
                disabled={busy}
                aria-label={`Drop ${item}`}
                onClick={() => void save(items.filter((_, j) => j !== i))}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="additem">
        <input
          className="field__input"
          value={adding}
          placeholder="Add an item"
          disabled={busy}
          onChange={(e) => setAdding(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add();
            }
          }}
        />
        <button className="btn" onClick={add} disabled={busy}>
          Add
        </button>
      </div>
    </article>
  );
}

/** The pane is the tab's ONE scroll container, and it must be the top-level
 * element. The previous shape had `.stack` at the top - a plain grid with no
 * overflow, sitting inside the shell's `overflow: hidden` - with a `.pane`
 * wrapped around each card where its `overflow: auto` could never bite.
 * Seventeen items rendered, four reachable, and no scrollbar anywhere. */
export function InventoryTab() {
  const { characters, reload } = useOutletContext<WorkspaceContext>();
  const roster = characters.filter((c) => c.active);

  return (
    <div className="pane">
      <div className="pane__inner">
        {roster.length === 0 ? (
          <div className="empty">
            <h2>Nothing to carry</h2>
            <p>Build a character first.</p>
          </div>
        ) : (
          <div className="stack">
            <p className="field__hint">
              The game master can give and take items during play. Anything
              added here shows up on the sheet the same way.
            </p>
            <div className="sheets">
              {roster.map((c) => (
                <Carried
                  key={c.id}
                  character={c}
                  onChanged={() => void reload()}
                />
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
