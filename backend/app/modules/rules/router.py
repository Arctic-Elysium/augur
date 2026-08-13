"""Read-only introspection of the loaded rulesets.

Drives the character creation UI and gives the AI gateway a way to enumerate
legal checks. There is deliberately no endpoint that *rolls* anything - checks
are resolved inside the turn loop, never by direct client request, or a player
could roll until they liked the answer.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.auth.deps import PrincipalDep
from app.modules.rules import registry

router = APIRouter()


class CheckKindOut(BaseModel):
    id: str
    label: str
    attribute: str
    lock_policy: str


class RulesetOut(BaseModel):
    id: str
    name: str
    version: str


@router.get("", response_model=list[RulesetOut])
async def list_rulesets(_: PrincipalDep) -> list[RulesetOut]:
    return [
        RulesetOut(id=r.id, name=r.name, version=r.version)
        for r in registry.available()
    ]


@router.get("/{ruleset_id}/schema")
async def character_schema(ruleset_id: str, _: PrincipalDep) -> dict:
    return registry.get(ruleset_id).character_schema()


@router.get("/{ruleset_id}/checks", response_model=list[CheckKindOut])
async def list_checks(ruleset_id: str, _: PrincipalDep) -> list[CheckKindOut]:
    return [
        CheckKindOut(
            id=k.id, label=k.label, attribute=k.attribute,
            lock_policy=k.lock_policy.value,
        )
        for k in registry.get(ruleset_id).check_kinds()
    ]


@router.get("/{ruleset_id}/conditions")
async def list_conditions(ruleset_id: str, _: PrincipalDep) -> list[dict]:
    return [
        {
            "id": c.id, "label": c.label, "description": c.description,
            "check_modifier": c.check_modifier,
            "blocks_actions": list(c.blocks_actions),
        }
        for c in registry.get(ruleset_id).condition_specs()
    ]
