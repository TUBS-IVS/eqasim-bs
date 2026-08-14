"""Per-cell attribute joining and Kreis-code derivation for popsim_mid.

- :func:`join_cell_attributes` -- join the per-cell attributes (the 12-digit
  ARS and, when available, ``RegioStaR7``) from the loaded cells frame back
  onto the merged PopulationSim output (one row per synthetic household).
- :func:`derive_geo_kreis_from_ars` -- derive the 5-digit Kreis ARS from a
  (nominally) 12-digit cell ARS column, shared by every call site that needs
  the Kreis-level code so they cannot drift apart.

Extracted verbatim from the stage module (``__init__``); see the package
docstring for the stage-level context.
"""

import logging

import pandas as pd

from braunschweig.popsim import mid

logger = logging.getLogger(__name__)


def join_cell_attributes(
    combined: pd.DataFrame,
    cells: pd.DataFrame,
    *,
    ars_col: str = mid._ARS_COLUMN,
) -> pd.DataFrame:
    """Join the per-cell attributes onto the merged PopulationSim output.

    PopulationSim writes only ``ZENSUS100m`` + ``H_ID`` to its output CSV, so
    everything the downstream assembly needs per SYNTHETIC HOME cell must be
    recovered here from the loaded cells frame:

    - the 12-digit ARS (``RegionalSchlussel_ARS``): required by
      ``assembly.derive_zone_ids`` for commune_id / departement_id / iris_id
      (bug D1: spatial home.zones KeyError without it);
    - ``RegioStaR7`` (when the cells parquet carries it): the synthetic home's
      urban/rural class. ``assembly.build_persons`` expands it onto every
      synthetic person, where it serves as the SPATIAL stage-B matching key
      (``braunschweig.popsim.trips.MATCHED_REPLACEMENT_COLUMNS``). Note this is
      deliberately the CELL's RS7, not the MiD donor household's survey RS7:
      the donor frame's ``RegioStaR7`` is never merged onto persons (it is not
      part of ``assembly._HOUSEHOLD_ATTRS``), so no collision can occur.

    Parameters
    ----------
    combined:
        Merged PopulationSim output (one row per synthetic household, with
        ``ZENSUS100m``).
    cells:
        Loaded (ZGB-filtered) cells frame from ``mid.load_control_cells``.
    ars_col:
        Name of the 12-digit ARS column on the cells frame.

    Returns
    -------
    pandas.DataFrame
        ``combined`` with ``ars_col`` (always) and ``RegioStaR7`` (when
        available on ``cells``) joined per 100 m cell.
    """
    join_cols = ["ZENSUS100m", ars_col]
    has_rs7 = "RegioStaR7" in cells.columns
    if has_rs7:
        join_cols.append("RegioStaR7")
    else:
        logger.info(
            "[popsim.stage] cells frame carries no 'RegioStaR7' column (older "
            "parquet); synthetic persons get no home-cell RS7 and stage-B chain "
            "matching falls back to the non-spatial key list."
        )

    cell_attributes = cells[join_cols].drop_duplicates("ZENSUS100m")
    combined = combined.merge(cell_attributes, on="ZENSUS100m", how="left")

    n_missing_ars = int(combined[ars_col].isna().sum())
    if n_missing_ars:
        logger.warning(
            "[popsim.stage] %d/%d households could not be matched to an ARS after "
            "the cells join (unexpected; cells used in PopulationSim must be a subset "
            "of the loaded cells frame).",
            n_missing_ars, len(combined),
        )

    if has_rs7:
        n_missing_rs7 = int(combined["RegioStaR7"].isna().sum())
        logger.info(
            "[popsim.stage] cell RegioStaR7 joined onto %d households "
            "(%d missing -> NaN).",
            len(combined), n_missing_rs7,
        )

    return combined


def derive_geo_kreis_from_ars(ars: pd.Series) -> pd.Series:
    """Derive the 5-digit Kreis ARS from a (nominally) 12-digit cell ARS column.

    Zero-pads to the full 12-digit Regionalschluessel BEFORE slicing the first
    five digits: an ARS that lost a leading zero (e.g. round-tripped through an
    integer column) would otherwise truncate the wrong five characters and
    silently join to the wrong Kreis. Mirrors ``mid.filter_zgb_cells`` and
    ``assembly.derive_zone_ids``, which both ``zfill(12)`` before deriving the
    Kreis-level ARS -- kept as a single reusable helper so the three call sites
    cannot drift apart.
    """
    return ars.astype(str).str.zfill(12).str[:5]
