"""Tier-3 KREIS control tables and per-batch Kreis apportionment for the popsim mid stage.

- ``merge_kreis_control_tables``     -- merge the cleancensus per-topic kreis_* tables
                                         into one, keyed by ``ARS_kreis``
- ``load_kreis_control_table``       -- load + merge the imported Tier-3 kreis_*
                                         parquets from disk
- ``resolved_kreis_per_cell``        -- per-cell RESOLVED dominant Kreis (one
                                         dominant Kreis per 1 km parent)
- ``_kreis_pop_from_crosswalk``      -- sum a weight column per RESOLVED dominant
                                         Kreis for a subset of cells
- ``_batch_kreis_apportion_weights`` -- this batch's population share of each Kreis
                                         (for KREIS-marginal apportionment)

Extracted verbatim from the stage module (``__init__``); see the package
docstring for the stage-level context.

``_KREIS_CONTROL_FILES`` (the three cleancensus kreis_* parquet names) moves here
alongside ``load_kreis_control_table``, its only consumer in the original module.

``resolved_kreis_per_cell`` defaults its ``ars_col`` parameter to
``_ARS_COLUMN``, defined in the sibling leaf module ``control_cells.py``. It
is imported here directly (``from .control_cells import _ARS_COLUMN``)
rather than through the package ``__init__``: submodules must not import
from the package facade (#267 split constraint).

``Optional`` is imported here for this module's own annotations
(``load_kreis_control_table``'s ``restrict_to_kreise`` parameter). Namespace
parity for external consumers of ``braunschweig.popsim.mid.Optional`` is kept
by a direct ``from typing import Optional`` in ``__init__.py`` rather than by
re-exporting it from this module (a later review follow-up: re-exporting a
stdlib generic alias through a sibling submodule was a fragile indirection).

``resolved_kreis_per_cell``, ``_kreis_pop_from_crosswalk`` and
``_batch_kreis_apportion_weights`` sat, in the original module, under a shared
"Folder assembly + orchestration" section header that also covers
``assemble_batch_folder`` / ``cell_groups`` / ``run_popsim_mid`` -- those three
stay in ``__init__.py`` under that original header, so a new dedicated header is
introduced below for the functions moved here (precedent: the sibling module
``participation.py`` introduced its own "Participation" header for functions
lifted out of the original module's shared "Seed" section).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence, Union

import pandas as pd

from braunschweig.popsim import folders

from .control_cells import _ARS_COLUMN

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Tier-3 KREIS control table (imported cleancensus kreis_* tables)
# --------------------------------------------------------------------------- #

_KREIS_CONTROL_FILES = (
    "kreis_erwerbsstatus.parquet",
    "kreis_schulabschluss.parquet",
    "kreis_berufl_abschluss.parquet",
)


def merge_kreis_control_tables(
    tables: Sequence[pd.DataFrame], *, key: str = "ARS_kreis"
) -> pd.DataFrame:
    """Merge the cleancensus per-topic kreis_* tables into one keyed by ``key``.

    Each topic table (erwerbsstatus / schulabschluss / berufl_abschluss) carries
    ``ARS_kreis`` + a label column (Name) + its STP source columns. They are
    outer-joined on ``key``; duplicate non-key columns (e.g. Name) are kept once.
    """
    if not tables:
        raise ValueError("merge_kreis_control_tables: no tables given.")
    merged = tables[0].copy()
    for table in tables[1:]:
        dup = [c for c in table.columns if c != key and c in merged.columns]
        merged = merged.merge(table.drop(columns=dup), on=key, how="outer")
    return merged


def load_kreis_control_table(
    kreis_dir: Union[str, Path],
    *,
    files: Sequence[str] = _KREIS_CONTROL_FILES,
    restrict_to_kreise: Optional[Iterable[str]] = None,
) -> pd.DataFrame:
    """Load + merge the imported Tier-3 kreis_* control tables from ``kreis_dir``.

    Reads the per-topic parquets (employment / school / vocational education) and
    merges them on ``ARS_kreis`` (re-padded to the 5-digit zero-padded string that
    matches the crosswalk's KREIS = ARS[:5]) into one table whose columns are the
    census_source classes the Tier-3 controls sum.

    The committed cleancensus kreis_* tables span ALL German Kreise (~400 rows).
    When ``restrict_to_kreise`` is given, the merged table is filtered to that set
    (each entry normalised to the 5-digit zero-padded ARS the table stores) so the
    accumulator carries only the rows the run actually looks up downstream, rather
    than ~390 national rows that ``build_kreis_control_totals`` never reads
    (issue #147; historically, before the KREIS-merge guard was scoped to the run's
    Kreise, these carried national rows caused a guard false-positive). Kreise in
    ``restrict_to_kreise`` that are absent from the
    national table are simply not present in the result (no phantom rows); the
    downstream per-Kreis target loaders fail-fast on a genuinely missing Kreis.
    """
    base = Path(kreis_dir)
    tables: list[pd.DataFrame] = []
    for name in files:
        path = base / name
        if not path.exists():
            raise FileNotFoundError(
                f"Tier-3 kreis control table not found: {path}. Import the cleancensus "
                "gemeinde_controls/kreis_* parquets into this directory first."
            )
        table = pd.read_parquet(path)
        table["ARS_kreis"] = table["ARS_kreis"].astype(str).str.zfill(5)
        tables.append(table)
    merged = merge_kreis_control_tables(tables)
    if restrict_to_kreise is not None:
        wanted = {str(k).zfill(5) for k in restrict_to_kreise}
        n_before = len(merged)
        merged = merged[merged["ARS_kreis"].isin(wanted)].reset_index(drop=True)
        logger.info(
            "[popsim.mid] load_kreis_control_table: restricted national Tier-3 table "
            "from %d to %d Kreis row(s) matching the run's %d Kreis(e) (issue #147).",
            n_before, len(merged), len(wanted),
        )
    return merged


# --------------------------------------------------------------------------- #
# Per-batch Kreis apportionment
# --------------------------------------------------------------------------- #

def resolved_kreis_per_cell(
    cells: pd.DataFrame,
    *,
    ars_col: str = _ARS_COLUMN,
    weight_col: str = "POP_TOTAL_100m_adj",
) -> pd.Series:
    """Per-cell RESOLVED dominant Kreis (one dominant Kreis per 1 km parent).

    Returns a Series aligned to ``cells.index`` giving each 100 m cell's resolved
    Kreis, built from the identical region-wide crosswalk the batch backbone uses
    (:func:`folders.build_geo_crosswalk` with ``resolve_parent_kreis=True`` and the
    same ``POP_TOTAL_100m_adj`` weight). This is what ``folders.build_kreis_control_totals``
    keys on, so grouping the per-Kreis attribute-control household/person totals by
    this Series -- instead of the raw ``ARS[:5]`` -- makes the category targets
    partition the SAME Kreis universe the 100 m backbone constrains (issue #147,
    sub-item 1). A border cell whose 1 km parent's dominant Kreis differs from its
    own ARS[:5] is attributed here to that dominant Kreis; region-wide per-Kreis
    sums are unchanged because a 1 km parent is atomic to one Kreis after resolution.
    """
    xwalk = folders.build_geo_crosswalk(
        cells,
        id_col_100m="ZENSUS100m",
        parent_col="ZENSUS1km",
        ars_col=ars_col,
        resolve_parent_kreis=True,
        kreis_weight_col=weight_col,
    )
    kreis_of = xwalk.set_index(folders.GEO_100M)[folders.GEO_KREIS]
    return cells["ZENSUS100m"].astype(str).map(kreis_of)


def _kreis_pop_from_crosswalk(
    cells_subset: pd.DataFrame,
    geo_crosswalk: pd.DataFrame,
    *,
    weight_col: str = "POP_TOTAL_100m_adj",
) -> dict[str, float]:
    """Sum ``weight_col`` per RESOLVED dominant Kreis for the given cells.

    Joins ``cells_subset`` (carrying ``ZENSUS100m`` + ``weight_col``) to the crosswalk's
    resolved ``KREIS`` (one dominant Kreis per 1 km parent) and sums the weight per Kreis.
    Keying on the crosswalk's resolved KREIS -- NOT raw ``ARS[:5]`` -- keeps this aligned
    with :func:`folders.build_kreis_control_totals` (which keys on the same resolved
    Kreis), so a boundary cell reassigned to its parent's dominant Kreis contributes its
    population to that SAME Kreis here. Because the dominant Kreis is resolved per 1 km
    parent and a parent is atomic to one batch, summing these per-batch dicts over all
    batches reproduces the region-wide per-Kreis total exactly.
    """
    kreis_of = geo_crosswalk.set_index(folders.GEO_100M)[folders.GEO_KREIS]
    work = pd.DataFrame(
        {
            folders.GEO_KREIS: cells_subset["ZENSUS100m"].astype(str).map(kreis_of),
            "_w": pd.to_numeric(cells_subset[weight_col], errors="coerce").fillna(0.0).to_numpy(),
        }
    )
    grouped = work.groupby(folders.GEO_KREIS, sort=False)["_w"].sum()
    return {str(k): float(v) for k, v in grouped.items()}


def _batch_kreis_apportion_weights(
    cells_subset: pd.DataFrame,
    geo_crosswalk: pd.DataFrame,
    kreis_total_pop: Mapping[str, float],
    *,
    weight_col: str = "POP_TOTAL_100m_adj",
) -> dict[str, float]:
    """This batch's population share of each Kreis (for KREIS-marginal apportionment).

    ``weight = batch_kreis_pop / kreis_total_pop`` per Kreis; a Kreis with zero (or
    missing) region-wide total gets weight 0 (guard divide-by-zero -- no population
    means no share to target). Summed over batches these shares equal 1 per Kreis
    (the batch pops partition the region-wide total), so the apportioned KREIS
    marginals reproduce the full marginal.
    """
    batch_pop = _kreis_pop_from_crosswalk(cells_subset, geo_crosswalk, weight_col=weight_col)
    weights: dict[str, float] = {}
    for kreis, pop in batch_pop.items():
        total = float(kreis_total_pop.get(kreis, 0.0))
        weights[kreis] = (pop / total) if total > 0 else 0.0
    return weights
