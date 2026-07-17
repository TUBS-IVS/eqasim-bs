"""Gravity model for Braunschweig - base + Kreis-level calibration.

This module is the merged successor of:
- ``bavaria/gravity/model.py`` (Origin: eqasim-bavaria @ b20fbe6) - the
  Gemeinde x Gemeinde gravity model with IDF-derived parameters.
- ``braunschweig/gravity/model.py`` - the BA Pendleratlas IPF calibration
  layer that scales the Gemeinde flows so Kreis aggregates match observed
  SvB-Pendlerstroeme.

Phase 2.11 of the eqasim-bs refactor merged both into a single module so
the BS pipeline no longer delegates through ``braunschweig.gravity.model``. The
behaviour is unchanged.

Output schema is identical to ``braunschweig.gravity.model``::

    origin_id          str   commune_id (8-digit AGS)
    destination_id     str
    weight             float row-normalised P(destination | origin)

Returned as ``(df_work_od, df_education_od)`` tuple. Education uses the
uncalibrated gravity result (no equivalent observed data).
"""

from __future__ import annotations

import logging
import os

import numpy as np
import pandas as pd

from braunschweig.data.bbsr.regiostar import ars_to_ags8
from braunschweig.gravity.friction import build_friction_matrix

logger = logging.getLogger(__name__)


# --- Inherited from eqasim-bavaria -----------------------------------------
# Gemeinde x Gemeinde gravity model with IDF-derived defaults.

# Defaults: -0.09 came from IDF, value -2.0 has been calibrated.
DEFAULT_SLOPE = -0.2
DEFAULT_CONSTANT = -2.4
DEFAULT_DIAGONAL = 1.0

# Escalation threshold (percent of origins) for the zero-total self-loop fallback
# rate log (no-silent-fallback rule). ASSUMPTION: above ~5% of origins the cause is
# almost certainly a broken friction/attraction join rather than genuinely empty zones.
ZERO_TOTAL_SELF_LOOP_WARN_PERCENT = 5.0


# --- Sector-aware destination attraction (model-improvement item #8) --------
#
# The work-flow gravity model draws destinations with a doubly-constrained
# balancing whose destination *attraction* vector is the employees-at-workplace
# headcount per Gemeinde (``braunschweig.data.census.employees``). A pure
# headcount discards the sectoral composition of a Gemeinde: a Gemeinde with one
# dominant large employer and a service-heavy Gemeinde with many small firms can
# carry the same headcount yet differ structurally. The BA Gemeindedaten publish
# a per-Gemeinde establishment count ``n_betriebe`` (number of Betriebe / local
# units), already documented in
# ``braunschweig.data.census.employment_gemband``. The establishment count per
# employee is a robust, always-present sectoral-structure signal.
#
# Behind ``braunschweig.gravity.sector_aware_enabled`` (default False -> the OFF
# path is byte-identical to the legacy headcount attraction) the attraction is
# tilted by the per-Gemeinde establishment density (Betriebe per employee)
# *relative to the Kreis mean*. The tilt is normalised to a flow-weighted mean of
# 1.0 WITHIN each Kreis, so the Kreis-level attraction total is preserved and the
# downstream BA-Pendler IPF (which constrains Kreis-pair flows,
# ``_calibrate``) stays consistent; only the sub-Kreis (Gemeinde) attraction is
# reshaped. The mechanism deliberately does NOT consume the sector-split BA OD
# ``braunschweig.data.ba.pendler_detailed`` because that file is not pinned in
# any config (the loader returns an empty frame when its path is unset); the
# always-present ``n_betriebe`` count is used instead.

# Exponent on the establishment-density tilt. 1.0 = full tilt (linear in the
# Betriebe-per-employee ratio); kept as a module constant rather than a magic
# number so a future config option can override it without changing the maths.
SECTOR_AWARE_TILT_EXPONENT = 1.0


def apply_sector_aware_attraction(
    df_employees: pd.DataFrame,
    df_betriebe: pd.DataFrame | None,
    enabled: bool,
    tilt_exponent: float = SECTOR_AWARE_TILT_EXPONENT,
) -> pd.DataFrame:
    """Tilt the destination ``employees`` attraction by establishment density.

    Parameters
    ----------
    df_employees
        Destination attraction with columns ``commune_id`` (12-digit ARS; the
        first 5 characters are the Kreis) and ``employees`` (headcount).
    df_betriebe
        Per-Gemeinde establishment counts with columns ``commune_id`` and
        ``n_betriebe``. May be ``None`` only when ``enabled`` is ``False``.
    enabled
        Sector-aware flag. When ``False`` the input frame is returned
        unchanged (byte-identical to the legacy headcount attraction).
    tilt_exponent
        Exponent applied to the within-Kreis density ratio (default 1.0).

    Returns
    -------
    pd.DataFrame
        A copy of ``df_employees`` with a tilted ``employees`` column when
        ``enabled``; the unchanged input when not.

    Notes
    -----
    For Gemeinde ``g`` in Kreis ``k`` the establishment density is
    ``rho_g = n_betriebe_g / employees_g`` (Betriebe per employee). Within each
    Kreis a relative tilt ``t_g = (rho_g / rho_bar_k) ** tilt_exponent`` is
    formed, where ``rho_bar_k`` is the employee-weighted mean density of the
    Kreis. The tilt is renormalised so the employee-weighted mean tilt within
    the Kreis is exactly 1.0, hence ``sum_g employees_g * t_g = sum_g
    employees_g`` -- the Kreis attraction total is preserved and only the
    sub-Kreis split changes. Gemeinden absent from ``df_betriebe`` or with a
    non-positive establishment count receive a neutral tilt (1.0) so no Gemeinde
    is boosted out of nothing or collapsed to zero. Gemeinden with zero
    headcount stay at zero (the tilt is multiplicative).
    """
    if not enabled:
        return df_employees

    if df_betriebe is None:
        raise ValueError(
            "[braunschweig.gravity.model] sector-aware attraction is enabled "
            "but no establishment-count table (n_betriebe) was provided."
        )

    df = df_employees.copy()
    df["__kreis"] = df["commune_id"].astype(str).str[:5]

    betriebe_lookup = (
        df_betriebe.groupby("commune_id")["n_betriebe"].sum().astype(float)
    )
    n_betriebe = df["commune_id"].map(betriebe_lookup).to_numpy()
    employees = df["employees"].to_numpy(dtype=float)

    # Establishment density rho_g = Betriebe per employee. Undefined where the
    # Gemeinde has no headcount or no/zero establishments.
    with np.errstate(divide="ignore", invalid="ignore"):
        rho = np.where(
            (employees > 0.0) & (n_betriebe > 0.0),
            n_betriebe / employees,
            np.nan,
        )
    df["__rho"] = rho

    # Employee-weighted mean density per Kreis (over Gemeinden with a defined
    # rho). Used as the within-Kreis reference so the tilt is dimensionless.
    df["__w_rho"] = np.where(np.isfinite(rho), employees * rho, 0.0)
    df["__w_def"] = np.where(np.isfinite(rho), employees, 0.0)
    grp = df.groupby("__kreis")
    rho_bar = (grp["__w_rho"].transform("sum")
               / grp["__w_def"].transform("sum")).to_numpy()

    # Relative tilt; Gemeinden without a defined density (or in a Kreis with no
    # defined density at all) get a neutral tilt of 1.0.
    with np.errstate(divide="ignore", invalid="ignore"):
        tilt = np.where(
            np.isfinite(rho) & np.isfinite(rho_bar) & (rho_bar > 0.0),
            (rho / rho_bar) ** float(tilt_exponent),
            1.0,
        )
    df["__tilt"] = tilt

    # Renormalise the tilt to an employee-weighted mean of 1.0 within each Kreis
    # so the Kreis attraction total is preserved exactly.
    df["__w_tilt"] = employees * tilt
    norm = (grp["__w_tilt"].transform("sum")
            / grp["employees"].transform("sum")).to_numpy()
    tilt_normalised = np.where(norm > 0.0, tilt / norm, 1.0)

    df["employees"] = employees * tilt_normalised
    df = df.drop(columns=["__kreis", "__rho", "__w_rho", "__w_def",
                          "__tilt", "__w_tilt"])

    n_reshaped = int((~np.isclose(tilt_normalised, 1.0)).sum())
    print(
        "[braunschweig.gravity.model] sector-aware attraction ON: "
        f"establishment-density tilt reshaped {n_reshaped} Gemeinde attractions "
        "(Kreis totals preserved)."
    )
    return df


