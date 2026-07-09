"""Generic per-Kreis attribute controls for popsim_mid (registry-driven).

Generalizes the L1 economic_status x Kreis control: any donor-inherited household/person
attribute with a committed per-Kreis MiD target (row-% shares) becomes a KREIS PopulationSim
control via a REGISTRY entry. This module turns one entry's shares (optionally Dirichlet-shrunk
toward the region-aggregate row) x the per-Kreis household total into integer per-Kreis counts
that partition the household total (IPF-consistent), plus the control-column naming. Pure module.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence, Union

import numpy as np
import pandas as pd

# Canonical low->high economic-status order (identical to
# braunschweig.synthesis.population.enriched.ECONOMIC_STATUS_CATEGORIES).
_ECON_CATEGORIES = ("very_low", "low", "medium", "high", "very_high")

# The region-aggregate row label used as the Dirichlet shrinkage prior mean. The H4 CSV uses
# the ars5 code "03ZGB"; other committed regional tables use "Gesamt". Both are accepted.
_AGG_ARS5 = ("03ZGB", "Gesamt")


@dataclass(frozen=True)
class KreisAttributeControl:
    """One registered per-Kreis attribute control (household or person level)."""
    name: str
    seed_column: str
    level: str  # "household" | "person"
    categories: tuple  # ((label, predicate on seed_column), ...), e.g. ("3", ">= 3")
    target_csv_relpath: str  # under data_path, e.g. "braunschweig/mid/mid2023_H4_status_by_kreis.csv"
    target_columns: tuple  # CSV share columns, in category order
    tier: str  # "hard" | "soft"


def control_columns(ctl: KreisAttributeControl) -> tuple:
    """The KREIS control / census-source column names (one per category), in category order."""
    return tuple(f"{ctl.name}_{label}" for label, _ in ctl.categories)


def load_kreis_target(
    data_path: Union[str, Path],
    ctl: KreisAttributeControl,
    *,
    expected_ars5: Sequence[str] | None = None,
    share_tolerance: float = 1e-6,
) -> pd.DataFrame:
    """Load a committed per-Kreis control target CSV for a registry entry.

    Reads ``ctl.target_csv_relpath`` (relative to ``data_path``), a comment-headed
    ``ars5,source,n_effective,<category shares...>`` CSV (the ``target2026_*`` blended
    tables). Returns a frame with columns ``["ars5", *ctl.target_columns]`` (comment
    lines and the ``source``/``n_effective`` provenance columns dropped). No silent
    fallback: fails fast if the file, a target category column, the region-aggregate
    row, an ``expected_ars5`` Kreis, or the per-row share normalisation is missing/invalid.
    """
    path = Path(data_path) / ctl.target_csv_relpath
    if not path.exists():
        raise FileNotFoundError(f"load_kreis_target[{ctl.name}]: target CSV not found at {path}.")
    df = pd.read_csv(path, comment="#")
    missing_cols = [c for c in ("ars5", *ctl.target_columns) if c not in df.columns]
    if missing_cols:
        raise ValueError(
            f"load_kreis_target[{ctl.name}]: target CSV {path} missing columns {missing_cols}; "
            f"has {list(df.columns)}.")
    df = df.copy()
    df["ars5"] = df["ars5"].astype(str)
    out = df[["ars5", *ctl.target_columns]].reset_index(drop=True)
    if not out["ars5"].isin(_AGG_ARS5).any():
        raise ValueError(
            f"load_kreis_target[{ctl.name}]: no region-aggregate row {_AGG_ARS5} in {path} "
            f"(required as the shrinkage prior mean).")
    if expected_ars5 is not None:
        have = set(out["ars5"])
        missing_kreise = [k for k in expected_ars5 if str(k) not in have]
        if missing_kreise:
            raise ValueError(
                f"load_kreis_target[{ctl.name}]: target {path} missing Kreis rows {missing_kreise}.")
    sums = out[list(ctl.target_columns)].to_numpy(dtype=float).sum(axis=1)
    bad = np.abs(sums - 1.0) > share_tolerance
    if bad.any():
        raise ValueError(
            f"load_kreis_target[{ctl.name}]: rows {out.loc[bad, 'ars5'].tolist()} do not "
            f"sum to 1 (got {sums[bad].tolist()}).")
    return out


# Path constants for the committed blended targets (FINAL; consume with prior_n = 0).
_TARGET_DIR = "braunschweig/targets"

# The economic_status entry reproduces the L1 status_kreis_control exactly: seed column oek_status
# (coded 1..5 -> very_low..very_high), the committed H4 CSV, and control columns
# economic_status_{class}. The per-category predicate "== k" (k = 1..5) is applied downstream in the
# catalog factory using the code; the labels + CSV columns are the canonical status classes.
REGISTRY: tuple = (
    KreisAttributeControl(
        name="economic_status",
        seed_column="oek_status",
        level="household",
        categories=tuple((k, f"== {i}") for i, k in enumerate(_ECON_CATEGORIES, start=1)),
        # Blended per-Kreis target (target2026_*): FINAL row-% shares (fractions summing to 1),
        # consumed via load_kreis_target. Replaces the old raw MiD H4 percentage CSV (Task 4).
        target_csv_relpath=f"{_TARGET_DIR}/target2026_economic_status_by_kreis.csv",
        target_columns=_ECON_CATEGORIES,
        tier="hard",
    ),
    KreisAttributeControl(
        name="number_of_cars",
        seed_column="number_of_cars",  # resolved column (H_ANZAUTO 99 imputed), see mid.load_mid_seed
        level="household",
        categories=(("0", "== 0"), ("1", "== 1"), ("2", "== 2"), ("3plus", ">= 3")),
        target_csv_relpath=f"{_TARGET_DIR}/target2026_number_of_cars_by_kreis.csv",
        target_columns=("cars_0", "cars_1", "cars_2", "cars_3plus"),
        tier="hard",
    ),
    KreisAttributeControl(
        name="number_of_bicycles",
        # resolved column (attributes.map_number_of_bicycles): 99 imputed within hhgr_gr,
        # source anzpedrad = bicycles INCLUDING pedelecs/e-bikes (MiD H12.3 / SrV
        # alle-Raeder construct, verified 2026-07-08 against the MiD B1 microdata).
        seed_column="number_of_bicycles",
        level="household",
        categories=(("0", "== 0"), ("1", "== 1"), ("2", "== 2"), ("3", "== 3"), ("4plus", ">= 4")),
        target_csv_relpath=f"{_TARGET_DIR}/target2026_number_of_bicycles_by_kreis.csv",
        target_columns=("bikes_0", "bikes_1", "bikes_2", "bikes_3", "bikes_4plus"),
        tier="soft",
    ),
    KreisAttributeControl(
        name="has_ebike",
        # 0/1 int resolved from H_ANZPED (Anzahl Pedelecs; verified 2026-07-08 against the
        # MiD B1 household microdata, see attributes.map_has_ebike).
        seed_column="has_ebike",
        level="household",
        categories=(("yes", "== 1"), ("no", "== 0")),
        target_csv_relpath=f"{_TARGET_DIR}/target2026_has_ebike_by_kreis.csv",
        target_columns=("ebike_yes", "ebike_no"),
        tier="soft",
    ),
    # The first PERSON-level entry (2026-07-08, issue #116 follow-on): trip_class
    # steers the per-Kreis distribution of weekday trips (0 / 1-2 / 3-4 / 5+), int-coded
    # 0..3 by attributes.map_trip_class from MiD anzwege1 (missing codes 803/804 imputed
    # within alter_gr1; see docs/data/MID2023_HANDBOOK_REFERENCE.md). The committed target
    # is built purely from the SrV 2023 Braunschweig+RGB aggregate
    # (scripts/build_trip_class_target.py; NO MiD blending) -- see that script's header
    # and docs/superpowers/plans/2026-07-08-trip-class-kreis-control.md for the documented
    # decisions:
    #   (1) UNIVERSE (weekday): the seed class is derived from each person's REALISED
    #       weekday plan source (mid.derive_trip_class_seed), not their own reporting-day
    #       diary. After weekend_plan_match every plan source is a weekday (kernwo 1-3)
    #       donor, so the seed class matches the SrV Di-Do mittlerer-Werktag target AND
    #       the trips the synthetic person actually executes. (The earlier "SrV Di-Do vs.
    #       MiD kernwo Mo-Fr seed <= 0.63pp" note was WRONG for the default pipeline, which
    #       keeps ALL reporting days in the donor -- ~29% weekend reporters, measured ~2pp
    #       more immobile; audit 2026-07-09 fixed the derivation.)
    #   (2) DECISION (level anchoring): the synthetic distribution is DELIBERATELY
    #       anchored to the SrV level (regional survey = regional behaviour authority),
    #       not corrected to the MiD mobility-rate level (uniform ~+5..+8pp offset).
    #   (3) ASSUMPTION (Wolfsburg): 03103 (not covered by SrV) uses the SrV region
    #       total, same convention as target2026_has_ebike.
    KreisAttributeControl(
        name="trip_class",
        seed_column="trip_class",
        level="person",
        categories=(("0", "== 0"), ("1_2", "== 1"), ("3_4", "== 2"), ("5plus", "== 3")),
        target_csv_relpath=f"{_TARGET_DIR}/target2026_trip_class_by_kreis.csv",
        target_columns=("trips_0", "trips_1_2", "trips_3_4", "trips_5plus"),
        tier="soft",
    ),
)


def _shrunk_shares(ctl: KreisAttributeControl, target_df: pd.DataFrame, prior_n: float) -> pd.DataFrame:
    """Per-Kreis category shares (rows sum to 1), Dirichlet-shrunk toward the region-aggregate row."""
    cols = list(ctl.target_columns)
    agg = target_df[target_df["ars5"].astype(str).isin(_AGG_ARS5)]
    if agg.empty:
        raise ValueError(f"target for {ctl.name}: no region-aggregate row {_AGG_ARS5} for shrinkage prior.")
    agg_vec = agg.iloc[0][cols].to_numpy(dtype=float)
    agg_share = agg_vec / agg_vec.sum()
    rows = []
    for _, r in target_df.iterrows():
        raw = r[cols].to_numpy(dtype=float)
        total = raw.sum()
        if str(r["ars5"]) in _AGG_ARS5 or prior_n <= 0.0:
            share = raw / total if total > 0 else agg_share.copy()
        else:
            share = (raw + prior_n * agg_share) / (total + prior_n)
        rows.append({"ars5": str(r["ars5"]), **dict(zip(cols, share))})
    return pd.DataFrame(rows)


def _largest_remainder(shares: np.ndarray, total: int) -> np.ndarray:
    if total <= 0:
        return np.zeros(len(shares), dtype=int)
    exact = shares * total
    floor = np.floor(exact).astype(int)
    rem = int(total - floor.sum())
    if rem > 0:
        floor[np.argsort(-(exact - floor))[:rem]] += 1
    return floor


def attribute_kreis_count_table(
    ctl: KreisAttributeControl,
    target_df: pd.DataFrame,
    hh_total_by_ars5: Mapping[str, float],
    *,
    prior_n: float = 0.0,
) -> pd.DataFrame:
    """Per-Kreis integer counts per category, summing to round(hh_total[k]); columns ARS_kreis +
    control_columns(ctl). Fail-fast if a Kreis is absent from the target (no under-constrained control)."""
    shares = _shrunk_shares(ctl, target_df, prior_n).set_index("ars5")
    cols = list(ctl.target_columns)
    out_cols = list(control_columns(ctl))
    out = []
    for ars5, hh_total in hh_total_by_ars5.items():
        key = str(ars5)
        if key not in shares.index:
            raise ValueError(f"attribute_kreis_count_table[{ctl.name}]: Kreis {key} absent from the target frame.")
        counts = _largest_remainder(shares.loc[key, cols].to_numpy(dtype=float), int(round(float(hh_total))))
        out.append({"ARS_kreis": key, **dict(zip(out_cols, counts))})
    return pd.DataFrame(out)
