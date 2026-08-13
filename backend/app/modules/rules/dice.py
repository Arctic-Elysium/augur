"""Dice notation parsing and rolling.

The RNG is injected so every test is deterministic and every roll is
reproducible from a seed - which matters when a player disputes an outcome
three sessions later.
"""

from __future__ import annotations

import random
import re
from typing import Protocol

from app.core.errors import InvalidRequest
from app.modules.rules.types import DiceRoll, RollMode

_NOTATION = re.compile(
    r"^\s*(?P<count>\d*)d(?P<sides>\d+)"
    r"(?:(?P<keep>[kK][hHlL]?)(?P<keep_n>\d+))?"
    r"(?P<mod>[+-]\s*\d+)?\s*$"
)

MAX_DICE = 100
MAX_SIDES = 1000


class Rng(Protocol):
    def randint(self, a: int, b: int) -> int: ...


class SeededRng:
    """Deterministic and replayable. Store the seed with the session."""

    def __init__(self, seed: int | None = None) -> None:
        self._random = random.Random(seed)
        self.seed = seed

    def randint(self, a: int, b: int) -> int:
        return self._random.randint(a, b)


def roll(notation: str, rng: Rng) -> DiceRoll:
    """Evaluate a dice expression.

    Supports `2d6`, `d20+3`, `4d6kh3` (keep highest 3), `2d20kl1` (keep lowest).
    """
    match = _NOTATION.match(notation)
    if not match:
        raise InvalidRequest(f"unparseable dice notation: {notation!r}")

    count = int(match.group("count") or 1)
    sides = int(match.group("sides"))
    modifier = int((match.group("mod") or "0").replace(" ", ""))

    if count < 1 or count > MAX_DICE:
        raise InvalidRequest(f"dice count out of range: {count}")
    if sides < 2 or sides > MAX_SIDES:
        raise InvalidRequest(f"die size out of range: {sides}")

    rolls = tuple(rng.randint(1, sides) for _ in range(count))

    keep_spec = match.group("keep")
    if keep_spec:
        keep_n = int(match.group("keep_n"))
        if keep_n < 1 or keep_n > count:
            raise InvalidRequest(f"cannot keep {keep_n} of {count} dice")
        lowest = keep_spec.lower() == "kl"
        kept = tuple(sorted(rolls, reverse=not lowest)[:keep_n])
    else:
        kept = rolls

    return DiceRoll(
        notation=notation,
        rolls=rolls,
        kept=kept,
        modifier=modifier,
        total=sum(kept) + modifier,
    )


def roll_d20(rng: Rng, *, modifier: int = 0, mode: RollMode = RollMode.NORMAL) -> DiceRoll:
    """A d20 check roll.

    Advantage and disadvantage roll two dice and keep one. `DiceRoll.natural`
    reports the *kept* die, so criticals fire off what actually counted.
    """
    if mode is RollMode.NORMAL:
        rolls = (rng.randint(1, 20),)
        kept = rolls
    else:
        rolls = (rng.randint(1, 20), rng.randint(1, 20))
        chosen = max(rolls) if mode is RollMode.ADVANTAGE else min(rolls)
        kept = (chosen,)

    return DiceRoll(
        notation=f"d20{mode.value if mode is not RollMode.NORMAL else ''}",
        rolls=rolls,
        kept=kept,
        modifier=modifier,
        total=kept[0] + modifier,
    )
