"""Building-potential fit report for SECONDARY activities (shop / leisure / other).

The secondary analogue of ``run_building_fit`` (work), using an EXACT
``location_id`` join -- not a spatial join. The realised secondary location_id
already identifies the candidate building: with building potentials ON, the
chainsolver's candidate set is built by
``braunschweig.synthesis.locations.secondary_chainsolvers.build_secondary_candidates``,
which gives shop/leisure candidates the id ``"sec_b_" + building_id`` (the gpkg
activity buildings, native potentials) and keeps the legacy broad catalog for
``other`` (id ``sec_N``, generic potential attached by footprint join). Each
candidate row already carries its per-purpose potential (pot_shop / pot_leisure /
pot_other), so we reconstruct that exact table with the project's own function
and join the realised location_id to it -- no geometry matching, no duplicated
logic.

Per-purpose potential column on the reconstructed candidate table:
  - shop    -> pot_shop      (= retail_daily + retail_non_daily)
  - leisure -> pot_leisure
  - other   -> pot_other     (generic potential; footprint-join fallback 0.0)

Zone = candidate ``commune_id`` (the unit at which the upstream gravity/region
totals are fixed; potentials govern the within-commune split). The within-zone
SHARE fit and excess_tv noise floor are sampling-rate-invariant (see building_fit).

REQUIRES a cache built with the building-potential feature ON (post PR #16): the
``braunschweig.data.building_potentials`` stage must be present and the realised
secondary location_ids must include ``sec_b_`` ids. The CLI fails fast with a clear
message otherwise (a pre-feature cache would silently have no shop/leisure
potential candidates -- CLAUDE.md no-silent-fallback).

Outputs per purpose into ``--output-dir``:
  building_potential_fit_secondary_<purpose>_by_commune.csv,
  ..._per_building.csv, and building_potential_fit_secondary_summary.md.
"""
from __future__ import annotations

import argparse
import glob
import logging
import os
import pickle
from pathlib import Path

import numpy as np
import pandas as pd

from braunschweig.calibration.building_fit import build_fit_report, per_building_residuals

LOGGER = logging.getLogger("run_building_fit_secondary")

STAGE_REALISED_SECONDARY = "braunschweig.synthesis.locations.secondary_chainsolvers"
STAGE_ACTIVITIES = "synthesis.population.activities"
# The legacy secondary candidate stage is aliased to braunschweig.locations.secondary
# in the run configs, so synpp caches it under that name (not the alias target
# "synthesis.locations.secondary"). Load the real cached name.
STAGE_LEGACY_CANDIDATES = "braunschweig.locations.secondary"
STAGE_BUILDING_POTENTIALS = "braunschweig.data.building_potentials"

# eqasim secondary purpose -> the reconstructed-candidate potential column.
PURPOSE_POTENTIAL_COLUMN = {
    "shop": "pot_shop",
    "leisure": "pot_leisure",
    "other": "pot_other",
}


def _load_stage(working_directory, stage_name):
    pattern = os.path.join(working_directory, f"{stage_name}__*.p")
    hits = glob.glob(pattern)
    if not hits:
        direct = os.path.join(working_directory, f"{stage_name}.p")
        if os.path.exists(direct):
            hits = [direct]
    if not hits:
        raise RuntimeError(
            f"[run_building_fit_secondary] no cached pickle for stage '{stage_name}' "
            f"in '{working_directory}'. For '{STAGE_BUILDING_POTENTIALS}' this usually "
            f"means the cache predates the building-potential feature (built with the "
            f"flag OFF) -- re-run the pipeline with secondary_building_potentials=true."
        )
    path = max(hits, key=os.path.getmtime)
    LOGGER.info("Loading stage '%s' from %s", stage_name, path)
    with open(path, "rb") as fh:
        return pickle.load(fh)


def secondary_potential_support(candidates, purpose, *,
                                id_col="location_id", zone_col="commune_id"):
    """Per-building potential support for one secondary purpose.

    ``candidates`` is the reconstructed candidate table from
    ``build_secondary_candidates`` (carrying pot_shop / pot_leisure / pot_other).
    Returns DataFrame[building_id, zone, potential] for the candidates with a
    strictly positive potential for ``purpose`` (the within-zone share denominator).
    ``building_id`` is the candidate ``location_id`` (the exact join key to the
    realised secondary locations).
    """
    col = PURPOSE_POTENTIAL_COLUMN[purpose]
    out = pd.DataFrame({
        "building_id": candidates[id_col].astype(str).values,
        "zone": candidates[zone_col].astype(str).values,
        "potential": candidates[col].astype(float).values,
    })
    return out[out["potential"] > 0].reset_index(drop=True)


