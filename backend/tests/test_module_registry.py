"""Guards the modularity contract. If these fail, the wiring is broken."""

from app.modules.base import build_registry
from app.platform.ai.gateway import REQUIREMENTS, Capability


def test_registry_builds_and_names_are_unique():
    registry = build_registry()
    names = [m.name for m in registry.all()]
    assert len(names) == len(set(names))
    assert "auth" in names
    assert "campaigns" in names


def test_every_module_can_import_its_models():
    build_registry().import_all_models()


def test_every_capability_declares_requirements():
    for capability in Capability:
        assert capability in REQUIREMENTS, f"{capability} has no requirements declared"


def test_routing_config_covers_every_capability():
    import yaml

    with open("config/ai_routing.yaml") as fh:
        config = yaml.safe_load(fh)
    routed = set(config["capabilities"].keys())
    declared = {c.value for c in Capability}
    assert declared <= routed, f"unrouted capabilities: {declared - routed}"