def build_destination_attraction(
    df_employees_raw: pd.DataFrame,
    df_betriebe: pd.DataFrame | None,
    sector_aware_enabled: bool,
) -> pd.DataFrame:
    """Build the gravity destination attraction from the raw employees stage output.

    Owns the schema handoff between ``braunschweig.data.census.employees``
    (columns ``commune_id``/``weight``) and ``apply_sector_aware_attraction``
    (which reads ``employees``): the rename happens BEFORE the flag-gated tilt.
    Applying the tilt to the raw stage frame crashed the ON path with
    ``KeyError: 'employees'`` because the rename only happened downstream
    (issue #128).

    Returns a frame with columns ``commune_id`` and ``employees``; on the OFF
    path the values are byte-identical to the legacy headcount attraction.
    """
    df_employees = df_employees_raw.rename(columns={"weight": "employees"})[
        ["commune_id", "employees"]
    ]
    return apply_sector_aware_attraction(
        df_employees, df_betriebe, enabled=sector_aware_enabled,
    )


# Iteration cap for the doubly-constrained balancing in ``evaluate_gravity``.
# Deliberately high so convergence (the 1e-3 per-step delta test below) is
# always reached before the cap on realistic inputs -- the cap only guards
# against a non-converging pathological input. Kept as a module constant (and
# overridable via the ``gravity_max_iterations`` config key declared in
# ``configure``) instead of the previous magic ``int(1e6)`` literal. The default
# preserves the exact prior behaviour: the loop ran up to 1e6 times before.
DEFAULT_GRAVITY_MAX_ITERATIONS = int(1e6)


def evaluate_gravity(
    population,
    employees,
    friction,
    max_iterations=DEFAULT_GRAVITY_MAX_ITERATIONS,
    debug=False,
):
    """Iterative balancing of a doubly-constrained gravity model.

    Parameters
    ----------
    population, employees, friction
        Production targets, attraction targets and the friction matrix.
    max_iterations
        Maximum number of balancing iterations before giving up. The default
        (``DEFAULT_GRAVITY_MAX_ITERATIONS`` = 1e6) reproduces the prior
        behaviour exactly; convergence is normally reached far earlier.
    debug
        When ``True`` the per-iteration convergence deltas are printed (off by
        default; the previous unconditional per-iteration print is the only
        behaviour change and is non-numerical).

    Notes
    -----
    The convergence test is unchanged: it compares ``production`` and
    ``attraction`` against their values at the start of the iteration and
    ``flow`` against its value from the previous iteration. ``production`` and
    ``attraction`` are mutated element-by-element in place, so their pre-update
    snapshots must be copies. ``flow`` is fully reassigned each iteration
    (``flow = np.copy(friction)``) and never mutated in place after the
    reassignment, so a plain reference to the previous ``flow`` object is
    sufficient for the delta -- the explicit ``np.copy(flow)`` was redundant and
    is removed. The returned matrix and the number of iterations performed are
    therefore identical to before.
    """
    # The production/attraction targets may arrive as pandas Series (compute_work_od
    # passes df columns). Their integer __getitem__ (``population[k]`` / ``employees[k]``
    # below) positionally indexes a string-indexed Series, which pandas deprecates with
    # a FutureWarning. Coerce to plain arrays once so positional access is explicit; the
    # values -- and hence the balancing result -- are unchanged.
    population = np.asarray(population)
    employees = np.asarray(employees)

    production = np.ones((len(population),))
    attraction = np.ones((len(population),))
    flow = np.ones((len(population), len(population)))
    converged = False

    for iteration in range(int(max_iterations)):
        previous_production = np.copy(production)
        previous_attraction = np.copy(attraction)
        # ``flow`` is reassigned (not mutated in place) below, so the old object
        # this reference points to stays valid for the delta -- no copy needed.
        previous_flow = flow

        for k in range(len(population)):
            production[k] = population[k] / np.sum(attraction * friction[k, :])

        for k in range(len(population)):
            attraction[k] = employees[k] / np.sum(production * friction[:, k])

        flow = np.copy(friction)
        for i in range(len(population)):
            flow[i, :] *= production[i]
        for j in range(len(population)):
            flow[:, j] *= attraction[j]

        production_delta = np.abs(production - previous_production)
        attraction_delta = np.abs(attraction - previous_attraction)
        flow_delta = np.abs(flow - previous_flow)

        if debug:
            print(
                "Gravity iteration", iteration,
                "prod. max. delta:", np.max(production_delta),
                "attr. max. delta:", np.max(attraction_delta),
                "flow max. delta:", np.max(flow_delta),
            )

        if (np.max(production_delta) < 1e-3
                and np.max(attraction_delta) < 1e-3
                and np.max(flow_delta) < 1e-3):
            converged = True
            break

    assert converged
    return flow


# Fallback-rate threshold for the per-RegioStaR slope override (CLAUDE.md
# "Fallback transparency"). Above this share of origins falling back to the
# scalar default slope, a WARNING is emitted: a high rate means the override
# map / RegioStaR join is not actually shaping the friction matrix for most
# origins (a wrong RS7 lookup, a stale override map, or an ARS-format mismatch),
# so the per-RS7 differentiation is silently inert. Below the threshold the
# per-RS7 primary path is doing its job. 10 % matches the "~5-10%" guidance.
ORIGIN_SLOPE_FALLBACK_WARN_THRESHOLD = 0.10


