import { useEffect, useState } from "react";
import { api, type BuildRules, type Hook } from "../../lib/api";

/** Hook kinds. Each is a thread the GM can pull on, which is why they are
 *  structured rather than free prose - "a person you owe, named, with a debt"
 *  is usable by the engine; three paragraphs are only usable by a human who
 *  read them. */
const HOOK_KINDS = [
  { id: "bond", label: "Bond", prompt: "Someone who matters to you.", subject: "Who?" },
  { id: "debt", label: "Debt", prompt: "Something you owe, and to whom.", subject: "Owed to?" },
  { id: "goal", label: "Goal", prompt: "What you are actually after.", subject: "What?" },
  { id: "flaw", label: "Flaw", prompt: "The thing that gets you into trouble.", subject: "What?" },
  { id: "fear", label: "Fear", prompt: "What you will not face willingly.", subject: "What?" },
  { id: "secret", label: "Secret", prompt: "What you have not told anyone.", subject: "What?" },
];

export function Builder({
  campaignId,
  onDone,
  onCancel,
}: {
  campaignId: string;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [rules, setRules] = useState<BuildRules | null>(null);
  const [name, setName] = useState("");
  const [attributes, setAttributes] = useState<Record<string, number>>({});
  const [skills, setSkills] = useState<Record<string, number>>({});
  const [backstory, setBackstory] = useState("");
  const [hooks, setHooks] = useState<Hook[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void api.rules
      .build()
      .then((r) => {
        setRules(r);
        setAttributes(Object.fromEntries(r.attributes.map((a) => [a.id, r.base])));
      })
      .catch((e) => setError((e as Error).message));
  }, []);

  if (error && !rules) return <p className="notice notice--bad">{error}</p>;
  if (!rules) return <p className="notice">Loading build rules</p>;

  const cost = (v: number) => rules.costs[String(v)] ?? (rules.costs as Record<number, number>)[v] ?? 0;
  const spent = Object.values(attributes).reduce((a, v) => a + cost(v), 0);
  const left = rules.budget - spent;
  const skillSpent = Object.values(skills).reduce((a, b) => a + b, 0);
  const skillLeft = rules.skill_points - skillSpent;

  const canRaise = (id: string) => {
    const next = (attributes[id] ?? rules.base) + 1;
    return next <= rules.max && cost(next) - cost(attributes[id] ?? rules.base) <= left;
  };
  const canLower = (id: string) => (attributes[id] ?? rules.base) > rules.min;

  const adjust = (id: string, delta: number) =>
    setAttributes({ ...attributes, [id]: (attributes[id] ?? rules.base) + delta });

  const ready = name.trim() !== "" && left >= 0 && skillLeft >= 0;

  const submit = async () => {
    if (!ready || busy) return;
    setBusy(true);
    try {
      await api.characters.create({
        campaign_id: campaignId,
        name: name.trim(),
        attributes,
        skills: Object.fromEntries(
          Object.entries(skills).filter(([, v]) => v > 0),
        ),
        backstory: backstory.trim() || undefined,
        hooks: hooks.filter((h) => h.subject.trim() !== ""),
      });
      onDone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const constitution = attributes["constitution"] ?? rules.base;
  const projectedHp = 10 + Math.floor((constitution - 10) / 2) * 2;

  return (
    <div className="builder">
      <label className="field">
        <span className="field__label">Name</span>
        <input
          className="field__input"
          value={name}
          autoFocus
          placeholder="Vessa Auram"
          onChange={(e) => setName(e.target.value)}
        />
      </label>

      {/* Attributes: the running budget is the whole interaction. Showing what
          each point costs before you spend it is what makes it a decision. */}
      <section className="build">
        <div className="build__head">
          <h3 className="field__label">Attributes</h3>
          <span className={`meter ${left < 0 ? "meter--over" : ""}`}>
            {left} of {rules.budget} points left
          </span>
        </div>

        {rules.attributes.map((attr) => {
          const value = attributes[attr.id] ?? rules.base;
          const mod = Math.floor((value - 10) / 2);
          const nextCost = cost(value + 1) - cost(value);
          return (
            <div key={attr.id} className="buy">
              <div className="buy__label">
                <span className="buy__name">{attr.label}</span>
                <span className="buy__desc">{attr.description}</span>
              </div>
              <div className="buy__controls">
                <button
                  className="step"
                  onClick={() => adjust(attr.id, -1)}
                  disabled={!canLower(attr.id)}
                  aria-label={`Lower ${attr.label}`}
                >
                  −
                </button>
                <span className="buy__value">{value}</span>
                <button
                  className="step"
                  onClick={() => adjust(attr.id, 1)}
                  disabled={!canRaise(attr.id)}
                  aria-label={`Raise ${attr.label}`}
                >
                  +
                </button>
              </div>
              <span className="buy__mod">{mod >= 0 ? `+${mod}` : mod}</span>
              <span className="buy__cost">
                {value < rules.max ? `next ${nextCost}` : "max"}
              </span>
            </div>
          );
        })}

        <p className="field__hint">
          The modifier is what gets added to a roll — the score itself never
          appears on the table. Constitution sets health: {projectedHp} to start.
        </p>
      </section>

      <section className="build">
        <div className="build__head">
          <h3 className="field__label">Training</h3>
          <span className={`meter ${skillLeft < 0 ? "meter--over" : ""}`}>
            {skillLeft} of {rules.skill_points} left
          </span>
        </div>

        <div className="skills">
          {rules.skills.map((skill) => {
            const rank = skills[skill.id] ?? 0;
            const canAdd = rank < rules.skill_max && skillLeft > 0;
            return (
              <div key={skill.id} className={`skill ${rank > 0 ? "skill--on" : ""}`}>
                <span className="skill__name">{skill.label}</span>
                <span className="skill__attr">{skill.attribute.slice(0, 3)}</span>
                <span className="skill__pips">
                  {Array.from({ length: rules.skill_max }, (_, i) => (
                    <span key={i} className={`pip ${i < rank ? "pip--on" : ""}`} />
                  ))}
                </span>
                <button
                  className="step"
                  onClick={() => setSkills({ ...skills, [skill.id]: rank - 1 })}
                  disabled={rank === 0}
                  aria-label={`Lower ${skill.label}`}
                >
                  −
                </button>
                <button
                  className="step"
                  onClick={() => setSkills({ ...skills, [skill.id]: rank + 1 })}
                  disabled={!canAdd}
                  aria-label={`Raise ${skill.label}`}
                >
                  +
                </button>
              </div>
            );
          })}
        </div>
      </section>

      <section className="build">
        <div className="build__head">
          <h3 className="field__label">Who they are</h3>
        </div>
        <textarea
          className="field__input"
          rows={5}
          value={backstory}
          placeholder="Where they come from, what they did before this, how they carry themselves."
          onChange={(e) => setBackstory(e.target.value)}
        />
        <p className="field__hint">
          The game master reads this and narrates from it.
        </p>
      </section>

      {/* Hooks matter more than the prose above: these are what the GM can
          actually act on, so they get their own structure. */}
      <section className="build">
        <div className="build__head">
          <h3 className="field__label">Threads</h3>
          <span className="meter">{hooks.length} of 6</span>
        </div>
        <p className="field__hint">
          Things the game master can pull on. A bond gets threatened, a debt
          comes due, a secret gets found out. Two or three is plenty.
        </p>

        {hooks.map((hook, i) => {
          const kind = HOOK_KINDS.find((k) => k.id === hook.kind)!;
          return (
            <div key={i} className="hook">
              <div className="hook__row">
                <span className="hook__kind">{kind.label}</span>
                <input
                  className="field__input"
                  value={hook.subject}
                  placeholder={kind.subject}
                  onChange={(e) => {
                    const next = [...hooks];
                    next[i] = { ...hook, subject: e.target.value };
                    setHooks(next);
                  }}
                />
                <button
                  className="step"
                  onClick={() => setHooks(hooks.filter((_, j) => j !== i))}
                  aria-label="Remove thread"
                >
                  ×
                </button>
              </div>
              <input
                className="field__input"
                value={hook.detail}
                placeholder={kind.prompt}
                onChange={(e) => {
                  const next = [...hooks];
                  next[i] = { ...hook, detail: e.target.value };
                  setHooks(next);
                }}
              />
            </div>
          );
        })}

        {hooks.length < 6 && (
          <div className="choices choices--inline">
            {HOOK_KINDS.map((k) => (
              <button
                key={k.id}
                className="chip"
                onClick={() =>
                  setHooks([...hooks, { kind: k.id, subject: "", detail: "" }])
                }
              >
                + {k.label}
              </button>
            ))}
          </div>
        )}
      </section>

      {error && <p className="notice notice--bad">{error}</p>}

      <div className="actions actions--sticky">
        <button className="btn btn--go" onClick={() => void submit()} disabled={!ready || busy}>
          {busy ? "Creating" : "Create character"}
        </button>
        <button className="btn" onClick={onCancel}>
          Cancel
        </button>
        {!ready && (
          <span className="field__hint">
            {name.trim() === ""
              ? "Give them a name to finish."
              : left < 0
                ? `${-left} points over budget.`
                : skillLeft < 0
                  ? `${-skillLeft} training points over.`
                  : ""}
          </span>
        )}
      </div>
    </div>
  );
}