def _build(working_directory, sampling_rate, output_dir):
    from braunschweig.synthesis.locations.secondary_chainsolvers import (
        build_secondary_candidates,
    )

    working_directory = str(working_directory)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    sec_stage = _load_stage(working_directory, STAGE_REALISED_SECONDARY)
    realised = sec_stage[0] if isinstance(sec_stage, tuple) else sec_stage
    activities = _load_stage(working_directory, STAGE_ACTIVITIES)
    legacy = _load_stage(working_directory, STAGE_LEGACY_CANDIDATES)
    buildings = _load_stage(working_directory, STAGE_BUILDING_POTENTIALS)

    # Reconstruct the exact candidate->potential table the chainsolver used.
    candidates = build_secondary_candidates(legacy, buildings)

    # Guard: a pre-feature cache has no sec_b_ realised ids -> the join would be
    # all-fallback. Surface it loudly rather than reporting a meaningless fit.
    realised_ids = realised["location_id"].astype(str)
    sec_b_share = float(realised_ids.str.startswith("sec_b_").mean())
    LOGGER.info("realised secondary on gpkg shop/leisure buildings (sec_b_): %.1f%%",
                sec_b_share * 100)
    if sec_b_share == 0.0:
        raise RuntimeError(
            "No realised secondary location uses a 'sec_b_' (gpkg building) id. The "
            "cache was built with secondary_building_potentials OFF; the building-"
            "potential fit cannot be measured. Re-run with the feature ON."
        )

    # Attach purpose to each realised secondary location.
    realised = realised.merge(
        activities[["person_id", "activity_index", "purpose"]],
        on=["person_id", "activity_index"], how="left",
    )

    summary_blocks = []
    for purpose in ("shop", "leisure", "other"):
        sub = realised[realised["purpose"] == purpose]
        support = secondary_potential_support(candidates, purpose)
        realised_ids = pd.DataFrame({"building_id": sub["location_id"].astype(str).values})

        report = build_fit_report(realised_ids, support, sampling_rate=sampling_rate)
        per_zone = report["per_zone"]
        cov = report["coverage"]

        per_zone.to_csv(
            output_dir / f"building_potential_fit_secondary_{purpose}_by_commune.csv",
            index=False)
        per_building_residuals(realised_ids, support).to_csv(
            output_dir / f"building_potential_fit_secondary_{purpose}_per_building.csv",
            index=False)
        summary_blocks.append(_summary_block(purpose, per_zone, cov))
        LOGGER.info("[%s] primary %.1f%%, communes scored %d",
                    purpose, cov["primary_rate"] * 100,
                    int((per_zone["n_buildings"] >= 2).sum()))

    (output_dir / "building_potential_fit_secondary_summary.md").write_text(
        "# Building-potential fit report (SECONDARY)\n\n"
        f"Sampling rate: {sampling_rate:g}. Exact location_id join to the "
        "reconstructed chainsolver candidate table (build_secondary_candidates). "
        "Zone = commune. excess_tv = observed TV minus the multinomial "
        "sampling-noise floor (sampling-rate-fair misfit).\n\n"
        + "\n".join(summary_blocks),
        encoding="utf-8")
    LOGGER.info("Wrote secondary summary to %s", output_dir)


def _summary_block(purpose, per_zone, coverage):
    valid = per_zone[(per_zone["n_buildings"] >= 2) & per_zone["pearson"].notna()]
    w = valid["realised_activities"].to_numpy(dtype=float)
    if w.sum() > 0:
        wp = float(np.average(valid["pearson"], weights=w))
        wex = float(np.average(valid["excess_tv"], weights=w))
        wtv = float(np.average(valid["tv_distance"], weights=w))
    else:
        wp = wex = wtv = float("nan")
    density = (valid["realised_activities"].sum() / valid["n_buildings"].sum()
               if valid["n_buildings"].sum() else float("nan"))
    return "\n".join([
        f"## {purpose}",
        "",
        f"- realised activities: {coverage['realised_total']:,}; "
        f"on a potential building (PRIMARY): {coverage['primary_rate']*100:.1f}%, "
        f"fallback: {coverage['fallback_rate']*100:.1f}%",
        f"- communes scored: {len(valid)}; activity density per building: {density:.3f}",
        f"- activity-weighted Pearson r: {wp:.3f}",
        f"- activity-weighted TV distance: {wtv:.4f} (EXCESS over noise floor: {wex:.4f})",
        "",
    ])


def _parse_args(argv=None):
    p = argparse.ArgumentParser(description="Building-potential fit report (SECONDARY).")
    p.add_argument("--working-directory", required=True)
    p.add_argument("--sampling-rate", type=float, required=True)
    p.add_argument("--output-dir", required=True)
    return p.parse_args(argv)


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = _parse_args(argv)
    _build(args.working_directory, args.sampling_rate, args.output_dir)


if __name__ == "__main__":
    main()
