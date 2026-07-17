"""OFF/ON invariant gate for placement_income (L2 of issue #108: donor-coherent income
via signature-preserving donor reallocation).

Compares TWO independent synpp caches of the SAME 2-Kreis popsim_mid pipeline (Salzgitter
ars5 03102, Wolfsburg ars5 03103; random_seed=1234) where
``braunschweig.population.popsim.placement_income`` (false / true) is the ONLY config
difference, and verifies the feature's HARD invariants DIRECTLY on the two REALIZED
``data.census.filtered`` (``braunschweig.popsim.stage``) populations -- no signature
rebuild, no per-run pseudonym map:

  1. Every PopulationSim control aggregate this gate can check from the realized
     population -- economic_status x Kreis, number_of_cars x Kreis, economic_status x
     100 m cell, household count x 100 m cell, person age-band x sex_raw x 100 m cell,
     person age-band x 100 m cell -- is IDENTICAL OFF vs ON: the reallocation only
     permutes WHICH donor occupies a cell/Kreis slot inside an exact control-signature
     group, so every control aggregate it could affect must come out unchanged.
  2. Every donor's total clone count (households per primary donor ``H_ID``, summed
     over the whole 2-Kreis region) is IDENTICAL OFF vs ON: the reallocation must
     relocate clones, never create or destroy them.
  3. The ON cache's ``popsim_work/placement_income_diag.csv`` is present (the stage only
     writes it when the reallocation actually ran).
  4. The OFF cache's ``popsim_work/placement_income_diag.csv`` is ABSENT (must not be
     produced when the flag is off).

PASS requires ALL of the above; see ``decide_gate``. Income-attainment (realized vs
INKAR-derived target per Kreis) and within-cell income coherence (Spearman income-vs-
car-ownership) are additionally REPORTED, never as pass/fail thresholds: this gate
checks exactness invariants, not a fit-to-target bound (no invented reference values;
convergence is not validation -- see CLAUDE.md "No invented reference values;
convergence is not validation").

Why realized aggregates, not donor control signatures
-------------------------------------------------------
An earlier version of this harness rebuilt each donor's PopulationSim control signature
and compared households through a per-run ``source_household_id -> raw H_ID`` pseudonym
map (``household_level`` asserted exactly one ``source_household_id`` per
``household_id``). That assertion is WRONG for the popsim_mid completed-donor path:
member completion (D3, ``braunschweig.popsim.member_completion``) fills an under-sized
donor household with members borrowed from OTHER donor households, so a filler's
``source_household_id`` (its own donor lineage) legitimately differs from its synthetic
household's other members. ``source_household_id`` is a PER-PERSON attribute, not a
per-household one, and enforcing uniqueness on it crashed the harness ("14488
household_id(s) carry more than one distinct source_household_id") even though the
reallocation was perfectly correct.

The per-household donor key that IS constant by construction is the PRIMARY donor
``H_ID``: ``braunschweig.popsim.expand.assign_synthetic_household_ids`` builds
``household_id = "<cell>_<H_ID>_<occurrence>"``, so every person copied from that one
placement shares the same ``H_ID`` regardless of member completion. Keying the
household-level frame on ``H_ID`` and comparing REALIZED aggregate counts directly
(this module's ``realized_invariants``) needs no signature catalog, no seed tables, and
no pseudonym map -- and is exactly the invariant the feature actually promises: every
control aggregate and every donor's clone count, unchanged.

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

The pure analysis functions (``household_level``, ``realized_invariants``,
``compare_counts``, ``decide_gate``) can be imported and unit-tested independently of
any pipeline run (see tests/test_gate_placement_income.py).
"""
from __future__ import annotations

import argparse
import logging
import pathlib
import pickle
import sys
from typing import Any

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


# ---------------------------------------------------------------------------
# Pure analysis functions (unit-testable without any pipeline run)
# ---------------------------------------------------------------------------

_HOUSEHOLD_LEVEL_COLUMNS = (
    "household_id",
    "H_ID",
    "ZENSUS100m",
    "departement_id",
    "economic_status",
    "number_of_cars",
    "household_income_eur",
)


