import type { Character } from "../../lib/api";

/** Sits in its own grid column with its own overflow, so it holds while the log
 *  scrolls. Under 1040px it becomes a narrow strip; under 760px it hides and
 *  the Party tab carries it. */
export function PartyRail({
  characters,
  spotlightId,
  onSpotlight,
  clocks,
}: {
  characters: Character[];
  spotlightId: string | null;
  onSpotlight: (id: string | null) => void;
  clocks: Record<string, { label: string; filled: number; size: number }>;
}) {
  const roster = characters.filter((c) => c.active);

  return (
    <aside className="rail" aria-label="Party">
      <div className="rail__full">
        <div className="rail__head">
          <span className="label">Party</span>
          {roster.length > 1 && (
            <button
              className="linkish"
              onClick={() => onSpotlight(null)}
              aria-pressed={spotlightId === null}
            >
              {spotlightId === null ? "acting together" : "act together"}
            </button>
          )}
        </div>

        {roster.map((c) => {
          const spot = spotlightId === c.id;
          return (
            <article
              key={c.id}
              className={`rail__card ${spot ? "rail__card--spot" : ""}`}
            >
              {spot && <span className="rail__spot">Spotlight</span>}
              <button
                className="rail__pick"
                onClick={() => onSpotlight(spot ? null : c.id)}
                aria-pressed={spot}
              >
                <span className="rail__name">{c.name}</span>
              </button>
              <Meter label="Vit" now={c.sheet.hp} max={c.sheet.hp_max} />
              {c.sheet.conditions.length > 0 && (
                <div className="rail__chips">
                  {c.sheet.conditions.map((x) => (
                    <span className="chip" key={x.spec_id}>
                      {x.spec_id}
                    </span>
                  ))}
                </div>
              )}
            </article>
          );
        })}

        {Object.keys(clocks).length > 0 && (
          <div className="rail__clocks">
            <span className="label">Clocks</span>
            {Object.entries(clocks).map(([id, clock]) => (
              <div key={id} className="railclock">
                <span className="railclock__label">{clock.label}</span>
                <div
                  className="meter meter--clock"
                  role="img"
                  aria-label={`${clock.label} ${clock.filled} of ${clock.size}`}
                >
                  {Array.from({ length: clock.size }, (_, i) => (
                    <span
                      key={i}
                      className={`meter__seg ${i < clock.filled ? "meter__seg--on" : ""}`}
                    />
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="rail__strip">
        {roster.map((c) => (
          <button
            key={c.id}
            className={`strip__card ${spotlightId === c.id ? "strip__card--spot" : ""}`}
            onClick={() => onSpotlight(spotlightId === c.id ? null : c.id)}
            title={`${c.name} — ${c.sheet.hp}/${c.sheet.hp_max}`}
          >
            <span className="strip__initials">{initials(c.name)}</span>
            <span className="strip__bar">
              <span
                className="strip__fill"
                style={{ width: `${(c.sheet.hp / c.sheet.hp_max) * 100}%` }}
              />
            </span>
          </button>
        ))}
      </div>
    </aside>
  );
}

function initials(name: string) {
  return name
    .split(/\s+/)
    .slice(0, 2)
    .map((p) => p[0]?.toUpperCase() ?? "")
    .join("");
}

/** Segmented rather than a bar: at these sizes a player counts segments, and
 *  "three left" is a decision where "62%" is not. */
function Meter({
  label,
  now,
  max,
}: {
  label: string;
  now: number;
  max: number;
}) {
  // Long health tracks get a bar; short ones get pips you can count.
  const segmented = max <= 12;
  return (
    <div className="railmeter">
      <span className="label railmeter__label">{label}</span>
      {segmented ? (
        <div
          className="meter"
          role="img"
          aria-label={`${label} ${now} of ${max}`}
        >
          {Array.from({ length: max }, (_, i) => (
            <span
              key={i}
              className={`meter__seg ${i < now ? "meter__seg--on" : ""}`}
            />
          ))}
        </div>
      ) : (
        <div
          className="meter meter--bar"
          role="img"
          aria-label={`${label} ${now} of ${max}`}
        >
          <span className="meter__fill" style={{ width: `${(now / max) * 100}%` }} />
        </div>
      )}
      <span className="railmeter__value">
        {now}/{max}
      </span>
    </div>
  );
}
