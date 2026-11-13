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
from braunschweig.popsim.fallback_rates import log_fallback_rate

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

    # Every cell PopulationSim placed a household in comes from the loaded cells frame, so a
    # household without an ARS after the join is a broken input, not a fallback. It used to
    # be only warned about and then derived the placeholder Kreis "00000" (audit P3-F5).
    missing_ars = combined[ars_col].isna()
    if missing_ars.any():
        examples = combined.loc[missing_ars, "ZENSUS100m"].astype(str).unique()[:5].tolist()
        raise ValueError(
            f"[popsim.stage] {int(missing_ars.sum())}/{len(combined)} households have no cell ARS "
            f"({ars_col!r}) after the cells join (example cells: {examples}). The cells used in "
            "PopulationSim must be a subset of the loaded cells frame; check that the PopulationSim "
            "work directory was built from the same cells parquet."
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

    Raises ``ValueError`` on a missing or malformed ARS (audit P3-F5): a missing value used to
    become the string "nan" and the placeholder Kreis "00000", and a float-typed column (what
    pandas makes of an integer column with one missing value) stringified valid rows as
    "31010000000.0" and derived a WRONG Kreis. Neither can come from the ZGB-filtered cells
    frame (``mid.filter_zgb_cells`` keeps only cells of an in-scope Kreis), so both are broken
    input, not a fallback.
    """
    raw = ars.astype(str)
    malformed = ars.isna() | ~raw.str.fullmatch(r"\d{1,12}")
    if malformed.any():
        examples = raw[malformed].unique()[:5].tolist()
        raise ValueError(
            f"[popsim.stage.geo_kreis] {int(malformed.sum())}/{len(ars)} cell ARS values are missing "
            f"or not a 1-12 digit Regionalschluessel (examples: {examples}); cannot derive the Kreis. "
            "Load the ARS column as a string (a float column means a missing value upstream)."
        )
    kreis = raw.str.zfill(12).str[:5]
    log_fallback_rate(
        logger, "popsim.stage.geo_kreis", int((kreis == "00000").sum()), len(kreis),
        what='rows derive the Kreis "00000", which is no real Kreis', threshold=0.0,
    )
    return kreis
