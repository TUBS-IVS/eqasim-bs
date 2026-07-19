"""popsim_mid orchestration: build + run PopulationSim folders from MiD + cells.

Folds the validated end-to-end logic (see ``scripts/popsim_mid_smoke.py``) into
small, focused, reusable functions:

- ``control_base_columns``  -- the control_field base columns from the control spec
- ``load_control_cells``    -- a TARGETED load of only the needed cell columns
- ``filter_zgb_cells``      -- restrict the national grid to the ZGB Kreise
- ``build_control_totals``  -- per-geography suffixed, hierarchically integerized
- ``load_mid_seed``         -- the consistent (complete-household) MiD seed
- ``load_completed_donor``  -- attribute donor tables, day-filtered + member-completed
                               (the ONE completion pass feeding seed AND expansion)
- ``assemble_batch_folder`` -- write one PopulationSim run folder
- ``run_popsim_mid``        -- batch over 1 km parents, run, merge, handoff

It reuses the building blocks in ``braunschweig.popsim`` (cells / controls /
folders / seed / batch / merge / handoff) rather than re-implementing them.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence, Union

import pandas as pd
import pyarrow.parquet as pq

from braunschweig.popsim import attributes
from braunschweig.popsim import batch
from braunschweig.popsim import cells as cellmod
from braunschweig.popsim import control_spec
from braunschweig.popsim import controls as ctrl
from braunschweig.popsim import folders
from braunschweig.popsim import member_completion as completion
from braunschweig.popsim import merge as mergemod
from braunschweig.popsim import prepared_cells
from braunschweig.popsim import seed as seedmod
from braunschweig.popsim import trips
from braunschweig.popsim.kreis_attribute_control import KreisAttributeControl
from braunschweig.popsim.kreis_attribute_control import REGISTRY as KREIS_CONTROL_REGISTRY

logger = logging.getLogger(__name__)

SUFFIX_100M = "_ZENSUS100m"
SUFFIX_1KM = "_ZENSUS1km"

# Re-exported for convenience: callers that already import braunschweig.popsim.mid
# can access the canonical MiD seed column mapping without a separate import of
# braunschweig.popsim.seed.  The authoritative definition remains in seed.py.
MID_SEED_COLUMNS = seedmod.MID_SEED_COLUMNS

# Cell columns always loaded in addition to the control bases: the population
# total (for parent selection / diagnostics), the ARS key (for the ZGB filter),
# and RegioStaR7 (for Phase 4B donor stratification by urban/rural class).
_EXTRA_CELL_COLUMNS = ("POP_TOTAL_100m_adj", "RegionalSchlussel_ARS", "RegioStaR7")
_ARS_COLUMN = "RegionalSchlussel_ARS"

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


def detect_csv_separator(path: Union[str, Path]) -> str:
    """Detect the field separator of a MiD CSV from its header line.

    The MiD 2023 scientific-use delivery has been observed with BOTH separators:
    a comma-separated export (the ZGB regional sample used here) and a
    semicolon-separated German-locale export. Hard-coding one separator silently
    mis-parses the other -- the whole header collapses into a single column,
    which then fails much later with a misleading "missing required columns"
    error (observed for ``MiD2023_Wege.csv``). The separator is therefore
    detected from the header rather than assumed.

    Parameters
    ----------
    path:
        Path to the MiD CSV file.

    Returns
    -------
    str
        ``","`` if the header contains at least as many commas as semicolons,
        otherwise ``";"``.

    Raises
    ------
    ValueError
        If the header line contains neither ``,`` nor ``;`` (so no separator can
        be inferred and a silent mis-parse must be avoided).
    """
    with open(path, "r", encoding="utf-8") as handle:
        header = handle.readline()
    n_comma = header.count(",")
    n_semicolon = header.count(";")
    if n_comma == 0 and n_semicolon == 0:
        raise ValueError(
            f"Cannot detect a ',' or ';' field separator in the header of {path}: "
            f"{header[:120]!r}. The MiD CSV delivery must be comma- or "
            "semicolon-separated."
        )
    return "," if n_comma >= n_semicolon else ";"


# --------------------------------------------------------------------------- #
# Control spec
# --------------------------------------------------------------------------- #

def control_base_columns(controls_df: pd.DataFrame, geography: str) -> list[str]:
    """Return the distinct control_field base names for a geography (suffix off).

    The control spec's ``control_field`` is ``<base>_<geography>`` (e.g.
    ``M_AGE_0_9_agg_ZENSUS100m``); the base (``M_AGE_0_9_agg``) is the prepared
    cell column the control counts.
    """
    rows = controls_df[controls_df["geography"] == geography]
    suffix = f"_{geography}"
    bases = [
        cf[: -len(suffix)] if cf.endswith(suffix) else cf
        for cf in rows["control_field"]
    ]
    return list(dict.fromkeys(bases))


# --------------------------------------------------------------------------- #
# Cells
# --------------------------------------------------------------------------- #

def load_control_cells(
    parquet_path: Union[str, Path],
    base_cols: Sequence[str],
) -> pd.DataFrame:
    """Load ONLY the needed columns of the prepared cell parquet (performant).

    Reads the grid id + the control base columns + the population total + the ARS
    key (matching cleaned names back to the raw parquet columns), instead of all
    ~570 columns x 3.1 M rows. Attaches ``ZENSUS1km`` / ``STAAT`` / ``WELT``.
    """
    raw_cols = pq.ParquetFile(parquet_path).schema.names
    clean_to_raw: dict[str, str] = {}
    for raw in raw_cols:
        clean_to_raw.setdefault(prepared_cells.clean_col_name(raw), raw)

    id_raw = raw_cols[0]  # GITTER_ID_100m is the first column
    raw_needed = [id_raw]
    for clean in [*base_cols, *_EXTRA_CELL_COLUMNS]:
        raw = clean_to_raw.get(clean)
        if raw is not None and raw not in raw_needed:
            raw_needed.append(raw)

    # RegioStaR7 is optional (graceful): older prepared-cell parquets do not
    # carry it. Without it the stage-B chain matching loses its spatial key and
    # falls back to the 4-key attribute list, so the absence is logged (info,
    # not warn -- the load itself is fully usable).
    if "RegioStaR7" not in clean_to_raw:
        logger.info(
            "[popsim.mid] cells parquet %s carries no 'RegioStaR7' column; "
            "proceeding without it (synthetic persons get no home-cell RS7; "
            "stage-B chain matching falls back to the non-spatial key list).",
            parquet_path,
        )

    df = pd.read_parquet(parquet_path, columns=raw_needed)
    df.columns = [prepared_cells.clean_col_name(c) for c in df.columns]
    df = df.rename(columns={prepared_cells.clean_col_name(id_raw): "ZENSUS100m"})
    df["ZENSUS1km"] = df["ZENSUS100m"].map(cellmod.derive_1km_parent_id)
    df["STAAT"] = 1
    df["WELT"] = 1
    return df


def filter_zgb_cells(
    cells: pd.DataFrame,
    kreis_ars5: Iterable[str],
    *,
    ars_col: str = _ARS_COLUMN,
) -> pd.DataFrame:
    """Restrict the national grid to the cells whose Kreis (ARS-5) is in scope.

    The cell ARS is the 12-digit Regionalschluessel; the Kreis is its first five
    digits.
    """
    if ars_col not in cells.columns:
        raise ValueError(
            f"cells frame has no ARS column {ars_col!r}; cannot filter to ZGB Kreise."
        )
    kreise = {str(k) for k in kreis_ars5}
    ars = cells[ars_col].astype(str).str.zfill(12)
    return cells[ars.str[:5].isin(kreise)].copy()


# --------------------------------------------------------------------------- #
# Control totals (notebook-faithful: per-geography suffix, integerized)
# --------------------------------------------------------------------------- #

def build_control_totals(
    per_cell_targets: pd.DataFrame,
    geo_crosswalk: pd.DataFrame,
    base_cols: Sequence[str],
    *,
    cell_col: str = "ZENSUS100m",
    parent_col: str = "ZENSUS1km",
) -> dict[str, pd.DataFrame]:
    """Build the four control-total tables with per-geography suffixed columns.

    Each base column is integerized within its 1 km parent (largest-remainder), so
    the integer 100 m values sum exactly to the 1 km total; the 100 m columns are
    suffixed ``_ZENSUS100m`` and the 1 km totals ``_ZENSUS1km``. STAAT / WELT carry
    only the geography key (no controls), matching the notebook + control spec.
    """
    parent_of = geo_crosswalk.set_index(cell_col)[parent_col]
    work = per_cell_targets.copy()
    work[parent_col] = work[cell_col].map(parent_of)

    # Zensus 2022 suppresses (Geheimhaltung) some per-cell aggregates: an inhabited
    # 100 m cell can carry a NaN in a control count column. largest_remainder_round
    # cannot integerize NaN, so the missing counts are filled with 0 (the cell
    # contributes no recorded units of that category to the control). This is made
    # observable per the no-silent-fallback policy: the affected cell count and rate
    # are logged, and a high rate (> 1 %) is flagged as a likely data/load problem
    # rather than genuine Zensus suppression.
    n_cells = len(work)
    for col in base_cols:
        n_nan = int(work[col].isna().sum())
        if n_nan:
            rate = n_nan / n_cells if n_cells else 0.0
            message = (
                "[popsim.controls] control column %r has %d/%d (%.3f%%) NaN cells "
                "(Zensus suppression); filling with 0."
            )
            if rate > 0.01:
                logger.warning(
                    message + " High rate -- check the prepared cell parquet load.",
                    col, n_nan, n_cells, 100.0 * rate,
                )
            else:
                logger.info(message, col, n_nan, n_cells, 100.0 * rate)
            work[col] = work[col].fillna(0)

    df_100m = pd.DataFrame({cell_col: work[cell_col].to_numpy()})
    for col in base_cols:
        df_100m[f"{col}{SUFFIX_100M}"] = ctrl.integerize_within_parents(
            work, value_col=col, parent_col=parent_col
        ).to_numpy()

    df_100m[parent_col] = work[parent_col].to_numpy()
    cols_100m = [f"{c}{SUFFIX_100M}" for c in base_cols]
    df_1km = df_100m.groupby(parent_col, sort=False)[cols_100m].sum().reset_index()
    df_1km = df_1km.rename(
        columns={f"{c}{SUFFIX_100M}": f"{c}{SUFFIX_1KM}" for c in base_cols}
    )

    return {
        "ZENSUS100m": df_100m.drop(columns=[parent_col]),
        "ZENSUS1km": df_1km,
        "STAAT": pd.DataFrame([{"STAAT": 1, "WELT": 1}]),
        "WELT": pd.DataFrame([{"WELT": 1}]),
    }


# --------------------------------------------------------------------------- #
# Seed
# --------------------------------------------------------------------------- #

def derive_trip_class_seed(persons, *, rng, household_id="H_ID", person_id="P_ID"):
    """Derive the ``trip_class`` control seed from each person's REALISED weekday plan.

    A synthetic person executes the MiD plan identified by ``(source_H_ID, source_P_ID)``.
    After ``weekend_plan_match`` every plan source resolves to a weekday (kernwo 1-3)
    donor (its A2 sweep guarantees no person sources a weekend diary), so that source's
    diary trip count (``anzwege1``) is BOTH the weekday universe the SrV Di-Do target
    measures AND the trips the synthetic person actually realises. The control must
    therefore be seeded from the SOURCE's ``anzwege1``, not the person's own
    reporting-day ``anzwege1``.

    Rationale (audit 2026-07-09): the default pipeline keeps ALL reporting days in the
    donor (``weekend_plan_match`` forces ``ALL_REPORTING_KERNWO``), so ~29% of donor
    persons are weekend reporters whose OWN ``anzwege1`` is a Saturday/Sunday count
    (measured ~2pp more immobile than weekday). Seeding ``trip_class`` from their own
    weekend count fit a weekday-anchored control to the wrong universe AND steered on a
    variable the person never realises (their plan is a remapped weekday donor). Sourcing
    the plan's ``anzwege1`` removes both mismatches.

    When the plan-source columns are absent (no member completion / weekend match ran),
    the seed is already weekday-filtered, so ``trip_class`` is derived from the person's
    own ``anzwege1``; the path taken is logged (no silent fallback). The 803/804 diary
    non-response codes on the resolved trip count are imputed within the PERSON's own
    ``alter_gr1`` age band, exactly as before.
    """
    has_source = "source_H_ID" in persons.columns and "source_P_ID" in persons.columns
    if not has_source:
        logger.info(
            "[popsim.mid] trip_class seed: derived from each person's own anzwege1 "
            "(no plan-source columns present -> seed is weekday-filtered).")
        return attributes.map_trip_class(persons, rng=rng)

    # Real (non-imputed) donor persons are the only valid plan sources; a filler's source
    # points at its mirror donor, which is one of these real persons.
    real = persons[~persons["member_imputed"].astype(bool)] if "member_imputed" in persons.columns else persons
    source_anzwege1 = real.set_index([household_id, person_id])["anzwege1"]
    src_idx = pd.MultiIndex.from_arrays([persons["source_H_ID"], persons["source_P_ID"]])
    mapped = pd.Series(source_anzwege1.reindex(src_idx).to_numpy(), index=persons.index)
    n_unresolved = int(mapped.isna().sum())
    if n_unresolved:
        # Defense-in-depth (no silent fallback): a plan source MUST resolve to a real
        # donor person; a NaN means upstream donor/source corruption (mirrors the
        # weekend_plan_match A2-sweep guard). Fail loudly rather than seed a NaN class.
        raise ValueError(
            f"derive_trip_class_seed: {n_unresolved} person(s) have a plan source "
            f"(source_H_ID, source_P_ID) absent from the donor frame; cannot derive "
            "trip_class from the realised plan (upstream donor/source corruption).")
    persons = persons.copy()
    persons["_plan_source_anzwege1"] = mapped.to_numpy()
    logger.info(
        "[popsim.mid] trip_class seed: derived from the realised weekday plan source "
        "(source anzwege1) for %d persons -- aligns the control with the SrV weekday "
        "target universe and the trips the synthetic person actually executes.",
        len(persons))
    out = attributes.map_trip_class(persons, trips_col="_plan_source_anzwege1", rng=rng)
    return out.drop(columns=["_plan_source_anzwege1"])


# Purpose -> W_ZWECK code set for each per-Kreis "participation" control (feature #224).
# Derived from the single source of truth trips.PURPOSE_BY_W_ZWECK rather than
# duplicating the code lists here: {"work": {1, 2}, "leisure": {7}, "education": {3, 11, 12}}.
# work_participation (task 4) is the first control built on this map; leisure_participation
# / education_participation (task 5) reuse the SAME derivation machinery, parametrized by
# purpose, rather than duplicating compute_has_work_trip / derive_work_participation_seed
# three times over.
PARTICIPATION_W_ZWECK: dict[str, set[int]] = {
    purpose: {code for code, p in trips.PURPOSE_BY_W_ZWECK.items() if p == purpose}
    for purpose in ("work", "leisure", "education")
}


def compute_has_purpose_trip(
    persons: pd.DataFrame, wege: pd.DataFrame, purpose: str, *,
    household_id: str = "H_ID", person_id: str = "P_ID",
    trips_col: str = "anzwege1", zweck_col: str = "W_ZWECK",
) -> pd.Series:
    """Derive the per-person ``has_<purpose>_trip`` flag (0/1, or a carried 803/804 diary
    non-response code) from each person's MiD Wege, for a ``<purpose>_participation`` seed
    (generic core behind ``compute_has_work_trip`` / the leisure / education controls,
    feature #224 task 5).

    A person has a ``purpose`` trip (flag = 1) if at least one of their Wege has
    ``zweck_col`` in ``PARTICIPATION_W_ZWECK[purpose]`` -- the ``W_ZWECK`` codes
    ``trips.PURPOSE_BY_W_ZWECK`` maps to that activity purpose; otherwise 0.

    Exception: if the person's own diary trip count (``trips_col``, default
    ``anzwege1``) is one of the 803/804 item non-response codes (trip module not
    covered -- no diary / rueckwirkende Wegeerhebung only; see
    ``attributes.map_trip_class``), the flag is UNKNOWN, not "no trip". The code is
    carried through unchanged so ``attributes.map_participation``'s
    ``missing.AttributeSpec(impute_codes=(803, 804))`` imputes it from the valid {0, 1}
    pool within the person's age band, exactly as ``trip_class`` handles the same codes.
    A diary non-response person must never be forced to 0.

    Returns a ``pd.Series`` indexed like ``persons`` (index preserved, not reset).

    Raises ``ValueError`` if ``purpose`` is not one of ``PARTICIPATION_W_ZWECK``.
    Raises ``KeyError`` if ``trips_col`` is absent from ``persons``, or if
    ``household_id`` / ``person_id`` / ``zweck_col`` are absent from ``wege`` (no silent
    fallback to a guessed column name).
    """
    if purpose not in PARTICIPATION_W_ZWECK:
        raise ValueError(
            f"compute_has_purpose_trip: purpose must be one of {sorted(PARTICIPATION_W_ZWECK)}, "
            f"got {purpose!r}.")
    if trips_col not in persons.columns:
        raise KeyError(
            f"compute_has_purpose_trip: source column {trips_col!r} absent from the person "
            f"frame (has {list(persons.columns)}); cannot derive has_{purpose}_trip.")
    missing_wege_cols = [c for c in (household_id, person_id, zweck_col) if c not in wege.columns]
    if missing_wege_cols:
        raise KeyError(
            f"compute_has_purpose_trip: column(s) {missing_wege_cols} absent from the Wege "
            f"frame (has {list(wege.columns)}); cannot derive has_{purpose}_trip.")

    purpose_codes = PARTICIPATION_W_ZWECK[purpose]
    purpose_wege = wege[wege[zweck_col].isin(purpose_codes)]
    purpose_person_keys = pd.MultiIndex.from_arrays(
        [purpose_wege[household_id], purpose_wege[person_id]]).unique()
    person_keys = pd.MultiIndex.from_arrays([persons[household_id], persons[person_id]])
    has_purpose_weg = person_keys.isin(purpose_person_keys)
    result = pd.Series(has_purpose_weg.astype(int), index=persons.index)

    nonresponse_mask = persons[trips_col].isin((803, 804))
    result = result.where(~nonresponse_mask, persons[trips_col])
    return result


def compute_has_work_trip(
    persons: pd.DataFrame, wege: pd.DataFrame, *,
    household_id: str = "H_ID", person_id: str = "P_ID",
    trips_col: str = "anzwege1", zweck_col: str = "W_ZWECK",
) -> pd.Series:
    """Derive the per-person ``has_work_trip`` flag (0/1, or a carried 803/804 diary
    non-response code) from each person's MiD Wege, for the ``work_participation`` seed.

    Thin wrapper over :func:`compute_has_purpose_trip` (purpose="work"); kept as a
    named entry point so existing callers/tests stay unchanged. See that function's
    docstring for the full 803/804 non-response handling.

    Returns a ``pd.Series`` indexed like ``persons`` (index preserved, not reset).

    Raises ``KeyError`` if ``trips_col`` is absent from ``persons``, or if
    ``household_id`` / ``person_id`` / ``zweck_col`` are absent from ``wege`` (no silent
    fallback to a guessed column name).
    """
    return compute_has_purpose_trip(
        persons, wege, "work",
        household_id=household_id, person_id=person_id,
        trips_col=trips_col, zweck_col=zweck_col)


def derive_participation_seed(persons, wege, purpose, *, rng, household_id="H_ID", person_id="P_ID"):
    """Derive the ``<purpose>_participation`` control seed from each person's REALISED
    weekday plan (generic core behind ``derive_work_participation_seed`` / the leisure /
    education controls, feature #224 task 5; mirrors ``derive_trip_class_seed`` -- see
    that docstring for the full weekday-vs-realised-plan rationale, which applies
    identically here).

    A synthetic person executes the MiD plan identified by ``(source_H_ID,
    source_P_ID)``, so ``<purpose>_participation`` must be seeded from that SOURCE
    donor's ``has_<purpose>_trip`` (derived from the source's own Wege via
    ``compute_has_purpose_trip``), not the person's own Wege -- the same weekday-reporter
    mismatch ``derive_trip_class_seed`` fixes for ``anzwege1`` applies here: a weekend
    reporter's own Wege are a Saturday/Sunday diary, not the weekday plan the synthetic
    person actually realises.

    When the plan-source columns are absent (no member completion / weekend match ran),
    the seed is already weekday-filtered, so ``has_<purpose>_trip`` is derived from the
    person's own Wege directly; the path taken is logged (no silent fallback). The
    803/804 diary non-response codes are imputed within the PERSON's own ``alter_gr1``
    age band, exactly as ``derive_trip_class_seed`` does.
    """
    name = f"{purpose}_participation"
    has_source = "source_H_ID" in persons.columns and "source_P_ID" in persons.columns
    if not has_source:
        logger.info(
            "[popsim.mid] %s seed: derived from each person's own Wege "
            "(no plan-source columns present -> seed is weekday-filtered).", name)
        persons = persons.copy()
        persons[f"has_{purpose}_trip"] = compute_has_purpose_trip(
            persons, wege, purpose, household_id=household_id, person_id=person_id)
        out = attributes.map_participation(persons, name, source_col=f"has_{purpose}_trip", rng=rng)
        return out.drop(columns=[f"has_{purpose}_trip"])

    # Real (non-imputed) donor persons are the only valid plan sources; a filler's source
    # points at its mirror donor, which is one of these real persons.
    real = persons[~persons["member_imputed"].astype(bool)] if "member_imputed" in persons.columns else persons
    real_has_purpose_trip = compute_has_purpose_trip(
        real, wege, purpose, household_id=household_id, person_id=person_id)
    source_has_purpose_trip = pd.Series(
        real_has_purpose_trip.to_numpy(),
        index=pd.MultiIndex.from_arrays([real[household_id], real[person_id]]))
    src_idx = pd.MultiIndex.from_arrays([persons["source_H_ID"], persons["source_P_ID"]])
    mapped = pd.Series(source_has_purpose_trip.reindex(src_idx).to_numpy(), index=persons.index)
    n_unresolved = int(mapped.isna().sum())
    if n_unresolved:
        # Defense-in-depth (no silent fallback): a plan source MUST resolve to a real
        # donor person; a NaN means upstream donor/source corruption (mirrors the
        # weekend_plan_match A2-sweep guard). Fail loudly rather than seed a NaN flag.
        raise ValueError(
            f"derive_participation_seed: {n_unresolved} person(s) have a plan source "
            f"(source_H_ID, source_P_ID) absent from the donor frame; cannot derive "
            f"{name} from the realised plan (upstream donor/source corruption).")
    persons = persons.copy()
    persons[f"_plan_source_has_{purpose}_trip"] = mapped.to_numpy()
    logger.info(
        "[popsim.mid] %s seed: derived from the realised weekday plan "
        "source (source has_%s_trip) for %d persons -- aligns the control with the "
        "trips the synthetic person actually executes.",
        name, purpose, len(persons))
    out = attributes.map_participation(
        persons, name, source_col=f"_plan_source_has_{purpose}_trip", rng=rng)
    return out.drop(columns=[f"_plan_source_has_{purpose}_trip"])


def derive_work_participation_seed(persons, wege, *, rng, household_id="H_ID", person_id="P_ID"):
    """Derive the ``work_participation`` control seed from each person's REALISED
    weekday plan.

    Thin wrapper over :func:`derive_participation_seed` (purpose="work"); kept as a
    named entry point so existing callers/tests stay unchanged. See that function's
    docstring for the full weekday-vs-realised-plan rationale.
    """
    return derive_participation_seed(
        persons, wege, "work", rng=rng, household_id=household_id, person_id=person_id)


def load_mid_seed(
    mid_dir: Union[str, Path],
    *,
    columns: seedmod.SeedColumns = seedmod.MID_SEED_COLUMNS,
    day_filter_values: Optional[Sequence[int]] = None,
    complete_members: bool = False,
    completion_rng=None,
    include_status_seed_col: bool = False,
    kreis_control_entries: Sequence[KreisAttributeControl] = (),
    kreis_seed_rng=None,
    ebike_seed_column: Optional[str] = None,
) -> tuple[pd.DataFrame, pd.DataFrame, seedmod.CompletenessReport]:
    """Load the consistent MiD seed (complete-household filtered) -- performant.

    Reads only the seed columns the controls need (not all 79 / 261 MiD columns),
    applies the complete-household (``kernwo``) filter so every kept household has
    its persons (no NaN incidence in PopulationSim), and returns
    ``(households, persons, report)`` with the essentials + ``STAAT``.

    Args:
        day_filter_values: Accepted ``kernwo`` values for the day filter.
            ``None`` (default) selects the standard weekday default declared on
            ``columns.day_filter_values``; ``()`` or any empty iterable DISABLES
            the day filter (all households kept); any non-empty iterable is used
            verbatim.
        complete_members: Opt-in (default False -> behaviour byte-identical to
            before). When True, member-incomplete households (fewer person rows
            than the declared ``H_GR``; 16.9 % of weekday-filtered seed
            households) are FILLED by mirror-household sampling (decision D3;
            see :mod:`braunschweig.popsim.member_completion`), and the person
            frame gains the total traceability columns ``member_imputed``,
            ``source_H_ID``, ``source_P_ID`` for the downstream trips join.
        completion_rng: Seeded :class:`numpy.random.RandomState` driving the
            mirror draw. REQUIRED when ``complete_members=True`` (seeded
            randomness rule: no random process without an explicit seed).
        include_status_seed_col: DEPRECATED alias for
            ``kreis_control_entries=(economic_status entry,)``; kept so existing
            callers/tests stay byte-identical (raw ``oek_status`` pass-through,
            no resolve/derivation).
        kreis_control_entries: The ACTIVE :class:`KreisAttributeControl` registry
            entries (see :mod:`braunschweig.popsim.kreis_attribute_control`) whose
            ``seed_column`` must be present on the returned seed households as a
            clean, MECE column. ``economic_status`` is a raw ``oek_status``
            pass-through; ``number_of_cars`` / ``number_of_bicycles`` / ``has_ebike``
            are derived via the corresponding ``attributes.map_*`` resolver (99
            missing code imputed) so the count-style predicates (e.g. ``>= 3``)
            never see the raw missing code.
        kreis_seed_rng: Seeded :class:`numpy.random.RandomState` for the count-style
            entry derivations (``number_of_cars`` / ``number_of_bicycles`` /
            ``has_ebike``). REQUIRED when any such entry is active (seeded
            randomness rule: no random process without an explicit seed).
        ebike_seed_column: Name of the (server-verified) MiD household e-bike
            column feeding the ``has_ebike`` entry. REQUIRED when ``has_ebike`` is
            active (no silent fallback to a guessed column name).
    """
    if complete_members and completion_rng is None:
        raise ValueError(
            "load_mid_seed(complete_members=True) requires completion_rng "
            "(a seeded numpy.random.RandomState); random processes must use an "
            "explicit seed."
        )
    # Deprecated alias: include_status_seed_col=True is equivalent to activating the
    # economic_status registry entry, so old callers/tests stay byte-identical
    # (oek_status is a raw pass-through -- the same behaviour as before this task).
    effective_kreis_entries: list[KreisAttributeControl] = list(kreis_control_entries)
    if include_status_seed_col and not any(
        entry.name == "economic_status" for entry in effective_kreis_entries
    ):
        effective_kreis_entries.append(
            next(entry for entry in KREIS_CONTROL_REGISTRY if entry.name == "economic_status")
        )
    active_kreis_entry_names = {entry.name for entry in effective_kreis_entries}
    mid_dir = Path(mid_dir)
    # Load household id, weight, and RegioStaR7 (Phase 4A plumbing: the RS7 code
    # is carried onto the seed households so Phase 4B donor stratification can use
    # the cell's urban/rural class to restrict the donor pool without an extra join).
    households_path = mid_dir / "MiD2023_Haushalte.csv"
    persons_path = mid_dir / "MiD2023_Personen.csv"
    # Always load H_GR (declared household size) so the Tier-1 household_size
    # control expression ``(households.H_GR == N)`` can be evaluated by
    # PopulationSim. H_GR was previously loaded only when complete_members=True;
    # the Tier-7 addition makes it unconditionally required in the seed.
    # Always load H_MIETE (tenure flag: 1=renter, 2=owner) so the Tier-2 tenure
    # control expressions ``(households.H_MIETE == 1/2)`` can be evaluated by
    # PopulationSim. Values 3/9/309 (ambiguous) are kept in the seed column;
    # the control expressions simply do not match them (they contribute 0 to
    # either tenure control, which is the correct treatment for excluded codes).
    # Always load haustyp (building type: 1=EFH/ZFH, 2=MFH, 3=Geschosswohnung,
    # 4=sonstiges, 95=n.z.) so the Tier-2 building_type control expressions
    # can be evaluated by PopulationSim. Code 95 (n.z.) does not match any
    # building_type expression and is therefore silently excluded from all three
    # building_type controls (correct behaviour, no fabricated assignments).
    household_cols = [columns.household_id, columns.household_weight, "RegioStaR7", "H_GR", "H_MIETE", "haustyp"]
    if complete_members:
        # Member completion additionally needs the mirror match keys
        # (hhgr_gr -> oek_status; RegioStaR7 and H_GR are already loaded above).
        household_cols.extend(("hhgr_gr", "oek_status"))
    if "economic_status" in active_kreis_entry_names and "oek_status" not in household_cols:
        # economic_status x Kreis control (issue #109): load oek_status so the seed
        # households can carry it for the control expression (households.oek_status == k).
        household_cols.append("oek_status")
    if (
        active_kreis_entry_names & {"number_of_cars", "number_of_bicycles", "has_ebike"}
        and "hhgr_gr" not in household_cols
    ):
        # Count-style kreis controls resolve their raw column via attributes.map_* with
        # group-wise (hhgr_gr) imputation of the 99 missing code (see below).
        household_cols.append("hhgr_gr")
    if "number_of_cars" in active_kreis_entry_names:
        household_cols.append("H_ANZAUTO")
    if "number_of_bicycles" in active_kreis_entry_names:
        # anzpedrad = bicycles INCLUDING pedelecs/e-bikes (MiD H12.3 / SrV alle-Raeder
        # construct; verified 2026-07-08 to equal min(H_ANZRAD + H_ANZPED, 10) on all
        # 218,039 valid MiD B1 household rows). See attributes.map_number_of_bicycles.
        household_cols.append("anzpedrad")
    if "has_ebike" in active_kreis_entry_names:
        if not ebike_seed_column:
            raise ValueError(
                "load_mid_seed: has_ebike kreis control is active but ebike_seed_column is "
                "not configured; set braunschweig.population.popsim.ebike_seed_column to the "
                "verified MiD household e-bike column (no silent fallback)."
            )
        household_cols.append(ebike_seed_column)
    households = pd.read_csv(
        households_path,
        usecols=list(dict.fromkeys(household_cols)),
        sep=detect_csv_separator(households_path),
    )
    person_cols = [
        columns.person_household_id, columns.person_id, columns.person_weight,
        columns.age, columns.sex,
    ]
    if columns.day_filter_col:
        person_cols.append(columns.day_filter_col)
    # Tier-3 raw control inputs (P_TAET / bildung1 / bildung2) for the employment/
    # education controls -- read + retained on the seed only when present (real MiD has
    # them; some fixtures don't), so tier0-2 + fixtures stay unaffected.
    _persons_sep = detect_csv_separator(persons_path)
    _persons_header = pd.read_csv(persons_path, sep=_persons_sep, nrows=0).columns
    _tier3_seed_cols = tuple(
        c for c in ("P_TAET", "bildung1", "bildung2")
        if c in _persons_header and c not in person_cols
    )
    person_cols.extend(_tier3_seed_cols)
    # trip_class (first PERSON-level KREIS control): load the raw diary trip count
    # (anzwege1) and the age-band conditioning column (alter_gr1) so the class can be
    # derived + the 803/804 item-nonresponse imputed within alter_gr1 after the
    # complete-household filter. Dedup-safe (alter_gr1 may already be present).
    if "trip_class" in active_kreis_entry_names:
        for _tc_col in ("anzwege1", "alter_gr1"):
            if _tc_col not in person_cols:
                person_cols.append(_tc_col)
    # employment_status (second PERSON-level KREIS control, task 4b / feature #172): load
    # the raw MiD Umfang-der-Erwerbstaetigkeit code (P_BKAT) and the age-band conditioning
    # column (alter_gr1) so the P9 seven-class label can be derived + the ~0.13% code-9
    # (keine Angabe) cases imputed within alter_gr1, mirroring the trip_class block above
    # exactly. Dedup-safe (alter_gr1 may already be present via trip_class or tier3).
    if "employment_status" in active_kreis_entry_names:
        for _es_col in ("P_BKAT", "alter_gr1"):
            if _es_col not in person_cols:
                person_cols.append(_es_col)
    # participation controls (work_participation task 4; leisure_participation /
    # education_participation task 5, feature #224): load the raw diary trip count
    # (anzwege1, the default trips_col mid.compute_has_purpose_trip uses to carry
    # through the 803/804 diary-nonresponse codes) and the age-band conditioning column
    # (alter_gr1) so the has-<purpose>-trip flag can be derived from the MiD Wege table +
    # the 803/804 codes imputed within alter_gr1, mirroring the trip_class block above.
    # Dedup-safe (anzwege1/alter_gr1 may already be present via trip_class/
    # employment_status/tier3); shared across all three participation entries since
    # they all read the same two raw columns.
    if active_kreis_entry_names & {"work_participation", "leisure_participation", "education_participation"}:
        for _pp_col in ("anzwege1", "alter_gr1"):
            if _pp_col not in person_cols:
                person_cols.append(_pp_col)
    persons = pd.read_csv(
        persons_path,
        usecols=list(dict.fromkeys(person_cols)),
        sep=_persons_sep,
    )

    # `None` -> standard weekday default; explicit empty iterable -> day filter OFF
    # (filter_complete_households treats `None` as "no day filtering"); a plain
    # `or` here would silently resurrect the default for an empty tuple.
    if day_filter_values is None:
        effective_day_filter = columns.day_filter_values
    elif len(tuple(day_filter_values)) == 0:
        effective_day_filter = None
    else:
        effective_day_filter = tuple(day_filter_values)
    households, persons, report = seedmod.filter_complete_households(
        households, persons, columns,
        day_filter_values=effective_day_filter,
    )

    # Derive the clean, MECE seed columns for the active count-style kreis controls
    # AFTER the complete-household filter (so the group-wise imputation pool reflects
    # only the kept seed households). economic_status needs no derivation here -- its
    # seed column (oek_status) is used RAW by the == k predicate (byte-identical to the
    # pre-existing include_status_seed_col=True behaviour).
    # Count-style entries impute the 99 missing code (household level); the person-level
    # trip_class entry imputes the 803/804 diary-nonresponse codes (within alter_gr1); the
    # person-level employment_status entry imputes the P_BKAT code-9 (keine Angabe) cases
    # (also within alter_gr1). All are random processes that REQUIRE the seeded
    # kreis_seed_rng (no unseeded randomness).
    _count_style_entries = active_kreis_entry_names & {
        "number_of_cars", "number_of_bicycles", "has_ebike"
    }
    _rng_style_entries = _count_style_entries | (
        active_kreis_entry_names & {
            "trip_class", "employment_status",
            "work_participation", "leisure_participation", "education_participation",
        }
    )
    if _rng_style_entries and kreis_seed_rng is None:
        raise ValueError(
            "load_mid_seed: kreis controls with seeded imputation "
            f"{sorted(_rng_style_entries)} are active but kreis_seed_rng is not set; "
            "random imputation of the missing/nonresponse codes must use an explicit seed."
        )
    if "number_of_cars" in active_kreis_entry_names:
        households = attributes.map_number_of_cars(households, rng=kreis_seed_rng)
    if "number_of_bicycles" in active_kreis_entry_names:
        households = attributes.map_number_of_bicycles(households, rng=kreis_seed_rng)
    if "has_ebike" in active_kreis_entry_names:
        households = attributes.map_has_ebike(
            households, ebike_col=ebike_seed_column, rng=kreis_seed_rng
        )

    extra_person_cols: tuple[str, ...] = ()
    if complete_members:
        # Fill member-incomplete households AFTER the day filter (the host
        # household must have passed it) and BEFORE the column selection (the
        # match keys H_GR/hhgr_gr/oek_status are dropped again below, so the
        # output household schema is unchanged).
        households, persons, _ = completion.complete_members(
            households, persons, rng=completion_rng,
            household_id=columns.household_id,
        )
        extra_person_cols = ("member_imputed", "source_H_ID", "source_P_ID")

    # trip_class (person-level KREIS control): derive the int-coded class (0..3) from the
    # raw diary trip count AFTER the complete-household filter + member completion, so the
    # 803/804 diary-nonresponse imputation pool reflects only the kept seed persons and any
    # mirror-imputed member inherits the mirror donor's diary (documented, see
    # MID_PERSON_ATTR_COLS). The rng guard above ensures kreis_seed_rng is set. The class
    # is seeded from the person's REALISED weekday plan source (not their own reporting-day
    # diary) so the control matches the SrV weekday target universe -- see
    # derive_trip_class_seed (audit 2026-07-09).
    if "trip_class" in active_kreis_entry_names:
        persons = derive_trip_class_seed(
            persons, rng=kreis_seed_rng,
            household_id=columns.person_household_id, person_id=columns.person_id)

    # employment_status (second PERSON-level KREIS control): derive the P9 seven-class
    # string from the raw P_BKAT code AFTER the complete-household filter + member
    # completion, so a mirror-imputed filler's employment_status matches its inherited
    # (donor-mirrored) P_BKAT and the code-9 imputation pool reflects only the kept seed
    # persons -- exactly mirroring the trip_class derivation above. The rng guard above
    # ensures kreis_seed_rng is set. Uses the SAME attributes.map_employment_status as the
    # post-expansion assembly.build_persons, so seed and expanded values agree
    # deterministically for the 99.87% of persons with a valid (non-9) P_BKAT; the code-9
    # imputed cases may differ by an independent rng draw -- acceptable for a control
    # (matches the trip_class precedent, task 4b).
    if "employment_status" in active_kreis_entry_names:
        persons = attributes.map_employment_status(persons, rng=kreis_seed_rng)

    # participation controls (work_participation task 4; leisure_participation /
    # education_participation task 5, feature #224): derive the 0/1 has-a-<purpose>-trip
    # flag from the MiD Wege table AFTER the complete-household filter + member
    # completion, seeded from the person's REALISED weekday plan source exactly like
    # trip_class (mid.derive_participation_seed) -- see that function's docstring for the
    # full weekday-vs-realised-plan rationale. The rng guard above ensures
    # kreis_seed_rng is set. Requires the full MiD Wege table, which load_mid_seed does
    # not otherwise read; loaded ONCE here (gated on ANY participation control being
    # active) so the OFF path never touches MiD2023_Wege.csv (byte-identical no-op).
    _active_participation_purposes = [
        purpose for purpose in ("work", "leisure", "education")
        if f"{purpose}_participation" in active_kreis_entry_names
    ]
    if _active_participation_purposes:
        wege = load_mid_wege(mid_dir)
        for purpose in _active_participation_purposes:
            persons = derive_participation_seed(
                persons, wege, purpose, rng=kreis_seed_rng,
                household_id=columns.person_household_id, person_id=columns.person_id)

    # Derive hh_type5 (Tier-1 household_type/Familientyp 5-class) from the
    # filtered persons frame.  derive_hh_type5 runs map_households_to_hhtype
    # (11-class) then collapses to the 5 Zensus Familientyp labels.  The result
    # is a per-household Series indexed by H_ID that is merged onto households.
    # This must happen BEFORE select_seed_columns so hh_type5 can be retained as
    # an extra_household_col; it uses the raw MiD column names (H_ID / HP_ALTER).
    hh_type5_series = seedmod.derive_hh_type5(
        persons,
        household_id_col=columns.person_household_id,
        age_col=columns.age,
    )
    households = households.join(
        hh_type5_series.rename("hh_type5"),
        on=columns.household_id,
    )

    _hh_extra = ("RegioStaR7", "H_GR", "hh_type5", "H_MIETE", "haustyp")
    _person_extra: tuple[str, ...] = ()
    for _entry in effective_kreis_entries:
        # Household-level entries retain their seed column on the households frame; the
        # first PERSON-level entry (trip_class) retains its derived seed column on the
        # persons frame so the population control expression (persons.trip_class == k)
        # can be evaluated by PopulationSim.
        if _entry.level == "household":
            if _entry.seed_column not in _hh_extra:
                _hh_extra = _hh_extra + (_entry.seed_column,)
        elif _entry.level == "person":
            if _entry.seed_column not in _person_extra:
                _person_extra = _person_extra + (_entry.seed_column,)
        else:
            raise NotImplementedError(
                f"load_mid_seed: kreis control entry {_entry.name!r} has unsupported level "
                f"{_entry.level!r} (expected 'household' or 'person')."
            )
    households, persons = seedmod.select_seed_columns(
        households, persons, columns,
        extra_household_cols=_hh_extra,
        extra_person_cols=extra_person_cols + _tier3_seed_cols + _person_extra,
    )
    return households, persons, report


def project_completed_seed(
    households, persons, columns, *,
    kreis_control_entries: Sequence[KreisAttributeControl] = (),
    kreis_seed_rng=None,
    ebike_seed_column: Optional[str] = None,
    include_status_seed_col: bool = False,
    mid_dir: Union[str, Path, None] = None,
):
    """Project completed-donor frames onto the PopulationSim seed, deriving the
    Tier-1 household_type column ``hh_type5`` exactly like :func:`load_mid_seed`.

    The complete_members=True path (stage.py) builds the seed from the completed
    donor frames; without the hh_type5 derivation below it called
    ``select_seed_columns`` WITHOUT deriving ``hh_type5``, so the per-batch seed CSV
    lacked the column and PopulationSim crashed (``'DataFrame' object has no
    attribute 'hh_type5'``) once the Tier-1 household_type control was enabled.
    This mirrors load_mid_seed's projection: derive the active count-style KREIS
    control seed columns from the raw donor columns the completed-donor frame
    already carries (H_ANZAUTO / anzpedrad / H_ANZPED / hhgr_gr; see
    :data:`MID_HOUSEHOLD_ATTR_COLS`), derive hh_type5 from the persons frame and
    join it onto households, then select the seed columns retaining the raw
    control cols (H_GR / H_MIETE / haustyp) plus hh_type5, RegioStaR7, and each
    active KREIS control's seed column.

    Args:
        kreis_control_entries: The ACTIVE :class:`KreisAttributeControl` registry
            entries (see :mod:`braunschweig.popsim.kreis_attribute_control`) whose
            ``seed_column`` must be present on the returned seed households.
            ``economic_status`` is a raw ``oek_status`` pass-through (already
            present on the completed-donor households). ``number_of_cars`` /
            ``number_of_bicycles`` / ``has_ebike`` are derived here via the
            corresponding ``attributes.map_*`` resolver (99 missing code imputed)
            from the raw H_ANZAUTO / anzpedrad / ``ebike_seed_column`` columns the
            completed-donor households already carry (``anzpedrad`` = bicycles
            INCLUDING pedelecs, MiD H12.3 / SrV alle-Raeder construct; the raw
            e-bike column was VERIFIED 2026-07-08 on the MiD B1 microdata to be
            ``H_ANZPED``, see :mod:`braunschweig.popsim.attributes`). has_ebike is
            now fully wired on this path (formerly deferred, issue #116).
        kreis_seed_rng: Seeded :class:`numpy.random.RandomState` for the
            count-style entry derivations (``number_of_cars`` /
            ``number_of_bicycles`` / ``has_ebike``). REQUIRED when any is active
            (seeded randomness rule: no random process without an explicit seed).
        ebike_seed_column: Name of the (server-verified) MiD household e-bike
            column feeding the ``has_ebike`` entry (default ``H_ANZPED`` at the
            stage config layer, see ``stage.KEY_EBIKE_SEED_COLUMN``). REQUIRED
            when ``has_ebike`` is active (no silent fallback to a guessed column
            name), mirroring :func:`load_mid_seed`.
        include_status_seed_col: DEPRECATED alias for
            ``kreis_control_entries=(economic_status entry,)``; kept so existing
            callers/tests stay byte-identical (raw ``oek_status`` pass-through,
            no resolve/derivation).
        mid_dir: Directory containing the MiD 2023 delivery (``MiD2023_Wege.csv``).
            REQUIRED when any participation entry (``work_participation`` feature #224
            task 4; ``leisure_participation`` / ``education_participation`` task 5) is
            active: unlike the other KREIS control seed columns, these are derived from
            the full MiD Wege table (mid.load_mid_wege), which the completed-donor
            frames do not carry. ``None`` (default) is a no-op when no participation
            control is active (no silent fallback if one is).
    """
    # Deprecated alias: include_status_seed_col=True is equivalent to activating the
    # economic_status registry entry (byte-identical to the pre-existing behaviour).
    effective_kreis_entries: list[KreisAttributeControl] = list(kreis_control_entries)
    if include_status_seed_col and not any(
        entry.name == "economic_status" for entry in effective_kreis_entries
    ):
        effective_kreis_entries.append(
            next(entry for entry in KREIS_CONTROL_REGISTRY if entry.name == "economic_status")
        )
    active_kreis_entry_names = {entry.name for entry in effective_kreis_entries}

    if "has_ebike" in active_kreis_entry_names and not ebike_seed_column:
        raise ValueError(
            "project_completed_seed: has_ebike kreis control is active but ebike_seed_column "
            "is not configured; set braunschweig.population.popsim.ebike_seed_column to the "
            "verified MiD household e-bike column (no silent fallback)."
        )

    # Derive the clean, MECE seed columns for the active count-style kreis controls from
    # the raw H_ANZAUTO / anzpedrad / ebike_seed_column columns the completed-donor
    # households already carry (see MID_HOUSEHOLD_ATTR_COLS); mirrors the load_mid_seed
    # derivation exactly.
    # Count-style entries impute the 99 missing code (household level); trip_class imputes
    # the 803/804 diary-nonresponse codes (person level); employment_status imputes the
    # P_BKAT code-9 (keine Angabe) cases (also person level). All REQUIRE the seeded rng.
    _count_style_entries = active_kreis_entry_names & {
        "number_of_cars", "number_of_bicycles", "has_ebike"
    }
    _rng_style_entries = _count_style_entries | (
        active_kreis_entry_names & {
            "trip_class", "employment_status",
            "work_participation", "leisure_participation", "education_participation",
        }
    )
    if _rng_style_entries and kreis_seed_rng is None:
        raise ValueError(
            "project_completed_seed: kreis controls with seeded imputation "
            f"{sorted(_rng_style_entries)} are active but kreis_seed_rng is not set; "
            "random imputation of the missing/nonresponse codes must use an explicit seed."
        )
    if "number_of_cars" in active_kreis_entry_names:
        households = attributes.map_number_of_cars(households, rng=kreis_seed_rng)
    if "number_of_bicycles" in active_kreis_entry_names:
        # Default bikes_col="anzpedrad" (bicycles INCLUDING pedelecs/e-bikes; MiD H12.3 /
        # SrV alle-Raeder construct); see attributes.map_number_of_bicycles.
        households = attributes.map_number_of_bicycles(households, rng=kreis_seed_rng)
    if "has_ebike" in active_kreis_entry_names:
        households = attributes.map_has_ebike(
            households, ebike_col=ebike_seed_column, rng=kreis_seed_rng
        )
    if "trip_class" in active_kreis_entry_names:
        # Derive trip_class from each person's REALISED weekday plan source, not their own
        # reporting-day diary: after weekend_plan_match the completed-donor frame's
        # source_(H_ID,P_ID) point to weekday donors, so the source's anzwege1 is both the
        # SrV Di-Do target universe and the trips the synthetic person executes. See
        # derive_trip_class_seed (audit 2026-07-09; ~29% weekend reporters otherwise
        # carried a weekend diary count into the weekday-anchored control).
        persons = derive_trip_class_seed(
            persons, rng=kreis_seed_rng,
            household_id=columns.person_household_id, person_id=columns.person_id)
    if "employment_status" in active_kreis_entry_names:
        # Derive employment_status from the completed donor's P_BKAT (already loaded via
        # MID_PERSON_ATTR_COLS -- see mid.MID_PERSON_ATTR_COLS); mirrors load_mid_seed's
        # derivation exactly. A mirror-imputed filler inherits the mirror donor's P_BKAT
        # (member completion samples whole donor person rows, same as anzwege1/trip_class),
        # so this reflects the full completed population, agreeing deterministically with
        # assembly.build_persons for the 99.87% of persons with a valid (non-9) P_BKAT.
        persons = attributes.map_employment_status(persons, rng=kreis_seed_rng)
    _active_participation_purposes = [
        purpose for purpose in ("work", "leisure", "education")
        if f"{purpose}_participation" in active_kreis_entry_names
    ]
    if _active_participation_purposes:
        # Derive each active <purpose>_participation from the completed donor's MiD Wege
        # table (loaded from mid_dir; the completed-donor frames do not carry the Wege
        # rows themselves). Seeded from the person's REALISED weekday plan source exactly
        # like trip_class (mid.derive_participation_seed) -- see that function's
        # docstring for the full weekday-vs-realised-plan rationale. Wege is loaded ONCE
        # and reused for every active purpose (work_participation task 4; leisure_
        # participation / education_participation task 5, feature #224).
        if mid_dir is None:
            raise ValueError(
                "project_completed_seed: a participation kreis control "
                f"{_active_participation_purposes} is active but mid_dir is not set; cannot "
                "load the MiD Wege table to derive has_<purpose>_trip (no silent fallback)."
            )
        wege = load_mid_wege(mid_dir)
        for purpose in _active_participation_purposes:
            persons = derive_participation_seed(
                persons, wege, purpose, rng=kreis_seed_rng,
                household_id=columns.person_household_id, person_id=columns.person_id)

    hh_type5_series = seedmod.derive_hh_type5(
        persons,
        household_id_col=columns.person_household_id,
        age_col=columns.age,
    )
    households = households.join(
        hh_type5_series.rename("hh_type5"),
        on=columns.household_id,
    )
    _hh_extra = ("RegioStaR7", "H_GR", "hh_type5", "H_MIETE", "haustyp")
    _person_extra: tuple[str, ...] = ()
    for _entry in effective_kreis_entries:
        # Household-level entries retain their seed column on the households frame; the
        # first PERSON-level entry (trip_class) retains its derived seed column on the
        # persons frame (mirrors load_mid_seed).
        if _entry.level == "household":
            if _entry.seed_column not in _hh_extra:
                _hh_extra = _hh_extra + (_entry.seed_column,)
        elif _entry.level == "person":
            if _entry.seed_column not in _person_extra:
                _person_extra = _person_extra + (_entry.seed_column,)
        else:
            raise NotImplementedError(
                f"project_completed_seed: kreis control entry {_entry.name!r} has unsupported "
                f"level {_entry.level!r} (expected 'household' or 'person')."
            )
    return seedmod.select_seed_columns(
        households, persons, columns,
        extra_household_cols=_hh_extra,
        extra_person_cols=tuple(
            c for c in ("P_TAET", "bildung1", "bildung2") if c in persons.columns
        ) + _person_extra,
    )


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
