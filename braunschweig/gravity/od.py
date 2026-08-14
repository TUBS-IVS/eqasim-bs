"""Pure work-OD gravity computation and its establishment-count input reader.

Two pieces of the ``braunschweig.gravity.model`` stage live here:

- ``compute_work_od`` -- the pure gravity computation that turns per-zone
  population, employee attraction, distances and friction parameters into a
  row-normalised origin-destination frame. Used for both the Gemeinde
  education pass and the (Gemeinde or TAZ) work pass.
- ``_read_betriebe_per_commune`` -- reads the per-Gemeinde establishment
  counts from the BA Gemeindedaten XLSX that feed the sector-aware
  destination attraction (``braunschweig.gravity.attraction_vector``) when
  ``braunschweig.gravity.sector_aware_enabled`` is on.

Extracted verbatim from ``braunschweig.gravity.model`` (issue #267 split): the
functions, their signatures, their arithmetic and their log lines -- including
the ``[braunschweig.gravity.model]`` message prefixes and the
``logging.getLogger`` name -- are unchanged, so the model output and the
console/log output are byte-identical to the pre-split stage. The logger is
bound to the literal ``"braunschweig.gravity.model"`` name (not
``__name__``, which would resolve to ``"braunschweig.gravity.od"`` here) so
every ``LogRecord.name`` emitted by the moved code is unchanged.

``braunschweig.gravity.model`` re-exports every name defined here -- including
the private ``_read_betriebe_per_commune`` and ``_GEMBAND_COLUMN_NAMES`` --
except its module-level ``logger`` object (see the re-export block in
``model.py``), so existing imports of the stage module path keep working. This
module must NEVER depend on ``braunschweig.gravity.model`` in any direction
other than downward (that would close an import cycle): the dependency runs
strictly model -> od.
"""

from __future__ import annotations

import logging
import os

import numpy as np
import pandas as pd

from braunschweig.data.bbsr.regiostar import ars_to_ags8
from braunschweig.gravity.balancing import _build_origin_slope_vector, evaluate_gravity
from braunschweig.gravity.friction import build_friction_matrix

# Bound to the literal facade stage name (not __name__, which would resolve to
# "braunschweig.gravity.od" here) so every LogRecord emitted by the moved
# compute_work_od code keeps the pre-split "braunschweig.gravity.model" logger
# name -- see the module docstring.
logger = logging.getLogger("braunschweig.gravity.model")


# Escalation threshold (percent of origins) for the zero-total self-loop fallback
# rate log (no-silent-fallback rule). ASSUMPTION: above ~5% of origins the cause is
# almost certainly a broken friction/attraction join rather than genuinely empty zones.
ZERO_TOTAL_SELF_LOOP_WARN_PERCENT = 5.0


# Column layout of the BA Gemeindedaten XLSX, mirrored from
# ``braunschweig.data.census.employment_gemband`` (column 14 = Zahl der
# Betriebe). Kept local so the sector-aware reader does not couple to that
# stage's output schema, which intentionally drops ``n_betriebe``.
_GEMBAND_COLUMN_NAMES = [
    "ags", "name", "svb_wohn",
    "svb_m", "svb_f",
    "svb_de", "svb_auslander",
    "svb_u20", "svb_20_25", "svb_55plus",
    "svb_arbeit", "wohn_eq_arbeit",
    "einpendler", "auspendler",
    "n_betriebe",
]