def household_level(persons: pd.DataFrame) -> pd.DataFrame:
    """Collapse a synthetic persons frame to one row per ``household_id``.

    Takes the first row (by ``household_id`` sort order) for each household-level
    column. Fails fast if any ``household_id`` mixes more than one distinct primary
    donor ``H_ID``: ``braunschweig.popsim.expand.assign_synthetic_household_ids`` builds
    ``household_id = "<cell>_<H_ID>_<occurrence>"``, so every person copied from one
    synthetic-household placement must share the SAME ``H_ID`` by construction --
    disagreement indicates a corrupted expansion, not something to silently paper over.

    Note: the per-PERSON ``source_household_id`` (present on the input frame but NOT
    part of this function's output) is deliberately NOT checked for consistency here.
    It legitimately varies within one ``household_id`` when member completion (D3)
    filled an under-sized donor household with members borrowed from ANOTHER donor
    household -- that is expected and correct, not a corruption (see the module
    docstring, "Why realized aggregates, not donor control signatures").

    Parameters
    ----------
    persons:
        Synthetic persons frame (``braunschweig.popsim.stage`` output) carrying at least
        ``household_id, H_ID, ZENSUS100m, departement_id, economic_status,
        number_of_cars, household_income_eur``.

    Returns
    -------
    pandas.DataFrame
        One row per ``household_id`` with the columns listed above.

    Raises
    ------
    ValueError
        If a required column is missing, or if any ``household_id`` carries more than
        one distinct primary donor ``H_ID``.
    """
    missing = [c for c in _HOUSEHOLD_LEVEL_COLUMNS if c not in persons.columns]
    if missing:
        raise ValueError(
            f"household_level requires columns {missing}; got {sorted(persons.columns)[:20]}."
        )
    n_distinct_hid = persons.groupby("household_id")["H_ID"].nunique()
    inconsistent = n_distinct_hid[n_distinct_hid > 1]
    if len(inconsistent):
        raise ValueError(
            f"household_level: {len(inconsistent)} household_id(s) carry more than one "
            f"distinct primary donor H_ID (e.g. {inconsistent.index[:5].tolist()}); "
            "household_id is constructed as '<cell>_<H_ID>_<occurrence>' so every person "
            "in a synthetic household must share the same H_ID -- a mismatch means the "
            "expansion is corrupted."
        )
    hh = (
        persons[list(_HOUSEHOLD_LEVEL_COLUMNS)]
        .sort_values("household_id")
        .groupby("household_id", sort=True)
        .first()
        .reset_index()
    )
    return hh


