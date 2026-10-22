"""Build the synpp stage registry for a composed config, read-only.

Shared by the explainer, the migration script and the hash-impact report so that every
tool sees exactly the registry the production launcher builds (deterministic propagation
installed, same definitions, same aliases).
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]


def load_settings(base: str, overlay: str | None) -> dict:
    """Return the composed settings document (``run``, ``config``, ``working_directory`` ...)."""
    if overlay:
        from braunschweig import config_compose
        return config_compose.compose(base, overlay)
    with open(base, encoding="utf-8") as handle:
        settings = yaml.safe_load(handle)
    if not settings:
        raise ValueError(f"{base}: empty config (expected a YAML mapping with a 'run' list)")
    if not isinstance(settings.get("run"), list) or not settings["run"]:
        raise ValueError(f"{base}: no 'run' list in the config (the stages to build are missing)")
    return settings


def definitions_from_settings(settings: dict) -> list:
    """Translate the ``run`` list into synpp stage definitions (parameterised entries kept)."""
    definitions = []
    for item in settings["run"]:
        parameters = {}
        if isinstance(item, dict):
            key = list(item.keys())[0]
            parameters = item[key]
            item = key
        definitions.append({"descriptor": item, "config": parameters})
    return definitions


def build_registry_from_settings(settings: dict, *, patched: bool = True) -> dict:
    """Build the registry; ``patched`` installs the project patches first (production path)."""
    if patched:
        from braunschweig import synpp_fork
        synpp_fork.install()
    from synpp.pipeline import process_stages
    definitions = definitions_from_settings(settings)
    previous = os.getcwd()
    os.chdir(REPO)
    try:
        return process_stages(definitions, settings.get("config", {}) or {},
                              settings.get("externals", {}) or {}, settings.get("aliases", {}) or {})
    finally:
        os.chdir(previous)


def build_registry(base: str, overlay: str | None = None, *, patched: bool = True) -> dict:
    """Compose ``base`` (+ ``overlay``) and build the stage registry read-only.

    ``patched`` defaults to True: the project's synpp patches are installed first, so the
    registry carries the production stage hashes. While building, the process changes into
    the repository root (synpp resolves stage modules relative to it) and restores the
    previous working directory afterwards.
    """
    return build_registry_from_settings(load_settings(base, overlay), patched=patched)
