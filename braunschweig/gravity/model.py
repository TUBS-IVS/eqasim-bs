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

import os

import numpy as np
import pandas as pd


# --- Inherited from eqasim-bavaria -----------------------------------------
# Gemeinde x Gemeinde gravity model with IDF-derived defaults.

# Defaults: -0.09 came from IDF, value -2.0 has been calibrated.
DEFAULT_SLOPE = -0.2
DEFAULT_CONSTANT = -2.4
DEFAULT_DIAGONAL = 1.0


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
    mask_krfr = (df["ags"].str.len() == 5) & df["ags"].isin(scope_kreise)
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


def _execute_gravity_base(context):
    """Run the bavaria-style Gemeinde x Gemeinde gravity model.

    Returns ``(df_work_od, df_education_od)`` of row-normalised
    conditional probabilities.
    """
    df_distances = context.stage("eqasim_common.gravity.distance_matrix")
    # data.census.filtered resolves to the configured population producer
    # (braunschweig.ipf.attributed in the legacy config -- unchanged behaviour --
    # or braunschweig.popsim.stage in the popsim configs), so the gravity weights
    # always come from the SAME population as the demand.
    df_population = context.stage("data.census.filtered")
    df_employees = context.stage("braunschweig.data.census.employees")
    df_regiostar = context.stage("braunschweig.data.bbsr.regiostar")

    # Sector-aware destination attraction (flag-gated; OFF -> byte-identical).
    # Tilts the per-Gemeinde ``employees`` attraction by establishment density
    # while preserving Kreis totals (see ``apply_sector_aware_attraction``).
    # synpp's ExecuteContext.config() takes only the key (no default argument);
    # the default False is declared in configure(). Passing a default here raises
    # "config() takes 2 positional arguments but 3 were given" and aborts the run.
    if context.config("braunschweig.gravity.sector_aware_enabled"):
        df_betriebe = _read_betriebe_per_commune(context)
        df_employees = apply_sector_aware_attraction(
            df_employees, df_betriebe, enabled=True,
        )

    df_population = df_population.rename(columns={
        "commune_id": "origin_id",
        "weight": "population",
    })[["origin_id", "population"]]

    df_employees = df_employees.rename(columns={
        "commune_id": "destination_id",
        "weight": "employees",
    })[["destination_id", "employees"]]

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

    slope = context.config("gravity_slope")
    constant = context.config("gravity_constant")
    diagonal = context.config("gravity_diagonal")
    slope_overrides = context.config("gravity_slope_by_regiostar7")

    # Per-origin slope: defaults to scalar ``slope`` for every Gemeinde.
    # When ``gravity_slope_by_regiostar7`` is non-empty, origins whose
    # RegioStaR-7 code matches an override key receive that slope; the
    # friction matrix becomes ``exp(slope_vec[:, None] * distances + c)``
    # so each row (origin Gemeinde) decays at its own urban/rural rate.
    slope_vec = _build_origin_slope_vector(
        municipalities, slope, slope_overrides, df_regiostar,
    )

    friction = (
        np.exp(slope_vec[:, None] * distances + constant)
        + np.eye(len(municipalities)) * diagonal
    )
    # ExecuteContext.config() takes the key alone (the default is declared in
    # configure()); passing a default here would raise.
    max_iterations = context.config("gravity_max_iterations")
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
    df_matrix.loc[
        f_missing_total & (df_matrix["origin_id"] == df_matrix["destination_id"]),
        "weight",
    ] = 1.0
    df_matrix.loc[f_missing_total, "total"] = 1.0

    df_matrix["weight"] = df_matrix["weight"] / df_matrix["total"]
    df_matrix = df_matrix[["origin_id", "destination_id", "weight"]]

    return df_matrix, df_matrix


# --- Braunschweig-specific -------------------------------------------------
# BA-Pendleratlas calibration: IPF the Gemeinde-level OD so Kreis aggregates
# match observed SvB flows; inject ZGB -> external Kreis outbound rows.

