"""
Build the four participation-universe Kreis control targets of Plan B (issue #368, ADR-0109,
task 5): target2026_work_by_employment_by_kreis.csv and the three target2026_education_<band>_
by_kreis.csv tables.

Reads ONLY committed CSVs, never the raw SrV/MiD microdata:
    eqasim-data/data/braunschweig/targets/target2026_employment_status_by_kreis.csv
    eqasim-data/data/braunschweig/srv/srv2023_work_by_employment_by_kreis.csv
    eqasim-data/data/braunschweig/srv/srv2023_education_by_age_by_kreis.csv

work_by_employment (decision Q1, spec section 2.2): MARGIN x CONDITIONAL. The employed margin
per Kreis is the sum of the employment_status classes decided in Q5
(attributes.EMPLOYED_EMPLOYMENT_STATUS_CLASSES = vollzeit, teilzeit, geringfuegig, in_ausbildung)
read from the blended target2026_employment_status_by_kreis.csv; the conditional rates
p_work_employed = P(work trip | employed) and p_work_nonemployed = P(work trip | not employed)
come from the committed SrV aggregate srv2023_work_by_employment_by_kreis.csv. This construction
exists so the new work_by_employment control and the existing employment_status control cannot
disagree on the employed margin (see that aggregate's own registry note for the ~1.4pp SrV-vs-
blend employed-share gap this avoids).

education_by_age (decision Q2, spec section 2.3): no margin, no MiD blending -- the two shares
are the SrV conditional education-participation rate for the requested age band, read directly
from srv2023_education_by_age_by_kreis.csv.

Wolfsburg (03103) and Gesamt conventions (both documented ASSUMPTIONs, not silent choices):
  - work_by_employment: 03103 uses ITS OWN employment_status margin row combined with the
    03ZGB (region-total) conditional rates (source "employment_status_target x
    srv_region_total"); Gesamt uses the Gesamt margin row combined with the SAME 03ZGB
    conditional rates (source "employment_status_target x srv_region_total").
  - education_by_age: 03103 and Gesamt both take the 03ZGB row's p_education directly (the
    scripts/build_participation_target.py convention), sources "srv_region_total" / "srv".

ASSUMPTION (controller ruling R15, universe mismatch, NOT reconciled): the employment_status
margin's universe is persons 14+ with a valid V_ERW; the SrV conditional rates' universe is
persons 14+ INCLUDING the persons with a missing/implausible V_ERW code (classed non-employed
there). The two universes differ by AT MOST 11 persons of the 14,845-person 14+ universe (0.07pp
on the employed share) -- srv2023_work_by_employment_by_kreis.csv's own "# Exclusions:" header
line measures n_missing_employment_code=11 on the 17,269-person at-home-or-mobile universe, not
on the 14,845-person 14+ subset, so 11 is an upper bound on how many of them are 14+ (fix round
1, item 7). This gap is combined as-is and documented in the written target's header, never
silently absorbed.

Output (committed, FINAL targets -- consume via kreis_attribute_control.load_kreis_target with
prior_n = 0): eqasim-data/data/braunschweig/targets/target2026_work_by_employment_by_kreis.csv
(columns ars5, source, n_effective, employed_work, employed_nowork, nonemployed_work,
nonemployed_nowork) and target2026_education_{0_5,6_17,18plus}_by_kreis.csv (columns ars5,
source, n_effective, edu, noedu). Shares are rounded to 4 decimal places (the sibling
target2026_*_participation_by_kreis.csv convention; kreis_attribute_control.
TARGET_SHARE_TOLERANCE = 1e-3 accepts the resulting rounding slack).

Usage:
    python scripts/build_participation_universe_targets.py [--data <eqasim-data/data/braunschweig>]
        [--out-dir <targets dir>]
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from braunschweig.analysis import spatial  # noqa: E402
from braunschweig.popsim.attributes import EMPLOYED_EMPLOYMENT_STATUS_CLASSES  # noqa: E402
from braunschweig.popsim.kreis_attribute_control import (  # noqa: E402
    EDUCATION_AGE_BOUNDS, EDUCATION_BY_AGE_ENTRY_NAMES, EDUCATION_FLAG_CATEGORIES,
    TARGET_SHARE_TOLERANCE, WORK_BY_EMPLOYMENT_CATEGORIES)

DATA_DEFAULT = REPO / "eqasim-data" / "data" / "braunschweig"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("build_participation_universe_targets")

# Wolfsburg (kreisfreie Stadt) is not covered by SrV; the region-aggregate row label used by
# every committed target2026_* table (kreis_attribute_control._AGG_ARS5 accepts both).
WOLFSBURG_ARS5 = "03103"
GESAMT_ARS5 = "Gesamt"

# The 5-digit ARS codes of the 7 Kreise SrV actually surveys, derived from
# spatial.ZGB8 (never re-listed literally) so a change to the canonical ZGB-8 set cannot
# silently drift out of sync with the coverage check below (fix round 1, item 3).
_EXPECTED_SRV_KREIS_CODES = frozenset(code for code in spatial.ZGB8 if code != WOLFSBURG_ARS5)

_EMPLOYMENT_STATUS_TARGET_RELPATH = "targets/target2026_employment_status_by_kreis.csv"
_SRV_WORK_BY_EMPLOYMENT_RELPATH = "srv/srv2023_work_by_employment_by_kreis.csv"
_SRV_EDUCATION_BY_AGE_RELPATH = "srv/srv2023_education_by_age_by_kreis.csv"

# The region-total row's code every written header asserts the conditional rate came from
# (fix round 1, item 5): both builders must not silently accept a relabelled total row.
_REGION_TOTAL_CODE = "03ZGB"


# --------------------------------------------------------------------------- input readers
def read_employment_status_target(data: Path) -> pd.DataFrame:
    """The blended target2026_employment_status_by_kreis.csv, validated to carry every
    employed-class column the work_by_employment margin needs (decision Q5). Fails fast
    (never a silent default) if a class column is absent -- a broken join or a stale
    committed file must be visible immediately, not diluted into a wrong margin."""
    path = data / _EMPLOYMENT_STATUS_TARGET_RELPATH
    if not path.exists():
        raise FileNotFoundError(
            f"build_participation_universe_targets: required committed input missing: {path}")
    df = pd.read_csv(path, comment="#", dtype={"ars5": str})
    missing = [c for c in EMPLOYED_EMPLOYMENT_STATUS_CLASSES if c not in df.columns]
    if missing:
        raise ValueError(
            f"build_participation_universe_targets: employment_status target {path} is missing "
            f"employed-class column(s) {missing} (attributes.EMPLOYED_EMPLOYMENT_STATUS_CLASSES = "
            f"{EMPLOYED_EMPLOYMENT_STATUS_CLASSES}); has {list(df.columns)}.")
    return df


def read_srv_work_by_employment(data: Path) -> pd.DataFrame:
    path = data / _SRV_WORK_BY_EMPLOYMENT_RELPATH
    if not path.exists():
        raise FileNotFoundError(
            f"build_participation_universe_targets: required committed input missing: {path}")
    return pd.read_csv(path, comment="#", dtype={"code": str})


def read_srv_education_by_age(data: Path) -> pd.DataFrame:
    path = data / _SRV_EDUCATION_BY_AGE_RELPATH
    if not path.exists():
        raise FileNotFoundError(
            f"build_participation_universe_targets: required committed input missing: {path}")
    return pd.read_csv(path, comment="#", dtype={"code": str})


# --------------------------------------------------------------------------- validation
def _require_unit_interval(value: float, ars5: str, label: str, context: str) -> None:
    """Raise if `value` is not a finite number inside [0, 1]. `0.0 <= NaN <= 1.0` is False in
    Python, so this also rejects NaN -- the SrV aggregate's own header documents NaN as a
    LEGITIMATE value when a conditioning class is empty in a Kreis, and a NaN reaching a
    written share must fail loudly here rather than propagate into employed_work etc. and
    slip past a sum-based guard (fix round 1, item 1)."""
    if not (0.0 <= value <= 1.0):
        raise ValueError(f"{context}: {label} for Kreis {ars5!r} is out of [0, 1]: {value}.")


def _check_shares_sum_to_one(df: pd.DataFrame, categories, context: str) -> None:
    """Fail fast (no silent under/over-constrained control) if any row's category shares do not
    sum to 1 within kreis_attribute_control.TARGET_SHARE_TOLERANCE -- the same tolerance
    load_kreis_target enforces at consumption time, checked here so a bad build is caught at
    build time, not three stages downstream.

    Checks finiteness FIRST and separately (fix round 1, item 1): `abs(NaN - 1.0) >
    TARGET_SHARE_TOLERANCE` evaluates to False in numpy, so a NaN cell would otherwise pass this
    guard silently. In practice `_require_unit_interval` on every individual share already
    rejects NaN before a row reaches this function; this is a second, independent line of
    defence so the sum guard itself cannot be defeated even if a future cell type skips the
    per-share check."""
    sums = df[list(categories)].to_numpy(dtype=float).sum(axis=1)
    non_finite = ~np.isfinite(sums)
    if non_finite.any():
        raise ValueError(
            f"{context}: rows {df.loc[non_finite, 'ars5'].tolist()} have a non-finite share sum "
            f"(NaN or inf), not a value comparable to 1; got {sums[non_finite].tolist()}.")
    bad = np.abs(sums - 1.0) > TARGET_SHARE_TOLERANCE
    if bad.any():
        raise ValueError(
            f"{context}: rows {df.loc[bad, 'ars5'].tolist()} do not sum to 1 within "
            f"TARGET_SHARE_TOLERANCE ({TARGET_SHARE_TOLERANCE}); got {sums[bad].tolist()}.")


# --------------------------------------------------------------------------- work_by_employment
def _work_by_employment_row(ars5: str, source: str, n_effective: int, margin: float,
                             p_work_employed: float, p_work_nonemployed: float) -> dict:
    _context = "build_work_by_employment_target"
    _require_unit_interval(margin, ars5, "employed margin", _context)
    # Both conditional rates must be validated too (fix round 1, item 1): the SrV aggregate's
    # own header documents NaN as a legitimate value when a conditioning class (e.g. nobody
    # employed in a tiny Kreis) is empty, so an unvalidated conditional would otherwise reach
    # a written share silently -- the education builder's _education_row already range-checks
    # its single share; this closes the corresponding gap on the work side.
    _require_unit_interval(p_work_employed, ars5, "p_work_employed", _context)
    _require_unit_interval(p_work_nonemployed, ars5, "p_work_nonemployed", _context)
    return {
        "ars5": ars5,
        "source": source,
        "n_effective": n_effective,
        "employed_work": round(margin * p_work_employed, 4),
        "employed_nowork": round(margin * (1.0 - p_work_employed), 4),
        "nonemployed_work": round((1.0 - margin) * p_work_nonemployed, 4),
        "nonemployed_nowork": round((1.0 - margin) * (1.0 - p_work_nonemployed), 4),
    }


def build_work_by_employment_target(data: Path) -> pd.DataFrame:
    """Build the work_by_employment target frame (ars5, source, n_effective,
    employed_work, employed_nowork, nonemployed_work, nonemployed_nowork) as MARGIN x
    CONDITIONAL (decision Q1): the employed margin per Kreis from the blended
    target2026_employment_status_by_kreis.csv (decision Q5 classes) times the SrV conditional
    rates p_work_employed / p_work_nonemployed from srv2023_work_by_employment_by_kreis.csv.
    Wolfsburg (03103) uses its OWN margin row combined with the SrV region-total (03ZGB)
    conditional rates; Gesamt uses the Gesamt margin row combined with the same 03ZGB
    conditional rates. Fails fast if a required column, Kreis row or region-total row is
    missing (no under-constrained control)."""
    emp_path = data / _EMPLOYMENT_STATUS_TARGET_RELPATH
    emp = read_employment_status_target(data)
    emp = emp.copy()
    emp["ars5"] = emp["ars5"].astype(str)
    # Fix round 1, item 8: a duplicate ars5 key would make emp_by_ars5.loc[ars5, ...] return a
    # DataFrame instead of a Series, and .sum() on that DataFrame silently collapses the
    # duplicate rows into one number -- checked once, up front, rather than relying on the
    # ambiguous-truth-value error that lookup would eventually raise.
    duplicate_ars5 = sorted(set(emp["ars5"][emp["ars5"].duplicated()]))
    if duplicate_ars5:
        raise ValueError(
            f"build_work_by_employment_target: employment_status target {emp_path} has "
            f"duplicate ars5 row(s) {duplicate_ars5}; the employed-margin lookup requires a "
            "unique key per Kreis.")
    emp_by_ars5 = emp.set_index("ars5")

    work = read_srv_work_by_employment(data)
    work = work.copy()
    work["code"] = work["code"].astype(str).str.zfill(5)

    kreis_rows = work[work["level"] == "kreis"]
    if kreis_rows.empty:
        raise ValueError(
            "build_work_by_employment_target: no Kreis rows (level == 'kreis') in the SrV "
            "work-by-employment source.")
    # Fix round 1, item 3: a delivery missing one of the 7 SrV-surveyed Kreise must not
    # silently ship a short target -- the two-argument .empty check above only catches the
    # all-Kreise-missing case.
    present_codes = set(kreis_rows["code"])
    missing_codes = sorted(_EXPECTED_SRV_KREIS_CODES - present_codes)
    if missing_codes:
        raise ValueError(
            f"build_work_by_employment_target: SrV work-by-employment source is missing kreis "
            f"row(s) for {missing_codes} (expected the 7 SrV-surveyed Kreise, "
            f"spatial.ZGB8 minus Wolfsburg {WOLFSBURG_ARS5!r}); present: {sorted(present_codes)}.")
    unexpected_codes = sorted(present_codes - _EXPECTED_SRV_KREIS_CODES)
    if unexpected_codes:
        raise ValueError(
            f"build_work_by_employment_target: SrV work-by-employment source has unexpected "
            f"kreis row code(s) {unexpected_codes}, not in the expected set "
            f"{sorted(_EXPECTED_SRV_KREIS_CODES)}.")
    total_rows = work[work["level"] == "total"]
    if len(total_rows) != 1:
        raise ValueError(
            "build_work_by_employment_target: expected exactly one region-total row "
            f"(level == 'total') in the SrV work-by-employment source, found {len(total_rows)}.")
    total_row = total_rows.iloc[0]
    # Fix round 1, item 5: every written header asserts the conditional rate came from 03ZGB;
    # a relabelled (or differently-coded) single total row must not be accepted silently.
    if str(total_row["code"]) != _REGION_TOTAL_CODE:
        raise ValueError(
            f"build_work_by_employment_target: expected the region-total row's code to be "
            f"{_REGION_TOTAL_CODE!r}, got {total_row['code']!r}.")

    def margin_for(ars5: str) -> float:
        if ars5 not in emp_by_ars5.index:
            raise ValueError(
                f"build_work_by_employment_target: employment_status target has no row for "
                f"Kreis {ars5!r}, needed as the employed margin for the work_by_employment cross.")
        row = emp_by_ars5.loc[ars5, list(EMPLOYED_EMPLOYMENT_STATUS_CLASSES)]
        # Fix round 1, item 2: pandas' .sum() defaults to skipna=True, so a NaN in one class
        # column would silently SHRINK the margin (e.g. a blank teilzeit contributes 0 instead
        # of failing) while the row can still sum to 1 downstream, defeating the sum guard. The
        # margin is this control's authoritative quantity, so a NaN class must raise, naming
        # the file, the Kreis and the column, rather than quietly disappear.
        nan_columns = [c for c in EMPLOYED_EMPLOYMENT_STATUS_CLASSES if pd.isna(row[c])]
        if nan_columns:
            raise ValueError(
                f"build_work_by_employment_target: employment_status target {emp_path} has "
                f"NaN in employed-class column(s) {nan_columns} for Kreis {ars5!r}; the "
                "employed margin cannot be computed without silently shrinking it.")
        return float(row.sum())

    rows = []
    for _, r in kreis_rows.iterrows():
        code = r["code"]
        rows.append(_work_by_employment_row(
            code, "employment_status_target x srv", int(r["n_unweighted"]), margin_for(code),
            float(r["p_work_employed"]), float(r["p_work_nonemployed"])))

    total_n = int(total_row["n_unweighted"])
    total_p_we = float(total_row["p_work_employed"])
    total_p_wn = float(total_row["p_work_nonemployed"])
    for ars5 in (WOLFSBURG_ARS5, GESAMT_ARS5):
        rows.append(_work_by_employment_row(
            ars5, "employment_status_target x srv_region_total", total_n, margin_for(ars5),
            total_p_we, total_p_wn))

    df = pd.DataFrame(rows, columns=["ars5", "source", "n_effective", *WORK_BY_EMPLOYMENT_CATEGORIES])
    _check_shares_sum_to_one(df, WORK_BY_EMPLOYMENT_CATEGORIES, "build_work_by_employment_target")
    return df


# --------------------------------------------------------------------------- education_by_age
def _education_row(ars5: str, source: str, n_effective: int, edu_share: float) -> dict:
    _require_unit_interval(edu_share, ars5, "p_education", "build_education_by_age_target")
    return {
        "ars5": ars5,
        "source": source,
        "n_effective": n_effective,
        "edu": round(edu_share, 4),
        "noedu": round(1.0 - edu_share, 4),
    }


def build_education_by_age_target(data: Path, entry_name: str) -> pd.DataFrame:
    """Build one education_by_age target frame (ars5, source, n_effective, edu, noedu) for the
    age band `entry_name` (one of kreis_attribute_control.EDUCATION_BY_AGE_ENTRY_NAMES). Shares
    are the SrV conditional education-participation rate directly (decision Q2: no margin, no
    MiD blending). Wolfsburg (03103) and Gesamt both take the 03ZGB region-total row's
    p_education directly (the build_participation_target.py convention). Fails fast if the
    band, a Kreis row or the region-total row is missing."""
    if entry_name not in EDUCATION_AGE_BOUNDS:
        raise ValueError(
            f"build_education_by_age_target: entry_name must be one of "
            f"{EDUCATION_BY_AGE_ENTRY_NAMES}, got {entry_name!r}.")

    edu = read_srv_education_by_age(data)
    edu = edu.copy()
    edu["code"] = edu["code"].astype(str).str.zfill(5)
    band = edu[edu["band"] == entry_name]
    if band.empty:
        raise ValueError(
            f"build_education_by_age_target: no rows for band {entry_name!r} in the SrV "
            "education-by-age source.")

    kreis_rows = band[band["level"] == "kreis"]
    if kreis_rows.empty:
        raise ValueError(
            f"build_education_by_age_target: no Kreis rows (level == 'kreis') for band "
            f"{entry_name!r} in the SrV education-by-age source.")
    # Fix round 1, item 3: a delivery missing one of the 7 SrV-surveyed Kreise for this band
    # must not silently ship a short target.
    present_codes = set(kreis_rows["code"])
    missing_codes = sorted(_EXPECTED_SRV_KREIS_CODES - present_codes)
    if missing_codes:
        raise ValueError(
            f"build_education_by_age_target: SrV education-by-age source is missing kreis "
            f"row(s) for band {entry_name!r}: {missing_codes} (expected the 7 SrV-surveyed "
            f"Kreise, spatial.ZGB8 minus Wolfsburg {WOLFSBURG_ARS5!r}); present: "
            f"{sorted(present_codes)}.")
    unexpected_codes = sorted(present_codes - _EXPECTED_SRV_KREIS_CODES)
    if unexpected_codes:
        raise ValueError(
            f"build_education_by_age_target: SrV education-by-age source has unexpected kreis "
            f"row code(s) {unexpected_codes} for band {entry_name!r}, not in the expected set "
            f"{sorted(_EXPECTED_SRV_KREIS_CODES)}.")
    total_rows = band[band["level"] == "total"]
    if len(total_rows) != 1:
        raise ValueError(
            f"build_education_by_age_target: expected exactly one region-total row "
            f"(level == 'total') for band {entry_name!r}, found {len(total_rows)}.")
    total_row = total_rows.iloc[0]
    # Fix round 1, item 5: every written header asserts the conditional rate came from 03ZGB.
    if str(total_row["code"]) != _REGION_TOTAL_CODE:
        raise ValueError(
            f"build_education_by_age_target: expected the region-total row's code to be "
            f"{_REGION_TOTAL_CODE!r} for band {entry_name!r}, got {total_row['code']!r}.")

    rows = [
        _education_row(r["code"], "srv", int(r["n_unweighted"]), float(r["p_education"]))
        for _, r in kreis_rows.iterrows()
    ]
    total_share = float(total_row["p_education"])
    total_n = int(total_row["n_unweighted"])
    rows.append(_education_row(WOLFSBURG_ARS5, "srv_region_total", total_n, total_share))
    rows.append(_education_row(GESAMT_ARS5, "srv", total_n, total_share))

    df = pd.DataFrame(rows, columns=["ars5", "source", "n_effective", *EDUCATION_FLAG_CATEGORIES])
    _check_shares_sum_to_one(df, EDUCATION_FLAG_CATEGORIES, f"build_education_by_age_target[{entry_name}]")
    return df


# --------------------------------------------------------------------------- headers + write
HEADER_WORK = """\
# work_by_employment per-Kreis control target (Plan B, issue #368, ADR-0109; decisions Q1 and
# Q5 in .superpowers/sdd/2026-09-07-participation-universe-controls-plan-b/SPEC.md).
# Built by scripts/build_participation_universe_targets.py from TWO committed inputs, NO raw
# microdata:
#   - eqasim-data/data/braunschweig/targets/target2026_employment_status_by_kreis.csv (the
#     employed MARGIN = vollzeit + teilzeit + geringfuegig + in_ausbildung, i.e.
#     attributes.EMPLOYED_EMPLOYMENT_STATUS_CLASSES; decision Q5 puts in_ausbildung IN the
#     employed set to match ADR-0060's MiD in_ausbildung / SrV V_ERW 8 apprentice equivalence).
#   - eqasim-data/data/braunschweig/srv/srv2023_work_by_employment_by_kreis.csv (the SrV
#     conditional rates p_work_employed = P(work trip | employed) and p_work_nonemployed =
#     P(work trip | not employed), per Kreis and for the 03ZGB region total).
#
# FORMULA (margin x conditional, decision Q1), per Kreis:
#   employed_work      = round(MARGIN * p_work_employed, 4)
#   employed_nowork     = round(MARGIN * (1 - p_work_employed), 4)
#   nonemployed_work    = round((1 - MARGIN) * p_work_nonemployed, 4)
#   nonemployed_nowork  = round((1 - MARGIN) * (1 - p_work_nonemployed), 4)
# The four cells partition to 1 exactly before rounding; independent 4-dp rounding of each cell
# can leave a row up to ~4e-4 off 1, inside kreis_attribute_control.TARGET_SHARE_TOLERANCE (1e-3).
#
# ASSUMPTION (controller ruling R15, universe mismatch -- documented, NOT reconciled): the
# employment_status margin's universe is persons 14+ with a valid V_ERW; the SrV conditional
# rates' universe is persons 14+ INCLUDING the persons with a missing/implausible V_ERW code
# (classed non-employed on the SrV side). The two universes differ by AT MOST
# n_missing_employment_code=11 persons of the 14,845-person 14+ universe (0.07 pp on the
# employed share) -- srv2023_work_by_employment_by_kreis.csv's own "# Exclusions:" header line
# measures that count on the 17,269-person at-home-or-mobile universe, not on the 14,845-person
# 14+ subset, so 11 is an upper bound on how many of them are 14+ (fix round 1, item 7). The two
# universes are combined as-is; this is a small, explicitly documented gap, not a silently
# absorbed one.
#
# ASSUMPTION (Wolfsburg 03103, SrV region-total convention -- same as target2026_has_ebike /
# target2026_work_participation): 03103 is not covered by SrV, so its row uses ITS OWN
# employment_status margin row combined with the 03ZGB (region-total) conditional rates;
# source = "employment_status_target x srv_region_total".
# ASSUMPTION (Gesamt): the Gesamt row uses the Gesamt margin row combined with the SAME 03ZGB
# conditional rates as Wolfsburg (source = "employment_status_target x srv_region_total"), since
# 03ZGB is the only committed region-total conditional rate available.
#
# NOTE (n_effective, fix round 1 item 6): n_effective is the SrV CONDITIONAL row's n_unweighted
# (e.g. 03101: 3629), not the employment_status margin row's own n_effective (03101: 1902).
# n_effective exists to size kreis_attribute_control._shrunk_shares' Dirichlet shrinkage prior;
# while this control is consumed with prior_n = 0 (see CONSUMER NOTE below) the choice is INERT
# and changes no shipped value. If a future prior_n > 0 is ever set for this control, the SrV
# row's roughly 2x larger unweighted count would shrink with roughly twice the confidence the
# margin row alone would justify -- stated here so that trade-off is visible before prior_n is
# ever moved off 0, not discovered afterwards.
#
# CONSUMER NOTE: FINAL target - use with kreis_attribute_control prior_n = 0 (this control is
# registered tier="hard").
# Columns: ars5, source, n_effective (the SrV row's n_unweighted), employed_work, employed_nowork,
# nonemployed_work, nonemployed_nowork (kreis_attribute_control.WORK_BY_EMPLOYMENT_CATEGORIES).
"""


def _education_header(entry_name: str) -> str:
    min_age, max_age = EDUCATION_AGE_BOUNDS[entry_name]
    age_range = f"{min_age}-{max_age}" if max_age is not None else f"{min_age}+"
    return f"""\
