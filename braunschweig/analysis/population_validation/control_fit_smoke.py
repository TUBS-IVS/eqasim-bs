"""Reusable, CI-sized control checks (issue #282).

The per-run control-fit check used to be ad hoc, so a control defect surfaced only inside a
full synthesis -- hours in, on the server. These checks run against the ACTIVE control
specification in seconds and without PopulationSim (which lives in a separate uv project and
is invoked as a subprocess), which means they verify the SPECIFICATION rather than the
balancer's numerical result. The region-scoped smoke config
(``configs/overlays/smoke_kreis_control_fit.yml``) covers the other half.

What each check is for, in the order a defect would otherwise bite:

``check_category_partition``
    Every Kreis control's categories must be mutually exclusive AND exhaustive over its
    universe, because ``kreis_attribute_control.attribute_kreis_count_table`` distributes the
    per-Kreis TOTAL across them. A person matching two categories is counted twice; a person
    matching none silently disappears from a total that is supposed to be partitioned. Both
    are invisible in the run log.

``check_census_sources_available``
    A control whose ``census_source`` column is neither present in the prepared cell parquet
    nor produced by the aggregation map fails inside PopulationSim with
    ``<field> not in index``, at the end of a long balancing.

``check_kreis_targets``
    Every registered Kreis control's committed target must load through the production
    loader, cover every expected Kreis, and sum to 1 per row.

``control_fit``
    The measurement itself: realised vs target share per category in percentage points,
    shared by the checks above and by run-time reporting.

Each check returns a :class:`CheckReport` rather than raising, so a caller can run all of
them and report every failure at once instead of stopping at the first.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from braunschweig.popsim import control_spec as cs
from braunschweig.popsim import kreis_attribute_control as kac

logger = logging.getLogger(__name__)

#: Cell parquet path relative to ``data_path`` (the file is local-only, see the Data Registry
#: entry ``zensus2022_grid_cells``), so every check that needs it degrades to "skipped"
#: instead of failing on a checkout without the data.
CELLS_100M_RELPATH = "braunschweig/popsim/cells/zensus2022_grid_100m_de_prepared.parquet"

#: Seed table name -> the frame keyword the expressions are evaluated against.
_SEED_TABLE_KEYWORD = {
    cs.SEED_TABLE_PERSONS: "persons",
    cs.SEED_TABLE_HOUSEHOLDS: "households",
}


@dataclass
class CheckReport:
    """Outcome of one check: what was looked at, and every failure found."""
    check: str
    n_controls_checked: int = 0
    failures: list = field(default_factory=list)
    skipped: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures

    def summary(self) -> str:
        state = "OK" if self.ok else f"{len(self.failures)} FAILURE(S)"
        skipped = f", {len(self.skipped)} skipped" if self.skipped else ""
        return (f"[{self.check}] {state} over {self.n_controls_checked} control(s){skipped}")


def _evaluate(expression: str, persons: pd.DataFrame,
              households: pd.DataFrame) -> np.ndarray:
    """Evaluate one rendered control expression, as PopulationSim would.

    Plain ``eval`` with ``persons`` / ``households`` / ``np`` in scope: the committed
    production baseline contains ``(households.H_GEW < np.inf)``, which proves the evaluator
    is Python-level and not ``DataFrame.eval`` (numexpr would not know ``np``).
    """
    value = eval(expression, {"np": np},  # noqa: S307 - control expressions are repo-owned
                 {"persons": persons, "households": households})
    return np.asarray(value, dtype=bool)


def check_category_partition(
    registry: Iterable[kac.KreisAttributeControl],
    *,
    persons: pd.DataFrame,
    households: pd.DataFrame,
) -> CheckReport:
    """Assert every Kreis control's categories partition its universe on a given seed."""
    report = CheckReport(check="category_partition")
    for ctl in registry:
        keyword = _SEED_TABLE_KEYWORD[{"person": cs.SEED_TABLE_PERSONS,
                                       "household": cs.SEED_TABLE_HOUSEHOLDS}[ctl.level]]
        frame = persons if keyword == "persons" else households
        missing = [c for c in (ctl.seed_column,) if c not in frame.columns]
        if missing:
            report.skipped.append(
                f"{ctl.name}: seed column {ctl.seed_column!r} absent from the {keyword} "
                "fixture")
            continue

        rendered = cs.attribute_kreis_controls([ctl])
        masks = {}
        for control in rendered:
            masks[control.name] = _evaluate(
                control.seed_expressions["mid"], persons, households)

        stacked = np.vstack(list(masks.values())) if masks else np.empty((0, len(frame)))
        hits = stacked.sum(axis=0)

        # The universe is the age-restricted subset when min_age is set: rows below it are
        # legitimately outside EVERY category and must not count as a gap.
        universe = np.ones(len(frame), dtype=bool)
        if ctl.min_age is not None and keyword == "persons":
            universe = (pd.to_numeric(persons["HP_ALTER"], errors="coerce")
                        >= ctl.min_age).to_numpy()

        uncovered = int(((hits == 0) & universe).sum())
        overlapping = int(((hits > 1) & universe).sum())
        if uncovered:
            report.failures.append(
                f"{ctl.name}: {uncovered} row(s) of the universe are uncovered by any "
                f"category (categories {[c for c, _ in ctl.categories]}) -- those rows drop "
                "out of a total the categories are supposed to partition")
        if overlapping:
            report.failures.append(
                f"{ctl.name}: {overlapping} row(s) match more than one category (overlap) "
                "-- those rows are counted twice")
        report.n_controls_checked += 1
    logger.info("%s", report.summary())
    return report


