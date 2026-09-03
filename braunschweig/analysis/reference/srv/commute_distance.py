"""synpp stage: the committed SrV 2023 primary-distance reference tables.

Mirrors eqasim's ``analysis/reference/hts/commute_distance`` (reference distribution as a
stage), but reads the committed per-Kreis aggregates written by
``scripts/extract_srv_primary_distance_targets.py`` instead of raw HTS microdata, so no
local-only data is needed at analysis time. Consumed by
``braunschweig.analysis.synthesis.commute_distance_by_kreis``.
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
    logger.info(
        "[srv reference] loaded %d commute, %d education, %d quantile rows from %s",
        len(commute),
        len(education),
        len(quantiles),
        srv_dir,
    )
    return dict(
        commute=commute,
        education=education,
        quantiles=quantiles,
        srv_dir=srv_dir,
    )