# education_by_age per-Kreis control target for the '{entry_name}' age band ({age_range}),
# Plan B issue #368, ADR-0109, decision Q2 (three 2-cell age-range controls incl. 0-5).
# Built by scripts/build_participation_universe_targets.py from ONE committed input, NO raw
# microdata, NO MiD blending and NO margin (unlike work_by_employment, decision Q2's shares are
# a direct SrV conditional rate):
#   eqasim-data/data/braunschweig/srv/srv2023_education_by_age_by_kreis.csv, band == '{entry_name}'
#   (p_education = weighted share of the age band's persons with >= 1 education-purpose leg on
#   the reporting day, E_ZWECK_9 in {{3, 4}}).
#
# FORMULA: edu = round(p_education, 4); noedu = round(1 - p_education, 4).
#
# ASSUMPTION (Wolfsburg 03103, SrV region-total convention -- same as target2026_has_ebike /
# target2026_work_participation): 03103 is not covered by SrV; its row uses the 03ZGB
# (region-total) p_education directly (source = "srv_region_total").
# ASSUMPTION (Gesamt): the Gesamt row also uses the 03ZGB p_education directly (source = "srv"),
# the same convention as scripts/build_participation_target.py.
#
# CONSUMER NOTE: FINAL target - use with kreis_attribute_control prior_n = 0 (this control is
# registered tier="hard"). Age universe (KreisAttributeControl.min_age / max_age) must match
# kreis_attribute_control.EDUCATION_AGE_BOUNDS['{entry_name}'] = ({min_age}, {max_age}).
# Columns: ars5, source, n_effective (the SrV row's n_unweighted), edu, noedu
# (kreis_attribute_control.EDUCATION_FLAG_CATEGORIES).
"""


def write_target(df: pd.DataFrame, out_path: Path, header: str) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        f.write(header)
        df.to_csv(f, index=False, lineterminator="\n", float_format="%.10g")
    log.info("wrote %s (%d rows; sources: %s)", out_path, len(df), df["source"].value_counts().to_dict())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DATA_DEFAULT)
    parser.add_argument("--out-dir", type=Path, default=DATA_DEFAULT / "targets")
    args = parser.parse_args(argv)

    work = build_work_by_employment_target(args.data)
    write_target(work, args.out_dir / "target2026_work_by_employment_by_kreis.csv", HEADER_WORK)

    for entry_name in EDUCATION_BY_AGE_ENTRY_NAMES:
        education = build_education_by_age_target(args.data, entry_name)
        write_target(
            education, args.out_dir / f"target2026_{entry_name}_by_kreis.csv",
            _education_header(entry_name))

    return 0


if __name__ == "__main__":
    sys.exit(main())