def _build_origin_slope_vector(
    municipalities: list[str],
    default_slope: float,
    overrides: dict | None,
    df_regiostar: pd.DataFrame | None,
) -> np.ndarray:
    """Return one slope per origin, optionally overridden by RegioStaR-7.

    ``overrides`` maps RegioStaR-7 codes (int 71..77) to slope values
    (negative floats, e.g. ``-0.05``). Origins whose commune_id is not
    in ``df_regiostar`` or whose RegioStaR-7 code has no override fall
    back to ``default_slope``. The returned array has shape ``(N,)``
    aligned with ``municipalities`` and is broadcast against the
    distance matrix as ``slope[:, None] * distances``.

    Fallback transparency (CLAUDE.md): the PRIMARY path is a per-RegioStaR-7
    override slope; the FALLBACK is the scalar ``default_slope`` (used when an
    origin has no RS7 in ``df_regiostar`` or its RS7 code is absent from
    ``overrides``). The primary-vs-fallback counts are logged as an explicit
    rate and a ``WARNING`` is printed when the fallback share exceeds
    ``ORIGIN_SLOPE_FALLBACK_WARN_THRESHOLD``, so a silently inert override map
    is surfaced rather than passing unnoticed.
    """
    slope_vec = np.full(len(municipalities), float(default_slope))
    if not overrides or df_regiostar is None or df_regiostar.empty:
        # No override map / no RegioStaR table -> every origin is the scalar
        # fallback by construction. This is the legitimate "feature off" case
        # (the scalar slope is the intended model), so it is reported at info
        # level without a WARNING: 100 % "fallback" here is the configured
        # behaviour, not a broken primary path.
        n = len(municipalities)
        print(
            "[braunschweig.gravity.model] per-RegioStaR slope inactive "
            f"(no override map): scalar slope used for all {n}/{n} origins "
            f"(default={default_slope})."
        )
        return slope_vec

    typed_overrides = {int(k): float(v) for k, v in overrides.items()}
    rs7_lookup = (
        df_regiostar.set_index("commune_id")["regiostar7"]
        .astype("Int64")
        .to_dict()
    )

    def _normalize(cid: str) -> str:
        """Convert 12-digit ARS to 8-digit AGS if needed.

        ``braunschweig.ipf.attributed`` produces commune_id in the full
        12-character ARS format (Land(2)+RB(1)+Kreis(2)+VG(4)+Gem(3)),
        while ``braunschweig.data.bbsr.regiostar`` keys on the 8-digit
        AGS = ARS[0:5] + ARS[9:12]. Other consumers may already pass
        the 8-digit form; in that case the slice is a no-op.
        """
        s = str(cid)
        if len(s) == 12:
            return s[0:5] + s[9:12]
        return s

    matched = 0
    used_codes: dict[int, int] = {}
    # Fallback breakdown for traceability: an origin can fall back either
    # because it has no RS7 in the RegioStaR table (no_rs7) or because its RS7
    # code is not a key in the override map (rs7_not_in_overrides). Both reuse
    # the scalar default slope.
    fallback_no_rs7 = 0
    fallback_rs7_not_in_overrides = 0
    for i, commune_id in enumerate(municipalities):
        key = _normalize(commune_id)
        rs7 = rs7_lookup.get(key)
        if rs7 is None or pd.isna(rs7):
            fallback_no_rs7 += 1
            continue
        rs7 = int(rs7)
        if rs7 in typed_overrides:
            slope_vec[i] = typed_overrides[rs7]
            matched += 1
            used_codes[rs7] = used_codes.get(rs7, 0) + 1
        else:
            fallback_rs7_not_in_overrides += 1

    n = len(municipalities)
    n_fallback = fallback_no_rs7 + fallback_rs7_not_in_overrides
    primary_pct = 100.0 * matched / n if n else 0.0
    fallback_pct = 100.0 * n_fallback / n if n else 0.0
    warn_prefix = (
        "WARNING: "
        if n and (n_fallback / n) > ORIGIN_SLOPE_FALLBACK_WARN_THRESHOLD
        else ""
    )
    print(
        f"[braunschweig.gravity.model] {warn_prefix}per-RegioStaR slope: "
        f"primary (per-RS7 override) {matched}/{n} ({primary_pct:.1f}%), "
        f"fallback (scalar default={default_slope}) {n_fallback}/{n} "
        f"({fallback_pct:.1f}%) "
        f"[no RS7 in table: {fallback_no_rs7}, "
        f"RS7 not in override map: {fallback_rs7_not_in_overrides}]; "
        f"overrides per RS7 = {used_codes}"
    )
    return slope_vec


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


