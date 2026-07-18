"""Cross-cordon student in-commuter synthesis stage (#140 sub-item 2).

Default-ON per project convention, but dependency-gated: it needs the resident
university placement, which lives in the ``education_gravity`` feature. When that
parent feature is OFF and the flag is left at its default, the stage SKIPS
(empty frames + one warn) rather than raising -- a legitimate config state, not a
silent fallback. Explicitly enabling the flag while the parent is OFF is a
contradiction and raises. See docs/superpowers/specs/2026-07-18-student-incommuters-design.md.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

_log = logging.getLogger(__name__)
_SENTINEL = object()
CRS_METRIC = "EPSG:25832"
# Fixed RNG offset so student in-commuters never share a substream with residents
# (100000) or SvB in-commuters. Distinct offset keeps draws reproducible+disjoint.
_RNG_OFFSET = 200000


def _empty_frames(crs=CRS_METRIC):
    import geopandas as gpd
    return {
        "persons": pd.DataFrame(),
        "households": pd.DataFrame(),
        "trips": pd.DataFrame(),
        "activities": pd.DataFrame(),
        "locations": gpd.GeoDataFrame(geometry=[], crs=crs),
    }


def configure(context):
    context.config("cordon_enabled")
    context.config("cordon_student_incommuters_enabled", None)
    context.config("education_gravity_enabled", False)
    context.config("student_incommuter_age_band", [18, 29])
    context.config("education_university_slope", -0.1415)
    context.config("education_university_max_radius_km", 150.0)
    context.config("sampling_rate")
    context.config("random_seed")
    context.config("cordon_network_source_buffer_m")
    context.config("data_path")
    context.stage("braunschweig.data.schools.university_facilities")
    context.stage("synthesis.population.spatial.primary.locations")
    context.stage("data.spatial.municipalities")
    context.stage("hts")
    context.stage("braunschweig.synthesis.cordon_gates")
    context.stage("synthesis.population.enriched")


def _active(context):
    """Resolve the tri-state activation. Returns True/False; raises on the
    contradictory explicit-on-but-parent-off case."""
    if not context.config("cordon_enabled"):
        return False
    flag = context.config("cordon_student_incommuters_enabled", None)
    parent = bool(context.config("education_gravity_enabled", False))
    if flag is True and not parent:
        raise RuntimeError(
            "cordon_student_incommuters_enabled=True requires "
            "education_gravity_enabled=True (the count anchor needs the resident "
            "university placement). Enable education_gravity or unset the flag.")
    if flag is False:
        return False
    if not parent:
        _log.warning(
            "[student_incommuters] skipped: requires education_gravity_enabled "
            "(parent feature off, flag left at default). Injecting no students.")
        return False
    return True


def execute(context):
    if not _active(context):
        return _empty_frames()
    return _inject(context)
