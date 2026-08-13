"""Check locking.

With absolute natural-20 criticals, an unlimited retry is an exploit: searching
the same desk twenty times guarantees a crit. Locking records a resolved check
and returns the stored result on re-attempt, rather than rolling again.

The lock is keyed on a *situation fingerprint*, not on phrasing. "Search the
desk" and "look through the drawers" must resolve to the same `target_ref`
upstream, or the lock is trivially laundered by rewording - which is why target
resolution belongs to the entity store (Milestone 4), not to string matching.

A locked check reopens when something material changes. That is what keeps the
rule from feeling arbitrary: you are not being told "no", you are being told
"nothing has changed since you tried".
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace

from app.modules.rules.types import (
    CheckKind,
    CheckRequest,
    CheckResult,
    LockPolicy,
)


@dataclass(frozen=True)
class Situation:
    """Everything that could legitimately reopen a locked check."""

    scene_id: str
    # Sorted condition ids on the actor at resolution time.
    condition_ids: tuple[str, ...] = ()
    # Items or abilities that bear on this kind of check.
    relevant_assets: tuple[str, ...] = ()
    # Bumped when in-fiction time passes, for time-sensitive checks.
    time_index: int = 0

    def fingerprint(self, *, include_time: bool) -> str:
        parts = [
            self.scene_id,
            ",".join(sorted(self.condition_ids)),
            ",".join(sorted(self.relevant_assets)),
        ]
        if include_time:
            parts.append(str(self.time_index))
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class LockKey:
    actor_id: str
    kind_id: str
    target_ref: str
    fingerprint: str


@dataclass(frozen=True)
class LockEntry:
    key: LockKey
    result: CheckResult
    scene_id: str

    @property
    def serialized(self) -> dict:
        """Storable form. Only what is needed to replay the answer - the full
        dice breakdown is already in the turn log."""
        return {
            "tier": self.result.tier.value,
            "margin": self.result.margin,
            "natural": self.result.natural,
            "dc": self.result.dc,
            "override": self.result.override,
            "boon": (
                {"kind": self.result.boon.kind.value,
                 "scale": self.result.boon.scale.value,
                 "hint": self.result.boon.hint}
                if self.result.boon else None
            ),
            "setback": (
                {"kind": self.result.setback.kind.value,
                 "scale": self.result.setback.scale.value,
                 "hint": self.result.setback.hint}
                if self.result.setback else None
            ),
        }


class CheckLedger:
    """In-memory for now; the session module persists it from Milestone 3.

    Deliberately not a database concern - keeping it as pure data means the
    turn loop can evaluate a lock without I/O, and tests need no fixtures.
    """

    def __init__(self) -> None:
        self._entries: dict[LockKey, LockEntry] = {}

    @staticmethod
    def key_for(
        request: CheckRequest, kind: CheckKind, situation: Situation
    ) -> LockKey | None:
        """None when this kind never locks (attacks, dodges, and so on)."""
        if kind.lock_policy is LockPolicy.NEVER or request.target_ref is None:
            return None

        if kind.lock_policy is LockPolicy.ONCE:
            # Recall-lore and decipher: you know it or you do not. Only a new
            # asset reopens it, so scene and conditions are excluded.
            fingerprint = hashlib.sha256(
                ",".join(sorted(situation.relevant_assets)).encode()
            ).hexdigest()[:16]
        elif kind.lock_policy is LockPolicy.PER_SCENE:
            fingerprint = situation.scene_id
        else:  # PER_CONDITION_CHANGE
            fingerprint = situation.fingerprint(include_time=kind.time_sensitive)

        return LockKey(
            actor_id=request.actor_id,
            kind_id=request.kind_id,
            target_ref=request.target_ref,
            fingerprint=fingerprint,
        )

    def lookup(
        self, request: CheckRequest, kind: CheckKind, situation: Situation
    ) -> CheckResult | None:
        """The stored result if this check is locked, else None.

        A pushed check bypasses the lock - spending a resource to try again is
        the one retry that should always be allowed, because it costs something.
        """
        if request.pushed:
            return None
        key = self.key_for(request, kind, situation)
        if key is None:
            return None
        entry = self._entries.get(key)
        if entry is None:
            return None
        return replace(
            entry.result,
            reason="already attempted; nothing has changed since",
        )

    def record(
        self,
        request: CheckRequest,
        kind: CheckKind,
        situation: Situation,
        result: CheckResult,
    ) -> None:
        key = self.key_for(request, kind, situation)
        if key is None:
            return
        self._entries[key] = LockEntry(key, result, situation.scene_id)

    def entries(self) -> tuple[LockEntry, ...]:
        """Everything recorded. Used to persist the ledger after a turn."""
        return tuple(self._entries.values())

    def restore(
        self, *, actor_id: str, kind_id: str, target_ref: str,
        fingerprint: str, scene_id: str, result: dict,
    ) -> None:
        """Rehydrate a lock from storage.

        The stored form is lossy - it carries the answer, not the dice - which
        is fine, because a locked check is never re-rolled. The player is told
        what happened last time, not shown a fresh roll.
        """
        from app.modules.rules.dice import roll_d20
        from app.modules.rules.types import (
            BoonKind, BoonSpec, Scale, SetbackKind, SetbackSpec, Tier,
        )

        class _Fixed:
            def randint(self, a: int, b: int) -> int:
                return a

        boon = result.get("boon")
        setback = result.get("setback")
        key = LockKey(actor_id, kind_id, target_ref, fingerprint)
        self._entries[key] = LockEntry(
            key=key,
            scene_id=scene_id,
            result=CheckResult(
                tier=Tier(result["tier"]),
                margin=result["margin"],
                natural=result["natural"],
                dc=result["dc"],
                roll=roll_d20(_Fixed()),
                override=result.get("override", False),
                boon=(
                    BoonSpec(BoonKind(boon["kind"]), Scale(boon["scale"]),
                             boon.get("hint", ""))
                    if boon else None
                ),
                setback=(
                    SetbackSpec(SetbackKind(setback["kind"]), Scale(setback["scale"]),
                                setback.get("hint", ""))
                    if setback else None
                ),
            ),
        )

    def clear_scene(self, scene_id: str) -> None:
        """Called when a scene ends, releasing PER_SCENE locks."""
        self._entries = {
            k: v for k, v in self._entries.items() if v.scene_id != scene_id
        }

    def __len__(self) -> int:
        return len(self._entries)