def _execute_gravity_base(context):
    """Run the bavaria-style Gemeinde x Gemeinde gravity model.

    Returns a 4-tuple ``(df_work_od, df_education_od, pop_taz,
    df_work_production)``. The first two are row-normalised conditional
    probabilities; ``pop_taz`` is the TAZ origin margin (non-None only on the
    TAZ ON path); ``df_work_production`` is the #132 work production frame
    (schema ``origin_id``, ``population``; non-None only when
    ``braunschweig.gravity.work_production_mass`` is ``svb_wohn`` AND the TAZ
    flag is OFF -- on the TAZ-ON + svb_wohn path it is None because the
    production mass travels inside ``pop_taz`` instead).

    When ``taz_work_location_choice`` is OFF (default) the function runs the
    gravity once on the Gemeinde universe and returns the same frame for both
    work and education -- byte-identical to the pre-TAZ behaviour. With
    ``work_production_mass: svb_wohn`` a SECOND Gemeinde gravity is run for
    work using employed residents (svb_wohn) as the production mass; education
    always keeps the population-based OD.

    When ON the gravity is run TWICE:
    - Gemeinde pass (``education_od``): standard Gemeinde x Gemeinde gravity.
    - TAZ pass (``work_od``): TAZ x TAZ gravity using TAZ-aggregated population
      and building-potential-weighted employee attraction.
    """
    # B1: read the flag with the key alone at execute time.  synpp's
    # ExecuteContext.config() takes only the key; passing a default here raises
    # "config() takes 2 positional arguments but 3 were given".  The default
    # False is declared in configure().
    taz_on = context.config("taz_work_location_choice")

    # #132: work production mass, read once and validated BEFORE any gravity
    # computation (build_work_production_mass validates again -- belt and
    # braces). The default "population" is declared in configure().
    production_mode = context.config("braunschweig.gravity.work_production_mass")
    from braunschweig.gravity.production_mass import PRODUCTION_MASS_MODES  # noqa: PLC0415
    if production_mode not in PRODUCTION_MASS_MODES:
        raise ValueError(
            "[braunschweig.gravity.model] unknown "
            f"braunschweig.gravity.work_production_mass {production_mode!r}; "
            f"expected one of {PRODUCTION_MASS_MODES}"
        )

    df_distances = context.stage("eqasim_common.gravity.distance_matrix")
    # data.census.filtered resolves to the configured population producer
    # (braunschweig.ipf.attributed in the legacy config -- unchanged behaviour --
    # or braunschweig.popsim.stage in the popsim configs), so the gravity weights
    # always come from the SAME population as the demand.
    df_population_raw = context.stage("data.census.filtered")
    df_employees_raw = context.stage("braunschweig.data.census.employees")
    df_regiostar = context.stage("braunschweig.data.bbsr.regiostar")

    # Sector-aware destination attraction (flag-gated; OFF -> byte-identical).
    # ``build_destination_attraction`` renames the stage's ``weight`` column to
    # ``employees`` BEFORE the flag-gated tilt (issue #128: tilting the raw
    # stage frame crashed with KeyError 'employees') and tilts the per-Gemeinde
    # attraction by establishment density while preserving Kreis totals.
    # synpp's ExecuteContext.config() takes only the key (no default argument);
    # the default False is declared in configure(). Passing a default here raises
    # "config() takes 2 positional arguments but 3 were given" and aborts the run.
    sector_aware_enabled = context.config("braunschweig.gravity.sector_aware_enabled")
    df_betriebe = _read_betriebe_per_commune(context) if sector_aware_enabled else None
    df_employees_gemeinde = build_destination_attraction(
        df_employees_raw, df_betriebe, sector_aware_enabled,
    )

    # Rename to the schema expected by compute_work_od.
    df_pop_gemeinde = df_population_raw.rename(columns={
        "commune_id": "origin_id",
        "weight": "population",
    })[["origin_id", "population"]]

    df_emp_gemeinde = df_employees_gemeinde.rename(columns={
        "commune_id": "destination_id",
    })[["destination_id", "employees"]]

    # #132: svb_wohn production needs a Gemeinde-AGGREGATED population frame.
    # data.census.filtered is per-PERSON (multiple rows per origin_id), but
    # build_work_production_mass documents a one-row-per-Gemeinde input: merging
    # svb per person-row would multiply each Gemeinde's svb_wohn by its person
    # count downstream. Aggregate ONCE here so both the Gemeinde svb path (OFF)
    # and the TAZ svb tilt (ON) consume the SAME frame. Left None on the
    # "population" default so the byte-identical path does no extra work.
    df_pop_gemeinde_aggregated = None
    if production_mode != "population":
        df_pop_gemeinde_aggregated = (
            df_pop_gemeinde.groupby("origin_id", as_index=False)["population"].sum()
        )

    slope = context.config("gravity_slope")
    constant = context.config("gravity_constant")
    diagonal = context.config("gravity_diagonal")
    slope_overrides = context.config("gravity_slope_by_regiostar7")
    friction_factors = context.config("gravity_friction_factors")
    # ExecuteContext.config() takes the key alone (the default is declared in
    # configure()); passing a default here would raise.
    max_iterations = context.config("gravity_max_iterations")

    # Gemeinde pass (used for education, and also for work when TAZ is OFF).
    education_od = compute_work_od(
        df_population=df_pop_gemeinde,
        df_employees=df_emp_gemeinde,
        df_distances=df_distances,
        df_regiostar=df_regiostar,
        rs7_by_zone=None,
        slope=slope,
        constant=constant,
        diagonal=diagonal,
        slope_overrides=slope_overrides,
        friction_factors=friction_factors,
        max_iterations=max_iterations,
    )

    if not taz_on:
        if production_mode == "population":
            # OFF path: byte-identical to the pre-extraction behaviour.
            # Trailing elements are None so execute() can unpack uniformly.
            return education_od, education_od, None, None
        # svb_wohn: run a SEPARATE work gravity with the svb production mass;
        # education keeps the population-based OD computed above.
        from braunschweig.gravity.production_mass import (  # noqa: PLC0415
            build_work_production_mass, read_svb_wohn_per_commune,
        )
        df_svb = read_svb_wohn_per_commune(context)
        # Read with the key alone: configure() declares this key only inside
        # the "production_mode != population" conditional we are already in,
        # so the OFF path never has to request it.
        warn_share = context.config("braunschweig.gravity.svb_wohn_fallback_warn_share")
        # df_pop_gemeinde_aggregated (one row per Gemeinde) was built above so
        # the SAME aggregated mass seeds the Gemeinde svb path here and the TAZ
        # tilt on the ON path.
        df_work_production = build_work_production_mass(
            df_pop_gemeinde_aggregated, df_svb, mode=production_mode,
            warn_share=warn_share)
        work_od = compute_work_od(
            df_population=df_work_production,
            df_employees=df_emp_gemeinde,
            df_distances=df_distances,
            df_regiostar=df_regiostar,
            rs7_by_zone=None,
            slope=slope,
            constant=constant,
            diagonal=diagonal,
            slope_overrides=slope_overrides,
            friction_factors=friction_factors,
            max_iterations=max_iterations,
        )
        return work_od, education_od, None, df_work_production

    # TAZ pass for work-location gravity (ON path).
    # The origin margin splits each commune's census weight across its TAZ by the
    # home-point distribution, keyed on the 12-digit ARS commune_id that BOTH
    # data.census.filtered and home.locations carry (their household_id spaces are
    # disjoint -- FULL vs SAMPLED population -- so a household_id join cannot work).
    from braunschweig.gravity.taz_margins import (  # noqa: PLC0415
        build_dest_attraction_per_taz,
        build_origin_population_per_taz,
    )

    df_taz = context.stage("braunschweig.data.spatial.taz")
    df_dist_taz = context.stage("braunschweig.gravity.distance_matrix_taz")
    df_homes = context.stage("synthesis.population.spatial.home.locations")
    df_buildings = context.stage("braunschweig.data.building_potentials")
    # Census Gemeinde polygons (commune_id = 12-digit ARS, the key both
    # data.census.filtered and the employees frame use). The dest margin assigns
    # each TAZ to its census commune by LOCATION against these polygons, which
    # reconciles the RVB gpkg AGS-8 codes with the census communes geometrically
    # (the ~10 Gemeinde-code mismatches vanish; no AGS->ARS crosswalk needed).
    df_municipalities = context.stage("data.spatial.municipalities")

    pop_taz, _, _ = build_origin_population_per_taz(df_homes, df_population_raw, df_taz)

    # #132: svb_wohn production tilt for the TAZ path. svb_wohn carries NO
    # sub-Gemeinde information, so each TAZ's population is scaled by its parent
    # Gemeinde's employment rate (svb_wohn_gem / pop_gem): this shifts the
    # BETWEEN-Gemeinde masses while preserving the within-Gemeinde home
    # distribution. Tilting pop_taz HERE -- before df_pop_taz (gravity margin)
    # and the returned pop_taz_from_base (Kreis-IPF _calibrate, _append_outbound_flows)
    # both derive from it -- keeps all three mass entry points consistent with a
    # single edit. The Gemeinde-aggregated frame is reused (see above).
    if production_mode != "population":
        from braunschweig.gravity.production_mass import (  # noqa: PLC0415
            read_svb_wohn_per_commune, tilt_taz_production_by_gemeinde_rate,
        )
        df_svb = read_svb_wohn_per_commune(context)
        # Same key-only read as the non-TAZ svb branch above (declared under
        # the same "production_mode != population" conditional in configure()).
        warn_share = context.config("braunschweig.gravity.svb_wohn_fallback_warn_share")
        pop_taz = tilt_taz_production_by_gemeinde_rate(
            pop_taz, df_pop_gemeinde_aggregated, df_svb, warn_share=warn_share)

    att_taz, _, _ = build_dest_attraction_per_taz(
        df_buildings, df_employees_raw, df_taz, df_municipalities)

    # TAZ origin population frame (schema: origin_id, population).
    df_pop_taz = pop_taz.rename(columns={"taz_id": "origin_id"})[["origin_id", "population"]]

    # TAZ destination attraction frame (schema: destination_id, employees).
    # att_taz carries commune_id (ARS-12) -- rename to destination_id and use
    # the ``attraction`` column as the employees analogue.
    df_emp_taz = att_taz.rename(columns={
        "taz_id": "destination_id",
        "attraction": "employees",
    })[["destination_id", "employees"]]

    # Per-origin RS7: resolved directly from the TAZ frame's regiostar7 column.
    rs7_by_zone = dict(zip(df_taz["taz_id"].astype(str), df_taz["regiostar7"].astype(int)))

    work_od = compute_work_od(
        df_population=df_pop_taz,
        df_employees=df_emp_taz,
        df_distances=df_dist_taz,
        df_regiostar=df_regiostar,
        rs7_by_zone=rs7_by_zone,
        slope=slope,
        constant=constant,
        diagonal=diagonal,
        slope_overrides=slope_overrides,
        friction_factors=friction_factors,
        max_iterations=max_iterations,
    )

    # Return the (possibly svb-tilted) pop_taz as the third element so execute()
    # can reuse it without calling build_origin_population_per_taz a second time
    # (sjoin is expensive). The fourth element (Gemeinde work production frame) is
    # None on the TAZ path: the #132 production mass travels INSIDE pop_taz, so
    # _calibrate / _append_outbound_flows read it via df_population_for_od (#132).
    return work_od, education_od, pop_taz, None


