"""Guard and table writer for the input consistency of the PopulationSim batches (#658, Task A6).

``braunschweig.popsim.input_consistency`` measures how far the KREIS control table of one batch
disagrees with its grid tables. This module acts on the measurement in the parent process, where
the batch folders are assembled (``mid.batch_folders.assemble_batch_folder``), BEFORE any
PopulationSim process starts:

* every batch appends its summary to one CSV (``batch`` column first), whatever the config;
* the guard raises ``ValueError`` for the pairs below, but only for the pairs whose config key is
  at its new value, because only then the pair is meant to hold (legacy values keep writing the
  table and never raise);
* the recorder also collects the counts the library steps of Part A produce (universe
  apportionment, KREIS following the grids, employment level), for the stage report file.

ASSUMPTIONS
-----------
* ``GRID_EQUAL_PAIRS`` are the KREIS controls that ``kreis_controls_follow_grids`` sets to the sum of
  their grid (Task A4, ``control_spec.kreis_grid_pairs``): they must agree to float noise.
* ``AGE_UNIVERSE_PAIRS`` are the pairs between a KREIS family on an age universe and the Zensus age
  x sex bands of that universe. They carry a rounding residual of the apportioned targets, so the
  bound is the configurable relative tolerance, applied only to Kreis parts with enough persons
  (a small part has a large relative rounding residual and no statistical weight).
* The pairs ``households``, ``persons_14plus_low`` / ``persons_14plus_high`` (bounds, not
  equalities) and the ``ratio`` pair ``employed_survey`` are measured and written, never guarded.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Mapping

import numpy as np
import pandas as pd

from braunschweig.popsim import folders
from braunschweig.popsim import input_consistency as ic

logger = logging.getLogger(__name__)
_TAG = "[popsim_input_consistency]"

#: File name of the per-run table, inside the PopulationSim work directory.
TABLE_FILE_NAME = "popsim_input_consistency.csv"

#: Pair labels (``input_consistency.KREIS_FINE_PAIRS``) of the KREIS controls that follow a grid
#: (Task A4): ``number_of_cars_*``, ``number_of_bicycles_*``, ``employed``. Listed, not derived.
GRID_EQUAL_PAIRS = (
    "cars_0", "cars_1", "cars_2", "cars_3plus",
    "bicycles_0", "bicycles_1", "bicycles_2", "bicycles_3", "bicycles_4plus",
    "employed",
)
#: Pair labels of the age-universe pairs (KREIS family vs Zensus age x sex bands). Listed, not derived.
AGE_UNIVERSE_PAIRS = ("persons_0_17", "persons_18plus", "persons_all")
#: Relative difference above which a grid-following pair is a violation (float noise).
GRID_EQUAL_TOLERANCE = 1e-6


def guard_violations(summary: pd.DataFrame, *, check_grid_pairs: bool, check_age_pairs: bool,
                     age_tolerance: float, min_persons: float) -> pd.DataFrame:
    """The KREIS rows of ``summary`` (``input_consistency.summarize``) that break the guard.

    ``grid pair``: relative difference (``share``) above :data:`GRID_EQUAL_TOLERANCE`, or any
    difference above ``input_consistency.TOLERANCE`` against a grid sum of 0.
    ``age pair``: ``share`` above ``age_tolerance`` in a Kreis part whose grid sum (``base``) is at
    least ``min_persons``. ``ratio_reported`` and ``skipped`` rows are never in the result.
    Columns of the result: those of ``summary``.
    """
    kreis = summary[summary["scope"] == "kreis"]
    amount, base, share = kreis["amount"], kreis["base"], kreis["share"]
    grid = kreis["check"].isin(GRID_EQUAL_PAIRS) & check_grid_pairs & (
        (share > GRID_EQUAL_TOLERANCE) | ((base == 0) & (amount > ic.TOLERANCE)))
    age = kreis["check"].isin(AGE_UNIVERSE_PAIRS) & check_age_pairs & (
        (share > age_tolerance) & (base >= min_persons))
    return kreis[grid | age]


def max_guarded_relative_difference(summary: pd.DataFrame) -> float:
    """Largest relative difference over the guarded pairs of ``summary`` (0.0 when there are none)."""
    kreis = summary[(summary["scope"] == "kreis")
                    & summary["check"].isin(GRID_EQUAL_PAIRS + AGE_UNIVERSE_PAIRS)]
    values = kreis["share"].to_numpy(dtype=float)
    return float(np.nanmax(values)) if np.isfinite(values).any() else 0.0


class ConsistencyRecorder:
    """Per-run collector: writes the table, enforces the guard and keeps the counts for the report.

    One instance per run, created by the stage and handed to ``mid.run_popsim_mid``. The CSV of an
    earlier run is removed on construction (the table always describes this run's batches).

    ``check_grid_pairs`` / ``check_age_pairs`` switch the two guard groups
    (``kreis_controls_follow_grids`` / ``kreis_batch_apportionment == "universe"``).
    """

    def __init__(self, csv_path, *, check_grid_pairs: bool, check_age_pairs: bool,
                 age_tolerance: float, min_persons: float):
        self.csv_path = Path(csv_path)
        self.check_grid_pairs = bool(check_grid_pairs)
        self.check_age_pairs = bool(check_age_pairs)
        self.age_tolerance = float(age_tolerance)
        self.min_persons = float(min_persons)
        self.max_rel = 0.0
        self.n_rows = 0
        self.n_batches = 0
        self.n_violations = 0
        self.sections: dict[str, dict] = {}
        self._skipped: set = set()
        self._once: set = set()
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        self.csv_path.unlink(missing_ok=True)

    # --- counts of the Part A steps -----------------------------------------------------------
    def add(self, section: str, **numbers) -> None:
        """Add ``numbers`` to the running sums of ``section`` (summed over batches)."""
        target = self.sections.setdefault(section, {})
        for name, value in numbers.items():
            target[name] = target.get(name, 0) + value

    def maximum(self, section: str, **numbers) -> None:
        """Keep the largest value per name of ``section`` (NaN is ignored)."""
        target = self.sections.setdefault(section, {})
        for name, value in numbers.items():
            if value is None or (isinstance(value, float) and np.isnan(value)):
                continue
            target[name] = max(target.get(name, value), value)

    def set(self, section: str, **values) -> None:
        """Store run-wide ``values`` (replacing earlier ones) in ``section``."""
        self.sections.setdefault(section, {}).update(values)

    def first_time(self, key: str) -> bool:
        """True the first time ``key`` is asked (for a message that is logged once per run)."""
        if key in self._once:
            return False
        self._once.add(key)
        return True

    # --- the batch hook -------------------------------------------------------------------------
    def record_batch(self, batch: str, control_totals: Mapping[str, pd.DataFrame],
                     geo_crosswalk: pd.DataFrame) -> pd.DataFrame:
        """Summarize one batch from its in-memory tables, append it to the CSV and apply the guard.

        Needs the KREIS table; the 100 m and 1 km tables are used when present (a pair whose
        columns are absent is listed once per run as skipped). Returns the summary.
        Raises: ``ValueError`` naming batch, Kreis and pair, after the rows are written.
        """
        def zone_table(geo: str) -> pd.DataFrame:
            table = control_totals.get(geo)
            return pd.DataFrame() if table is None else table.set_index(geo)

        summary = ic.summarize(
            zone_table(folders.GEO_KREIS), zone_table(folders.GEO_100M), geo_crosswalk,
            cells_1km=zone_table(folders.GEO_1KM), skip_log_level=logging.DEBUG)
        self._skipped.update(summary.attrs["skipped"])
        out = summary.copy()
        out.insert(0, "batch", batch)
        out.to_csv(self.csv_path, mode="a", header=not self.csv_path.exists(), index=False)
        self.n_batches += 1
        self.n_rows += len(out)
        self.max_rel = max(self.max_rel, max_guarded_relative_difference(summary))
        bad = guard_violations(
            summary, check_grid_pairs=self.check_grid_pairs, check_age_pairs=self.check_age_pairs,
            age_tolerance=self.age_tolerance, min_persons=self.min_persons)
        self.n_violations += len(bad)
        if len(bad):
            details = "; ".join(
                f"Kreis {row.key}, pair {row.check}: difference {row.amount:.6g} on a grid sum of "
                f"{row.base:.6g} (relative {row.share:.4g})" for row in bad.head(10).itertuples())
            raise ValueError(
                f"{_TAG} batch {batch}: {len(bad)} KREIS control pair(s) contradict their grid "
                f"({details}). PopulationSim would carry the contradiction as a permanent misfit and "
                f"no weighting can meet it. Full table: {self.csv_path}. The guard is active for "
                f"grid-following pairs only with kreis_controls_follow_grids true "
                f"(now {self.check_grid_pairs}) and for age-universe pairs only with "
                f"kreis_batch_apportionment 'universe' (now {self.check_age_pairs}); age-universe "
                f"pairs are judged above a relative tolerance of {self.age_tolerance} in Kreis parts "
                f"of at least {self.min_persons:g} persons.")
        return summary

    def log_run_summary(self) -> None:
        """One INFO line over the run: batches, rows, largest guarded difference, skipped pairs."""
        logger.info("%s %d batch(es), %d row(s) in %s; largest relative difference over the guarded "
                    "pairs %.4g; %d guarded row(s) above their bound; pairs skipped for absent "
                    "columns: %s", _TAG, self.n_batches, self.n_rows, self.csv_path, self.max_rel,
                    self.n_violations, sorted(self._skipped) or "none")

    def report(self) -> dict:
        """The counts for the stage report file (JSON-ready)."""
        return {
            "guard": {
                "table": str(self.csv_path),
                "batches": self.n_batches,
                "rows": self.n_rows,
                "max_relative_difference": self.max_rel,
                "rows_above_bound": self.n_violations,
                "grid_pairs_enforced": self.check_grid_pairs,
                "age_pairs_enforced": self.check_age_pairs,
                "age_tolerance": self.age_tolerance,
                "min_persons": self.min_persons,
                "skipped": sorted(self._skipped),
            },
            **{name: dict(values) for name, values in self.sections.items()},
        }
