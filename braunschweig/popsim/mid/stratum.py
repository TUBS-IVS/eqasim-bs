"""RegioStaR donor stratification helpers for the popsim mid stage (Phase 4B).

- ``dominant_stratum_for_1km``  -- majority-vote dominant RegioStaR stratum per
                                   1 km parent cell, plus the border approximation rate
- ``filter_seed_to_stratum``    -- filter the donor seed to households matching one
                                   stratum value

Extracted verbatim from the stage module (``__init__``); see the package
docstring for the stage-level context.
"""

from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Donor stratification helpers (Phase 4B)
# --------------------------------------------------------------------------- #

def dominant_stratum_for_1km(
    cells: pd.DataFrame,
    source,
) -> tuple[dict, float]:
    """Compute the dominant stratum per 1 km parent cell by majority vote.

    Each 100 m cell contributes its source-specific stratum label (via
    ``source.cell_stratum``).  The dominant stratum for a 1 km parent is the
    most-frequent label among its 100 m children.  Ties are broken by the
    smallest label (sort-stable).

    A 1 km cell that straddles a Gemeinde or RegioStaR boundary is assigned
    the dominant stratum of its 100 m children; the fraction of children whose
    stratum differs from their 1 km parent's dominant is the **border
    approximation rate** (logged by the caller; returned here for transparency).

    Parameters
    ----------
    cells:
        100 m cells frame.  Must carry ``ZENSUS1km`` and ``RegioStaR7`` (or
        whatever column the source's ``cell_stratum`` reads).
    source:
        Active :class:`braunschweig.popsim.sources.base.PopsimSource` instance.
        Provides :meth:`cell_stratum` to map per-cell RS7 codes to stratum labels.

    Returns
    -------
    tuple[dict[str, Any], float]
        ``(dominant_map, border_rate)`` where:
        - ``dominant_map`` maps each ``ZENSUS1km`` id to its dominant stratum.
        - ``border_rate`` is the fraction of 100 m cells whose stratum differs
          from their 1 km parent's dominant stratum (0.0 = all cells homogeneous).
    """
    cells_work = cells[["ZENSUS1km", "ZENSUS100m"]].copy()
    cells_work["_stratum"] = source.cell_stratum(cells).values

    # Count stratum occurrences per 1 km parent.
    grouped = cells_work.groupby(["ZENSUS1km", "_stratum"], sort=True).size()
    # For each 1 km parent, pick the stratum with the highest count; stable sort
    # means ties resolve to the smallest label alphabetically / numerically.
    dominant_series = grouped.groupby(level="ZENSUS1km").idxmax()
    # idxmax returns (ZENSUS1km, _stratum) tuples as the value; extract stratum.
    dominant_map = {km: idx[1] for km, idx in dominant_series.items()}

    # Compute border rate: fraction of 100m cells whose stratum != parent dominant.
    cells_work["_dominant"] = cells_work["ZENSUS1km"].map(dominant_map)
    n_total = len(cells_work)
    n_border = int((cells_work["_stratum"] != cells_work["_dominant"]).sum())
    border_rate = n_border / n_total if n_total > 0 else 0.0

    return dominant_map, border_rate


def filter_seed_to_stratum(
    seed_households: pd.DataFrame,
    seed_persons: pd.DataFrame,
    stratum_value,
    source,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Filter the donor seed to households matching a single stratum value.

    Retains only the households whose :meth:`source.donor_stratum` equals
    ``stratum_value``, then filters ``seed_persons`` to those household ids.
    The join key is ``source.seed_columns().household_id`` for the MiD path
    (``"H_ID"``); for ENTD it is ``"household_id"``.

    Parameters
    ----------
    seed_households:
        Full donor household frame (returned by the source's ``load_donor``).
    seed_persons:
        Full donor person frame.
    stratum_value:
        The stratum label to retain (e.g. RS7 code ``72`` for MiD, or ``"urban"``
        for ENTD).
    source:
        Active :class:`braunschweig.popsim.sources.base.PopsimSource` instance.

    Returns
    -------
    tuple[pd.DataFrame, pd.DataFrame]
        ``(filtered_households, filtered_persons)`` retaining only the matching
        stratum.

    Raises
    ------
    ValueError
        If no households match ``stratum_value`` (zero-donor guard: the caller
        MUST NOT assemble a PopulationSim batch with an empty seed).
    """
    stratum_series = source.donor_stratum(seed_households)
    mask = stratum_series == stratum_value
    filtered_hh = seed_households[mask].copy()

    if len(filtered_hh) == 0:
        raise ValueError(
            f"[popsim.mid] Donor stratification: no donor households found for "
            f"stratum '{stratum_value}'. "
            f"The donor seed contains strata: {sorted(stratum_series.unique())}. "
            f"To disable stratification set "
            f"'braunschweig.population.popsim.stratify_regiostar' to False."
        )

    # Determine the household-id join column for this source.
    # For MiD, seed_columns().household_id = "H_ID" (matches the post-load_mid_seed frame).
    # For ENTD, build_seed() renames household_id -> "H_ID"; the post-build_seed frame
    # therefore also uses "H_ID".  EntdSource.built_seed_columns() exposes this.
    # Strategy: try built_seed_columns() first (post-build_seed path where column name
    # is H_ID for both sources); if the reported column is not present in the frame,
    # fall back to seed_columns().  This handles tests that pass pre-build_seed ENTD
    # frames directly (those still carry "household_id", not "H_ID").
    _built_cols = getattr(source, "built_seed_columns", None)
    _fallback_cols = source.seed_columns()
    if _built_cols is not None:
        _preferred_col = _built_cols().household_id
        _preferred_p_col = _built_cols().person_household_id
    else:
        _preferred_col = _fallback_cols.household_id
        _preferred_p_col = _fallback_cols.person_household_id

    if _preferred_col in filtered_hh.columns:
        hh_id_col = _preferred_col
        person_hh_id_col = _preferred_p_col
    else:
        # Frame does not carry the built-seed column (pre-build_seed test path).
        hh_id_col = _fallback_cols.household_id
        person_hh_id_col = _fallback_cols.person_household_id
        logger.debug(
            "[popsim.mid] filter_seed_to_stratum: preferred hh_id column %r not found "
            "in seed frame; using fallback %r (likely a pre-build_seed test fixture).",
            _preferred_col, hh_id_col,
        )

    retained_hids = set(filtered_hh[hh_id_col])
    filtered_persons = seed_persons[
        seed_persons[person_hh_id_col].isin(retained_hids)
    ].copy()

    return filtered_hh, filtered_persons