# --- Braunschweig-specific -------------------------------------------------
# BA-Pendleratlas calibration: IPF the Gemeinde-level OD so Kreis aggregates
# match observed SvB flows; inject ZGB -> external Kreis outbound rows.

# IPF convergence parameters for the Kreis-level calibration step.
MAX_IPF_ITERATIONS = 20
IPF_TOLERANCE = 1e-3


def configure(context):
    # TAZ work-location gravity branch.  Default False -> the OFF path is
    # byte-identical to the pre-TAZ behaviour (single Gemeinde pass returned
    # for both work and education).  When True a second TAZ-keyed gravity pass
    # is computed for work location choice.
    context.config("taz_work_location_choice", False)

    # Base stages and configs are declared unconditionally so the OFF path
    # needs no new keys (and all existing pipeline configs remain valid).
    context.stage("eqasim_common.gravity.distance_matrix")
    # data.census.filtered resolves to the configured population producer
    # (braunschweig.ipf.attributed in the legacy config -- unchanged behaviour --
    # or braunschweig.popsim.stage in the popsim configs), so the gravity weights
    # always come from the SAME population as the demand.
    context.stage("data.census.filtered")
    context.stage("braunschweig.data.census.employees")
    context.stage("braunschweig.data.bbsr.regiostar")
    context.config("gravity_slope", DEFAULT_SLOPE)
    context.config("gravity_constant", DEFAULT_CONSTANT)
    context.config("gravity_diagonal", DEFAULT_DIAGONAL)
    # Iteration cap for the doubly-constrained balancing (``evaluate_gravity``).
    # The default reproduces the prior magic 1e6 literal, so convergence is
    # reached exactly as before; exposed only so a non-converging run can be
    # bounded explicitly.
    context.config("gravity_max_iterations", DEFAULT_GRAVITY_MAX_ITERATIONS)
    # Optional dict {regiostar7_code: slope}. None/absent = use scalar slope.
    # The default MUST be ``None`` and not ``{}``: synpp's ``flatten()`` drops
    # empty-dict values entirely, so an absent override with a ``{}`` default
    # vanishes from ``required_config`` and ``context.config(...)`` then raises
    # "Config option ... is not requested" at execute time. ``None`` survives
    # flattening and is treated as "no overrides" by ``_build_origin_slope_vector``.
    context.config("gravity_slope_by_regiostar7", None)
    # Optional per-distance-band friction factors. None/absent = legacy
    # exp(slope*d) friction (byte-identical OFF path). {band: f} = global per-band;
    # {rs7: {band: f}} = per-origin-RS7 per-band. Written by
    # scripts/calibrate_gravity_distribution.py; do not hand-edit. Must default to
    # None (not {}) so synpp flatten() does not drop it (see gravity_slope_by_regiostar7).
    context.config("gravity_friction_factors", None)
    context.stage("braunschweig.data.census.pendler")
    context.stage("braunschweig.data.census.employment")
    context.stage("braunschweig.data.external_workplaces")
    context.config("braunschweig.political_prefix")
    # Sector-aware destination attraction (model-improvement item #8). Default
    # False -> the ``employees`` attraction is the legacy headcount and the
    # gravity result is byte-identical to before. When True the per-Gemeinde
    # establishment density (BA Gemeindedaten ``n_betriebe``) tilts the
    # within-Kreis attraction (see ``apply_sector_aware_attraction``).
    context.config("braunschweig.gravity.sector_aware_enabled", False)
    if context.config("braunschweig.gravity.sector_aware_enabled", False):
        # Only declared as required when the flag is on, so the legacy OFF path
        # needs no new config keys or stages.
        context.config("data_path")
        context.config(
            "braunschweig.employment_gemband_path",
            "braunschweig/gemband-dlk-0-202506-xlsx.xlsx",
        )
        context.stage("eqasim_common.spatial.codes")

    # #132: production mass for the WORK gravity ("population" reproduces the
    # legacy behaviour byte-identically; "svb_wohn" uses employed residents,
    # see braunschweig/gravity/production_mass.py). Education always uses
    # population (pupils/students are not SvB).
    context.config("braunschweig.gravity.work_production_mass", "population")
    if context.config("braunschweig.gravity.work_production_mass", "population") != "population":
        # Only declared as required when the svb_wohn mode is active, so the
        # default "population" path needs no new config keys or stages. These
        # duplicate the sector-aware declarations above on purpose (synpp
        # tolerates repeated declaration); either flag alone must suffice.
        context.config("data_path")
        context.config(
            "braunschweig.employment_gemband_path",
            "braunschweig/gemband-dlk-0-202506-xlsx.xlsx",
        )
        context.stage("eqasim_common.spatial.codes")
        # Fallback-transparency threshold (CLAUDE.md): share of Gemeinden
        # falling back to the Kreis-mean employment rate above which
        # build_work_production_mass / tilt_taz_production_by_gemeinde_rate
        # escalate their rate log to a WARNING. Declared only under this
        # conditional -- like the keys above -- so the "population" OFF path
        # needs no new config key. The default is the single source of truth
        # in production_mass (lazy import, consistent with the execute paths).
        from braunschweig.gravity.production_mass import (  # noqa: PLC0415
            SVB_FALLBACK_WARN_SHARE,
        )
        context.config(
            "braunschweig.gravity.svb_wohn_fallback_warn_share",
            SVB_FALLBACK_WARN_SHARE,
        )

    # #193: inner VerBindungen calibration anchor. Default False -> byte-
    # identical work OD (the anchor CHANGES scientific output when ON; the
    # default flips only via the pre-registered decision rule + ADR --
    # see docs/superpowers/specs/2026-07-16-verbindungen-calibration-anchor-design.md).
    context.config("braunschweig.gravity.verbindungen_anchor_enabled", False)
    if context.config("braunschweig.gravity.verbindungen_anchor_enabled", False):
        # Reference stages only required when the anchor is ON, so the OFF
        # path needs no new stages or data files.
        context.stage("braunschweig.data.verbindungen.zones")
        context.stage("braunschweig.data.verbindungen.work_od")
        # PROVISIONAL default (not empirical): per-row minimum observed
        # reference commuters below which a row is not anchored (guards the
        # censoring edge, values 10-12 are coarse small-count noise). The
        # measured default from the real coverage distribution replaces this
        # value via the holdout script (#193 Task 8) BEFORE any default-ON.
        context.config("braunschweig.verbindungen.anchor_min_observed_commuters", 30)

    # TAZ-specific stages: only declared when the flag is ON so the OFF path
    # (all existing configs) needs no new keys or stages.
    if context.config("taz_work_location_choice", False):
        context.stage("braunschweig.data.spatial.taz")
        context.stage("braunschweig.gravity.distance_matrix_taz")
        context.stage("synthesis.population.spatial.home.locations")
        context.stage("braunschweig.data.building_potentials")
        # Census Gemeinde polygons (ARS-12) for the geometric TAZ -> census
        # commune assignment in the dest margin (build_dest_attraction_per_taz).
        context.stage("data.spatial.municipalities")


