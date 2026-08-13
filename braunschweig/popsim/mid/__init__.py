"""popsim_mid orchestration: build + run PopulationSim folders from MiD + cells.

Folds the validated end-to-end logic (see ``scripts/popsim_mid_smoke.py``) into
small, focused, reusable functions:

- ``load_mid_seed``         -- the consistent (complete-household) MiD seed
- ``load_completed_donor``  -- attribute donor tables, day-filtered + member-completed
                               (the ONE completion pass feeding seed AND expansion)
- ``assemble_batch_folder`` -- write one PopulationSim run folder
- ``run_popsim_mid``        -- batch over 1 km parents, run, merge, handoff

It reuses the building blocks in ``braunschweig.popsim`` (cells / controls /
folders / seed / batch / merge / handoff) rather than re-implementing them.

Package layout (issue #267 split; formerly one ~1900-line module, itself the
rename of the legacy ``mid.py``): this ``__init__`` is a plain helper facade --
``mid`` is NOT a synpp stage, so unlike the ``enriched`` stage-package split
there is no ``configure``/``execute``/``validate()`` hook here. It re-exports
every extracted submodule name so external imports of
``braunschweig.popsim.mid`` keep working unchanged. No synpp stage currently
hashes this package's source, so the split is cache-neutral by construction;
closing that pre-existing helper-trap gap (a synpp ``validate()`` hashing the
whole package) is module 3's job (``popsim/stage.py``, issue #267). Submodules
extracted so far:

    control_cells  Control-cell loading (targeted parquet columns), ZGB Kreis
                   filtering, and per-geography integerized control totals
                   (``control_base_columns``, ``load_control_cells``,
                   ``filter_zgb_cells``, ``build_control_totals``)
    csv_format     MiD CSV field-separator detection (``detect_csv_separator``);
                   a small leaf module because both ``seed_loading`` and the
                   donor loaders (task 5) call it
    participation  Participation-control seed derivation from the realised
                   weekday plan (``derive_trip_class_seed``,
                   ``compute_has_purpose_trip``, ``compute_has_work_trip``,
                   ``derive_participation_seed``,
                   ``derive_work_participation_seed``)
    seed_loading   The consistent MiD seed load + the completed-donor
                   projection (``load_mid_seed``, ``project_completed_seed``)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence, Union

import pandas as pd

from braunschweig.popsim import batch
from braunschweig.popsim import control_spec
from braunschweig.popsim import folders
from braunschweig.popsim import member_completion as completion
from braunschweig.popsim import merge as mergemod
from braunschweig.popsim import seed as seedmod

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Package submodules (extracted stage sections). Every name is re-exported
# here so external consumers (pipeline stages, tests) keep importing from the
# braunschweig.popsim.mid module path unchanged.
# ---------------------------------------------------------------------------

from . import control_cells
from .control_cells import (  # noqa: F401  (re-exports)
    SUFFIX_100M,
    SUFFIX_1KM,
    _ARS_COLUMN,
    _EXTRA_CELL_COLUMNS,
    build_control_totals,
    cellmod,
    control_base_columns,
    ctrl,
    filter_zgb_cells,
    load_control_cells,
    pq,
    prepared_cells,
)

from . import csv_format
from .csv_format import (  # noqa: F401  (re-exports)
    detect_csv_separator,
)

from . import participation
from .participation import (  # noqa: F401  (re-exports)
    PARTICIPATION_W_ZWECK,
    compute_has_purpose_trip,
    compute_has_work_trip,
    derive_participation_seed,
    derive_trip_class_seed,
    derive_work_participation_seed,
    trips,
)

from . import seed_loading
from .seed_loading import (  # noqa: F401  (re-exports)
    KREIS_CONTROL_REGISTRY,
    KreisAttributeControl,
    attributes,
    load_mid_seed,
    project_completed_seed,
)

# Re-exported for convenience: callers that already import braunschweig.popsim.mid
# can access the canonical MiD seed column mapping without a separate import of
# braunschweig.popsim.seed.  The authoritative definition remains in seed.py.
MID_SEED_COLUMNS = seedmod.MID_SEED_COLUMNS

# A PopulationSim run is only scientifically usable if EVERY batch produced
# output: batches partition the 100 m cells, so one missing batch silently
# removes whole regions from the synthetic population. Decision 2026-07-10
# (after OOM-killed batch workers went unnoticed mid-run): no tolerated miss
# rate -- any missing batch raises instead of returning a partial population
# that would surface much later as an opaque "missing ZENSUS100m" merge error
# (no-silent-fallback policy).
MAX_MISSING_BATCH_RATE = 0.0

# Above this share of zone-integerizations falling back to smart-rounding
# (PopulationSim returning INFEASIBLE), the popsim stage logs a WARNING instead of
# INFO. This is a quality signal, not a hard failure: PopulationSim still produces a
# (smart-rounded) population. A high rate usually means the control set is
# over-constrained for small cells -- common at low sampling rates where a 100 m
# cell holds very few households (no-silent-fallback policy: surface the rate).
INTEGERIZER_INFEASIBLE_WARN_RATE = 0.05


def summarize_integerizer_feasibility(work_dir: Union[str, Path]) -> dict:
    """Aggregate the PopulationSim LP-integerizer feasibility across batch logs.

    PopulationSim integerizes each control zone with an LP; when the control set
    cannot be satisfied integer-simultaneously (typically tiny cells at a low
    sampling rate) it returns INFEASIBLE and falls back INTERNALLY to "smart-rounded
    original weights". That fallback is otherwise invisible to this pipeline -- it
    lives only in the per-batch ``populationsim.log`` -- so this parser surfaces it
    (CLAUDE.md: no silent fallbacks; treat a high rate as a quality signal).

    Parameters
    ----------
    work_dir:
        The popsim working directory containing the ``batch_*/output/populationsim.log``
        files.

    Returns
    -------
    dict
        ``n_logs`` (batch logs scanned), ``n_optimal`` (zones integerized optimally),
        ``n_infeasible`` (zones that fell back to smart-rounding),
        ``n_simul_retry_failed`` (zone-group simultaneous-integerize retries that
        failed), ``n_total`` (optimal + infeasible) and ``infeasible_rate``.
    """
    work_dir = Path(work_dir)
    n_logs = n_optimal = n_infeasible = n_simul_retry_failed = 0
    for log_path in sorted(work_dir.glob("*/output/populationsim.log")):
        n_logs += 1
        text = log_path.read_text(encoding="utf-8", errors="replace")
        # ": OPTIMAL" terminates each "Integerizer status for ...: OPTIMAL" line;
        # "Integerizer failed for ... status INFEASIBLE" is one smart-round fallback.
        n_optimal += text.count(": OPTIMAL")
        n_infeasible += text.count("Integerizer failed for")
        n_simul_retry_failed += text.count("do_simul_integerizing retry failed")
    n_total = n_optimal + n_infeasible
    infeasible_rate = (n_infeasible / n_total) if n_total else 0.0
    return {
        "n_logs": n_logs,
        "n_optimal": n_optimal,
        "n_infeasible": n_infeasible,
        "n_simul_retry_failed": n_simul_retry_failed,
        "n_total": n_total,
        "infeasible_rate": infeasible_rate,
    }


def _run_batches_and_merge(
    batch_folders: Sequence[str], run_one, *, num_workers: int
) -> "mergemod.MergeReport":
    """Run every batch, merge the outputs, and fail loudly on ANY missing batch.

    ``batch.run_batches`` already logs each failed batch's captured PopulationSim
    error. Here the merged report is checked: if any batch produced no output
    (miss rate above ``MAX_MISSING_BATCH_RATE``, which is 0), a ValueError is
    raised naming the miss count. Batches partition the 100 m cells, so a single
    missing batch (e.g. an OOM-killed worker) means whole regions are absent from
    the population -- scientifically unusable, never merely "a recoverable miss".
    """
    batch.run_batches(batch_folders, run_one, num_workers=num_workers)
    report = mergemod.merge_batch_folders(batch_folders)
    n_total = len(batch_folders)
    n_missing = int(getattr(report, "n_missing", 0) or 0)
    # A "missing" batch wrote no output file at all (the broken-PopulationSim /
    # killed-worker signal). This is distinct from a batch that ran but produced
    # an empty table, so the guard keys on the missing count, not on content.
    rate = n_missing / n_total if n_total else 1.0
    if n_total == 0 or rate > MAX_MISSING_BATCH_RATE:
        raise ValueError(
            f"PopulationSim batch run incomplete: {n_missing}/{n_total} batches "
            f"missing their output ({100.0 * rate:.1f}%; ALL batches are required "
            "-- each covers a disjoint set of 100 m cells). Check the per-batch "
            "failure messages logged above (captured PopulationSim stderr / an "
            "OOM-killed worker leaves no output). Re-running the pipeline skips "
            "completed batches and re-runs only the missing ones."
        )
    logger.info(
        "[popsim.mid] merged %d/%d batches (%d missing, %.1f%%).",
        n_total - n_missing, n_total, n_missing, 100.0 * rate,
    )
    return report


# MiD columns needed to enrich the synthetic persons (beyond the seed control cols).
MID_PERSON_ATTR_COLS = (
    "H_ID", "P_ID", "HP_ALTER", "HP_SEX", "P_TAET", "P_FSCHEIN", "P_FKARTE", "P_BKAT",
    "alter_gr1",  # conditioning column for grouped item-nonresponse imputation
    # anzwege1: raw MiD diary trip count (Anzahl Wege am Stichtag; valid 0..50, missing
    # codes 803/804 = trip module not covered) feeding the person-level trip_class KREIS
    # control (attributes.map_trip_class). Carried on the completed-donor frames so
    # project_completed_seed can derive trip_class; a mirror-imputed member inherits the
    # mirror donor's diary trip count (documented -- the completion samples whole donor
    # person rows, so the filler's anzwege1 is the donor's).
    "anzwege1",
    # P_GEW (seed person weight) + kernwo (day filter): needed so the completed
    # donor frames (load_completed_donor) can serve BOTH the expansion AND the
    # PopulationSim seed from ONE member-completion pass.
    "P_GEW", "kernwo",
)
# Tier-3 education control inputs: present in the real MiD person table, absent in some
# small test fixtures -> loaded only when present (load_mid_attributes / load_mid_seed).
MID_PERSON_OPTIONAL_COLS = ("bildung1", "bildung2")
MID_HOUSEHOLD_ATTR_COLS = (
    "H_ID", "oek_status", "hheink_gr1", "H_ANZAUTO", "H_ANZRAD",
    # anzpedrad: MiD-provided combined bicycle count INCLUDING pedelecs/e-bikes
    # (verified 2026-07-08 to equal min(H_ANZRAD + H_ANZPED, 10) on all 218,039 valid
    # MiD B1 household rows). This is the number_of_bicycles construct (MiD H12.3 /
    # SrV alle-Raeder); H_ANZRAD (conventional bikes only) is retained for any
    # downstream consumer that still needs the exclusive count.
    "anzpedrad",
    # H_ANZPED: verified MiD household e-bike (Pedelec) column feeding has_ebike
    # (attributes.map_has_ebike); see the module docstring there for the full
    # verification note.
    "H_ANZPED",
    "RegioStaR7",  # Phase 4A: RegioStaR-7 code for donor urban/rural stratification
    "hhgr_gr",  # conditioning column for grouped item-nonresponse imputation
    # H_GR (declared household size; drives member completion) + H_GEW (seed
    # household weight): needed so the completed frames can serve BOTH the
    # expansion AND the PopulationSim seed.
    "H_GR", "H_GEW",
    # Tier-2 popsim control attributes: carried from the MiD donor table onto the
    # synthetic persons frame so the popsim control-fit validation can compare
    # realized vs. target distributions.
    # H_MIETE: tenure flag (1=renter/Mieter, 2=owner/Eigentuemer; 3/9/309=excluded).
    # haustyp: building type (1=EFH/ZFH, 2=MFH 3-12Wohn., 3=Geschosswohn. 13+, 4=sonstiges, 95=n.z.).
    "H_MIETE", "haustyp",
)

# Minimum columns required by build_trip_table / trips_stage.
# All remaining columns are carried as extras (loaded via usecols=None -> all).
MID_WEGE_REQUIRED_COLS = (
    "H_ID", "P_ID", "W_ID",
    "W_ZWECK", "hvm_imp",
    "W_SZS", "W_SZM",
    "W_AZS", "W_AZM",
    "wegkm_imp",
    # MiD-imputed trip duration in MINUTES; fully populated and code-free for
    # all rbW (time code 701) rows.  Consumed by the stage A time imputation
    # (braunschweig.popsim.time_imputation) — never use raw wegmin, which
    # carries the code 70701 on those rows.
    "wegmin_imp1",
)


def load_mid_attributes(
    mid_dir: Union[str, Path],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load the MiD donor attribute columns (households + persons) for enrichment.

    Reads only the columns the eqasim attribute mapping needs (not all MiD
    columns); returns ``(households, persons)`` for
    ``braunschweig.popsim.assembly.build_persons``.
    """
    mid_dir = Path(mid_dir)
    households_path = mid_dir / "MiD2023_Haushalte.csv"
    persons_path = mid_dir / "MiD2023_Personen.csv"
    households = pd.read_csv(
        households_path, usecols=list(MID_HOUSEHOLD_ATTR_COLS),
        sep=detect_csv_separator(households_path),
    )
    _persons_sep = detect_csv_separator(persons_path)
    _persons_header = pd.read_csv(persons_path, sep=_persons_sep, nrows=0).columns
    persons = pd.read_csv(
        persons_path,
        usecols=list(MID_PERSON_ATTR_COLS)
        + [c for c in MID_PERSON_OPTIONAL_COLS if c in _persons_header],
        sep=_persons_sep,
    )
    return households, persons


