import { useEffect, useState } from "react";
import { useOutletContext } from "react-router-dom";
import { api, type CodexEntity, type PendingQueue } from "../../lib/api";
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
  const [editing, setEditing] = useState<string | null>(null);
  const [merging, setMerging] = useState<string | null>(null);
  const [queue, setQueue] = useState<PendingQueue | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());

  const reload = () =>
    Promise.all([
      api.memory.codex(campaign.id).then((r) => setEntities(r.entities)),
      api.memory
        .pending(campaign.id)
        // Players do not have a queue; a 403 here is expected, not an error.
        .then(setQueue)
        .catch(() => setQueue(null)),
    ]).catch((e) => setError((e as Error).message));

  const toggle = (key: string) =>
    setPicked((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });

  const review = (accept: boolean, all = false) => {
    const body = all
      ? { all_pending: true }
      : {
          entity_refs: [...picked]
            .filter((k) => k.startsWith("e:"))
            .map((k) => k.slice(2)),
          fact_ids: [...picked]
            .filter((k) => k.startsWith("f:"))
            .map((k) => k.slice(2)),
        };
    const call = accept
      ? api.memory.accept(campaign.id, body)
      : api.memory.reject(campaign.id, body);
    void call
      .then(() => {
        setPicked(new Set());
        return reload();
      })
      .catch((e) => setError((e as Error).message));
  };

  const run = (p: Promise<unknown>) =>
    void p.then(reload).catch((e) => setError((e as Error).message));

  useEffect(() => {
    void reload();
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

        {queue && (queue.entities.length > 0 || queue.facts.length > 0) && (
          <section className="review">
            <header className="review__head">
              <div>
                <span className="label label--lit">Waiting on you</span>
                <p className="review__note">
                  Augur proposed these. Nothing here reaches the codex, or any
                  future session, until you accept it — but play is not waiting:
                  the current session already remembers all of it.
                </p>
              </div>
              <div className="actions">
                <button
                  className="btn"
                  disabled={picked.size === 0}
                  onClick={() => review(false)}
                >
                  Reject {picked.size || ""}
                </button>
                <button
                  className="btn btn--go"
                  disabled={picked.size === 0}
                  onClick={() => review(true)}
                >
                  Accept {picked.size || ""}
                </button>
                <button className="linkish" onClick={() => review(true, true)}>
                  Accept all
                </button>
              </div>
            </header>

            <ul className="review__list">
              {queue.entities.map((e) => (
                <li key={`e:${e.ref}`} className="review__item">
                  <label>
                    <input
                      type="checkbox"
                      checked={picked.has(`e:${e.ref}`)}
                      onChange={() => toggle(`e:${e.ref}`)}
                    />
                    <span className="review__kind">{e.kind}</span>
                    <span className="review__name">{e.name}</span>
                    <span className="review__body">{e.summary}</span>
                  </label>
                </li>
              ))}
              {queue.facts.map((f) => (
                <li key={`f:${f.id}`} className="review__item">
                  <label>
                    <input
                      type="checkbox"
                      checked={picked.has(`f:${f.id}`)}
                      onChange={() => toggle(`f:${f.id}`)}
                    />
                    <span className="review__kind">fact</span>
                    <span className="review__body">
                      {f.subject_ref.split(":").pop()} {f.predicate}{" "}
                      {f.object_text}
                    </span>
                  </label>
                </li>
              ))}
            </ul>
          </section>
        )}
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

                    {expanded && editing === entity.ref && (
                      <EditEntity
                        entity={entity}
                        onSave={(body) =>
                          run(
                            api.memory
                              .updateEntity(campaign.id, entity.ref, body)
                              .then(() => setEditing(null)),
                          )
                        }
                        onCancel={() => setEditing(null)}
                      />
                    )}

                    {expanded && merging === entity.ref && (
                      <div className="entry-card__facts">
                        <span className="field__label">Merge into</span>
                        <p className="field__hint">
                          Facts and mentions move across. This one is removed.
                        </p>
                        <div className="choices--inline">
                          {shown
                            .filter(
                              (o) => o.ref !== entity.ref && o.kind === entity.kind,
                            )
                            .map((o) => (
                              <button
                                key={o.ref}
                                className="chip"
                                onClick={() =>
                                  run(
                                    api.memory
                                      .mergeEntity(campaign.id, entity.ref, o.ref)
                                      .then(() => setMerging(null)),
                                  )
                                }
                              >
                                {o.name}
                              </button>
                            ))}
                        </div>
                        <button className="linkish" onClick={() => setMerging(null)}>
                          Cancel
                        </button>
                      </div>
                    )}

                    {expanded && (
                      <div className="entry-card__actions">
                        <button
                          className="linkish"
                          onClick={() => {
                            setEditing(entity.ref);
                            setMerging(null);
                          }}
                        >
                          Edit
                        </button>
                        <button
                          className="linkish"
                          onClick={() => {
                            setMerging(entity.ref);
                            setEditing(null);
                          }}
                        >
                          Merge
                        </button>
                        <button
                          className="linkish"
                          onClick={() =>
                            run(api.memory.deleteEntity(campaign.id, entity.ref))
                          }
                        >
                          Delete
                        </button>
                      </div>
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

const KIND_OPTIONS = ["npc", "location", "faction", "item", "creature", "concept"];

/** Extraction is a guess, and a wrong guess is visible to the player. This is
 *  the repair: rename it, reclassify it, or hide it from the codex while the
 *  game master keeps knowing about it. */
function EditEntity({
  entity,
  onSave,
  onCancel,
}: {
  entity: CodexEntity;
  onSave: (body: {
    name?: string;
    kind?: string;
    summary?: string;
    known_to_players?: boolean;
  }) => void;
  onCancel: () => void;
}) {
  const [name, setName] = useState(entity.name);
  const [kind, setKind] = useState(entity.kind);
  const [summary, setSummary] = useState(entity.summary);

  return (
    <div className="entry-card__facts">
      <label className="field">
        <span className="field__label">Name</span>
        <input
          className="field__input"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </label>
      <fieldset className="field">
        <span className="field__label">Kind</span>
        <div className="choices--inline">
          {KIND_OPTIONS.map((k) => (
            <button
              key={k}
              className={`filter ${kind === k ? "filter--on" : ""}`}
              onClick={() => setKind(k)}
            >
              {k}
            </button>
          ))}
        </div>
      </fieldset>
      <label className="field">
        <span className="field__label">Summary</span>
        <textarea
          className="field__input"
          rows={3}
          value={summary}
          onChange={(e) => setSummary(e.target.value)}
        />
      </label>
      <div className="actions">
        <button
          className="btn btn--go"
          onClick={() => onSave({ name, kind, summary })}
        >
          Save
        </button>
        <button className="btn" onClick={onCancel}>
          Cancel
        </button>
        <button
          className="btn"
          onClick={() => onSave({ known_to_players: false })}
          title="Keeps it in the game master's memory, hides it from the codex"
        >
          Hide from codex
        </button>
      </div>
    </div>
  );
}