def compare_counts(off: pd.Series, on: pd.Series) -> dict:
    """Compare two count Series (e.g. per-(cell, economic_status), per-(Kreis,
    number_of_cars), or per-donor clone counts) via an aligned reindex over the union
    of their keys.

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


def _person_ageband_frame(persons: pd.DataFrame) -> pd.DataFrame:
    """Build the small ``(ageband, sex_raw, ZENSUS100m)`` frame used by the person-level
    realized invariants, from Series rather than copying the (potentially large) full
    persons frame.

    ``ageband`` is the same 10-year banding (0-8, i.e. 0-9, 10-19, ..., 80+) as the
    tier0 100 m age x sex census-source control columns
    (``braunschweig.popsim.stage._TIER0_AGE_SEX_100M_COLUMNS``), so this check operates
    at the same granularity as the real control it stands in for.
    """
    return pd.DataFrame({
        "ageband": (pd.to_numeric(persons["age"]) // 10).clip(0, 8),
        "sex_raw": persons["sex_raw"].to_numpy(),
        "ZENSUS100m": persons["ZENSUS100m"].to_numpy(),
    })


def realized_invariants(off_persons: pd.DataFrame, on_persons: pd.DataFrame) -> dict[str, dict]:
    """Compare the OFF/ON realized populations on the PopulationSim control aggregates
    and the donor clone profile -- directly, with NO signature rebuild and NO per-run
    pseudonym map.

    The placement_income reallocation only permutes WHICH real donor household occupies
    a given (100 m cell, Kreis) slot inside an exact control-signature group, so every
    PopulationSim control aggregate at every geography, and every donor's total clone
    count, must come out EXACTLY unchanged between the OFF and ON realized populations.
    A household's control-relevant attributes (``economic_status``, ``number_of_cars``,
    its cell, its Kreis) already ARE its aggregate-level signature, so this is checked
    directly on the two realized frames -- there is no need to reconstruct the donor
    control signature catalog or a ``source_household_id -> raw H_ID`` pseudonym map
    (see the module docstring for why the OLD approach was retired).

    NOTE on sex: the person-level checks use ``sex_raw`` (the untouched HP_SEX
    category: male/female/diverse/not_specified), NOT the imputed BINARY ``sex``.
    HP_SEX codes 3/9 (diverse / no answer, ~977 persons in the Task-7 gate region) are
    resolved to a binary male/female by a SEEDED draw that consumes frame order
    (``braunschweig.popsim.expand.map_demographics``); the reallocation changes person
    order, so it perturbs a handful of these draws -- a harmless cosmetic MATSim
    tie-break, NOT a control (the real 100 m sex controls only ever count HP_SEX==1/2).
    ``sex_raw`` is unaffected by reallocation-induced reordering and is therefore the
    control-faithful check; age x sex_raw x cell is expected to be preserved exactly.

    Parameters
    ----------
    off_persons, on_persons:
        Persons frames loaded from the OFF/ON synpp caches (see
        :func:`_load_persons_from_cache`); each row is one synthetic person.

    Returns
    -------
    dict[str, dict]
        Keyed by a short invariant name; each value is a :func:`compare_counts` result
        (``equal``, ``n_keys_off``, ``n_keys_on``, ``n_diff_keys``, ``max_abs_diff``).

    Raises
    ------
    ValueError
        If a required column is missing from either persons frame, or if
        :func:`household_level` finds a household with more than one distinct primary
        donor ``H_ID`` (see its docstring).
    """
    required_person_columns = {"age", "sex_raw", "ZENSUS100m"}
    for label, persons in (("off_persons", off_persons), ("on_persons", on_persons)):
        missing = sorted(required_person_columns - set(persons.columns))
        if missing:
            raise ValueError(f"realized_invariants: {label} is missing columns {missing}.")

    hh_off = household_level(off_persons)
    hh_on = household_level(on_persons)
    pp_off = _person_ageband_frame(off_persons)
    pp_on = _person_ageband_frame(on_persons)

    return {
        "economic_status_x_departement_id": compare_counts(
            hh_off.groupby(["economic_status", "departement_id"]).size(),
            hh_on.groupby(["economic_status", "departement_id"]).size(),
        ),
        "number_of_cars_x_departement_id": compare_counts(
            hh_off.groupby(["number_of_cars", "departement_id"]).size(),
            hh_on.groupby(["number_of_cars", "departement_id"]).size(),
        ),
        "economic_status_x_ZENSUS100m": compare_counts(
            hh_off.groupby(["economic_status", "ZENSUS100m"]).size(),
            hh_on.groupby(["economic_status", "ZENSUS100m"]).size(),
        ),
        "ZENSUS100m_household_count": compare_counts(
            hh_off.groupby("ZENSUS100m").size(),
            hh_on.groupby("ZENSUS100m").size(),
        ),
        "age_x_sex_raw_x_ZENSUS100m": compare_counts(
            pp_off.groupby(["ageband", "sex_raw", "ZENSUS100m"]).size(),
            pp_on.groupby(["ageband", "sex_raw", "ZENSUS100m"]).size(),
        ),
        "age_x_ZENSUS100m": compare_counts(
            pp_off.groupby(["ageband", "ZENSUS100m"]).size(),
            pp_on.groupby(["ageband", "ZENSUS100m"]).size(),
        ),
        "clone_counts_by_H_ID": compare_counts(
            hh_off["H_ID"].value_counts(),
            hh_on["H_ID"].value_counts(),
        ),
    }


def decide_gate(
    invariants: dict[str, dict],
    diag_present_on: bool,
    diag_absent_off: bool,
) -> tuple[str, list[str]]:
    """Decide PASS/FAIL from the placement_income realized-aggregate invariants.

    PASS requires ALL of:
      1. every entry of ``invariants`` (see :func:`realized_invariants`) has
         ``equal == True``;
      2. the ON cache's ``placement_income_diag.csv`` is present;
      3. the OFF cache's ``placement_income_diag.csv`` is ABSENT.

    Income-attainment and within-cell coherence metrics are deliberately NOT inputs
    here: they are REPORTED only (see ``run_gate``'s metrics section) -- this function
    checks exactness invariants, not a fit-to-target threshold (no invented reference
    values; see CLAUDE.md "No invented reference values; convergence is not validation").

    Parameters
    ----------
    invariants:
        :func:`realized_invariants` output: a mapping from invariant name to a
        :func:`compare_counts`-shaped dict.
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
    for name, cmp_ in invariants.items():
        if not cmp_.get("equal", False):
            reasons.append(
                f"realized aggregate {name!r} differs OFF vs ON "
                f"(n_diff_keys={cmp_.get('n_diff_keys')}, max_abs_diff={cmp_.get('max_abs_diff')}): "
                "a PopulationSim control aggregate or the donor clone profile was not "
                "preserved by the reallocation."
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
        cell coherence, persons.attrs echo) and return only the realized-invariant
        comparisons + verdict.

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

    invariants = realized_invariants(persons_off, persons_on)
    for name, cmp_ in invariants.items():
        logger.info(
            "Realized invariant %r: equal=%s n_keys_off=%d n_keys_on=%d n_diff_keys=%d "
            "max_abs_diff=%d",
            name, cmp_["equal"], cmp_["n_keys_off"], cmp_["n_keys_on"], cmp_["n_diff_keys"],
            cmp_["max_abs_diff"],
        )

    diag_path_on = work_on / "placement_income_diag.csv"
    diag_path_off = work_off / "placement_income_diag.csv"
    diag_present_on = diag_path_on.is_file()
    diag_absent_off = not diag_path_off.is_file()

    verdict, reasons = decide_gate(invariants, diag_present_on, diag_absent_off)
    for reason in reasons:
        logger.warning("GATE FAIL reason: %s", reason)
    logger.info("GATE verdict: %s", verdict)

    hh_off = household_level(persons_off)
    hh_on = household_level(persons_on)
    logger.info(
        "Household-level frames: OFF %d households, ON %d households.",
        len(hh_off), len(hh_on),
    )

    results: dict[str, Any] = {
        "cache_off": str(cache_off),
        "cache_on": str(cache_on),
        "verdict": verdict,
        "reasons": reasons,
        "invariants": invariants,
        "diag_present_on": diag_present_on,
        "diag_absent_off": diag_absent_off,
        "n_households_off": int(len(hh_off)),
        "n_households_on": int(len(hh_on)),
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
        # NOTE (dtype gotcha): the diag CSV's ars5 column is written from Python str
        # keys ("03102", ...), but a plain pd.read_csv WITHOUT a dtype hint auto-infers
        # the column as int64 and silently drops the leading zero (3102). Casting to
        # str at read time AND re-zero-padding to 5 digits defends against either
        # representation ending up on disk, so the join key always matches the
        # household frame's zero-padded departement_id (e.g. "03102").
        diag_df = pd.read_csv(diag_path_on, dtype={"ars5": str})
        diag_df["ars5"] = diag_df["ars5"].astype(str).str.zfill(5)
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
    lines.append(
        f"- Households: OFF={results.get('n_households_off')}, "
        f"ON={results.get('n_households_on')}"
    )
    lines.append("")
    lines.append("## Realized-aggregate invariants (OFF vs ON)")
    for name, cmp_ in results.get("invariants", {}).items():
        lines.append(
            f"- {name}: equal={cmp_.get('equal')}, n_keys_off={cmp_.get('n_keys_off')}, "
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
