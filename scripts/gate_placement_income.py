"""OFF/ON invariant gate for placement_income (L2 of issue #108: donor-coherent income
via signature-preserving donor reallocation).

Compares TWO independent synpp caches of the SAME 2-Kreis popsim_mid pipeline (Salzgitter
ars5 03102, Wolfsburg ars5 03103; random_seed=1234) where
``braunschweig.population.popsim.placement_income`` (false / true) is the ONLY config
difference, and verifies the feature's HARD invariants on the real ``data.census.filtered``
(``braunschweig.popsim.stage``) output:

  1. Per-(100 m cell, control signature) household counts are IDENTICAL OFF vs ON: the
     reallocation only permutes WHICH equal-signature donor occupies a cell/Kreis slot,
     so every PopulationSim control aggregate at every geography must be unchanged.
  2. Per-(Kreis, control signature) household counts are IDENTICAL OFF vs ON (the same
     invariant at the coarser Kreis geography).
  3. Every donor's total clone count (summed over the whole 2-Kreis region) is IDENTICAL
     OFF vs ON: the reallocation must relocate clones, never create or destroy them.
  4. The ON cache's ``popsim_work/placement_income_diag.csv`` is present (the stage only
     writes it when the reallocation actually ran).
  5. The OFF cache's ``popsim_work/placement_income_diag.csv`` is ABSENT (must not be
     produced when the flag is off).

PASS requires ALL FIVE; see ``decide_gate``. Income-attainment (realized vs INKAR-derived
target per Kreis) and within-cell income coherence (Spearman income-vs-car-ownership) are
additionally REPORTED, never as pass/fail thresholds: this gate checks exactness
invariants, not a fit-to-target bound (no invented reference values; convergence is not
validation -- see CLAUDE.md "No invented reference values; convergence is not validation").

Usage (from the repo root, with the eqasim env python, PYTHONUTF8=1)::

    python scripts/gate_placement_income.py

    python scripts/gate_placement_income.py --cache-off <off_dir> --cache-on <on_dir>

    python scripts/gate_placement_income.py --skip-metrics   # hard invariants only

Defaults to the Task-7 gate caches:
  OFF: eqasim-data/cache_gate_l2_off  (braunschweig.population.popsim.placement_income=false)
  ON:  eqasim-data/cache_gate_l2_on   (braunschweig.population.popsim.placement_income=true)

Both caches must already exist and contain a completed ``data.census.filtered`` run; this
script never launches synpp itself (contrast scripts/gate_income_tilt.py, which can launch
the pipeline via --config-base). Exit codes: 0 = PASS, 1 = FAIL (an invariant was
violated), 2 = BLOCKED (a required cache directory or artifact is missing -- the message
names exactly what).

The pure analysis functions (``household_level``, ``signature_group_counts``,
``clone_profile``, ``compare_counts``, ``decide_gate``) can be imported and unit-tested
independently of any pipeline run (see tests/test_gate_placement_income.py).
"""
from __future__ import annotations

import argparse
import logging
import pathlib
import pickle
import sys
from typing import Any, Mapping

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Make the repo root importable when run as a script.
# ---------------------------------------------------------------------------
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

logger = logging.getLogger(__name__)

# The Task-7 gate caches (see the config_gate_placement_income_{off,on}.yml working
# directories); the ONLY config difference between them is the placement_income flag.
_DEFAULT_CACHE_OFF = "C:/Users/bienzeisler/Documents/GitHub/eqasim-bs/eqasim-data/cache_gate_l2_off"
_DEFAULT_CACHE_ON = "C:/Users/bienzeisler/Documents/GitHub/eqasim-bs/eqasim-data/cache_gate_l2_on"

# The control-tier / employment-grid configuration used by BOTH gate runs (see
# config_gate_placement_income_{off,on}.yml: control_tiers: tier0,tier1,tier2; the
# employment grid toggle is not set by either config, so it stays at its "off" default).
_CONTROL_TIERS = ("tier0", "tier1", "tier2")
_INCLUDE_EMPLOYMENT_GRID = False
_SEED_NAME = "mid"


# ---------------------------------------------------------------------------
# Pure analysis functions (unit-testable without any pipeline run)
# ---------------------------------------------------------------------------

_HOUSEHOLD_LEVEL_COLUMNS = (
    "household_id",
    "source_household_id",
    "ZENSUS100m",
    "departement_id",
    "household_income_eur",
    "economic_status",
    "number_of_cars",
)


