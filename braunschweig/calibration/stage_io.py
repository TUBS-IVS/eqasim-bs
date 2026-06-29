"""Shared synpp run-config + stage-cache helpers for the calibration corner.

Resolves the workflow-dependent synpp aliases (population / enriched / home
locations / activities / spatial locations) to their concrete producers, and
loads the most-recent cached pickle for a stage. Extracted from
scripts/calibrate_gravity_distribution.py so the CLI diagnostics, the synpp
stage, and the legacy calibrators share one implementation.
"""
from __future__ import annotations

import glob
import logging
import os
import pickle

logger = logging.getLogger(__name__)

POPULATION_ALIAS_KEY = "data.census.filtered"
ENRICHED_ALIAS_KEY = "synthesis.population.enriched"
HOME_LOCATIONS_ALIAS_KEY = "synthesis.population.spatial.home.locations"
ACTIVITIES_ALIAS_KEY = "synthesis.population.activities"
LOCATIONS_ALIAS_KEY = "synthesis.population.spatial.locations"

DEFAULT_POPULATION_PRODUCER = "braunschweig.data.census.population"
DEFAULT_ENRICHED_PRODUCER = "braunschweig.synthesis.population.enriched"
DEFAULT_HOME_LOCATIONS_PRODUCER = "synthesis.population.spatial.home.locations"


def load_aliases(config_path: str) -> dict:
    """Return the top-level synpp ``aliases`` block of a YAML run config ({} if none)."""
    try:
        import yaml  # type: ignore
    except ImportError as exc:
        raise RuntimeError("PyYAML is required to load the run config.") from exc
    with open(config_path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if isinstance(raw, dict) and isinstance(raw.get("aliases"), dict):
        return raw["aliases"]
    return {}


def resolve_stage(aliases, alias_key: str, default: str) -> str:
    """Resolve a logical synpp stage name to its concrete producer via the aliases block."""
    producer = (aliases or {}).get(alias_key)
    if not producer:
        logger.warning(
            "[calibration] config has no '%s' alias; falling back to '%s' "
            "(CLAUDE.md no-silent-fallback).", alias_key, default,
        )
        return default
    return str(producer)


def load_cached_stage(working_directory: str, stage: str):
    """Load the most-recently-modified pickle for a synpp stage; raise if none found."""
    pattern = os.path.join(working_directory, stage + "__*.p")
    matches = glob.glob(pattern)
    if not matches:
        raise RuntimeError(
            f"No cached pickle for stage '{stage}' in '{working_directory}' "
            f"(pattern {pattern})."
        )
    latest = max(matches, key=os.path.getmtime)
    logger.info("Loading stage '%s' from '%s'", stage, latest)
    with open(latest, "rb") as fh:
        return pickle.load(fh)