def _zone_to_kreis(series: pd.Series, lookup: dict | None = None) -> pd.Series:
    """Map a zone id to its 5-digit Kreis ARS. lookup is None -> legacy commune_id
    str[:5] (byte-identical). lookup given -> map each taz_id, raise on unmapped.

    On the ON path an unmapped taz_id is a TAZ coverage gap (the lookup must map
    every zone). Raise a descriptive ``RuntimeError`` naming the offending ids
    instead of letting a bare ``KeyError`` from the dict lookup bubble up, so the
    failure is actionable (consistent with the other explicit guards here and the
    no-silent-fallback contract)."""
    if lookup is None:
        return series.astype(str).str[:5]
    zones = series.astype(str)
    unmapped = sorted(set(zones) - set(lookup))
    if unmapped:
        raise RuntimeError(
            "%d zone id(s) have no Kreis in the taz->kreis lookup, e.g. %s "
            "(TAZ coverage gap; the lookup must map every taz_id)"
            % (len(unmapped), ", ".join(unmapped[:5]))
        )
    return zones.map(lookup)


def _gemeinde_to_kreis(series: pd.Series) -> pd.Series:
    """Backwards-compatible shim (tests/braunschweig/test_stages.py imports this)."""
    return _zone_to_kreis(series)


def _synthesise_intra_kreis(df_pendler: pd.DataFrame,
                            df_employment: pd.DataFrame,
                            scope: list[str]) -> pd.DataFrame:
    """Inject intra-Kreis SvB flows (``K -> K``) into the Pendler frame."""
    wohnort = (df_employment.groupby("departement_id")["weight"]
                            .sum()
                            .rename("svb_wohnort")
                            .reset_index()
                            .rename(columns={"departement_id": "kreis"}))
    wohnort["kreis"] = wohnort["kreis"].astype(str)
    wohnort = wohnort[wohnort["kreis"].isin(scope)]

    auspendler = (df_pendler[df_pendler["orig_ars"].isin(scope)]
                  .groupby("orig_ars")["flow"].sum()
                  .rename("auspendler")
                  .reset_index()
                  .rename(columns={"orig_ars": "kreis"}))

    merged = wohnort.merge(auspendler, on="kreis", how="left")
    merged["auspendler"] = merged["auspendler"].fillna(0)
    merged["intra"] = (merged["svb_wohnort"] - merged["auspendler"]).clip(lower=0)

    intra = pd.DataFrame({
        "orig_ars": merged["kreis"],
        "dest_ars": merged["kreis"],
        "flow": merged["intra"].astype(int),
    })

    print(
        "[braunschweig.gravity.model] synthesised intra-Kreis flows: "
        + ", ".join(f"{r.orig_ars}={int(r.flow):,}" for r in intra.itertuples())
    )
    return pd.concat([df_pendler, intra], ignore_index=True)


def _calibrate(df_od: pd.DataFrame,
               df_population: pd.DataFrame,
               df_pendler: pd.DataFrame,
               zone_to_kreis: dict | None = None,
               population_key: str = "commune_id",
               population_value: str = "weight") -> pd.DataFrame:
    """IPF-scale zone-level OD so Kreis aggregates match BA Pendler.

    OFF path (zone_to_kreis=None): uses legacy commune_id[:5] -> byte-identical.
    ON path (zone_to_kreis=dict): maps each taz_id via the lookup, raises if no
    in-scope flows are found after mapping (silent BA-skip guard).

    Parameters
    ----------
    df_od:
        Origin-destination frame with columns ``origin_id``, ``destination_id``, ``weight``.
    df_population:
        Population frame; grouped by ``population_key``, summed on ``population_value``.
    df_pendler:
        BA Pendleratlas Kreis-pair flows (columns ``orig_ars``, ``dest_ars``, ``flow``).
    zone_to_kreis:
        None -> legacy str[:5] mapping (OFF path). dict -> explicit taz_id->Kreis map (ON path).
    population_key:
        Column to group ``df_population`` by. OFF: ``"commune_id"``; ON: ``"taz_id"``.
    population_value:
        Column to sum from ``df_population``. OFF: ``"weight"``; ON: ``"population"``.
    """
    df = df_od.copy()
    df["orig_kreis"] = _zone_to_kreis(df["origin_id"], zone_to_kreis)
    df["dest_kreis"] = _zone_to_kreis(df["destination_id"], zone_to_kreis)

    pop = (
        df_population.groupby(population_key)[population_value].sum()
                     .rename("pop")
                     .reset_index()
                     .rename(columns={population_key: "origin_id"})
    )
    df = pd.merge(df, pop, on="origin_id", how="left")
    df["pop"] = df["pop"].fillna(0.0)
    df["flow"] = df["weight"] * df["pop"]

    obs = (
        df_pendler.rename(columns={
            "orig_ars": "orig_kreis",
            "dest_ars": "dest_kreis",
        })
        .groupby(["orig_kreis", "dest_kreis"])["flow"].sum()
        .rename("obs")
        .reset_index()
    )

    def kreis_flows(frame):
        return (
            frame.groupby(["orig_kreis", "dest_kreis"])["flow"].sum()
                 .rename("cur")
                 .reset_index()
        )

    scope_pairs = obs[["orig_kreis", "dest_kreis"]].drop_duplicates()
    df_scope = df.merge(scope_pairs, on=["orig_kreis", "dest_kreis"], how="inner")
    df_rest = df.merge(scope_pairs, on=["orig_kreis", "dest_kreis"],
                       how="left", indicator=True)
    df_rest = df_rest[df_rest["_merge"] == "left_only"].drop(columns=["_merge"])

    if len(df_scope) == 0:
        if zone_to_kreis is not None:
            raise RuntimeError(
                "[gravity TAZ] no in-scope flow after taz->kreis mapping; "
                "BA calibration would be silently skipped"
            )
        print("[braunschweig.gravity.model] no scope overlap; returning raw gravity")
        return df_od

    for it in range(MAX_IPF_ITERATIONS):
        cur = kreis_flows(df_scope)
        merged = cur.merge(obs, on=["orig_kreis", "dest_kreis"], how="inner")
        merged["ratio"] = np.where(
            merged["cur"] > 0, merged["obs"] / merged["cur"], 1.0
        )

        df_scope = df_scope.merge(
            merged[["orig_kreis", "dest_kreis", "ratio"]],
            on=["orig_kreis", "dest_kreis"], how="left",
        )
        df_scope["ratio"] = df_scope["ratio"].fillna(1.0)
        df_scope["flow"] = df_scope["flow"] * df_scope["ratio"]

        max_delta = float(np.abs(merged["ratio"] - 1.0).max())
        df_scope = df_scope.drop(columns=["ratio"])

        if max_delta < IPF_TOLERANCE:
            print(f"[braunschweig.gravity.model] IPF converged after {it+1} iter (delta={max_delta:.4g})")
            break
    else:
        print(f"[braunschweig.gravity.model] IPF stopped at {MAX_IPF_ITERATIONS} iter (delta={max_delta:.4g})")

    df_out = pd.concat([df_scope, df_rest], ignore_index=True)
    return df_out[["origin_id", "destination_id", "flow"]]


