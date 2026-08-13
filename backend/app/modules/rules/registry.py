"""Ruleset registry.

Adding a system: create a directory under `systems/`, implement the `Ruleset`
protocol, register it here. One line.

Campaigns bind to a ruleset at creation via `Campaign.ruleset_id` and never
change - a mid-campaign system swap would invalidate every stored character.
"""

from __future__ import annotations

from app.core.errors import InvalidRequest
from app.modules.rules.protocol import Ruleset

_REGISTRY: dict[str, Ruleset] = {}


def register(ruleset: Ruleset) -> None:
    if ruleset.id in _REGISTRY:
        raise ValueError(f"duplicate ruleset id: {ruleset.id}")
    _REGISTRY[ruleset.id] = ruleset


def get(ruleset_id: str) -> Ruleset:
    ruleset = _REGISTRY.get(ruleset_id)
    if ruleset is None:
        raise InvalidRequest(f"unknown ruleset: {ruleset_id}")
    return ruleset


def available() -> tuple[Ruleset, ...]:
    return tuple(_REGISTRY.values())


def _bootstrap() -> None:
    from app.modules.rules.systems.d20.ruleset import D20Ruleset

    if not _REGISTRY:
        register(D20Ruleset())


_bootstrap()