def household_level(persons: pd.DataFrame) -> pd.DataFrame:
    """Collapse a synthetic persons frame to one row per ``household_id``.

    Takes the first row (by ``household_id`` sort order) for each household-level
    column -- the same idiom ``braunschweig.popsim.placement_income.apply_own_income``
    uses to collapse persons to households. Fails fast if any household mixes more than
    one ``source_household_id``: every person in a synthetic household must be expanded
    from the SAME donor household, so disagreement indicates a corrupted expansion, not
    something to silently paper over by picking an arbitrary value.

    Parameters
    ----------
    persons:
        Synthetic persons frame (``braunschweig.popsim.stage`` output) carrying at least
        ``household_id, source_household_id, ZENSUS100m, departement_id,
        household_income_eur, economic_status, number_of_cars``.

    Returns
    -------
    pandas.DataFrame
        One row per ``household_id`` with the columns listed above.

    Raises
    ------
    ValueError
        If a required column is missing, or if any ``household_id`` carries more than
        one distinct ``source_household_id``.
    """
    missing = [c for c in _HOUSEHOLD_LEVEL_COLUMNS if c not in persons.columns]
    if missing:
        raise ValueError(
            f"household_level requires columns {missing}; got {sorted(persons.columns)[:20]}."
        )
    n_distinct_donor = persons.groupby("household_id")["source_household_id"].nunique()
    inconsistent = n_distinct_donor[n_distinct_donor > 1]
    if len(inconsistent):
        raise ValueError(
            f"household_level: {len(inconsistent)} household_id(s) carry more than one "
            f"distinct source_household_id (e.g. {inconsistent.index[:5].tolist()}); every "
            f"person in a synthetic household must share one donor household."
        )
    hh = (
        persons[list(_HOUSEHOLD_LEVEL_COLUMNS)]
        .sort_values("household_id")
        .groupby("household_id", sort=True)
        .first()
        .reset_index()
    )
    return hh


def signature_group_counts(
    hh: pd.DataFrame,
    raw_id_by_surrogate: Mapping,
    signature_by_raw_id: Mapping,
    *,
    cell_col: str = "ZENSUS100m",
    kreis_col: str = "departement_id",
) -> tuple[pd.Series, pd.Series]:
    """Count households per (cell, signature) and per (Kreis, signature).

    Each household's run-local surrogate ``source_household_id`` is mapped to its raw
    MiD donor household id (via ``raw_id_by_surrogate``, THIS run's own pseudonym map --
    never share a mapping across runs) and then to its control signature (via
    ``signature_by_raw_id``, rebuilt once and shared across both runs since the seed is
    identical). Two households with equal (cell, signature) [or (Kreis, signature)]
    counts OFF vs ON contribute identically to every PopulationSim control aggregate at
    that geography; this is the per-run invariant the placement_income reallocation must
    preserve exactly.

    Parameters
    ----------
    hh:
        Household-level frame (``household_level`` output) with at least
        ``source_household_id``, ``cell_col``, ``kreis_col``.
    raw_id_by_surrogate:
        Mapping from this run's surrogate ``source_household_id`` to the raw MiD donor
        household id.
    signature_by_raw_id:
        Mapping from raw donor household id to its control signature.
    cell_col, kreis_col:
        Column names for the cell and Kreis geography keys.

    Returns
    -------
    tuple[pandas.Series, pandas.Series]
        ``(cell_signature_counts, kreis_signature_counts)``, each indexed by a
        ``(geography, signature)`` MultiIndex with integer household counts.

    Raises
    ------
    ValueError
        If any surrogate id has no raw-id mapping, or any raw id has no signature
        (fail-fast: a silently-dropped household would understate its control
        contribution and hide a broken exactness guarantee).
    """
    surrogates = hh["source_household_id"]
    missing_surrogate = sorted(set(surrogates.unique()) - set(raw_id_by_surrogate.keys()))
    if missing_surrogate:
        raise ValueError(
            f"signature_group_counts: {len(missing_surrogate)} surrogate household id(s) "
            f"have no raw-id mapping (e.g. {missing_surrogate[:10]}); the pseudonym map "
            f"must cover every synthetic household's donor."
        )
    raw_ids = surrogates.map(raw_id_by_surrogate)
    missing_signature = sorted(set(raw_ids.unique()) - set(signature_by_raw_id.keys()))
    if missing_signature:
        raise ValueError(
            f"signature_group_counts: {len(missing_signature)} raw donor id(s) have no "
            f"control signature (e.g. {missing_signature[:10]}); signatures must cover "
            f"every donor the seed carries."
        )
    work = pd.DataFrame({
        cell_col: hh[cell_col].to_numpy(),
        kreis_col: hh[kreis_col].to_numpy(),
        "_signature": raw_ids.map(signature_by_raw_id).to_numpy(),
    })
    cell_counts = work.groupby([cell_col, "_signature"]).size()
    kreis_counts = work.groupby([kreis_col, "_signature"]).size()
    return cell_counts, kreis_counts


