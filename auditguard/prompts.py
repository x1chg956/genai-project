"""Prompt files (docs/06): YAML front matter plus six slots, hashed for the run record."""

from __future__ import annotations

import hashlib
import re

import yaml

from auditguard.config import ConfigError, Settings, resolve_path
from auditguard.schemas import Model

SLOTS = ("Role", "Context", "Task", "Constraints", "Format", "Examples")
_FRONT = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.DOTALL)


class Prompt(Model):
    id: str
    version: int
    model_role: str
    temperature: float
    text: str
    sha256: str


def load_prompt(name: str, settings: Settings) -> Prompt:
    """Load `prompts.<name>` from config. Refuses a file missing any of the six slots."""
    try:
        path = resolve_path(settings.prompts[name])
    except KeyError:
        raise ConfigError(f"prompts.{name} is not configured") from None
    raw = path.read_text(encoding="utf-8")
    match = _FRONT.match(raw)
    if match is None:
        raise ConfigError(f"{path.name}: missing YAML front matter")
    meta = yaml.safe_load(match.group(1)) or {}
    body = match.group(2).strip()
    missing = [s for s in SLOTS if not re.search(rf"^## {s}\s*$", body, re.MULTILINE)]
    if missing:
        raise ConfigError(f"{path.name}: missing slot(s) {', '.join(missing)}")
    return Prompt(
        id=str(meta["id"]),
        version=int(meta["version"]),
        model_role=str(meta["model_role"]),
        temperature=float(meta["temperature"]),
        text=body,
        sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
    )
