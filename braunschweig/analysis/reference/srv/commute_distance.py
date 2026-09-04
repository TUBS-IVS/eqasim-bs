"""synpp stage: the committed SrV 2023 primary-distance reference tables.

Mirrors eqasim's ``analysis/reference/hts/commute_distance`` (reference distribution as a
stage), but reads the committed per-Kreis aggregates written by
``scripts/extract_srv_primary_distance_targets.py`` instead of raw HTS microdata, so no
local-only data is needed at analysis time. Consumed by
``braunschweig.analysis.synthesis.commute_distance_by_kreis``.

Besides the three CALIBRATION TARGET tables (commute, education, quantiles) the stage also
loads the SENSITIVITY table written by the same script (addendum Task 15/16, item 3). That
table is explicitly NOT a target: it carries the ``inter_zgb``, ``all_gis_fallback`` and
``inter_gis_fallback`` variants used only to quantify how far the two documented caveats of
the target table (polygon-external destinations, GIS-invalid tail) could move the reference.
The consuming stage keeps it strictly out of the pre-registered decision.
"""
from __future__ import annotations

import logging
import os

from braunschweig.calibration import srv_distance_targets as T

logger = logging.getLogger(__name__)

SRV_SUBDIR = ("braunschweig", "srv")


def configure(context):
    context.config("data_path")


def execute(context):
    srv_dir = os.path.join(context.config("data_path"), *SRV_SUBDIR)
    commute = T.load_commute_targets(srv_dir)
    education = T.load_education_targets(srv_dir)
    quantiles = T.load_commute_quantiles(srv_dir)
    sensitivity = T.load_commute_sensitivity(srv_dir)
    logger.info(
        "[srv reference] loaded %d commute, %d education, %d quantile, %d sensitivity "
        "(variants: %s; NOT a calibration target) rows from %s",
        len(commute),
        len(education),
        len(quantiles),
        len(sensitivity),
        ", ".join(sorted(sensitivity["variant"].unique())) if len(sensitivity) else "none",
        srv_dir,
    )
    return dict(
        commute=commute,
        education=education,
        quantiles=quantiles,
        sensitivity=sensitivity,
        srv_dir=srv_dir,
    )
