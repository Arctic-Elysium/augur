import type { Roll } from "../../lib/log";

const VERDICT: Record<string, string> = {
  "critical-success": "Critical success",
  "critical-failure": "Critical failure",
  success: "Success",
  partial: "Partial",
  miss: "Failure",
};

const BOON_LABEL: Record<string, string> = {
  extra_resource: "something extra found",
  extra_information: "more learned than asked",
  position_gained: "ground gained",
  clock_reduced: "pressure eased",
  condition_cleared: "a burden lifted",
  disposition_improved: "goodwill earned",
};

const SETBACK_LABEL: Record<string, string> = {
  resource_lost: "something lost",
  information_leaked: "something given away",
  position_lost: "ground given up",
  clock_advanced: "pressure mounts",
  condition_applied: "a new burden",
  disposition_damaged: "goodwill spent",
};

/** The one place colour is allowed. Gold for a critical success, rust for a
 *  critical failure, parchment otherwise. Kept typographically apart from
 *  prose: a boon folded into narration is a boon the player misses. */
export function DiceReadout({ roll }: { roll: Roll }) {
  const tone =
    roll.outcome === "critical-success"
      ? "readout--crit"
      : roll.outcome === "critical-failure"
        ? "readout--fumble"
        : roll.outcome === "miss"
          ? "readout--miss"
          : "";

  if (roll.notRolled) {
    return (
      <div className="readout readout--miss" role="group">
        <span className="label label--lit">{roll.label}</span>
        <span className="readout__math">{roll.notRolled}</span>
        <span className="readout__out">
          <span className="readout__verdict">{VERDICT[roll.outcome]}</span>
        </span>
      </div>
    );
  }

  const spoken =
    `${roll.label}: rolled ${roll.natural}` +
    (roll.dc ? `, difficulty ${roll.dc}` : "") +
    `, ${VERDICT[roll.outcome]}` +
    (roll.boon ? `, with a boon` : "") +
    (roll.setback ? `, with a setback` : "");

  return (
    <div className={`readout ${tone}`} role="group" aria-label={spoken}>
      <span className="label label--lit">{roll.label}</span>
      <span className="readout__dice">
        <span className="readout__die">{roll.natural}</span>
      </span>
      <span className="readout__math">
        {roll.margin >= 0 ? `+${roll.margin}` : roll.margin}
        {roll.dc ? ` vs ${roll.dc}` : ""}
      </span>
      <span className="readout__out">
        <span className="readout__verdict">{VERDICT[roll.outcome]}</span>
        {roll.override && <span className="readout__flag">natural</span>}
        {roll.locked && <span className="readout__flag">already tried</span>}
        {roll.boon && (
          <span className="readout__flag readout__flag--boon">
            {BOON_LABEL[roll.boon] ?? "boon"}
          </span>
        )}
        {roll.setback && (
          <span className="readout__flag readout__flag--setback">
            {SETBACK_LABEL[roll.setback] ?? "setback"}
          </span>
        )}
      </span>
    </div>
  );
}