def _append_outbound_flows(df_od: pd.DataFrame,
                           df_population: pd.DataFrame,
                           df_pendler: pd.DataFrame,
                           df_external: pd.DataFrame,
                           scope: list[str],
                           zone_to_kreis: dict | None = None,
                           population_key: str = "commune_id",
                           population_value: str = "weight") -> pd.DataFrame:
    """Add rows ``(origin_zone, synthetic_external_commune, weight)``.

    Each outbound BA Kreis flow is split across the per-Gemeinde EXT points
    in ``df_external`` proportional to their ``employees`` share, so the
    emitted ``destination_id`` equals the per-Gemeinde ``commune_id``
    (``"EXT" + gem_ags``, 8-digit AGS).  This ensures exact-string matching
    against the work-pool ``commune_id`` in the downstream candidate sampler
    (``synthesis/population/spatial/primary/candidates.py``).

    Mass is conserved: for every (origin, Kreis) pair the sum of per-zone
    flows equals the original Kreis-level outbound flow.

    OFF path (zone_to_kreis=None): groups ``df_population`` by ``commune_id``,
    derives Kreis via ``str[:5]`` -- byte-identical to the prior behaviour.
    ON path (zone_to_kreis=dict): groups by ``taz_id`` (population_key), maps
    each taz_id to its Kreis via the lookup; origin_id in injected rows is the
    taz_id so the key space is consistent with the calibrated work OD.
    """
    ext_ars = set(df_external["ars5"].astype(str))
    df_out_pendler = df_pendler[
        df_pendler["orig_ars"].isin(scope)
        & df_pendler["dest_ars"].isin(ext_ars)
    ].copy()

    if zone_to_kreis is None:
        # OFF path: classic commune_id[:5] grouping and origin key.
        pop = (
            df_population.groupby("commune_id")["weight"].sum()
                         .rename("pop").reset_index()
        )
        pop["orig_ars"] = pop["commune_id"].astype(str).str[:5]
        pop["kreis_total"] = pop.groupby("orig_ars")["pop"].transform("sum")
        pop["share"] = np.where(pop["kreis_total"] > 0,
                                pop["pop"] / pop["kreis_total"], 0.0)
        pop = pop[pop["orig_ars"].isin(scope)]
        # origin_id for injected rows is the Gemeinde commune_id (OFF behaviour).
        pop_origin_col = "commune_id"
    else:
        # ON path: group by taz_id, map to Kreis via lookup; origin_id = taz_id.
        pop = (
            df_population.groupby(population_key)[population_value].sum()
                         .rename("pop").reset_index()
                         .rename(columns={population_key: "taz_id"})
        )
        pop["orig_ars"] = pop["taz_id"].astype(str).map(zone_to_kreis)
        if pop["orig_ars"].isna().any():
            missing_n = int(pop["orig_ars"].isna().sum())
            raise RuntimeError(
                "[gravity TAZ _append_outbound_flows] %d taz_id values have no "
                "Kreis mapping in zone_to_kreis; cannot build outbound shares" % missing_n
            )
        pop["kreis_total"] = pop.groupby("orig_ars")["pop"].transform("sum")
        pop["share"] = np.where(pop["kreis_total"] > 0,
                                pop["pop"] / pop["kreis_total"], 0.0)
        pop = pop[pop["orig_ars"].isin(scope)]
        pop_origin_col = "taz_id"

    # Build per-Gemeinde employee shares keyed by Kreis ars5, carrying commune_id.
    # gem_share = employees / Sigma_{Gemeinde in Kreis} employees.
    gem_emp = (
        df_external[["ars5", "commune_id", "employees"]]
        .copy()
        .astype({"ars5": str, "commune_id": str, "employees": float})
    )
    kreis_total_emp = gem_emp.groupby("ars5")["employees"].transform("sum")
    gem_emp["gem_share"] = np.where(
        kreis_total_emp > 0, gem_emp["employees"] / kreis_total_emp, 0.0
    )

    n_ext_rows = 0
    ext_svb = 0.0
    if df_out_pendler.empty:
        print("[braunschweig.gravity.model] no outbound flows to inject")
        df_all = df_od.copy()
    else:
        # origin_zone x dest_kreis rows, with each origin's flow share.
        df_inj = pop.merge(df_out_pendler, on="orig_ars", how="inner")
        df_inj["flow"] = df_inj["share"] * df_inj["flow"].astype(float)
        df_inj = df_inj[df_inj["flow"] > 0]
        # df_inj now has columns: <pop_origin_col>, orig_ars, dest_ars, flow (Kreis-level split).

        # Expand each Kreis-level flow to per-Gemeinde rows via the employee share.
        # Join on ars5 == dest_ars (many-to-many: one origin->Kreis row becomes N rows).
        df_inj = df_inj.merge(
            gem_emp[["ars5", "commune_id", "gem_share"]].rename(
                columns={"commune_id": "destination_id"}
            ),
            left_on="dest_ars",
            right_on="ars5",
            how="inner",
        )

        # Guard: warn if any Kreis present in outbound had no Gemeinde points.
        kreise_in_inj_before = set(df_out_pendler["dest_ars"].astype(str).unique())
        kreise_expanded = set(gem_emp["ars5"].unique())
        kreise_missing = kreise_in_inj_before - kreise_expanded
        if kreise_missing:
            print(
                "WARNING: [braunschweig.gravity.model] "
                f"{len(kreise_missing)} Kreis(e) in outbound have NO per-Gemeinde "
                f"EXT point in df_external and their flow is lost: "
                + ", ".join(sorted(kreise_missing))
            )

        df_inj["flow"] = df_inj["flow"] * df_inj["gem_share"]
        df_inj = df_inj[df_inj["flow"] > 0]
        df_inj = df_inj.rename(columns={pop_origin_col: "origin_id"})
        df_inj = df_inj[["origin_id", "destination_id", "flow"]]

        n_ext_rows = len(df_inj)
        ext_svb = float(df_inj["flow"].sum())

        df_all = pd.concat([df_od, df_inj], ignore_index=True)

    totals = df_all.groupby("origin_id")["flow"].sum().rename("total").reset_index()
    df_all = df_all.merge(totals, on="origin_id", how="left")
    f_missing = df_all["total"] <= 0.0
    # No-silent-fallback (CLAUDE.md): an origin with zero total flow (no BA-Pendler
    # row, no EXT injection) would otherwise divide 0/0 below; the self-loop is
    # forced to weight 1.0 instead. Count and log the rate so a systematically
    # empty outbound-flow set (e.g. a broken scope/ars join) is visible.
    n_missing_origins = int(df_all.loc[f_missing, "origin_id"].nunique())
    n_total_origins = int(df_all["origin_id"].nunique())
    if n_missing_origins:
        share = 100.0 * n_missing_origins / max(n_total_origins, 1)
        level = logger.warning if share > ZERO_TOTAL_SELF_LOOP_WARN_PERCENT else logger.info
        level(
            "[gravity] zero-total origins forced to self-loop: %d/%d (%.1f%%)",
            n_missing_origins, n_total_origins, share,
        )
    df_all.loc[f_missing & (df_all["origin_id"] == df_all["destination_id"]), "flow"] = 1.0
    df_all.loc[f_missing, "total"] = 1.0
    df_all["weight"] = df_all["flow"] / df_all["total"]

    if n_ext_rows:
        print(
            "[braunschweig.gravity.model] "
            f"injected {n_ext_rows:,} outbound rows (per-Gemeinde EXT destinations) "
            f"({ext_svb:,.0f} synthetic SvB distributed to external Gemeinden)"
        )
    return df_all[["origin_id", "destination_id", "weight"]]