def drop_invalid_households(households, persons, *, household_id="H_ID"):
    """Drop the invalid-household sentinel: rows whose household id is 0 or null.

    Real MiD 2023 has an H_ID=0 bucket of 312 unrelated persons with mixed
    reporting days -- not a real household; it must not be a population donor.
    Returns ``(households, persons, n_hh_dropped, n_persons_dropped)``.
    """
    bad_hh = households[household_id].isna() | (households[household_id] == 0)
    n_hh = int(bad_hh.sum())
    keep_p = ~(persons[household_id].isna() | (persons[household_id] == 0))
    n_p = int((~keep_p).sum())
    return (
        households[~bad_hh].copy(),
        persons[keep_p].copy(),
        n_hh,
        n_p,
    )


def load_completed_donor(
    mid_dir: Union[str, Path],
    *,
    completion_rng,
    day_filter_values: Optional[Sequence[int]] = None,
) -> tuple[
    pd.DataFrame, pd.DataFrame,
    seedmod.CompletenessReport, completion.MemberCompletionReport,
]:
    """Load the donor attribute tables, apply the day-filter completeness rule,
    and fill member-incomplete households (mirror-household sampling).

    This is the ONE member-completion pass of the popsim_mid workflow: BOTH the
    PopulationSim seed (via ``seed.select_seed_columns`` on the returned frames)
    AND the expansion (``assembly.build_persons``) derive from the completed
    frames, so seed and expansion are guaranteed to contain the SAME fillers.
    The attribute usecols therefore include the seed columns (``P_GEW`` /
    ``kernwo`` / ``H_GR`` / ``H_GEW``, see ``MID_PERSON_ATTR_COLS`` /
    ``MID_HOUSEHOLD_ATTR_COLS``).

    Args:
        mid_dir: Directory with ``MiD2023_Haushalte.csv`` / ``MiD2023_Personen.csv``.
        completion_rng: Seeded :class:`numpy.random.RandomState` driving the
            mirror draw (REQUIRED; no random process without an explicit seed).
        day_filter_values: Accepted ``kernwo`` values. ``None`` (default) ->
            the standard weekday default on ``MID_SEED_COLUMNS``; an empty
            iterable DISABLES the day filter; any non-empty iterable is used
            verbatim (same tri-state contract as :func:`load_mid_seed`).

    Returns:
        ``(households, persons, completeness_report, completion_report)``.
        The persons frame carries ``member_imputed`` + ``source_H_ID`` /
        ``source_P_ID`` (total columns: regular persons reference themselves;
        fillers reference their mirror donor for the trips join and the
        pseudonym map).
    """
    if completion_rng is None:
        raise ValueError(
            "load_completed_donor requires completion_rng (a seeded "
            "numpy.random.RandomState); random processes must use an explicit seed."
        )
    households, persons = load_mid_attributes(mid_dir)

    # Drop H_ID=0 / null sentinel before any downstream logic (donor validity).
    households, persons, _n_hh_bad, _n_p_bad = drop_invalid_households(
        households, persons, household_id=MID_SEED_COLUMNS.household_id)
    if _n_hh_bad or _n_p_bad:
        logger.warning(
            "[mid.load_completed_donor] dropped %d invalid-household row(s) "
            "(H_ID=0 or null) and %d associated person(s) from the donor data; "
            "these are not real households and must not be population donors.",
            _n_hh_bad, _n_p_bad,
        )

    # Same tri-state day-filter contract as load_mid_seed: `None` -> standard
    # weekday default; explicit empty iterable -> day filter OFF; else verbatim.
    if day_filter_values is None:
        effective_day_filter = MID_SEED_COLUMNS.day_filter_values
    elif len(tuple(day_filter_values)) == 0:
        effective_day_filter = None
    else:
        effective_day_filter = tuple(day_filter_values)

    households, persons, completeness_report = seedmod.filter_complete_households(
        households, persons, MID_SEED_COLUMNS,
        day_filter_values=effective_day_filter,
    )
    households, persons, completion_report = completion.complete_members(
        households, persons, rng=completion_rng,
        household_id=MID_SEED_COLUMNS.household_id,
    )
    return households, persons, completeness_report, completion_report


