"""The module contract.

Every feature area (campaigns, rules, world, memory, narrative...) is a Module.
A Module owns its own SQLAlchemy models, its own router, and optionally a set of
tools it exposes to the AI gateway. Nothing outside a module imports its
internals; cross-module access goes through the service object a module
publishes on the registry.

This is what keeps a project this large from turning into a single tangled
package: adding a feature means adding a directory and one registration line.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from fastapi import APIRouter


@runtime_checkable
class Module(Protocol):
    """Implemented by each feature package's `module.py`."""

    name: str

    def router(self) -> APIRouter | None:
        """HTTP surface, mounted under /api/<name>. None for headless modules."""
        ...

    def import_models(self) -> None:
        """Import ORM models so Alembic autogenerate can see them.

        Called at startup and by alembic/env.py. Keep it to imports only.
        """
        ...


class ModuleRegistry:
    """Ordered registry. Order matters only for router mount order."""

    def __init__(self) -> None:
        self._modules: list[Module] = []

    def register(self, module: Module) -> None:
        if any(m.name == module.name for m in self._modules):
            raise ValueError(f"duplicate module name: {module.name}")
        self._modules.append(module)

    def all(self) -> list[Module]:
        return list(self._modules)

    def import_all_models(self) -> None:
        for module in self._modules:
            module.import_models()

    def mount(self, parent: APIRouter) -> None:
        for module in self._modules:
            router = module.router()
            if router is not None:
                parent.include_router(router, prefix=f"/{module.name}", tags=[module.name])


def build_registry() -> ModuleRegistry:
    """The one place modules are wired in. Add new modules here."""
    from app.modules.campaigns.module import CampaignsModule
    from app.modules.characters.module import CharactersModule
    from app.modules.identity.module import IdentityModule
    from app.modules.memory.module import MemoryModule
    from app.modules.narrative.module import NarrativeModule
    from app.modules.rules.module import RulesModule
    from app.modules.sessions.module import SessionsModule
    from app.modules.world.module import WorldModule

    registry = ModuleRegistry()
    for module in (
        IdentityModule(),
        CampaignsModule(),
        SessionsModule(),
        CharactersModule(),
        RulesModule(),
        WorldModule(),
        MemoryModule(),
        NarrativeModule(),
    ):
        registry.register(module)
    return registry
