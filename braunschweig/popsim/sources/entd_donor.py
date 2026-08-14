"""ENTD donor loading and stratum derivation.

- :func:`load_donor`: loads ``(households, persons, trips)`` from ENTD parquet
  files or injected frames (the popsim_open stage always injects the frames;
  tests always inject too, so the filesystem path only matters for real runs).
- :func:`donor_stratum`: the per-household RS2 stratum label used for donor
  stratification, derived from the ENTD ``urban_type`` UU2010 field.
- :func:`cell_stratum`: the per-100m-cell RS2 stratum label, derived from the
  cell's ``RegioStaR7`` code, in the same label space as :func:`donor_stratum`.

Extracted verbatim from ``braunschweig.popsim.sources.entd`` (issue #267);
``entd.py`` re-exports the name so external imports of the facade module are
unaffected. ``EntdSource.load_donor``, ``EntdSource.donor_stratum`` and
``EntdSource.cell_stratum`` are one-line delegations to the module-level
functions here (``EntdSource`` has no instance state, so ``self`` carried
nothing the moved bodies needed).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Tuple, Union

import pandas as pd

from braunschweig.popsim.sources.entd_schema import entd_persons_to_donor_schema
from braunschweig.popsim.stratum import cell_urban_class_from_rs7, entd_urban_class

# Logger name string identical to the facade's (braunschweig.popsim.sources.entd)
# so log records emitted from here are indistinguishable from records emitted
# before the extraction; logging.getLogger caches by name, so this returns the
# SAME logger object as the facade's `logging.getLogger(__name__)`.
logger = logging.getLogger("braunschweig.popsim.sources.entd")


def load_donor(
    data_dir: Union[str, Path],
    *,
    injected: Optional[Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Load (households, persons, trips) from ENTD parquet files or injected frames.

    Parameters
    ----------
    data_dir:
        Directory containing ``entd_households.parquet``, ``entd_persons.parquet``,
        and ``entd_trips.parquet``.  Ignored when ``injected`` is provided.
    injected:
        Optional pre-loaded ``(households, persons, trips)`` tuple.  When provided
        the filesystem is not accessed.  The popsim_open stage injects the frames
        directly (they are produced by the upstream ``data.hts.entd.cleaned``
        synpp stage).  Tests always use injection.

    Returns
    -------
    tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]
        ``(households, persons, trips)`` donor tables.

    Raises
    ------
    FileNotFoundError
        If a parquet file is absent (only when not injected).
    """
    if injected is not None:
        households, persons, trips = injected
        persons = entd_persons_to_donor_schema(persons)
        logger.info(
            "[EntdSource] using injected donor frames: "
            "%d households, %d persons, %d trips (persons mapped to donor schema "
            "H_ID/P_ID/HP_ALTER/HP_SEX)",
            len(households), len(persons), len(trips),
        )
        return households, persons, trips

    data_dir = Path(data_dir)
    hh_path = data_dir / "entd_households.parquet"
    p_path = data_dir / "entd_persons.parquet"
    t_path = data_dir / "entd_trips.parquet"
    for path in (hh_path, p_path, t_path):
        if not path.is_file():
            raise FileNotFoundError(
                f"[EntdSource] ENTD parquet file not found: {path}. "
                "Run the popsim_open export step or pass injected frames."
            )
    households = pd.read_parquet(hh_path)
    persons = pd.read_parquet(p_path)
    trips = pd.read_parquet(t_path)
    persons = entd_persons_to_donor_schema(persons)
    logger.info(
        "[EntdSource] loaded donor: %d households, %d persons, %d trips from %s "
        "(persons mapped to donor schema H_ID/P_ID/HP_ALTER/HP_SEX)",
        len(households), len(persons), len(trips), data_dir,
    )
    return households, persons, trips


def donor_stratum(seed_households: pd.DataFrame) -> pd.Series:
    """Return the per-household stratum label for donor stratification.

    For ENTD, the stratum is the RegioStaR-2 binary label (``"urban"`` /
    ``"rural"``), derived from ``urban_type`` via
    :func:`braunschweig.popsim.stratum.entd_urban_class`.  ENTD donors do not
    carry a native RegioStaR-7 code; the UU2010 ``urban_type`` field is the
    closest available urbanity indicator and collapses cleanly to the RS2
    binary (see CLAUDE.md and ``braunschweig.popsim.stratum`` module docs).

    Parameters
    ----------
    seed_households:
        ENTD seed household frame.  Must carry ``urban_type`` (the UU2010
        category string, loaded into households via ``_HH_JOIN_COLS``).

    Returns
    -------
    pd.Series
        RS2 label string (``"urban"`` or ``"rural"``) per household row,
        same index as ``seed_households``.

    Raises
    ------
    KeyError
        If ``urban_type`` is absent from ``seed_households``.
    """
    return seed_households["urban_type"].map(entd_urban_class)


def cell_stratum(cells: pd.DataFrame) -> pd.Series:
    """Return the per-100m-cell stratum label for donor stratification.

    For ENTD, the cell stratum is the RS2 binary label derived from the
    cell's ``RegioStaR7`` code via
    :func:`braunschweig.popsim.stratum.cell_urban_class_from_rs7`.  This
    gives the same label space as :meth:`donor_stratum` so cells and donors
    can be matched by a simple equality check.

    Parameters
    ----------
    cells:
        Cells frame.  Must carry ``RegioStaR7``.

    Returns
    -------
    pd.Series
        RS2 label string per cell row, same index as ``cells``.

    Raises
    ------
    KeyError
        If ``RegioStaR7`` is absent from ``cells``.
    ValueError
        If a RS7 code is outside 71-77 (delegates to
        :func:`braunschweig.data.bbsr.regiostar.regiostar2_label`).
    """
    return cells["RegioStaR7"].map(cell_urban_class_from_rs7)