def clone_profile(hh: pd.DataFrame, raw_id_by_surrogate: Mapping) -> pd.Series:
    """Per raw donor household id, the total number of synthetic households cloned from it.

    Parameters
    ----------
    hh:
        Household-level frame with at least ``source_household_id``.
    raw_id_by_surrogate:
        Mapping from this run's surrogate ``source_household_id`` to the raw donor id.

    Returns
    -------
    pandas.Series
        Indexed by raw donor id, values = clone count, sorted by index (deterministic).

    Raises
    ------
    ValueError
        If any surrogate id has no raw-id mapping (fail-fast; same rationale as
        ``signature_group_counts``).
    """
    surrogates = hh["source_household_id"]
    missing_surrogate = sorted(set(surrogates.unique()) - set(raw_id_by_surrogate.keys()))
    if missing_surrogate:
        raise ValueError(
            f"clone_profile: {len(missing_surrogate)} surrogate household id(s) have no "
            f"raw-id mapping (e.g. {missing_surrogate[:10]})."
        )
    raw_ids = surrogates.map(raw_id_by_surrogate)
    return raw_ids.value_counts().sort_index()


def compare_counts(off: pd.Series, on: pd.Series) -> dict:
    """Compare two count Series (per-(cell,signature), per-(Kreis,signature), or
    per-donor clone counts) via an aligned reindex over the union of their keys.

    A key present in only one of the two series is treated as (value, 0) rather than
    being silently ignored, so a household/donor that disappeared (or appeared) between
    OFF and ON is counted as a difference.

    Returns
    -------
    dict
        ``{"equal": bool, "n_keys_off": int, "n_keys_on": int, "n_diff_keys": int,
        "max_abs_diff": int}``.
    """
    all_keys = off.index.union(on.index)
    if len(all_keys) == 0:
        return {"equal": True, "n_keys_off": 0, "n_keys_on": 0, "n_diff_keys": 0, "max_abs_diff": 0}
    off_aligned = off.reindex(all_keys, fill_value=0).astype(np.int64)
    on_aligned = on.reindex(all_keys, fill_value=0).astype(np.int64)
    diff = (off_aligned - on_aligned).abs()
    n_diff_keys = int((diff != 0).sum())
    return {
        "equal": n_diff_keys == 0,
        "n_keys_off": int(len(off)),
        "n_keys_on": int(len(on)),
        "n_diff_keys": n_diff_keys,
        "max_abs_diff": int(diff.max()),
    }


def decide_gate(
    cell_counts_cmp: dict,
    kreis_counts_cmp: dict,
    clone_cmp: dict,
    diag_present_on: bool,
    diag_absent_off: bool,
) -> tuple[str, list[str]]:
    """Decide PASS/FAIL from the placement_income hard invariants.

    PASS requires ALL of:
      1. per-(cell, signature) household counts equal OFF vs ON;
      2. per-(Kreis, signature) household counts equal OFF vs ON;
      3. per-donor clone profile equal OFF vs ON;
      4. the ON cache's ``placement_income_diag.csv`` is present;
      5. the OFF cache's ``placement_income_diag.csv`` is ABSENT.

    Income-attainment and within-cell coherence metrics are deliberately NOT inputs
    here: they are REPORTED only (see ``run_gate``'s metrics section) -- this function
    checks exactness invariants, not a fit-to-target threshold (no invented reference
    values; see CLAUDE.md "No invented reference values; convergence is not validation").

    Parameters
    ----------
    cell_counts_cmp, kreis_counts_cmp, clone_cmp:
        ``compare_counts`` output dicts.
    diag_present_on:
        Whether the ON cache's ``popsim_work/placement_income_diag.csv`` exists.
    diag_absent_off:
        Whether the OFF cache's ``popsim_work/placement_income_diag.csv`` is ABSENT.

    Returns
    -------
    tuple[str, list[str]]
        ``(verdict, reasons)`` where ``verdict`` is ``"PASS"`` or ``"FAIL"`` and
        ``reasons`` lists every violated condition (empty on PASS).
    """
    reasons: list[str] = []
    if not cell_counts_cmp.get("equal", False):
        reasons.append(
            "per-(cell, signature) household counts differ OFF vs ON "
            f"(n_diff_keys={cell_counts_cmp.get('n_diff_keys')}, "
            f"max_abs_diff={cell_counts_cmp.get('max_abs_diff')}): a 100 m control "
            "aggregate was not preserved by the reallocation."
        )
    if not kreis_counts_cmp.get("equal", False):
        reasons.append(
            "per-(Kreis, signature) household counts differ OFF vs ON "
            f"(n_diff_keys={kreis_counts_cmp.get('n_diff_keys')}, "
            f"max_abs_diff={kreis_counts_cmp.get('max_abs_diff')}): a Kreis-level "
            "control aggregate was not preserved by the reallocation."
        )
    if not clone_cmp.get("equal", False):
        reasons.append(
            "per-donor clone profile differs OFF vs ON "
            f"(n_diff_keys={clone_cmp.get('n_diff_keys')}, "
            f"max_abs_diff={clone_cmp.get('max_abs_diff')}): at least one donor's total "
            "clone count changed, which the reallocation must never do."
        )
    if not diag_present_on:
        reasons.append(
            "placement_income_diag.csv is MISSING from the ON cache's popsim_work "
            "(expected whenever braunschweig.population.popsim.placement_income=true)."
        )
    if not diag_absent_off:
        reasons.append(
            "placement_income_diag.csv is PRESENT in the OFF cache's popsim_work "
            "(must not exist when braunschweig.population.popsim.placement_income=false)."
        )
    verdict = "PASS" if not reasons else "FAIL"
    return verdict, reasons


