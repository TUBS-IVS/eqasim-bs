"""Shared INKAR-scaled income derivation for popsim producers.

Provides :func:`apply_inkar_income_eur`, which turns a per-person income-class
EUR midpoint Series into:

  - ``household_income_eur`` = income_class_midpoint_EUR * INKAR_scale[Kreis]
  - ``high_income``           = (household_income_eur >= HIGH_INCOME_THRESHOLD_EUR)

This replicates the formula used by the IPF / enriched path
(:func:`braunschweig.synthesis.population.enriched._apply_inkar_income_scale`)
for the two popsim producers (popsim_mid and popsim_open) so all three
producers produce comparable, Kreis-adjusted income values.

The IPF / enriched income code is NOT touched; this module is popsim-only.

INKAR scale source
------------------
``braunschweig.data.inkar.household_income`` returns a DataFrame with columns:
    ars5 (str, 5-digit Kreis Kennziffer)
    einkommen_eur (float, average monthly HH income per inhabitant)
    scale (float, einkommen_eur / national_mean)

The join key is ``departement_id`` (= first 5 chars of commune_id = Kreis-5 code,
derived from the 12-digit ARS by :func:`braunschweig.popsim.assembly.derive_zone_ids`).

Fallback transparency
---------------------
Persons whose Kreis is absent from the INKAR frame receive scale=1.0 (midpoint
unchanged). The primary/fallback counts are logged and stored in ``out.attrs``
so callers and tests can assert the primary path is taken on representative data
(CLAUDE.md no-silent-fallback rule).

ENTD income class midpoints
---------------------------
:data:`ENTD_INCOME_CLASS_MIDPOINT_EUR` maps ENTD ``income_class`` (0..13) to EUR
midpoints. The ENTD bands come from :mod:`data.hts.entd.cleaned`
``INCOME_CLASS_BOUNDS = [400, 600, 800, 1000, 1200, 1500, 1800, 2000, 2500, 3000,
4000, 6000, 10000, 1e6]``. Midpoints are the arithmetic midpoint of each band;
the open top band (>=10000, class 13) uses a conservative estimate of 12000.0 EUR.
Class -1 (missing / not reported) has midpoint ``None`` and is treated as a
structural fallback (scale=1.0 applied to 0.0 -> household_income_eur=0.0, logged).
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared threshold
# ---------------------------------------------------------------------------

# Monthly household net income threshold (EUR) above which high_income=True.
# Applies uniformly to all popsim sources.
# Semantically: households above ~5000 EUR/month net income are 'high income'
# for the eqasim mode-choice income-elastic models.
# This replaces:
#   - popsim_mid: high_income = (household_income == "over_7000")    [MiD label check]
#   - popsim_open: high_income = (income_class >= 13)                [ENTD band check]
# A numeric EUR threshold makes both comparable and consistent.
HIGH_INCOME_THRESHOLD_EUR: float = 5000.0

# ---------------------------------------------------------------------------
# ENTD income class (0..13) -> EUR midpoint
# ---------------------------------------------------------------------------

# ENTD INCOME_CLASS_BOUNDS from data/hts/entd/cleaned.py:
#   [400, 600, 800, 1000, 1200, 1500, 1800, 2000, 2500, 3000, 4000, 6000, 10000, 1e6]
# Each class i covers the range [BOUNDS[i-1], BOUNDS[i]) for i>0;
#   class 0 covers [0, 400).
# Midpoints are arithmetic midpoints; class 13 (>=10000) uses a conservative
# open-ended estimate of 12000.0 EUR.
# Class -1 is the missing / not-reported code in cleaned.py.
ENTD_INCOME_CLASS_MIDPOINT_EUR: dict[int, Optional[float]] = {
    -1: None,      # missing / not reported -> structural NaN
    0:   200.0,    # <400 EUR
    1:   500.0,    # 400-600 EUR
    2:   700.0,    # 600-800 EUR
    3:   900.0,    # 800-1000 EUR
    4:  1100.0,    # 1000-1200 EUR
    5:  1350.0,    # 1200-1500 EUR
    6:  1650.0,    # 1500-1800 EUR
    7:  1900.0,    # 1800-2000 EUR
    8:  2250.0,    # 2000-2500 EUR
    9:  2750.0,    # 2500-3000 EUR
    10: 3500.0,    # 3000-4000 EUR
    11: 5000.0,    # 4000-6000 EUR (midpoint 5000)
    12: 8000.0,    # 6000-10000 EUR
    13: 12000.0,   # >=10000 EUR (open-ended, conservative estimate)
}

# ---------------------------------------------------------------------------
# Fallback transparency constants
# ---------------------------------------------------------------------------

# INKAR fallback rate above which a WARNING is emitted (fraction in [0,1]).
# Zero: even a single missing Kreis is unexpected on correct data (all 8 ZGB
# Kreise should be present in the INKAR file), so flag it immediately.
_INKAR_FALLBACK_WARN_RATE: float = 0.0


def apply_inkar_income_eur(
    persons: pd.DataFrame,
    inkar_scale: pd.DataFrame,
    *,
    midpoint_series: pd.Series,
    kreis_col: str = "departement_id",
    high_income_threshold_eur: float = HIGH_INCOME_THRESHOLD_EUR,
) -> pd.DataFrame:
    """Apply INKAR per-Kreis income scaling and set high_income.

    Replicates the enriched-path formula
    ``braunschweig.synthesis.population.enriched._apply_inkar_income_scale``:

        household_income_eur = midpoint_series * INKAR_scale[Kreis]
        high_income          = (household_income_eur >= high_income_threshold_eur)

    Parameters
    ----------
    persons:
        DataFrame with at least ``kreis_col`` (5-digit Kreis ARS-5 string,
        = ``departement_id``). The result is written back onto a copy.
    inkar_scale:
        Per-Kreis INKAR scale frame from
        ``braunschweig.data.inkar.household_income``.  Must carry columns
        ``ars5`` (str) and ``scale`` (float).  When ``None``, scale=1.0 is used
        for all persons and the absence is logged at INFO level (unit-test mode).
    midpoint_series:
        Per-person EUR midpoint Series aligned to ``persons.index``.  This is
        the raw income-class midpoint for each person; INKAR multiplies it.
        Persons with a ``None`` / NaN midpoint receive ``household_income_eur``
        = NaN and ``high_income`` = False.
    kreis_col:
        Column name in ``persons`` that carries the 5-digit Kreis ARS-5 code
        (default ``"departement_id"``; produced by
        :func:`braunschweig.popsim.assembly.derive_zone_ids`).
    high_income_threshold_eur:
        EUR/month threshold for ``high_income`` (default
        :data:`HIGH_INCOME_THRESHOLD_EUR` = 5000.0).

    Returns
    -------
    pandas.DataFrame
        A copy of ``persons`` with ``household_income_eur`` (float) and
        ``high_income`` (bool) set (both overwrite any pre-existing values).
        ``out.attrs`` carries:
          ``inkar_primary_count``  (int)
          ``inkar_fallback_count`` (int)
          ``inkar_fallback_rate``  (float)
        for test introspection (CLAUDE.md no-silent-fallback).

    Notes
    -----
    The rounding formula ``round(0)`` (round to nearest integer EUR) matches
    the IPF path (enriched.py line 2689).
    """
    out = persons.copy()
    n_total = len(out)

    # --- Build per-person INKAR scale vector --------------------------------
    if inkar_scale is None or len(inkar_scale) == 0:
        # No INKAR data provided: use scale=1.0 for all persons (unit-test mode
        # or pipeline stage called before INKAR is wired). This is a known
        # intentional absent-data path, not a surprise fallback.
        scale = pd.Series(1.0, index=out.index)
        n_fallback = n_total
        n_primary = 0
        logger.info(
            "[braunschweig.popsim.income] INKAR scale absent (None or empty); "
            "using scale=1.0 for all %d persons (pure midpoint mode). "
            "Wire braunschweig.data.inkar.household_income in stage.py for "
            "per-Kreis scaling.",
            n_total,
        )
    else:
        scale_lookup = dict(zip(inkar_scale["ars5"], inkar_scale["scale"]))
        kreis = out[kreis_col].astype(str)
        scale = kreis.map(scale_lookup)

        fallback_mask = scale.isna()
        n_fallback = int(fallback_mask.sum())
        n_primary = n_total - n_fallback

        # Fill missing Kreise with 1.0 (midpoint unchanged) — documented fallback.
        scale = scale.fillna(1.0).astype(float)

        fallback_rate = (n_fallback / n_total) if n_total else 0.0

        # Store on attrs for test introspection (CLAUDE.md no-silent-fallback).
        out.attrs["inkar_primary_count"] = n_primary
        out.attrs["inkar_fallback_count"] = n_fallback
        out.attrs["inkar_fallback_rate"] = fallback_rate

        if n_fallback == 0:
            logger.info(
                "[braunschweig.popsim.income] INKAR income scale: "
                "primary (Kreis lookup) %d/%d (100.00%%), fallback 0.",
                n_primary, n_total,
            )
        else:
            missing_kreise = sorted(
                kreis[fallback_mask].unique().tolist()
            )
            level = "WARNING: " if fallback_rate > _INKAR_FALLBACK_WARN_RATE else ""
            logger.warning(
                "[braunschweig.popsim.income] %sINKAR income scale fallback: "
                "primary %d/%d (%.1f%%), fallback %d (%.1f%%) with scale=1.0. "
                "Missing Kreise: %s. Check that ars5 keys match departement_id format.",
                level,
                n_primary, n_total, 100.0 * n_primary / max(n_total, 1),
                n_fallback, 100.0 * fallback_rate,
                missing_kreise,
            )

    # --- Compute household_income_eur ----------------------------------------
    # midpoint_series may contain None (ENTD class -1 = missing income; MiD
    # map_household_income_eur default=None). NaN midpoints are intentionally
    # PRESERVED as NaN in household_income_eur — the caller (income_spatial_tilt)
    # shields these rows from the tilt, and high_income uses .fillna(0.0) below so
    # those households are correctly classified False.  Do NOT fill NaN to 0.0 here.
    midpoint_float = pd.to_numeric(midpoint_series, errors="coerce").reindex(out.index)
    # Round to nearest integer EUR (matches enriched.py line 2689).
    out["household_income_eur"] = (midpoint_float * scale).round(0)

    # --- Derive high_income from the numeric EUR value -----------------------
    # Persons with NaN household_income_eur (missing income class) get False.
    out["high_income"] = (
        out["household_income_eur"].fillna(0.0) >= high_income_threshold_eur
    ).astype(bool)

    return out
