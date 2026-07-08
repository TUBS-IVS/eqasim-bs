"""Inspect a pipeline config YAML against the curated flag registry.

inspect(): group curated flags with their template values (value=None +
in_template=False when absent); count uncurated config keys (shown as an
info badge -- visible, not editable). diff(): validate overrides against the
registry (unknown key -> ValueError, out-of-range -> ValueError) and report
only real changes."""
from __future__ import annotations

from dataclasses import dataclass, field

import yaml

from .. import registry


@dataclass
class InspectResult:
    groups: dict[str, list[dict]] = field(default_factory=dict)
    run_list: list[str] = field(default_factory=list)
    uncurated_count: int = 0


def _load(yaml_text: str) -> dict:
    doc = yaml.safe_load(yaml_text) or {}
    if "config" not in doc:
        raise ValueError("config YAML has no 'config:' section -- not a synpp pipeline config")
    return doc


def inspect(yaml_text: str) -> InspectResult:
    doc = _load(yaml_text)
    cfg = doc["config"]
    curated = registry.by_key()
    res = InspectResult(run_list=list(doc.get("run", [])),
                        uncurated_count=sum(1 for k in cfg if k not in curated))
    for group in registry.groups():
        res.groups[group] = []
    for f in registry.FLAGS:
        res.groups[f.group].append({
            "key": f.key, "type": f.type, "unit": f.unit, "choices": list(f.choices),
            "description": f.description, "value": cfg.get(f.key),
            "in_template": f.key in cfg,
        })
    return res


def _validate(flag, value):
    if flag.type == "int":
        value = int(value)
    elif flag.type == "float":
        value = float(value)
    elif flag.type == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{flag.key}: expected bool, got {value!r}")
    elif flag.type == "choice" and value not in flag.choices:
        raise ValueError(f"{flag.key}: {value!r} not in {flag.choices}")
    if flag.min is not None and value < flag.min:
        raise ValueError(f"{flag.key}: {value} below minimum {flag.min}")
    if flag.max is not None and value > flag.max:
        raise ValueError(f"{flag.key}: {value} above maximum {flag.max}")
    return value


def diff(template_yaml: str, overrides: dict) -> list[dict]:
    cfg = _load(template_yaml)["config"]
    curated = registry.by_key()
    changes = []
    for key, new in overrides.items():
        if key not in curated:
            raise ValueError(f"override key '{key}' is not in the curated registry "
                             f"(decision: curated-only; edit the template YAML instead)")
        new = _validate(curated[key], new)
        old = cfg.get(key)
        if old != new:
            changes.append({"key": key, "old": old, "new": new})
    return changes