# ---------------------------------------------------------------------------
# Runtime assembly (guarded; loads real synpp caches -- see __main__ below)
# ---------------------------------------------------------------------------

class GateBlockedError(Exception):
    """Raised when a required cache directory or artifact is missing/invalid, blocking
    the gate from proceeding. Distinct from a gate FAIL (decide_gate ran and an
    invariant was violated): a blocker means the harness could not even run the check.
    """


def _require_dir(path: pathlib.Path, what: str) -> pathlib.Path:
    """Fail fast with a clear message naming exactly what is missing."""
    if not path.is_dir():
        raise GateBlockedError(f"{what} not found: {path}")
    return path


def _require_file(path: pathlib.Path, what: str) -> pathlib.Path:
    """Fail fast with a clear message naming exactly what is missing."""
    if not path.is_file():
        raise GateBlockedError(f"{what} not found: {path}")
    return path


def _load_persons_from_cache(working_directory: pathlib.Path) -> pd.DataFrame:
    """Load the ``data.census.filtered`` (``braunschweig.popsim.stage``) output from a
    synpp cache directory.

    Mirrors ``scripts/gate_income_tilt.py:_load_persons_from_cache``'s glob-and-pick-
    latest mechanics exactly: pattern ``braunschweig.popsim.stage*.p``, most-recently-
    modified match wins if several hashes exist (synpp names cache files after the
    underlying stage class, not the run config's alias, so this pattern is unaffected by
    the ``data.census.filtered`` alias). Adapted to RAISE (not return None) because a
    missing stage cache always blocks this gate -- there is no partial analysis to fall
    back to; see the module docstring for the documented deviation.
    """
    pattern = "braunschweig.popsim.stage*.p"
    matches = list(working_directory.glob(pattern))
    if not matches:
        raise GateBlockedError(
            f"no synpp stage cache matching {pattern!r} found in {working_directory} "
            "(the data.census.filtered / braunschweig.popsim.stage run has not produced "
            "output yet)."
        )
    latest = max(matches, key=lambda p: p.stat().st_mtime)
    with open(latest, "rb") as f:
        return pickle.load(f)


def _load_pseudonym_map(work_dir: pathlib.Path) -> dict:
    """Load ``<work_dir>/pseudonym_map.csv`` into a ``{source_household_id: H_ID}`` dict.

    Both sides are cast to plain ``int``. ``H_ID`` is a MiD respondent id (no
    leading-zero risk, unlike AGS/ARS area codes), but different seed-construction code
    paths have been observed to serialise it as int64 in one CSV and float64 (e.g.
    "10000010.0") in another; casting to ``int`` normalises both so the join never
    silently misses a valid match purely because of a numeric-dtype mismatch.
    """
    path = _require_file(work_dir / "pseudonym_map.csv", "pseudonym_map.csv")
    df = pd.read_csv(path)
    required = {"source_household_id", "H_ID"}
    missing = required - set(df.columns)
    if missing:
        raise GateBlockedError(f"{path} is missing columns {sorted(missing)}.")
    mapping: dict = {}
    for surrogate, raw_id in zip(df["source_household_id"].to_numpy(), df["H_ID"].to_numpy()):
        mapping[int(surrogate)] = int(raw_id)
    return mapping


def _find_one_batch_dir(work_dir: pathlib.Path) -> pathlib.Path:
    """Return the first (sorted) ``batch_*`` PopulationSim run folder under ``work_dir``."""
    batches = sorted(work_dir.glob("batch_*"))
    if not batches:
        raise GateBlockedError(f"no batch_* PopulationSim run folders found in {work_dir}.")
    return batches[0]