# IPF convergence parameters for the Kreis-level calibration step.
MAX_IPF_ITERATIONS = 20
IPF_TOLERANCE = 1e-3


def configure(context):
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


def _gemeinde_to_kreis(series: pd.Series) -> pd.Series:
    """Strip a commune_id (8-digit AGS) down to a 5-digit Kreis ARS."""
    return series.astype(str).str[:5]


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
               df_pendler: pd.DataFrame) -> pd.DataFrame:
    """IPF-scale Gemeinde-level OD so Kreis aggregates match BA Pendler."""
    df = df_od.copy()
    df["orig_kreis"] = _gemeinde_to_kreis(df["origin_id"])
    df["dest_kreis"] = _gemeinde_to_kreis(df["destination_id"])

    pop = (
        df_population.groupby("commune_id")["weight"].sum()
                     .rename("pop")
                     .reset_index()
                     .rename(columns={"commune_id": "origin_id"})
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
                           scope: list[str]) -> pd.DataFrame:
    """Add rows ``(origin_gemeinde, synthetic_external_commune, weight)``.

    Each outbound BA Kreis flow is split across the per-Gemeinde EXT points
    in ``df_external`` proportional to their ``employees`` share, so the
    emitted ``destination_id`` equals the per-Gemeinde ``commune_id``
    (``"EXT" + gem_ags``, 8-digit AGS).  This ensures exact-string matching
    against the work-pool ``commune_id`` in the downstream candidate sampler
    (``synthesis/population/spatial/primary/candidates.py``).

    Mass is conserved: for every (origin, Kreis) pair the sum of per-Gemeinde
    flows equals the original Kreis-level outbound flow.
    """
    ext_ars = set(df_external["ars5"].astype(str))
    df_out_pendler = df_pendler[
        df_pendler["orig_ars"].isin(scope)
        & df_pendler["dest_ars"].isin(ext_ars)
    ].copy()

    pop = (
        df_population.groupby("commune_id")["weight"].sum()
                     .rename("pop").reset_index()
    )
    pop["orig_ars"] = pop["commune_id"].astype(str).str[:5]
    pop["kreis_total"] = pop.groupby("orig_ars")["pop"].transform("sum")
    pop["share"] = np.where(pop["kreis_total"] > 0,
                            pop["pop"] / pop["kreis_total"], 0.0)
    pop = pop[pop["orig_ars"].isin(scope)]

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
        # origin_gemeinde x dest_kreis rows, with each origin's flow share.
        df_inj = pop.merge(df_out_pendler, on="orig_ars", how="inner")
        df_inj["flow"] = df_inj["share"] * df_inj["flow"].astype(float)
        df_inj = df_inj[df_inj["flow"] > 0]
        # df_inj now has columns: commune_id, orig_ars, dest_ars, flow (Kreis-level split).

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
        df_inj = df_inj.rename(columns={"commune_id": "origin_id"})
        df_inj = df_inj[["origin_id", "destination_id", "flow"]]

        n_ext_rows = len(df_inj)
        ext_svb = float(df_inj["flow"].sum())

        df_all = pd.concat([df_od, df_inj], ignore_index=True)

    totals = df_all.groupby("origin_id")["flow"].sum().rename("total").reset_index()
    df_all = df_all.merge(totals, on="origin_id", how="left")
    f_missing = df_all["total"] <= 0.0
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
    df_work_od, df_education_od = _execute_gravity_base(context)
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

    print(
        "[braunschweig.gravity.model] calibrating {:,} Gemeinde-pairs "
        "against {:,} BA Kreis-pair flows".format(len(df_work_od), len(df_pendler))
    )

    df_work_calibrated = _calibrate(df_work_od, df_population, df_pendler)
    df_work_extended = _append_outbound_flows(
        df_work_calibrated, df_population, df_pendler, df_external, scope,
    )

    return df_work_extended, df_education_od
