"""Per-100m-cell realised-vs-target error for the ZENSUS100m PopulationSim controls.

Realised counts are recomputed from the ID-only synthetic output joined to the
completed-donor attributes; control definitions come from control_spec (not
re-encoded). Target = the per-batch control_totals_ZENSUS100m.csv that PopulationSim
balanced against.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, Union

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_CELL = "ZENSUS100m"


def _is_person_family(control) -> bool:
    fam = getattr(control, "family", "") or ""
    return "person" in fam.lower() or "employ" in fam.lower() or "licen" in fam.lower()


def realised_counts(synthetic_households, synthetic_persons, donor_households,
                    donor_persons, controls: Iterable) -> pd.DataFrame:
    """Long [zensus100m, control, realised] for ZENSUS100m-geography controls."""
    rows = []
    hh = synthetic_households[[_CELL, "H_ID"]].copy()

    # Guard: deduplicate the household donor on H_ID before any merge.  The
    # household donor is expected to have exactly one row per H_ID.  If duplicates
    # are present they would fan out the left-merge and silently inflate realised
    # counts — a no-silent-fallback violation.  Deduplicate here once (not inside
    # the loop) and log a warning so the issue is always observable.
    donor_hh_unique = donor_households.drop_duplicates("H_ID")
    n_dropped = len(donor_households) - len(donor_hh_unique)
    if n_dropped > 0:
        logger.warning(
            "realised_counts: dropped %d duplicate H_ID rows from donor_households "
            "(unique H_IDs: %d, total rows before dedup: %d); fan-out prevented",
            n_dropped, len(donor_hh_unique), len(donor_households),
        )

    for control in controls:
        if getattr(control, "geography", None) != _CELL:
            continue
        expr = control.expression_for("mid")
        if expr is None:
            logger.info("control %s: no expression for mid; skipped", control.name)
            continue
        if _is_person_family(control):
            joined = hh.merge(donor_persons, on="H_ID", how="left")
        else:
            joined = hh.merge(donor_hh_unique, on="H_ID", how="left")
        try:
            mask = joined.eval(expr)
        except Exception as error:  # surface, never silently skip
            logger.warning("control %s: expression %r failed (%s); skipped",
                           control.name, expr, error)
            continue
        counts = joined.loc[mask.astype(bool)].groupby(_CELL).size()
        for cell, n in counts.items():
            rows.append({"zensus100m": cell, "control": control.name, "realised": int(n)})
    return pd.DataFrame(rows, columns=["zensus100m", "control", "realised"])


def _load_targets(control_totals_path: Union[str, Path]) -> pd.DataFrame:
    """control_totals_ZENSUS100m.csv -> long [zensus100m, control, target]."""
    df = pd.read_csv(control_totals_path)
    id_col = _CELL if _CELL in df.columns else df.columns[0]
    long = df.melt(id_vars=[id_col], var_name="control", value_name="target")
    return long.rename(columns={id_col: "zensus100m"})


def cell_error_table(work_dir, mid_dir, *, random_seed: int, tiers, employment_grid: bool,
                     weekend: bool) -> pd.DataFrame:
    """Long [zensus100m, control, realised, target, abs_error, batch] over all batches."""
    from braunschweig.popsim import control_spec, mid as midmod, seed as seedmod

    work_dir = Path(work_dir)
    controls = control_spec.full_catalog(include_tiers=tuple(tiers),
                                         include_employment_grid=employment_grid)
    controls = [c for c in controls if getattr(c, "geography", None) == _CELL]
    rng = np.random.RandomState(random_seed + 74513)
    day_filter = seedmod.ALL_REPORTING_KERNWO if weekend else None
    donor_hh, donor_p, _creport, _mreport = midmod.load_completed_donor(
        mid_dir, completion_rng=rng, day_filter_values=day_filter)

    parts = []
    for batch_dir in sorted(work_dir.glob("batch_*")):
        out = batch_dir / "output"
        targets_path = batch_dir / "data" / "control_totals_ZENSUS100m.csv"
        syn_hh_path = out / "synthetic_households.csv"
        syn_p_path = out / "synthetic_persons.csv"
        if not (syn_hh_path.is_file() and targets_path.is_file()):
            logger.warning("batch %s: missing synthetic_households or control_totals; skipped",
                           batch_dir.name)
            continue
        syn_hh = pd.read_csv(syn_hh_path)
        syn_p = pd.read_csv(syn_p_path) if syn_p_path.is_file() else pd.DataFrame()
        realised = realised_counts(syn_hh, syn_p, donor_hh, donor_p, controls)
        target = _load_targets(targets_path)
        merged = target.merge(realised, on=["zensus100m", "control"], how="left")
        merged["realised"] = merged["realised"].fillna(0).astype(int)
        merged["abs_error"] = (merged["realised"] - merged["target"]).abs()
        merged["batch"] = batch_dir.name
        parts.append(merged)
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(
        columns=["zensus100m", "control", "realised", "target", "abs_error", "batch"])
