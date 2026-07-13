"""Per-100m-cell realised-vs-target error for the ZENSUS100m PopulationSim controls.

Realised counts are recomputed from the ID-only synthetic output joined to the
completed-donor attributes; control definitions come from control_spec (not
re-encoded). Target = the per-batch control_totals_ZENSUS100m.csv that PopulationSim
balanced against.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, Tuple, Union

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_CELL = "ZENSUS100m"


def _evaluate(expr: str, frame: pd.DataFrame) -> np.ndarray:
    """Evaluate a control_spec prefixed boolean expression against ``frame``.

    The expression uses the seed table name as a namespace prefix
    (``persons.<col>`` / ``households.<col>``).  Both names are bound to
    ``frame`` (only the control's own table is actually referenced) plus
    numpy, and Python ``eval`` is used (expressions are trusted, from
    control_spec).  Returns a boolean numpy array aligned to ``frame``.
    """
    env = {"np": np, "persons": frame, "households": frame, "__builtins__": {}}
    return np.asarray(eval(expr, env)).astype(bool)  # noqa: S307 (trusted source)


def realised_counts(
    synthetic_households: pd.DataFrame,
    donor_households: pd.DataFrame,
    donor_persons: pd.DataFrame,
    controls: Iterable,
) -> Tuple[pd.DataFrame, int, int]:
    """Long [zensus100m, control, realised] for ZENSUS100m-geography controls.

    Person counts come from ``donor_persons`` via the synthetic household's
    ``H_ID``; ``donor_households`` supplies household-level attributes.

    Returns
    -------
    tuple[pd.DataFrame, int, int, set[str]]
        ``(frame, n_resolved, n_skipped, resolved_fields)`` so the caller can
        enforce a primary-vs-fallback rate guard (CLAUDE.md mandatory) AND
        restrict the target side to controls that were actually evaluated --
        otherwise a skipped control's target merges against realised=0 and
        fabricates a 100% error (2026-07-12 validation audit).
    """
    from braunschweig.popsim import control_spec

    rows = []
    n_resolved = 0
    n_skipped = 0
    resolved_fields: set = set()

    # Deduplicate the household donor on H_ID so the household-table join is
    # one row per synthetic household (no silent fan-out).  Log a warning when
    # duplicates are present so the issue is always observable.
    n_before = len(donor_households)
    donor_hh_unique = donor_households.drop_duplicates("H_ID")
    n_dropped = n_before - len(donor_hh_unique)
    if n_dropped > 0:
        logger.warning(
            "[integerizer_quality] dropped %d duplicate H_ID row(s) from the "
            "household donor before the realised-count join (kept %d).",
            n_dropped, len(donor_hh_unique),
        )

    hh = synthetic_households[[_CELL, "H_ID"]]

    for control in controls:
        if getattr(control, "geography", None) != _CELL:
            continue
        expr = control.expression_for("mid")
        if expr is None:
            n_skipped += 1
            logger.info("[integerizer_quality] control %s: no mid expression; skipped",
                        control.name)
            continue
        # Route on seed_table (CatalogControl has NO 'family' field).
        if control.seed_table == control_spec.SEED_TABLE_PERSONS:
            frame = hh.merge(donor_persons, on="H_ID", how="left")
        else:
            frame = hh.merge(donor_hh_unique, on="H_ID", how="left")
        try:
            mask = _evaluate(expr, frame)
        except Exception as error:  # surface, never silently swallow
            n_skipped += 1
            logger.warning(
                "[integerizer_quality] control %s: expression %r failed (%s); skipped",
                control.name, expr, error,
            )
            continue
        n_resolved += 1
        # Key the realised count by the geography-suffixed control field
        # (``{name}_{geography}``) -- this is the SAME identifier the control_totals
        # target files carry (e.g. ``has_car_ZENSUS100m``). Keying by the bare
        # ``control.name`` made the target<-realised merge in cell_error_table never
        # match, so realised was filled 0 everywhere (fabricated -100% fit).
        control_field = f"{control.name}_{control.geography}"
        resolved_fields.add(control_field)
        counts = frame.loc[mask].groupby(_CELL).size()
        for cell, n in counts.items():
            rows.append({"zensus100m": cell, "control": control_field, "realised": int(n)})

    return (pd.DataFrame(rows, columns=["zensus100m", "control", "realised"]),
            n_resolved, n_skipped, resolved_fields)


def _load_targets(control_totals_path: Union[str, Path]) -> pd.DataFrame:
    """control_totals_ZENSUS100m.csv -> long [zensus100m, control, target]."""
    df = pd.read_csv(control_totals_path)
    id_col = _CELL if _CELL in df.columns else df.columns[0]
    long = df.melt(id_vars=[id_col], var_name="control", value_name="target")
    return long.rename(columns={id_col: "zensus100m"})


def cell_error_table(work_dir, mid_dir, *, random_seed: int, tiers, employment_grid: bool,
                     weekend: bool) -> pd.DataFrame:
    """Long [zensus100m, control, realised, target, abs_error, batch, KREIS] over all batches."""
    from braunschweig.popsim import control_spec, mid as midmod, seed as seedmod

    from tqdm import tqdm

    work_dir = Path(work_dir)
    controls = control_spec.full_catalog(include_tiers=tuple(tiers),
                                         include_employment_grid=employment_grid)
    controls = [c for c in controls if getattr(c, "geography", None) == _CELL]
    logger.info("[integerizer_quality] %d ZENSUS100m controls; loading completed donor "
                "(member completion + weekend match) -- this is the slow phase...",
                len(controls))
    rng = np.random.RandomState(random_seed + 74513)
    day_filter = seedmod.ALL_REPORTING_KERNWO if weekend else None
    donor_hh, donor_p, _creport, _mreport = midmod.load_completed_donor(
        mid_dir, completion_rng=rng, day_filter_values=day_filter)
    # Attach the derived hh_type5 (Zensus Familientyp) onto the donor households so the
    # household-type controls (Typ_priv_HH_Familie, whose mid expression is
    # ``households.hh_type5 == '<class>'``) are MEASURABLE. load_completed_donor does not
    # carry hh_type5 -- it is derived in project_completed_seed -- so we reuse the SAME
    # helper here (no reimplementation), keeping the realised count consistent with the
    # seed convention. Without it those expressions raise (missing column) -> skipped ->
    # fabricated -100% fit.
    hh_type5 = seedmod.derive_hh_type5(donor_p, household_id_col="H_ID", age_col="HP_ALTER")
    donor_hh = donor_hh.copy()
    donor_hh["hh_type5"] = donor_hh["H_ID"].map(hh_type5)
    logger.info("[integerizer_quality] donor loaded: %d households, %d persons; "
                "hh_type5 attached (%d classified); evaluating controls per batch...",
                len(donor_hh), len(donor_p), int(donor_hh["hh_type5"].notna().sum()))

    parts = []
    total_resolved = 0
    total_skipped = 0
    batch_dirs = sorted(work_dir.glob("batch_*"))
    # Progress bar over the per-batch realised-vs-target evaluation so the run's
    # duration is visible (the donor load above emits its own progress heartbeats).
    for batch_dir in tqdm(batch_dirs, desc="[integerizer_quality] batches", unit="batch"):
        out = batch_dir / "output"
        targets_path = batch_dir / "data" / "control_totals_ZENSUS100m.csv"
        syn_hh_path = out / "synthetic_households.csv"
        if not (syn_hh_path.is_file() and targets_path.is_file()):
            logger.warning("batch %s: missing synthetic_households or control_totals; skipped",
                           batch_dir.name)
            continue
        syn_hh = pd.read_csv(syn_hh_path)
        realised, n_resolved, n_skipped, resolved_fields = realised_counts(
            syn_hh, donor_hh, donor_p, controls)
        total_resolved += n_resolved
        total_skipped += n_skipped
        target = _load_targets(targets_path)
        # Only score controls that were actually EVALUATED against the synthetic
        # population. A skipped control (missing donor column / no expression)
        # produces no realised rows; keeping its target and filling realised=0
        # fabricates a 100% error for a control we simply could not measure
        # (2026-07-12 validation audit). Dropped target controls are logged.
        dropped = sorted(set(target["control"]) - resolved_fields)
        if dropped:
            logger.warning(
                "batch %s: %d target control(s) were not evaluated and are "
                "EXCLUDED from the error table (not scored as 100%% error): %s",
                batch_dir.name, len(dropped), dropped[:5])
        target = target[target["control"].isin(resolved_fields)]
        merged = target.merge(realised, on=["zensus100m", "control"], how="left")
        merged["realised"] = merged["realised"].fillna(0).astype(int)
        merged["abs_error"] = (merged["realised"] - merged["target"]).abs()
        merged["batch"] = batch_dir.name
        # Carry the KREIS column forward from synthetic_households when available.
        if "KREIS" in syn_hh.columns:
            cell_kreis = syn_hh[[_CELL, "KREIS"]].drop_duplicates(_CELL)
            merged = merged.merge(cell_kreis.rename(columns={_CELL: "zensus100m"}),
                                  on="zensus100m", how="left")
        parts.append(merged)

    logger.info("[integerizer_quality] control expressions resolved %d, skipped %d",
                total_resolved, total_skipped)
    if total_resolved == 0:
        raise RuntimeError(
            "integerizer_quality: NO control expression resolved against the synthetic "
            "population -- realised counts would be all-zero (fabricated 100%% error). "
            "Check the control_spec expression format / donor attribute columns."
        )

    long = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(
        columns=["zensus100m", "control", "realised", "target", "abs_error", "batch"])

    # Defense-in-depth: the realised counts must actually ALIGN to the targets after
    # the per-batch target<-realised merge. If realised is 0 on (almost) every cell
    # that has a positive target, the realised and target control keys are not matching
    # (e.g. a {name}_{geography} suffix mismatch) -- which silently fabricates a -100%
    # fit. Fail loud. (A control genuinely absent from a cell legitimately contributes
    # 0, so some zeros are expected; ~0% realised>0 across the whole frame is the signal
    # that the keys did not join -- the exact bug class that slipped past the
    # expression-resolved guard, since NaN-attribute comparisons evaluate to False.)
    if len(long):
        pos_target = long["target"] > 0
        n_pos = int(pos_target.sum())
        n_realised_pos = int((long.loc[pos_target, "realised"] > 0).sum())
        share = (n_realised_pos / n_pos) if n_pos else 0.0
        logger.info(
            "[integerizer_quality] realised>0 on %d/%d positive-target cells (%.1f%%)",
            n_realised_pos, n_pos, 100.0 * share,
        )
        if n_pos and share < 0.01:
            raise RuntimeError(
                "integerizer_quality: realised is ~0 on essentially all positive-target "
                "cells (%.2f%%) -- the realised and target control keys are not aligning "
                "(check the {name}_{geography} control field). Refusing to emit a "
                "fabricated ~100%% error." % (100.0 * share)
            )

    return long
