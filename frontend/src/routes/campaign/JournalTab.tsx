import { useEffect, useState } from "react";
import { useOutletContext } from "react-router-dom";
import { api, type Note } from "../../lib/api";
import type { WorkspaceContext } from "./Workspace";

/** Your notes. Never sent to the game master.
 *
 * A journal you suspect is being read is a journal you stop being honest in,
 * and half the value of taking notes is recording a suspicion you are not yet
 * ready to act on. */
export function JournalTab() {
  const { campaign, sessions } = useOutletContext<WorkspaceContext>();
  const [notes, setNotes] = useState<Note[] | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState({ title: "", body: "" });
  const [error, setError] = useState<string | null>(null);

  const active = sessions.find((s) => s.status === "active");

  const load = () =>
    api.memory
      .notes(campaign.id)
      .then(setNotes)
      .catch((e) => setError((e as Error).message));

  useEffect(() => {
    void load();
  }, [campaign.id]);

  const create = async () => {
    if (!draft.title.trim() && !draft.body.trim()) return;
    await api.memory.createNote(campaign.id, {
      ...draft,
      session_number: active?.number ?? null,
    });
    setDraft({ title: "", body: "" });
    await load();
  };

  const togglePin = async (note: Note) => {
    await api.memory.updateNote(note.id, { ...note, pinned: !note.pinned });
    await load();
  };

  const save = async (note: Note, title: string, body: string) => {
    await api.memory.updateNote(note.id, { ...note, title, body });
    setEditing(null);
    await load();
  };

  const remove = async (note: Note) => {
    await api.memory.deleteNote(note.id);
    await load();
  };

  if (error) return (
            <p className="notice notice--bad">{error}</p>
  );
  if (!notes) return (
        <p className="notice">Loading journal</p>
  );

  return (
            <div className="stack">
              <div className="panel">
                <input
                  className="field__input"
                  value={draft.title}
                  placeholder="What is this about?"
                  onChange={(e) => setDraft({ ...draft, title: e.target.value })}
                />
                <textarea
                  className="field__input"
                  rows={4}
                  value={draft.body}
                  placeholder="Suspicions, names to remember, things that did not add up."
                  onChange={(e) => setDraft({ ...draft, body: e.target.value })}
                />
                <div className="actions">
                  <button className="button" onClick={() => void create()}>
                    Add note
                  </button>
                  <span className="field__hint">
                    Private. The game master never reads these.
                  </span>
                </div>
              </div>

              {notes.length === 0 ? (
                <div className="empty">
                  <h2>Nothing written yet</h2>
                  <p>
                    Names, debts, doors you could not open. The things you will want
                    three sessions from now.
                  </p>
                </div>
              ) : (
                <ul className="notes">
                  {notes.map((note) =>
                    editing === note.id ? (
                      <li key={note.id} className="note panel">
                        <EditNote note={note} onSave={save} onCancel={() => setEditing(null)} />
                      </li>
                    ) : (
                      <li key={note.id} className={`note ${note.pinned ? "note--pinned" : ""}`}>
                        <div className="note__head">
                          <h3 className="note__title">{note.title || "Untitled"}</h3>
                          <span className="note__meta">
                            {note.session_number ? `Session ${note.session_number}` : ""}
                          </span>
                        </div>
                        <p className="prose">{note.body}</p>
                        <div className="note__actions">
                          <button className="linkish" onClick={() => void togglePin(note)}>
                            {note.pinned ? "Unpin" : "Pin"}
                          </button>
                          <button className="linkish" onClick={() => setEditing(note.id)}>
                            Edit
                          </button>
                          <button className="linkish" onClick={() => void remove(note)}>
                            Delete
                          </button>
                        </div>
                      </li>
                    ),
                  )}
                </ul>
              )}
            </div>
  );
}

function EditNote({
  note,
  onSave,
  onCancel,
}: {
  note: Note;
  onSave: (n: Note, title: string, body: string) => Promise<void>;
  onCancel: () => void;
}) {
  const [title, setTitle] = useState(note.title);
  const [body, setBody] = useState(note.body);
  return (
    <div className="pane">
      <div className="pane__inner">
                <>
                  <input
                    className="field__input"
                    value={title}
                    onChange={(e) => setTitle(e.target.value)}
                  />
                  <textarea
                    className="field__input"
                    rows={5}
                    value={body}
                    onChange={(e) => setBody(e.target.value)}
                  />
                  <div className="actions">
                    <button className="button" onClick={() => void onSave(note, title, body)}>
                      Save
                    </button>
                    <button className="button button--quiet" onClick={onCancel}>
                      Cancel
                    </button>
                  </div>
                </>
      </div>
    </div>
  );
}
