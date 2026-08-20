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

from braunschweig.popsim.attributes import EMPLOYMENT_STATUS_BY_P_BKAT, PT_TICKET_GROUPS

# Canonical low->high economic-status order (identical to
# braunschweig.synthesis.population.enriched.ECONOMIC_STATUS_CATEGORIES).
_ECON_CATEGORIES = ("very_low", "low", "medium", "high", "very_high")

# The seven MiD P_BKAT (Umfang der Erwerbstaetigkeit) employment-status classes, in
# codebook code order 1..7 (see attributes.EMPLOYMENT_STATUS_BY_P_BKAT for the full
# code->label mapping and provenance). Reused here (not re-listed literally) so the
# REGISTRY entry's category/target-column order stays in sync with the seed column's
# actual value set by construction.
_EMP_STATUS_CATEGORIES = tuple(EMPLOYMENT_STATUS_BY_P_BKAT.values())

# The region-aggregate row label used as the Dirichlet shrinkage prior mean. The H4 CSV uses
# the ars5 code "03ZGB"; other committed regional tables use "Gesamt". Both are accepted.
_AGG_ARS5 = ("03ZGB", "Gesamt")

# The share tolerance every CONSUMER of the committed blended targets (target2026_*) must
# pass to :func:`load_kreis_target`. Those CSVs store shares rounded to 4 decimals, so a row
# can sum to 0.9999 / 1.0001 (max observed deviation 1e-4); the loader's own 1e-6 default
# REJECTS them. 1e-3 accepts that rounding while still catching a genuinely mis-normalised
# row (e.g. 0.9 / 1.1); the per-Kreis counts are renormalised + integer-partitioned
# downstream regardless. Stated once here so the two consumers in the popsim stage (the
# KREIS attribute controls and the 1 km ownership grid, issue #240) cannot drift apart.
TARGET_SHARE_TOLERANCE = 1e-3


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
    # Minimum person age the control's universe is restricted to (inclusive), or None for
    # no restriction (the default, and the behaviour of every pre-existing entry). Set this
    # when the committed target's shares are reported over an age-restricted base (e.g. MiD
    # P9 / SrV "employment_status" is 14+) while the seed attribute itself is assigned to ALL
    # persons -- without this the control's per-Kreis universe would silently include
    # under-min_age persons and distort the target shares (the #97 universe trap). When set,
    # BOTH the rendered seed expression (control_spec.attribute_kreis_controls) and the
    # per-Kreis total the category counts partition (stage.person_total_by_kreis_min_age)
    # must apply this restriction; level must be "person" for min_age to have any effect
    # (household-level entries have no natural per-person age to restrict on).
    min_age: int | None = None


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
    # dtype=str at READ time: int64 inference would strip the ars5 leading
    # zero irreversibly; the later .astype(str) could not repair it. Today the
    # mandatory region-aggregate row forces object dtype, but the key padding
    # must not depend on that.
    df = pd.read_csv(path, comment="#", dtype={"ars5": str})
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


