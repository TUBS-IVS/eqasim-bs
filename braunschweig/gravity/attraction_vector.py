"""Destination attraction vector for the work-flow gravity model.

The gravity balancing consumes three per-zone quantities: the production mass
(``braunschweig.gravity.production_mass``), the friction matrix
(``braunschweig.gravity.friction``) and the destination ATTRACTION vector built
here -- the employees-at-workplace headcount per Gemeinde, optionally tilted by
the per-Gemeinde establishment density (see the block comment below).

Extracted verbatim from ``braunschweig.gravity.model`` (issue #267 split): the
functions, their signatures, their arithmetic and their log lines -- including
the ``[braunschweig.gravity.model]`` message prefixes -- are unchanged, so the
model output and the console log are byte-identical to the pre-split stage.
The prefixes deliberately still read ``model`` because they identify the STAGE
that emits them, not the file that hosts the code.

``braunschweig.gravity.model`` re-exports every public name defined here, so
existing imports of the stage module path keep working. This module must NEVER
depend on ``braunschweig.gravity.model`` in any direction other than downward
(that would close an import cycle): the dependency runs strictly
model -> attraction_vector.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


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