def _load_seed_tables(batch_dir: pathlib.Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load the seed households/persons CSVs PopulationSim consumed for one batch."""
    hh_path = _require_file(batch_dir / "data" / "seed_households.csv", "seed_households.csv")
    pp_path = _require_file(batch_dir / "data" / "seed_persons.csv", "seed_persons.csv")
    return pd.read_csv(hh_path), pd.read_csv(pp_path)


def _sort_key_columns(df: pd.DataFrame) -> list[str]:
    """Deterministic sort key for a seed table: (H_ID, P_ID) when both exist, else H_ID."""
    if "H_ID" in df.columns and "P_ID" in df.columns:
        return ["H_ID", "P_ID"]
    if "H_ID" in df.columns:
        return ["H_ID"]
    return [df.columns[0]]


def _assert_seed_tables_identical(
    off_households: pd.DataFrame,
    off_persons: pd.DataFrame,
    on_households: pd.DataFrame,
    on_persons: pd.DataFrame,
) -> None:
    """Fail loudly if the OFF and ON batch seed tables are not identical.

    placement_income runs strictly AFTER the per-batch PopulationSim synthesis that
    consumes these seed tables (see ``braunschweig.popsim.stage``), so with the SAME
    random_seed and region the seed tables must be byte-identical between the two runs.
    A difference would mean the OFF/ON configs differ in more than the placement_income
    flag, invalidating the whole gate premise (signatures rebuilt from one run's seed
    would not necessarily apply to the other).
    """
    for label, off_df, on_df in (
        ("seed_households.csv", off_households, on_households),
        ("seed_persons.csv", off_persons, on_persons),
    ):
        if list(off_df.columns) != list(on_df.columns):
            raise GateBlockedError(
                f"{label} column sets differ between the OFF and ON cache batch folders "
                f"(OFF={list(off_df.columns)}, ON={list(on_df.columns)}); the two runs "
                "must differ ONLY in placement_income."
            )
        if off_df.shape != on_df.shape:
            raise GateBlockedError(
                f"{label} shapes differ between the OFF ({off_df.shape}) and ON "
                f"({on_df.shape}) cache batch folders."
            )
        sort_cols = _sort_key_columns(off_df)
        off_sorted = off_df.sort_values(sort_cols).reset_index(drop=True)
        on_sorted = on_df.sort_values(sort_cols).reset_index(drop=True)
        if not off_sorted.equals(on_sorted):
            raise GateBlockedError(
                f"{label} content differs between the OFF and ON cache batch folders; "
                "the two runs must differ ONLY in placement_income."
            )


def _default_active_kreis_control_names() -> list[str]:
    """Replicate ``braunschweig.popsim.stage.active_kreis_entries``'s outcome for a
    config that overrides NONE of the per-attribute KREIS-control toggles -- exactly the
    OFF/ON gate configs, which set only
    ``braunschweig.population.popsim.placement_income`` and leave every
    ``*_kreis_control`` key at its declared default.

    Returns the REGISTRY entry names (in REGISTRY order) whose toggle default is "on",
    read from ``stage._KREIS_CONTROL_DEFAULT`` / ``stage._KREIS_CONTROL_TOGGLE_KEY``
    rather than hard-coded, so a future registry change is picked up automatically. This
    mirrors ``tests/test_kreis_control_stage_wiring.py``'s ``_FakeContext({})`` case
    (empty override dict).
    """
    from braunschweig.popsim import stage as _stage

    class _DefaultOnlyContext:
        """Minimal synpp ExecuteContext stand-in carrying no config overrides."""

        @staticmethod
        def config(key: str):
            for name, toggle_key in _stage._KREIS_CONTROL_TOGGLE_KEY.items():
                if key == toggle_key:
                    return _stage._KREIS_CONTROL_DEFAULT[name]
            raise KeyError(f"_DefaultOnlyContext: no declared default for config key {key!r}.")

    active = _stage.active_kreis_entries(_DefaultOnlyContext(), _SEED_NAME)
    return [entry.name for entry in active]


def _fmt(x: Any, spec: str = "{:.4f}") -> str:
    """Format a possibly-NaN/None/str value defensively for report printing."""
    try:
        return spec.format(float(x))
    except (TypeError, ValueError):
        return str(x)


def run_gate(
    cache_off: pathlib.Path,
    cache_on: pathlib.Path,
    *,
    skip_metrics: bool = False,
) -> dict[str, Any]:
    """Run the full placement_income OFF/ON gate against two REAL synpp caches.

    Parameters
    ----------
    cache_off, cache_on:
        Working directories of the OFF and ON synpp caches (each must already contain a
        completed ``data.census.filtered`` / ``braunschweig.popsim.stage`` run and its
        ``popsim_work`` folder).
    skip_metrics:
        When True, skip the REPORTED-only metrics section (income attainment, within-
        cell coherence, diag-CSV echo, persons.attrs echo) and return only the hard
        invariant comparisons + verdict.

    Returns
    -------
    dict
        Consumed by ``_render_report``; see its body for the full key set.

    Raises
    ------
    GateBlockedError
        If a required cache directory or artifact is missing/invalid.
    """
    cache_off = pathlib.Path(cache_off)
    cache_on = pathlib.Path(cache_on)
    _require_dir(cache_off, "OFF cache working_directory")
    _require_dir(cache_on, "ON cache working_directory")
    work_off = _require_dir(cache_off / "popsim_work", "OFF popsim_work directory")
    work_on = _require_dir(cache_on / "popsim_work", "ON popsim_work directory")

    logger.info("Loading OFF persons frame from %s ...", cache_off)
    persons_off = _load_persons_from_cache(cache_off)
    logger.info("Loaded OFF persons frame: %d rows.", len(persons_off))
    logger.info("Loading ON persons frame from %s ...", cache_on)
    persons_on = _load_persons_from_cache(cache_on)
    logger.info("Loaded ON persons frame: %d rows.", len(persons_on))

    hh_off = household_level(persons_off)
    hh_on = household_level(persons_on)
    logger.info(
        "Household-level frames: OFF %d households, ON %d households.",
        len(hh_off), len(hh_on),
    )

    raw_id_by_surrogate_off = _load_pseudonym_map(work_off)
    raw_id_by_surrogate_on = _load_pseudonym_map(work_on)

    batch_off = _find_one_batch_dir(work_off)
    batch_on = _find_one_batch_dir(work_on)
    seed_hh_off, seed_pp_off = _load_seed_tables(batch_off)
    seed_hh_on, seed_pp_on = _load_seed_tables(batch_on)
    _assert_seed_tables_identical(seed_hh_off, seed_pp_off, seed_hh_on, seed_pp_on)
    logger.info(
        "Verified OFF (%s) / ON (%s) seed tables are identical.", batch_off.name, batch_on.name,
    )

    active_entry_names = _default_active_kreis_control_names()
    logger.info(
        "Active KREIS attribute-control registry entries used for the signature catalog "
        "(default-ON, no toggle overrides in either gate config): %s", active_entry_names,
    )

    from braunschweig.popsim import control_spec as _cs
    from braunschweig.popsim import placement_income as _pi

    catalog = _cs.full_catalog(
        include_tiers=_CONTROL_TIERS,
        include_employment_grid=_INCLUDE_EMPLOYMENT_GRID,
        kreis_control_names=active_entry_names,
    )
    controls = _cs.controls_for_seed(catalog, _SEED_NAME)
    signatures = _pi.donor_control_signatures(controls, seed_hh_off, seed_pp_off, seed=_SEED_NAME)
    signature_by_raw_id = {int(k): v for k, v in signatures.items()}
    logger.info(
        "Rebuilt %d donor control signatures (%d distinct groups) from batch %s.",
        len(signature_by_raw_id), len(set(signature_by_raw_id.values())), batch_off.name,
    )

    cell_counts_off, kreis_counts_off = signature_group_counts(
        hh_off, raw_id_by_surrogate_off, signature_by_raw_id)
    cell_counts_on, kreis_counts_on = signature_group_counts(
        hh_on, raw_id_by_surrogate_on, signature_by_raw_id)
    cell_counts_cmp = compare_counts(cell_counts_off, cell_counts_on)
    kreis_counts_cmp = compare_counts(kreis_counts_off, kreis_counts_on)

    clone_off = clone_profile(hh_off, raw_id_by_surrogate_off)
    clone_on = clone_profile(hh_on, raw_id_by_surrogate_on)
    clone_cmp = compare_counts(clone_off, clone_on)

    diag_path_on = work_on / "placement_income_diag.csv"
    diag_path_off = work_off / "placement_income_diag.csv"
    diag_present_on = diag_path_on.is_file()
    diag_absent_off = not diag_path_off.is_file()

    verdict, reasons = decide_gate(
        cell_counts_cmp, kreis_counts_cmp, clone_cmp, diag_present_on, diag_absent_off)
    for reason in reasons:
        logger.warning("GATE FAIL reason: %s", reason)
    logger.info("GATE verdict: %s", verdict)

    results: dict[str, Any] = {
        "cache_off": str(cache_off),
        "cache_on": str(cache_on),
        "verdict": verdict,
        "reasons": reasons,
        "cell_counts_cmp": cell_counts_cmp,
        "kreis_counts_cmp": kreis_counts_cmp,
        "clone_cmp": clone_cmp,
        "diag_present_on": diag_present_on,
        "diag_absent_off": diag_absent_off,
        "active_kreis_control_names": active_entry_names,
        "n_households_off": int(len(hh_off)),
        "n_households_on": int(len(hh_on)),
        "batch_used": batch_off.name,
        "metrics_skipped": bool(skip_metrics),
    }

    if skip_metrics:
        logger.info("--skip-metrics: hard invariants only; metrics section skipped.")
        return results

    # --- Metrics (REPORTED only; never a gate threshold) ------------------------------
    if not diag_present_on:
        logger.warning(
            "placement_income_diag.csv absent from the ON cache; the target-dependent "
            "metric (income_attainment_by_kreis) is skipped."
        )
        results["diag_rows"] = None
    else:
        diag_df = pd.read_csv(diag_path_on, dtype={"ars5": str})
        results["diag_rows"] = diag_df.to_dict("records")
        target_mean_eur = dict(zip(diag_df["ars5"], diag_df["target_mean_eur"]))

        from braunschweig.analysis.population_validation import placement_income_gate as _pig

        hh_on_ars5 = hh_on.rename(columns={"departement_id": "ars5"})
        hh_off_ars5 = hh_off.rename(columns={"departement_id": "ars5"})

        attainment = _pig.income_attainment_by_kreis(hh_on_ars5, target_mean_eur)
        results["income_attainment_on"] = attainment.to_dict("records")

        coherence_off = _pig.income_coherence_within_cells(hh_off_ars5)
        coherence_on = _pig.income_coherence_within_cells(hh_on_ars5)
        results["coherence_off"] = coherence_off
        results["coherence_on"] = coherence_on
        off_rho = coherence_off.get("pooled_spearman", float("nan"))
        on_rho = coherence_on.get("pooled_spearman", float("nan"))
        results["coherence_delta"] = (
            on_rho - off_rho if not (np.isnan(off_rho) or np.isnan(on_rho)) else float("nan")
        )
        logger.info(
            "Metrics: income attainment (ON) over %d Kreis rows; within-cell coherence "
            "OFF=%s ON=%s delta=%s (REPORTED, not a gate threshold).",
            len(attainment), _fmt(off_rho), _fmt(on_rho), _fmt(results["coherence_delta"]),
        )

    attrs_diag = persons_on.attrs.get("placement_income_diag")
    if attrs_diag:
        results["attrs_diag"] = {
            "moved_share": attrs_diag.get("moved_share"),
            "no_freedom_slot_share": attrs_diag.get("no_freedom_slot_share"),
            "converged": attrs_diag.get("converged"),
            "sweeps_used": attrs_diag.get("sweeps_used"),
        }
        logger.info(
            "ON persons.attrs['placement_income_diag'] found: moved_share=%s, "
            "no_freedom_slot_share=%s, converged=%s.",
            attrs_diag.get("moved_share"), attrs_diag.get("no_freedom_slot_share"),
            attrs_diag.get("converged"),
        )
    else:
        logger.warning(
            "ON persons.attrs did NOT carry 'placement_income_diag' (either "
            "DataFrame.attrs did not survive this cache's pickle, or the stage did not "
            "set it); relying on the diag CSV only for reallocation diagnostics."
        )
        results["attrs_diag"] = None

    return results


def _render_report(results: dict[str, Any]) -> str:
    """Render the full gate report as Markdown text (used for both stdout and the
    written report file, so the two never drift apart)."""
    lines: list[str] = []
    lines.append("# placement_income OFF/ON gate report (issue #108 L2, Task 7)")
    lines.append("")
    lines.append(f"Verdict: **{results.get('verdict', 'UNKNOWN')}**")
    lines.append("")
    lines.append("## Provenance")
    lines.append(f"- OFF cache: `{results.get('cache_off')}`")
    lines.append(f"- ON cache: `{results.get('cache_on')}`")
    lines.append(f"- Batch folder used to rebuild signatures: `{results.get('batch_used')}`")
    lines.append(
        f"- Active KREIS control names (default-ON, no toggle overrides): "
        f"{results.get('active_kreis_control_names')}"
    )
    lines.append(
        f"- Control tiers: {list(_CONTROL_TIERS)}; employment_grid: "
        f"{_INCLUDE_EMPLOYMENT_GRID}; seed: {_SEED_NAME!r}"
    )
    lines.append(
        f"- Households: OFF={results.get('n_households_off')}, "
        f"ON={results.get('n_households_on')}"
    )
    lines.append("")
    lines.append("## Hard invariants")
    for key, label in (
        ("cell_counts_cmp", "Per-(cell, signature) household counts"),
        ("kreis_counts_cmp", "Per-(Kreis, signature) household counts"),
        ("clone_cmp", "Per-donor clone profile"),
    ):
        cmp_ = results.get(key, {})
        lines.append(
            f"- {label}: equal={cmp_.get('equal')}, n_keys_off={cmp_.get('n_keys_off')}, "
            f"n_keys_on={cmp_.get('n_keys_on')}, n_diff_keys={cmp_.get('n_diff_keys')}, "
            f"max_abs_diff={cmp_.get('max_abs_diff')}"
        )
    lines.append(f"- ON placement_income_diag.csv present: {results.get('diag_present_on')}")
    lines.append(f"- OFF placement_income_diag.csv absent: {results.get('diag_absent_off')}")
    lines.append("")

    reasons = results.get("reasons", [])
    if reasons:
        lines.append("## FAIL reasons")
        for reason in reasons:
            lines.append(f"- {reason}")
        lines.append("")

    if results.get("metrics_skipped"):
        lines.append("## Metrics")
        lines.append("SKIPPED (--skip-metrics): hard-invariants-only run.")
        lines.append("")
        return "\n".join(lines)

    lines.append(
        "## Metrics (REPORTED only -- not gate thresholds; see CLAUDE.md "
        "\"No invented reference values; convergence is not validation\")"
    )
    diag_rows = results.get("diag_rows")
    if diag_rows is None:
        lines.append(
            "- WARNING: placement_income_diag.csv (ON) absent; attainment metric skipped."
        )
    else:
        lines.append("- Per-Kreis reallocation diagnostics (ON cache diag CSV):")
        for row in diag_rows:
            lines.append(
                f"    ars5={row.get('ars5')}: target={_fmt(row.get('target_mean_eur'), '{:.0f}')} EUR, "
                f"before={_fmt(row.get('realized_before_eur'), '{:.0f}')} EUR, "
                f"after={_fmt(row.get('realized_after_eur'), '{:.0f}')} EUR, "
                f"lambda={_fmt(row.get('lambda'))}, clamped={row.get('clamped')}"
            )
        attainment = results.get("income_attainment_on", [])
        lines.append("- Income attainment by Kreis (ON, realized vs target):")
        for row in attainment:
            lines.append(
                f"    ars5={row.get('ars5')}: n_households={row.get('n_households')}, "
                f"realized={_fmt(row.get('realized_mean_eur'), '{:.0f}')} EUR, "
                f"target={_fmt(row.get('target_mean_eur'), '{:.0f}')} EUR, "
                f"residual={_fmt(row.get('residual_pct'), '{:.2f}')}%"
            )
        coherence_off = results.get("coherence_off", {})
        coherence_on = results.get("coherence_on", {})
        lines.append(
            "- Within-cell income coherence (household-count-weighted pooled Spearman, "
            f"income vs number_of_cars): OFF={_fmt(coherence_off.get('pooled_spearman'))} "
            f"(n_cells={coherence_off.get('n_cells')}), "
            f"ON={_fmt(coherence_on.get('pooled_spearman'))} (n_cells={coherence_on.get('n_cells')}), "
            f"delta={_fmt(results.get('coherence_delta'))}"
        )
    attrs_diag = results.get("attrs_diag")
    if attrs_diag:
        lines.append(
            "- ON persons.attrs['placement_income_diag'] echo: "
            f"moved_share={_fmt(attrs_diag.get('moved_share'))}, "
            f"no_freedom_slot_share={_fmt(attrs_diag.get('no_freedom_slot_share'))}, "
            f"converged={attrs_diag.get('converged')}, "
            f"sweeps_used={attrs_diag.get('sweeps_used')}"
        )
    else:
        lines.append(
            "- WARNING: ON persons.attrs did not carry 'placement_income_diag' (attrs may "
            "not survive this cache's pickle, or the stage did not set it); relying on "
            "the diag CSV only."
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        level=logging.INFO,
        force=True,
    )
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--cache-off", default=_DEFAULT_CACHE_OFF,
        help="OFF synpp cache working_directory (placement_income=false).",
    )
    parser.add_argument(
        "--cache-on", default=_DEFAULT_CACHE_ON,
        help="ON synpp cache working_directory (placement_income=true).",
    )
    parser.add_argument(
        "--skip-metrics", action="store_true",
        help="Run the hard invariant checks only; skip the REPORTED-only income "
             "attainment / coherence metrics section.",
    )
    args = parser.parse_args(argv)

    try:
        results = run_gate(
            pathlib.Path(args.cache_off), pathlib.Path(args.cache_on),
            skip_metrics=args.skip_metrics,
        )
    except GateBlockedError as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return 2

    report_text = _render_report(results)
    print(report_text)
    report_path = pathlib.Path(args.cache_on) / "gate_placement_income_report.md"
    report_path.write_text(report_text, encoding="utf-8")
    logger.info("Report written to %s", report_path)

    return 0 if results["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