def kreis_rows_indexed_by_ars5(target_df: pd.DataFrame) -> pd.DataFrame:
    """The per-Kreis rows of a loaded target, indexed by ``ars5``.

    :func:`load_kreis_target` deliberately keeps the mandatory region-aggregate row
    (``_AGG_ARS5``) alongside the per-Kreis rows, because :func:`_shrunk_shares` needs it
    as the Dirichlet prior mean. A consumer that instead looks targets up BY KREIS -- e.g.
    the 1 km ownership rake (``ownership_grid.rake_ownership_targets``, issue #240) -- must
    drop it: it is not a Kreis, and leaving it in the index would let a lookup of a
    non-Kreis key silently succeed instead of failing loudly.
    """
    out = target_df.set_index("ars5")
    return out.drop(index=[key for key in _AGG_ARS5 if key in out.index])


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
    # tier="hard" (feature #224 task 6, flipped from the original "soft"): the SOFT tier
    # missed its SrV Mobilitaetsquote target (synthetic immobility ~26.5% vs. SrV
    # target ~11.2%, the largest single SrV gap of all Kreis controls). Registering
    # trip_class HARD classifies its rendered KREIS control columns into the
    # "kreis_hard" importance group (control_spec.IMPORTANCE_PROFILES), par with
    # economic_status/number_of_cars/work_participation/leisure_participation/
    # education_participation, so the control is no longer allowed to yield gracefully
    # to the Zensus backbone in small cells. This is a deliberate BEHAVIOUR CHANGE,
    # verified by the Task-8 smoke of the 2026-07 feature #224 plan.
    KreisAttributeControl(
        name="trip_class",
        seed_column="trip_class",
        level="person",
        categories=(("0", "== 0"), ("1_2", "== 1"), ("3_4", "== 2"), ("5plus", "== 3")),
        target_csv_relpath=f"{_TARGET_DIR}/target2026_trip_class_by_kreis.csv",
        target_columns=("trips_0", "trips_1_2", "trips_3_4", "trips_5plus"),
        tier="hard",
    ),
    # employment_status x Kreis control (feature #172, task 4): the second PERSON-level
    # entry. seed_column employment_status is the P_BKAT-derived seven-class string
    # (attributes.map_employment_status), assigned to ALL persons including <14 (0%
    # structural missing). Its committed blended target (MiD P9 x SrV V_ERW, tasks 1-3)
    # reports shares over the MiD P9 / SrV base of persons aged 14+ ONLY. min_age=14
    # restricts BOTH the rendered seed expression (control_spec.attribute_kreis_controls
    # AND-s in "(persons.HP_ALTER >= 14)") and the per-Kreis person total this control's
    # category counts partition (stage.person_total_by_kreis_min_age) to that same 14+
    # universe -- omitting either half would let <14 children distort
    # nicht_erwerbstaetig (the #97 universe trap this mirrors).
    KreisAttributeControl(
        name="employment_status",
        seed_column="employment_status",
        level="person",
        categories=tuple((k, f"== '{k}'") for k in _EMP_STATUS_CATEGORIES),
        target_csv_relpath=f"{_TARGET_DIR}/target2026_employment_status_by_kreis.csv",
        target_columns=_EMP_STATUS_CATEGORIES,
        tier="soft",
        min_age=14,
    ),
    # pt_ticket_group x Kreis control (issue #321): the PT-subscription entry. seed_column
    # pt_ticket_group is the THREE-group collapse of the resolved pt_subscription_type
    # (attributes.map_pt_ticket_group), derived onto the seed on both seed paths exactly
    # like employment_status. Three groups and not the nine P24.1 categories because
    # BraunschweigPtCostModel.calculateCost_MU returns 0.0 for every flatrate holder: the
    # four flatrate types are simulation-equivalent and the non-flatrate split has no
    # simulation effect, so 9 x 8 Kreise = 72 control columns would mostly steer
    # simulation-neutral structure. deutschlandticket stays its own group because it is the
    # only flatrate category with a second committed survey and the natural policy lever.
    #   * tier SOFT, not hard: the MiD P24.1 Deutschlandticket component sits ~4pp above
    #     the committed SrV figure, so the MiD flatrate aggregate may be biased high. A
    #     hard control would force a level the evidence does not pin down; soft shrinks
    #     toward the region row instead.
    #   * min_age=14: P24.1 is an "ab 14 Jahre" table, so BOTH the seed expression and the
    #     per-Kreis person total it partitions are restricted to 14+ (the #97 trap).
    #   * The target is MiD-only -- see the CSV header and ADR-0060 for why SrV is a
    #     corridor check here rather than a target.
    KreisAttributeControl(
        name="pt_ticket_group",
        seed_column="pt_ticket_group",
        level="person",
        categories=tuple((g, f"== '{g}'") for g in PT_TICKET_GROUPS),
        target_csv_relpath=f"{_TARGET_DIR}/target2026_pt_ticket_group_by_kreis.csv",
        target_columns=PT_TICKET_GROUPS,
        tier="soft",
        min_age=14,
    ),
    # work_participation x Kreis control (feature #224, task 4): the third PERSON-level
    # entry. seed_column work_participation is the 0/1 has-a-work-trip flag
    # (attributes.map_work_participation from mid.compute_has_work_trip / mid's Wege
    # table), assigned to ALL persons (no age restriction; the SrV target reports
    # shares over the full weighted-person universe). The committed target
    # (target2026_work_participation_by_kreis.csv) is built PURELY from the SrV 2023
    # Braunschweig+RGB participation aggregate (scripts/build_participation_target.py;
    # NO MiD blending), mirroring the trip_class target's construction:
    #   (1) DECISION (level anchoring): the synthetic distribution is anchored to the
    #       SrV level (regional survey = regional behaviour authority).
    #   (2) ASSUMPTION (Wolfsburg): 03103 (not covered by SrV) uses the SrV region
    #       total, the SAME convention as target2026_has_ebike / target2026_trip_class.
    # tier="hard": unlike employment_status (soft, yields gracefully to the Zensus
    # backbone in small cells), work_participation is registered HARD so its importance
    # is classified into the "kreis_hard" group (control_spec.IMPORTANCE_PROFILES)
    # alongside economic_status/number_of_cars. (trip_class was also SOFT here but was
    # promoted to HARD by feature #224 task 6 -- see its entry's own comment above.)
    KreisAttributeControl(
        name="work_participation",
        seed_column="work_participation",
        level="person",
        categories=(("yes", "== 1"), ("no", "== 0")),
        target_csv_relpath=f"{_TARGET_DIR}/target2026_work_participation_by_kreis.csv",
        target_columns=("work_yes", "work_no"),
        tier="hard",
    ),
    # leisure_participation / education_participation x Kreis controls (feature #224
    # task 5): the fourth and fifth PERSON-level entries. Identical shape to
    # work_participation -- same seed machinery (attributes.map_participation from
    # mid.compute_has_purpose_trip / mid's Wege table), parametrized by purpose rather
    # than duplicated (mid.PARTICIPATION_W_ZWECK: leisure={7}, education={3, 11, 12}).
    # Their committed targets (target2026_leisure_participation_by_kreis.csv /
    # target2026_education_participation_by_kreis.csv) are built PURELY from the SAME
    # SrV 2023 Braunschweig+RGB participation aggregate (scripts/build_participation_
    # target.py --purpose {leisure,education}; NO MiD blending), mirroring the
    # work_participation target's construction and the same two documented decisions:
    #   (1) DECISION (level anchoring): the synthetic distribution is anchored to the
    #       SrV level (regional survey = regional behaviour authority).
    #   (2) ASSUMPTION (Wolfsburg): 03103 (not covered by SrV) uses the SrV region
    #       total, the SAME convention as target2026_has_ebike / target2026_work_
    #       participation.
    # tier="hard": mirrors work_participation (registered hard, not soft like
    # employment_status), so both are classified into the "kreis_hard" importance group.
    KreisAttributeControl(
        name="leisure_participation",
        seed_column="leisure_participation",
        level="person",
        categories=(("yes", "== 1"), ("no", "== 0")),
        target_csv_relpath=f"{_TARGET_DIR}/target2026_leisure_participation_by_kreis.csv",
        target_columns=("leisure_yes", "leisure_no"),
        tier="hard",
    ),
    KreisAttributeControl(
        name="education_participation",
        seed_column="education_participation",
        level="person",
        categories=(("yes", "== 1"), ("no", "== 0")),
        target_csv_relpath=f"{_TARGET_DIR}/target2026_education_participation_by_kreis.csv",
        target_columns=("education_yes", "education_no"),
        tier="hard",
    ),
    # escort_participation x Kreis control (issue #227): the sixth PERSON-level entry.
    # Identical shape to work/leisure/education_participation -- same seed machinery
    # (attributes.map_participation from mid.compute_has_purpose_trip), parametrized by
    # purpose. mid.PARTICIPATION_W_ZWECK["escort"] = {6}: the ACTIVE escort leg only
    # (W_ZWECK 6, Bringen/Holen); the PASSIVE leg (W_ZWECK 13, the escorted minors' own
    # trips) is deliberately excluded to match the SrV target universe (E_ZWECK_9 == 6
    # codes only the escorter's trip) -- see the PARTICIPATION_W_ZWECK comment. The
    # committed target (target2026_escort_participation_by_kreis.csv) is built PURELY
    # from the SAME SrV 2023 Braunschweig+RGB participation aggregate
    # (scripts/build_participation_target.py --purpose escort; NO MiD blending), with
    # the same two documented decisions as the other three (SrV level anchoring;
    # Wolfsburg = SrV region total). tier="hard": mirrors work/leisure/education, so it
    # is classified into the "kreis_hard" importance group.
    # HONEST CAVEAT (issue #227): the SrV-vs-MiD escort gap is small (~+2.8pp) and the
    # #224 importance sweep showed participation fit is donor/feasibility-bound --
    # expect partial attainment, as for work_participation.
    KreisAttributeControl(
        name="escort_participation",
        seed_column="escort_participation",
        level="person",
        categories=(("yes", "== 1"), ("no", "== 0")),
        target_csv_relpath=f"{_TARGET_DIR}/target2026_escort_participation_by_kreis.csv",
        target_columns=("escort_yes", "escort_no"),
        tier="hard",
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
