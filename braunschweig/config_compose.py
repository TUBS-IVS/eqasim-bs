"""Compose a synpp run config from a fixed base YAML plus a thin per-scale overlay.

synpp (pinned 1.6.2) loads a single YAML and has no include/inherit mechanism, so
composition lives here: the base carries everything fixed (feature flags, paths,
PopulationSim settings), an overlay carries only per-scale knobs (sampling_rate,
working_directory, run target, worker counts, ...). Used by scripts/run_synpp.py
(2-argument form) and by the config contract tests.

Merge contract (deterministic, documented in tests/test_config_compose.py):
- nested mappings merge recursively; an overlay key overrides the base key,
- scalars and LISTS are replaced wholesale (never concatenated),
- base-only keys are kept, overlay-only keys are added,
- every override/addition is logged at INFO (no silent merges -- project rule).
"""
from __future__ import annotations

import logging
import os

import yaml

logger = logging.getLogger("braunschweig")

MERGED_CONFIG_NAME = ".merged_config.yml"


def deep_merge(base, overlay, path=""):
    """Merge ``overlay`` into ``base`` (pure -- inputs are not mutated).

    Returns ``(merged, changes)`` where ``changes`` is a list of
    ``(dotted_key, base_value, overlay_value, kind)`` with kind ``"overridden"``
    or ``"added"``; identical values are not reported.
    """
    merged = dict(base)
    changes = []
    for key, value in overlay.items():
        dotted = f"{path}.{key}" if path else str(key)
        if key not in merged:
            merged[key] = value
            changes.append((dotted, None, value, "added"))
        elif isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key], sub_changes = deep_merge(merged[key], value, dotted)
            changes.extend(sub_changes)
        else:
            if merged[key] != value:
                changes.append((dotted, merged[key], value, "overridden"))
            merged[key] = value
    return merged, changes


def compose(base_path, overlay_path):
    """Load base + overlay YAML, deep-merge, log every change, validate, return the doc.

    Fails early (ValueError) when the merged doc lacks ``working_directory`` or a
    non-empty ``run`` list -- the base intentionally omits them, so a missing key
    means the overlay is incomplete.
    """
    for p in (base_path, overlay_path):
        if not os.path.isfile(p):
            raise FileNotFoundError(f"config file not found: {p}")
    with open(base_path, encoding="utf-8") as f:
        base = yaml.safe_load(f) or {}
    with open(overlay_path, encoding="utf-8") as f:
        overlay = yaml.safe_load(f) or {}
    merged, changes = deep_merge(base, overlay)
    for dotted, old, new, kind in changes:
        if kind == "added":
            logger.info("[config-merge] %s: (added) %s", dotted, new)
        else:
            logger.info("[config-merge] %s: %s -> %s", dotted, old, new)
    logger.info(
        "[config-merge] composed %s + %s: %d overridden, %d added",
        base_path, overlay_path,
        sum(1 for c in changes if c[3] == "overridden"),
        sum(1 for c in changes if c[3] == "added"),
    )
    if not merged.get("working_directory"):
        raise ValueError(
            f"composed config ({base_path} + {overlay_path}) has no "
            "'working_directory' -- the overlay must set it (the base omits "
            "per-scale keys on purpose)")
    if not merged.get("run"):
        raise ValueError(
            f"composed config ({base_path} + {overlay_path}) has no 'run' "
            "stage list -- the overlay must set it")
    return merged


def write_merged(merged, working_directory):
    """Write the merged doc to <working_directory>/.merged_config.yml, return its path.

    The file is the exact config the run uses -- persisted next to the run's cache
    for provenance/reproducibility (offline analysis drivers read it from there).
    """
    os.makedirs(working_directory, exist_ok=True)
    path = os.path.join(working_directory, MERGED_CONFIG_NAME)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(merged, f, sort_keys=False, allow_unicode=True)
    logger.info("[config-merge] merged config written to %s", path)
    return path