def execute(context):
    # _execute_gravity_base returns a 4-tuple:
    # (work_od, education_od, pop_taz, df_work_production).
    # pop_taz is the TAZ origin-margin DataFrame (non-None only on the ON path);
    # it is threaded out here so execute() does not call build_origin_population_per_taz
    # a second time (the sjoin is expensive). df_work_production is the #132 work
    # production frame (non-None only when work_production_mass=svb_wohn); it is
    # threaded out so the SAME mass that seeded the gravity also seeds the
    # Kreis-IPF (_calibrate) and the outbound flows (_append_outbound_flows).
    df_work_od, df_education_od, pop_taz_from_base, df_work_production = \
        _execute_gravity_base(context)
    # data.census.filtered resolves to the configured population producer
    # (braunschweig.ipf.attributed in the legacy config -- unchanged behaviour --
    # or braunschweig.popsim.stage in the popsim configs), so the gravity weights
    # always come from the SAME population as the demand.
    df_population = context.stage("data.census.filtered")
    df_pendler = context.stage("braunschweig.data.census.pendler")
    df_employment = context.stage("braunschweig.data.census.employment")
    df_external = context.stage("braunschweig.data.external_workplaces")

    scope = [str(p) for p in context.config("braunschweig.political_prefix")]
    mask = df_pendler["orig_ars"].isin(scope) | df_pendler["dest_ars"].isin(scope)
    df_pendler = df_pendler[mask].copy()

    df_pendler = _synthesise_intra_kreis(df_pendler, df_employment, scope)

    # ExecuteContext.config() takes the key alone; the default is declared in configure().
    # Passing a default here raises "config() takes 2 positional arguments but 3 were given".
    taz_on = context.config("taz_work_location_choice")

    if taz_on:
        # ON path: reuse the TAZ population margin already computed by
        # _execute_gravity_base (pop_taz_from_base) -- no second sjoin needed.
        from braunschweig.gravity.taz_margins import taz_to_kreis_lookup  # noqa: PLC0415
        df_taz = context.stage("braunschweig.data.spatial.taz")
        zone_to_kreis = taz_to_kreis_lookup(df_taz)
        population_key = "taz_id"
        population_value = "population"
        # pop_taz schema: taz_id, commune_id, population -- the _calibrate and
        # _append_outbound_flows functions group by population_key so they receive
        # the correct per-TAZ margin.
        df_population_for_od = pop_taz_from_base
    else:
        # OFF path: defaults -> byte-identical behaviour.
        zone_to_kreis = None
        population_key = "commune_id"
        population_value = "weight"
        if df_work_production is None:
            df_population_for_od = df_population
        else:
            # #132: the SAME production mass that seeded the gravity must
            # seed the Kreis-IPF and the outbound flows (consistency across
            # all three mass entry points).
            df_population_for_od = df_work_production.rename(columns={
                "origin_id": "commune_id",
                "population": "weight",
            })

    print(
        "[braunschweig.gravity.model] calibrating {:,} zone-pairs "
        "against {:,} BA Kreis-pair flows".format(len(df_work_od), len(df_pendler))
    )

    df_work_calibrated = _calibrate(
        df_work_od, df_population_for_od, df_pendler,
        zone_to_kreis=zone_to_kreis,
        population_key=population_key,
        population_value=population_value,
    )
    # #193: inner VerBindungen anchor (flag-gated, default OFF). Runs on the
    # CALIBRATED OD so the outer Kreis anchor is already satisfied; the inner
    # step preserves every Kreis-pair block total exactly (asserted inside).
    if context.config("braunschweig.gravity.verbindungen_anchor_enabled"):
        from braunschweig.gravity.verbindungen_anchor import run_inner_anchor  # noqa: PLC0415
        df_cells_vb, df_cell_commune_vb = context.stage(
            "braunschweig.data.verbindungen.zones")
        df_ref_od_vb = context.stage("braunschweig.data.verbindungen.work_od")
        df_work_calibrated, _anchor_stats = run_inner_anchor(
            df_work_calibrated, df_cells_vb, df_cell_commune_vb, df_ref_od_vb,
            min_observed_commuters=context.config(
                "braunschweig.verbindungen.anchor_min_observed_commuters"),
        )
    df_work_extended = _append_outbound_flows(
        df_work_calibrated, df_population_for_od, df_pendler, df_external, scope,
        zone_to_kreis=zone_to_kreis,
        population_key=population_key,
        population_value=population_value,
    )

    return df_work_extended, df_education_od
