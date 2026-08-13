"""Versioned prompt templates.

Templates live in files, not inline strings, and every call records the version
that produced it. When narration quality shifts you need to know which template
was responsible - and with a model in the loop, quality shifts for reasons that
are not always your code.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

_DIR = Path(__file__).parent


@dataclass(frozen=True)
class Template:
    name: str
    version: str
    body: str

    def render(self, **values: object) -> str:
        return self.body.format(**values)


_CACHE: dict[str, Template] = {}


def load(name: str) -> Template:
    if name in _CACHE:
        return _CACHE[name]
    path = _DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"no prompt template: {name}")
    raw = path.read_text()
    version = "unversioned"
    if raw.startswith("<!-- version:"):
        header, _, rest = raw.partition("-->")
        version = header.removeprefix("<!-- version:").strip()
        raw = rest.lstrip("\n")
    template = Template(name=name, version=version, body=raw)
    _CACHE[name] = template
    return template


def render_prompt(name: str, **values: object) -> tuple[str, str]:
    """Render a template and return (text, version).

    Callers persist the version alongside whatever the model produced, so a
    later quality regression can be traced to a specific template revision.
    """
    template = load(name)
    return template.render(**values), template.version


def available() -> tuple[str, ...]:
    return tuple(sorted(p.stem for p in _DIR.glob("*.md")))