def cell_parquet_columns(data_path: str | Path) -> list | None:
    """CLEANED column names of the prepared 100 m cell parquet, or ``None`` when absent.

    The names are passed through ``prepared_cells.clean_col_name``, which is what the loader
    applies: the raw schema carries e.g. ``..._100m-Gitter_adj`` while every control refers to
    ``..._100m_Gitter_adj``. Comparing against the RAW names would report all 25 grid controls
    as missing -- a false alarm that would make the check worthless.

    Returns ``None`` rather than raising because the parquet is local-only (licence), so a
    fresh checkout legitimately has no copy and the caller should skip instead of fail.
    """
    path = Path(data_path) / CELLS_100M_RELPATH
    if not path.exists():
        logger.info("cell parquet absent at %s; source-column check not possible", path)
        return None
    import pyarrow.parquet as pq

    from braunschweig.popsim.prepared_cells import clean_col_name

    return [clean_col_name(c) for c in pq.ParquetFile(path).schema_arrow.names]


def check_census_sources_available(
    controls: Iterable[cs.CatalogControl],
    *,
    available_columns: Sequence[str],
    aggregation_map: Mapping[str, tuple],
) -> CheckReport:
    """Assert every control's census source is either a real cell column or aggregated."""
    report = CheckReport(check="census_sources_available")
    available = set(available_columns)
    for control in controls:
        for source in control.census_source:
            if source in available:
                continue
            if source in aggregation_map:
                continue
            report.failures.append(
                f"{control.name}: census source column {source!r} is neither in the cell "
                "parquet nor produced by the aggregation map -- PopulationSim would fail "
                "with '<field> not in index' at run time")
        report.n_controls_checked += 1
    logger.info("%s", report.summary())
    return report


def check_kreis_targets(
    registry: Iterable[kac.KreisAttributeControl],
    data_path: str | Path,
    *,
    expected_ars5: Sequence[str],
    share_tolerance: float = 1e-3,
) -> CheckReport:
    """Assert every registered Kreis control's committed target loads and is normalised.

    ``share_tolerance`` defaults to 1e-3 because the committed tables store shares rounded to
    four decimals, so an exactly-1.0 row can read as 0.9999 -- the same tolerance the stage
    uses.
    """
    report = CheckReport(check="kreis_targets")
    for ctl in registry:
        try:
            kac.load_kreis_target(str(data_path), ctl, expected_ars5=expected_ars5,
                                  share_tolerance=share_tolerance)
        except FileNotFoundError as exc:
            report.skipped.append(f"{ctl.name}: {exc}")
            continue
        except (ValueError, KeyError, RuntimeError) as exc:
            report.failures.append(f"{ctl.name}: {exc}")
            continue
        report.n_controls_checked += 1
    logger.info("%s", report.summary())
    return report


def control_fit(realised: pd.DataFrame, target: pd.DataFrame, *,
                category_col: str = "category", count_col: str = "count",
                target_share_col: str = "target_share") -> pd.DataFrame:
    """Realised vs target share per category, with the deviation in percentage points.

    Parameters
    ----------
    realised:
        Frame with ``category_col`` and ``count_col`` (absolute realised counts).
    target:
        Frame with ``category_col`` and ``target_share_col`` (fractions).

    Returns
    -------
    pandas.DataFrame
        ``[category, realised_share, target_share, delta_pp, abs_delta_pp]``, sorted by
        descending absolute deviation so the worst cell reads first.
    """
    total = float(realised[count_col].sum())
    if total <= 0:
        raise ValueError("control_fit: realised counts sum to zero; nothing to compare")
    merged = realised.merge(target, on=category_col, how="outer", validate="one_to_one")
    if merged[count_col].isna().any() or merged[target_share_col].isna().any():
        missing = merged.loc[merged[count_col].isna()
                             | merged[target_share_col].isna(), category_col].tolist()
        raise ValueError(
            f"control_fit: category set mismatch between realised and target for {missing}")
    merged["realised_share"] = merged[count_col] / total
    merged["delta_pp"] = (merged["realised_share"] - merged[target_share_col]) * 100.0
    merged["abs_delta_pp"] = merged["delta_pp"].abs()
    return (merged[[category_col, "realised_share", target_share_col, "delta_pp",
                    "abs_delta_pp"]]
            .sort_values("abs_delta_pp", ascending=False)
            .reset_index(drop=True))