def _read_betriebe_per_commune(context) -> pd.DataFrame:
    """Read per-Gemeinde establishment counts from the BA Gemeindedaten XLSX.

    Returns a frame with ``commune_id`` (12-digit ARS, matching the
    ``braunschweig.data.census.employees`` destination key) and ``n_betriebe``
    (int). The BA XLSX is keyed by 8-digit AGS (kreisfreie Staedte at 5-digit
    are normalised to ``AGS5 + "000"``), then mapped to the 12-digit ARS via
    ``eqasim_common.spatial.codes`` -- the same AGS->commune_id mapping
    ``braunschweig.data.census.employees`` uses, so the join keys align.
    """
    # Both keys are declared in configure() (data_path is required; the gemband
    # path carries the default there). This helper runs in the execute context,
    # whose config() takes the key alone -- passing a default here would raise
    # "config() takes 2 positional arguments but 3 were given".
    path = os.path.join(
        context.config("data_path"),
        context.config("braunschweig.employment_gemband_path"),
    )
    if not os.path.exists(path):
        raise RuntimeError(
            "[braunschweig.gravity.model] sector-aware attraction is enabled "
            f"but the BA Gemeindedaten XLSX is missing: {path}"
        )

    df = pd.read_excel(
        path, sheet_name="Gemeindedaten",
        header=None, skiprows=8, names=_GEMBAND_COLUMN_NAMES,
    )
    df = df.dropna(subset=["ags"]).copy()
    df["ags"] = df["ags"].astype(str).str.strip()

    df_codes = context.stage("eqasim_common.spatial.codes")
    scope_kreise = set(df_codes["departement_id"].astype(str).unique())

    # Normalise 5-digit kreisfreie AGS to 8-digit (e.g. 03101 -> 03101000).
    # Same guard as braunschweig.data.census.employees: only a 5-digit AGS
    # whose padded form is a real Gemeinde is kreisfrei; other 5-digit rows
    # are Landkreis aggregate totals and must not be padded into fabricated
    # AGS (the merge below would silently drop them -- and a Kreis aggregate
    # must never enter the per-Gemeinde establishment counts).
    valid_gemeinde_ags = set(df_codes["ags"].astype(str))
    mask_krfr = (
        (df["ags"].str.len() == 5)
        & df["ags"].isin(scope_kreise)
        & (df["ags"] + "000").isin(valid_gemeinde_ags)
    )
    df.loc[mask_krfr, "ags"] = df.loc[mask_krfr, "ags"] + "000"
    df = df[(df["ags"].str.len() == 8) & df["ags"].str[:5].isin(scope_kreise)].copy()

    df["n_betriebe"] = (
        pd.to_numeric(df["n_betriebe"], errors="coerce").fillna(0).astype(int)
    )

    df_codes_scope = df_codes[df_codes["departement_id"].astype(str).isin(scope_kreise)]
    df = df.merge(
        df_codes_scope[["ags", "commune_id"]].astype(str),
        on="ags", how="inner",
    )
    return df[["commune_id", "n_betriebe"]]