def load_mid_wege(
    mid_dir: Union[str, Path],
) -> pd.DataFrame:
    """Load the MiD 2023 Wege (trip) table for the trips_stage.

    The field separator is detected from the header (``detect_csv_separator``)
    because the MiD 2023 delivery occurs in both comma- and semicolon-separated
    variants; assuming one silently mis-parses the other into a single column.
    All columns are loaded (no usecols filter) so that every MiD Wege extra
    column is available as a traceability/analysis extra in the output trip
    table. The minimum columns required by
    ``braunschweig.popsim.trips.build_trip_table`` are listed in
    ``MID_WEGE_REQUIRED_COLS``; the file is validated to contain them.

    Parameters
    ----------
    mid_dir:
        Directory containing ``MiD2023_Wege.csv``.

    Returns
    -------
    pd.DataFrame
        Full Wege table, one row per (household, person, trip).
    """
    mid_dir = Path(mid_dir)
    wege_path = mid_dir / "MiD2023_Wege.csv"
    if not wege_path.exists():
        raise FileNotFoundError(
            f"MiD Wege file not found: {wege_path}. "
            "Ensure the MiD 2023 delivery is present in the configured mid_dir."
        )
    df = pd.read_csv(wege_path, sep=detect_csv_separator(wege_path), low_memory=False)
    missing = [c for c in MID_WEGE_REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(
            f"MiD Wege file is missing required columns: {missing}. "
            f"Available columns: {list(df.columns[:20])} ..."
        )
    return df


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
# Folder assembly + orchestration
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


def assemble_batch_folder(
    folder: Union[str, Path],
    cells_subset: pd.DataFrame,
    base_cols: Sequence[str],
    controls_df: pd.DataFrame,
    seed_households: pd.DataFrame,
    seed_persons: pd.DataFrame,
    *,
    settings_yaml: str,
    logging_yaml: str,
    kreis_table: pd.DataFrame | None = None,
    kreis_controls_map: Mapping[str, Sequence[str]] | None = None,
    ars_col: str = _ARS_COLUMN,
    kreis_weight_col: str = "POP_TOTAL_100m_adj",
    kreis_total_pop: Mapping[str, float] | None = None,
    kreis_total_hh: Mapping[str, float] | None = None,
    hh_weight_col: str = control_spec.HH_TOTAL_CENSUS_COLUMN,
    household_control_names: Iterable[str] | None = None,
) -> dict[str, Path]:
    """Assemble one PopulationSim run folder for a subset of cells.

    When ``kreis_table`` and ``kreis_controls_map`` are given (Tier-3 active), an
    additional KREIS control geography is built: the geo crosswalk gains a KREIS
    column (resolved to one dominant Kreis per 1 km parent so WELT>STAAT>KREIS>
    1km>100m nests strictly), and ``control_totals_KREIS.csv`` is written from the
    per-Kreis census table. Without them the folder is the tier0-2 baseline
    (byte-identical: no KREIS column, no KREIS control file).

    Per-batch Kreis apportionment
    -----------------------------
    A single Kreis can be split across several batches (RegioStaR stratification cuts
    the region into cell-disjoint strata). If every such batch targeted the FULL Kreis
    marginal, PopulationSim would be over-constrained N-fold and saturate the control
    (the observed 98.5% employment inflation in multi-Kreis runs). ``kreis_total_pop``
    is the total ``kreis_weight_col`` (POP_TOTAL_100m_adj) per resolved dominant Kreis
    over the WHOLE region, computed once by the caller. Given it, this batch's per-Kreis
    population (summed from THIS batch's cells, keyed by the SAME resolved dominant Kreis
    the geo crosswalk uses) is divided by the region-wide total to obtain the batch's
    population share of each Kreis; those shares are passed as ``apportion_weights`` to
    :func:`folders.build_kreis_control_totals`. Because the dominant Kreis is resolved
    per 1 km parent and parents are atomic to a batch, the shares sum to exactly 1 across
    batches, so the apportioned marginals reproduce the full Kreis marginal. When
    ``kreis_total_pop`` is None (single-batch / legacy), no apportionment is applied
    (full marginal).
    """
    tier3 = kreis_table is not None and bool(kreis_controls_map)
    if tier3 and ars_col not in cells_subset.columns:
        raise ValueError(
            f"Tier-3 KREIS controls requested but cells carry no ARS column "
            f"{ars_col!r}; cannot build the KREIS geography."
        )
    geo_crosswalk = folders.build_geo_crosswalk(
        cells_subset,
        id_col_100m="ZENSUS100m",
        parent_col="ZENSUS1km",
        ars_col=(ars_col if tier3 else None),
        resolve_parent_kreis=tier3,
        kreis_weight_col=(kreis_weight_col if tier3 else None),
    )
    targets = cells_subset[["ZENSUS100m", *base_cols]].copy()
    control_totals = build_control_totals(targets, geo_crosswalk, base_cols)
    if tier3:
        apportion_weights = None
        if kreis_total_pop is not None:
            apportion_weights = _batch_kreis_apportion_weights(
                cells_subset, geo_crosswalk, kreis_total_pop,
                weight_col=kreis_weight_col,
            )
        # Household-level KREIS controls are apportioned across batches by the batch's
        # HOUSEHOLD share, not the population share (issue #148): where persons-per-
        # household varies across a Kreis's batches, a pop-share split mis-allocates the
        # household-level targets. Person-level controls keep the pop share above.
        household_apportion_weights = None
        if kreis_total_hh is not None:
            household_apportion_weights = _batch_kreis_apportion_weights(
                cells_subset, geo_crosswalk, kreis_total_hh,
                weight_col=hh_weight_col,
            )
        control_totals[folders.GEO_KREIS] = folders.build_kreis_control_totals(
            kreis_table, geo_crosswalk, controls_map=kreis_controls_map,
            apportion_weights=apportion_weights,
            household_apportion_weights=household_apportion_weights,
            household_control_names=household_control_names,
        )
    return folders.write_popsim_folder(
        folder,
        geo_crosswalk=geo_crosswalk,
        control_totals=control_totals,
        controls_csv=controls_df,
        seed_households=seed_households,
        seed_persons=seed_persons,
        settings_yaml=settings_yaml,
        logging_yaml=logging_yaml,
    )


def cell_groups(cells_subset: pd.DataFrame) -> dict[str, list[str]]:
    """Map each 1 km parent to its 100 m children (for batching)."""
    return {
        str(parent): group["ZENSUS100m"].astype(str).tolist()
        for parent, group in cells_subset.groupby("ZENSUS1km", sort=True)
    }


def run_popsim_mid(
    cells: pd.DataFrame,
    base_cols: Sequence[str],
    controls_df: pd.DataFrame,
    seed_households: pd.DataFrame,
    seed_persons: pd.DataFrame,
    *,
    work_dir: Union[str, Path],
    settings_yaml: str,
    logging_yaml: str,
    max_cells: int,
    run_one,
    num_workers: int = 3,
    source=None,
    stratify_regiostar: bool = False,
    kreis_table: pd.DataFrame | None = None,
    kreis_controls_map: Mapping[str, Sequence[str]] | None = None,
    household_control_names: Iterable[str] | None = None,
) -> mergemod.MergeReport:
    """Batch the cells into PopulationSim runs, execute them, and merge the output.

    Partitions the 1 km parents into batches of at most ``max_cells`` 100 m cells
    (1 km atomic), assembles one PopulationSim folder per batch, runs them
    concurrently via the injected ``run_one`` (``batch.make_populationsim_run_one``
    in production; a fake in tests), and merges the cell-disjoint outputs. Returns
    the merge report (with the combined expanded-household table).

    When ``stratify_regiostar`` is False (default): the full seed is shared by
    every batch; only the controls / crosswalk are batch-specific. This is
    byte-identical to the pre-4B behaviour.

    When ``stratify_regiostar`` is True: each 1 km parent is assigned its
    dominant RegioStaR stratum (majority vote among its 100 m children); batches
    are partitioned WITHIN each stratum (so a batch never mixes strata); and each
    batch receives only the donor households whose stratum matches the batch's
    stratum.  A zero-donor batch raises :class:`ValueError`.

    Parameters
    ----------
    cells:
        100 m cells frame (ZGB-filtered, with ``ZENSUS1km`` and ``RegioStaR7``).
    base_cols:
        Control base column names (without geography suffix).
    controls_df:
        PopulationSim controls CSV as a DataFrame.
    seed_households:
        Donor household frame (from :func:`load_mid_seed` or the ENTD adapter).
    seed_persons:
        Donor person frame.
    work_dir:
        Directory for batch folders.
    settings_yaml / logging_yaml:
        PopulationSim configuration files as strings.
    max_cells:
        Maximum 100 m cells per batch (soft; a single oversize 1 km parent may
        exceed it).
    run_one:
        Injectable callable ``(folder: str) -> BatchResult``.
    num_workers:
        Thread-pool size for concurrent batch execution.
    source:
        Active :class:`braunschweig.popsim.sources.base.PopsimSource` instance.
        Required when ``stratify_regiostar=True``; ignored when False.
    stratify_regiostar:
        Flag-gate.  Default False (OFF path is byte-identical).
    """
    # Resolve to an ABSOLUTE path: PopulationSim is launched with cwd set to the
    # popsimprep repo (not the synpp working directory), so a relative batch
    # folder path (e.g. "eqasim-data/.../batch_000") would resolve against
    # popsimprep and fail with "[WinError 3] path not found". The folders are
    # written and read here, then passed verbatim as "-w <folder>" to the
    # subprocess, so the path must be absolute to be cwd-independent.
    work_dir = Path(work_dir).resolve()
    groups = cell_groups(cells)

    # Tier-3 per-batch KREIS apportionment basis: the region-wide population per
    # RESOLVED dominant Kreis, computed ONCE over ALL cells. A Kreis split across
    # batches must target only each batch's share of its marginal (not the full
    # marginal in every batch), so each batch divides its own per-Kreis population
    # by this region-wide total. Built from a full-region geo crosswalk
    # (resolve_parent_kreis=True) so the Kreis assignment matches each batch's
    # crosswalk exactly (dominant Kreis is per-1km-parent; parents are atomic to a
    # batch, so the batch pops partition this total). None for tier0-2 (no KREIS).
    tier3 = kreis_table is not None and bool(kreis_controls_map)
    kreis_total_pop: dict[str, float] | None = None
    kreis_total_hh: dict[str, float] | None = None
    if tier3:
        if _ARS_COLUMN not in cells.columns:
            raise ValueError(
                f"Tier-3 KREIS controls requested but cells carry no ARS column "
                f"{_ARS_COLUMN!r}; cannot compute the region-wide Kreis population."
            )
        full_xwalk = folders.build_geo_crosswalk(
            cells,
            id_col_100m="ZENSUS100m",
            parent_col="ZENSUS1km",
            ars_col=_ARS_COLUMN,
            resolve_parent_kreis=True,
            kreis_weight_col="POP_TOTAL_100m_adj",
        )
        kreis_total_pop = _kreis_pop_from_crosswalk(cells, full_xwalk)
        # Household-level KREIS controls need the region-wide HOUSEHOLD total per
        # resolved Kreis so each batch can be apportioned by its household share, not
        # its population share (issue #148). Only computed when such controls are
        # active; a missing household-total column with household controls active is a
        # hard error, not a silent fall-back to the population share (CLAUDE.md).
        if household_control_names:
            _hh_col = control_spec.HH_TOTAL_CENSUS_COLUMN
            if _hh_col not in cells.columns:
                raise ValueError(
                    f"Household-level KREIS controls {sorted(household_control_names)} require "
                    f"the household-total column {_hh_col!r} for household-share apportionment "
                    f"(issue #148), but it is absent from the cells frame; refusing to fall back "
                    f"to the population share silently."
                )
            kreis_total_hh = _kreis_pop_from_crosswalk(cells, full_xwalk, weight_col=_hh_col)

    if not stratify_regiostar:
        # Default OFF path: unchanged behaviour (byte-identical to pre-4B).
        partitions = batch.partition_by_1km(groups, max_cells)
        batch_folders: list[str] = []
        for index, km_cells in enumerate(partitions):
            subset = cells[cells["ZENSUS1km"].isin(km_cells)].copy()
            folder = work_dir / f"batch_{index:03d}"
            assemble_batch_folder(
                folder, subset, base_cols, controls_df,
                seed_households, seed_persons,
                settings_yaml=settings_yaml, logging_yaml=logging_yaml,
                kreis_table=kreis_table, kreis_controls_map=kreis_controls_map,
                kreis_total_pop=kreis_total_pop,
                kreis_total_hh=kreis_total_hh,
                household_control_names=household_control_names,
            )
            batch_folders.append(str(folder))

        return _run_batches_and_merge(
            batch_folders, run_one, num_workers=num_workers
        )

    # Stratified ON path (Phase 4B).
    # Requires a source with cell_stratum and donor_stratum.
    if source is None:
        raise ValueError(
            "[popsim.mid] stratify_regiostar=True requires a source adapter "
            "(pass source=<PopsimSource instance>)."
        )

    # (1) Compute dominant stratum per 1 km parent; log border approximation rate.
    dominant_map, border_rate = dominant_stratum_for_1km(cells, source)
    logger.info(
        "[popsim.mid] RegioStaR stratification: %d 1km parents; "
        "border approximation rate %.1f%% (fraction of 100m cells whose stratum "
        "differs from their 1km parent's dominant).",
        len(dominant_map), 100.0 * border_rate,
    )

    # (2) Group 1km parents by dominant stratum.
    stratum_to_km_ids: dict = {}
    for km_id, stratum in dominant_map.items():
        stratum_to_km_ids.setdefault(stratum, []).append(km_id)

    logger.info(
        "[popsim.mid] strata: %s",
        {str(s): len(ids) for s, ids in stratum_to_km_ids.items()},
    )

    # (3) Partition WITHIN each stratum + filter seed per batch.
    batch_folders_stratified: list[str] = []
    global_batch_index = 0
    for stratum, km_ids in sorted(stratum_to_km_ids.items(), key=lambda x: str(x[0])):
        # Build the per-stratum cell_groups (only 1km parents in this stratum).
        stratum_groups = {km: groups[km] for km in km_ids if km in groups}
        partitions_s = batch.partition_by_1km(stratum_groups, max_cells)

        # Filter donor seed once per stratum; reuse across all batches of the stratum.
        hh_stratum, p_stratum = filter_seed_to_stratum(
            seed_households, seed_persons, stratum, source
        )
        logger.info(
            "[popsim.mid] stratum %s: %d batches, %d donor households.",
            stratum, len(partitions_s), len(hh_stratum),
        )

        for km_cells in partitions_s:
            subset = cells[cells["ZENSUS1km"].isin(km_cells)].copy()
            folder = work_dir / f"batch_{global_batch_index:03d}"
            assemble_batch_folder(
                folder, subset, base_cols, controls_df,
                hh_stratum, p_stratum,
                settings_yaml=settings_yaml, logging_yaml=logging_yaml,
                kreis_table=kreis_table, kreis_controls_map=kreis_controls_map,
                kreis_total_pop=kreis_total_pop,
                kreis_total_hh=kreis_total_hh,
                household_control_names=household_control_names,
            )
            batch_folders_stratified.append(str(folder))
            global_batch_index += 1

    return _run_batches_and_merge(
        batch_folders_stratified, run_one, num_workers=num_workers
    )
