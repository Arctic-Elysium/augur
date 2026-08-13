import { useEffect, useState } from "react";
import { useOutletContext } from "react-router-dom";
import { api, type CodexEntity } from "../../lib/api";
import type { WorkspaceContext } from "./Workspace";

const KINDS = ["npc", "location", "faction", "item", "creature", "concept"];
const KIND_LABEL: Record<string, string> = {
  npc: "People",
  location: "Places",
  faction: "Factions",
  item: "Things",
  creature: "Creatures",
  concept: "Matters",
};

/** What the world has established.
 *
 * Distinct from the journal: this is what the game master committed to, not
 * what you believe. Secret facts are filtered server-side - a prepared
 * adventure keeps its secrets in canon so the GM can run the scene, and
 * surfacing them here would spoil the thing you are playing to find out. */
export function CodexTab() {
  const { campaign } = useOutletContext<WorkspaceContext>();
  const [entities, setEntities] = useState<CodexEntity[] | null>(null);
  const [filter, setFilter] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.memory
      .codex(campaign.id)
      .then((r) => setEntities(r.entities))
      .catch((e) => setError((e as Error).message));
  }, [campaign.id]);

  const present = KINDS.filter((k) => (entities ?? []).some((e) => e.kind === k));
  const shown =
    filter && entities ? entities.filter((e) => e.kind === filter) : entities ?? [];

  return (
    <div className="pane">
      <div className="pane__inner">
        <header className="pane__head">
          <div>
            <h1 className="pane__title">Codex</h1>
            <span className="pane__sub">
              What the world has established, and where it was established.
            </span>
          </div>
        </header>

        {error && <p className="notice notice--bad">{error}</p>}
        {!entities && !error && <p className="notice">Loading codex</p>}

        {entities && entities.length === 0 && (
          <div className="empty">
            <h2>Nothing recorded yet</h2>
            <p>
              People, places and things get written here as they come up in play.
              The game master reads from the same record, which is what keeps a
              long campaign from contradicting itself.
            </p>
          </div>
        )}

        {entities && entities.length > 0 && (
          <>
            <div className="choices--inline">
              <button
                className={`filter ${filter === null ? "filter--on" : ""}`}
                onClick={() => setFilter(null)}
              >
                All {entities.length}
              </button>
              {present.map((kind) => (
                <button
                  key={kind}
                  className={`filter ${filter === kind ? "filter--on" : ""}`}
                  onClick={() => setFilter(kind)}
                >
                  {KIND_LABEL[kind] ?? kind}{" "}
                  {entities.filter((e) => e.kind === kind).length}
                </button>
              ))}
            </div>

            <ul className="codex">
              {shown.map((entity) => {
                const expanded = open === entity.ref;
                return (
                  <li key={entity.ref} className="entry-card">
                    <button
                      className="entry-card__head"
                      onClick={() => setOpen(expanded ? null : entity.ref)}
                      aria-expanded={expanded}
                    >
                      <span className="entry-card__kind">
                        {KIND_LABEL[entity.kind] ?? entity.kind}
                      </span>
                      <span className="entry-card__name">{entity.name}</span>
                      <span className="entry-card__meta">
                        {entity.first_seen_session
                          ? `session ${entity.first_seen_session}`
                          : ""}
                      </span>
                    </button>

                    {entity.summary && (
                      <p className="entry-card__summary">{entity.summary}</p>
                    )}

                    {expanded && (
                      <div className="entry-card__facts">
                        {entity.facts.length === 0 ? (
                          <p className="field__hint">Nothing further established.</p>
                        ) : (
                          <ul className="facts">
                            {entity.facts.map((fact, i) => (
                              <li key={i} className="fact">
                                <span className="fact__predicate">
                                  {fact.predicate}
                                </span>
                                <span>{fact.object_text}</span>
                                {fact.session_number && (
                                  <span className="fact__when">
                                    s{fact.session_number}
                                  </span>
                                )}
                              </li>
                            ))}
                          </ul>
                        )}
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          </>
        )}
      </div>
    </div>
  );
}