def compute_work_od(
    df_population,
    df_employees,
    df_distances,
    df_regiostar,
    rs7_by_zone,
    slope,
    constant,
    diagonal,
    slope_overrides,
    friction_factors,
    max_iterations,
):
    """Pure gravity computation returning one row-normalised OD frame.

    Extracts the inner body of the legacy ``_execute_gravity_base`` so it can
    be called twice (once for Gemeinde education, once for TAZ work) when the
    TAZ branch is active.  When called from the OFF path the single result is
    returned twice by the caller (byte-identical to the pre-extraction behaviour).

    Parameters
    ----------
    df_population
        Frame with columns ``origin_id`` and ``population`` (already aggregated
        per zone; no further groupby is applied here on the first group --
        the groupby on ``origin_id`` IS applied inside this function to handle
        per-person input frames where multiple rows share an origin).
    df_employees
        Frame with columns ``destination_id`` and ``employees``.
    df_distances
        Frame with columns ``origin_id``, ``destination_id``, ``distance_km``.
    df_regiostar
        RegioStaR reference frame (``commune_id``, ``regiostar7``).  Used only
        when ``rs7_by_zone`` is ``None`` (the Gemeinde pass).
    rs7_by_zone
        When ``None`` the legacy ``df_regiostar``/``_normalize``/``ars_to_ags8``
        RS7 resolution is used (Gemeinde pass, byte-identical).  When a dict
        ``{zone_id: rs7_int}`` is given the per-origin RS7 is resolved directly
        from it -- this only affects results when ``slope_overrides`` or
        ``friction_factors`` are non-None (both default ``None`` in popsim, so
        the lookup is inert by default).
    slope, constant, diagonal
        Gravity friction parameters.
    slope_overrides
        Optional ``{rs7: slope}`` dict; ``None`` = scalar slope everywhere.
    friction_factors
        Optional friction-band factor dict; ``None`` = legacy ``exp`` friction.
    max_iterations
        Convergence cap for the Furness balancing loop.

    Returns
    -------
    pd.DataFrame
        Row-normalised OD with columns ``origin_id``, ``destination_id``,
        ``weight``.  Each origin's weights sum to 1.0 (origins with no outbound
        flow receive weight=1.0 on the self-loop).
    """
    df_population = df_population.groupby("origin_id")["population"].sum().reset_index()

    municipalities = set(df_population["origin_id"])
    municipalities |= set(df_employees["destination_id"])
    municipalities |= set(df_distances["origin_id"])
    municipalities |= set(df_distances["destination_id"])
    municipalities = sorted(list(municipalities))

    df_population = df_population.set_index("origin_id").reindex(municipalities).fillna(0.0)
    df_employees = df_employees.set_index("destination_id").reindex(municipalities).fillna(0.0)
    df_distances = df_distances.set_index(["origin_id", "destination_id"]).reindex(
        pd.MultiIndex.from_product([municipalities, municipalities])
    )

    distances = df_distances["distance_km"].values.reshape((len(municipalities), len(municipalities)))

    # Fail fast on distance-matrix coverage gaps: a zone pair absent from
    # df_distances reindexes to NaN, which propagates through the friction
    # matrix and only surfaces as a bare `assert converged` after up to 1e6
    # gravity iterations -- an obscured near-hang instead of a key error.
    n_nan_distances = int(np.isnan(distances).sum())
    if n_nan_distances:
        nan_origins = sorted(
            {municipalities[i] for i in np.where(np.isnan(distances).any(axis=1))[0]}
        )[:5]
        raise RuntimeError(
            f"[braunschweig.gravity.model] distance matrix has {n_nan_distances} "
            f"NaN entries after reindexing to the union zone set "
            f"({len(municipalities)} zones) -- the distance frame does not cover "
            f"every zone pair (zone-id mismatch between the population/employees "
            f"and distance producers?). Example origins: {nan_origins}"
        )

    population = df_population["population"]
    employees = df_employees["employees"]

    observations = min(np.sum(population), np.sum(employees))
    population *= observations / np.sum(population)
    employees *= observations / np.sum(employees)

    # Per-origin slope: build the RS7 lookup depending on the zone universe.
    # When rs7_by_zone is given, resolve from the TAZ->RS7 dict directly;
    # when None, use the legacy df_regiostar/_normalize/ars_to_ags8 path.
    if rs7_by_zone is not None:
        # TAZ pass: resolve per-origin RS7 from the explicit zone->rs7 dict.
        # _build_origin_slope_vector accepts a df_regiostar frame whose index is
        # commune_id->regiostar7; we build a synthetic one from rs7_by_zone so
        # the function's internal logic is reused without modification.
        df_regiostar_for_slope = pd.DataFrame({
            "commune_id": list(rs7_by_zone.keys()),
            "regiostar7": list(rs7_by_zone.values()),
        })
    else:
        df_regiostar_for_slope = df_regiostar

    slope_vec = _build_origin_slope_vector(
        municipalities, slope, slope_overrides, df_regiostar_for_slope,
    )

    rs7_vec = None
    friction_factors_resolved = friction_factors
    if isinstance(friction_factors, dict) and friction_factors and all(
        isinstance(v, dict) for v in friction_factors.values()
    ):
        # Per-RS7 per-band factors: resolve the rs7_vec for each municipality.
        if rs7_by_zone is not None:
            # TAZ pass: resolve from the explicit zone->rs7 dict.
            rs7_vec = np.array([
                int(rs7_by_zone.get(str(c), -1)) for c in municipalities
            ])
        else:
            # Gemeinde pass: resolve via df_regiostar + ars_to_ags8 (legacy path).
            rs7_lookup = (
                df_regiostar.set_index("commune_id")["regiostar7"].astype("Int64").to_dict()
            )
            rs7_vec = np.array([
                int(rs7_lookup.get(ars_to_ags8(c)) or -1) for c in municipalities
            ])

        n_missing_rs7 = int(np.sum(rs7_vec == -1))
        n_total_origins = len(municipalities)
        logger.info(
            "[gravity friction] per-RS7 factors: %d/%d origins matched an RS7 code (%.1f%%), %d unmatched",
            n_total_origins - n_missing_rs7, n_total_origins,
            100.0 * (n_total_origins - n_missing_rs7) / max(n_total_origins, 1),
            n_missing_rs7,
        )
        if n_missing_rs7:
            logger.warning(
                "[gravity friction] %d/%d origins have no RS7 code; their per-band "
                "factors would be missing -- check the regiostar coverage",
                n_missing_rs7, n_total_origins,
            )
        friction_factors_resolved = {int(k): {int(b): float(f) for b, f in v.items()}
                                     for k, v in friction_factors.items()}
    elif isinstance(friction_factors, dict) and friction_factors:
        friction_factors_resolved = {int(b): float(f) for b, f in friction_factors.items()}
    else:
        # Also catches {}: a missing or empty mapping is the OFF path (byte-identical).
        friction_factors_resolved = None

    friction = build_friction_matrix(
        distances, slope_vec, constant, diagonal,
        factors=friction_factors_resolved, rs7_vec=rs7_vec,
    )
    flow = evaluate_gravity(population, employees, friction, max_iterations)

    df_matrix = pd.DataFrame({
        "weight": flow.reshape((-1,)),
    }, index=pd.MultiIndex.from_product(
        [municipalities, municipalities],
        names=["origin_id", "destination_id"],
    )).reset_index()

    df_total = (df_matrix[["origin_id", "weight"]]
                .groupby("origin_id").sum()
                .reset_index()
                .rename({"weight": "total"}, axis=1))
    df_matrix = pd.merge(df_matrix, df_total, on="origin_id")

    f_missing_total = df_matrix["total"] == 0.0
    # No-silent-fallback (CLAUDE.md): an origin with zero total outbound flow (no
    # population, no reachable employees, or a fully-masked friction row) would
    # otherwise divide 0/0 below; the self-loop is forced to weight 1.0 instead.
    # Count and log the rate so a systematically empty friction matrix (a real
    # bug, e.g. a broken distance join) is visible rather than silently "working".
    n_missing_origins = int(df_matrix.loc[f_missing_total, "origin_id"].nunique())
    n_total_origins = len(municipalities)
    if n_missing_origins:
        share = 100.0 * n_missing_origins / max(n_total_origins, 1)
        level = logger.warning if share > ZERO_TOTAL_SELF_LOOP_WARN_PERCENT else logger.info
        level(
            "[gravity] zero-total origins forced to self-loop: %d/%d (%.1f%%)",
            n_missing_origins, n_total_origins, share,
        )

    df_matrix.loc[
        f_missing_total & (df_matrix["origin_id"] == df_matrix["destination_id"]),
        "weight",
    ] = 1.0
    df_matrix.loc[f_missing_total, "total"] = 1.0

    df_matrix["weight"] = df_matrix["weight"] / df_matrix["total"]
    df_matrix = df_matrix[["origin_id", "destination_id", "weight"]]

    return df_matrix
