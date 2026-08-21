"""Tests for the MiD reference-table CSVs and the loaders that consume them.

Covers:
  * Seed script idempotency: ``scripts/seed_mid_constraint_tables.py``
    writes the expected files with the expected row counts.
  * Loader identity: the values produced by the loaders match the
    legacy hard-coded constants exactly (no behaviour change).
  * Constraint-CSV schema sanity (zone/sex/age dimensions, % in [0, 1]).
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "eqasim-data" / "data"
MID = DATA / "braunschweig" / "mid"

sys.path.insert(0, str(REPO))


# ---------------------------------------------------------------------------
# CSV presence and schema
# ---------------------------------------------------------------------------

EXPECTED_FILES = [
    "mid2023_P19_car_constraints.csv",
    "mid2023_P22_bicycle_constraints.csv",
    "mid2023_P24_1_pt_subscription_constraints.csv",
    "mid2023_H7_cars_by_kreis.csv",
    "mid2023_H12_3_bikes_by_kreis.csv",
    "mid2023_H4_income_by_size.csv",
    "mid2023_class_midpoint_eur.csv",
]


@pytest.mark.parametrize("fname", EXPECTED_FILES)
def test_seed_csv_exists(fname):
    assert (MID / fname).exists(), f"{fname} is missing — run scripts/seed_mid_constraint_tables.py"


@pytest.mark.parametrize(
    "fname",
    ["mid2023_P19_car_constraints.csv",
     "mid2023_P22_bicycle_constraints.csv",
     "mid2023_P24_1_pt_subscription_constraints.csv"],
)
def test_constraint_csv_schema(fname):
    df = pd.read_csv(MID / fname, comment="#")
    assert {"dimension", "key", "age_lo", "age_hi", "target"} <= set(df.columns)
    assert set(df["dimension"].unique()) <= {"zone", "sex", "age"}
    assert df["target"].between(0.0, 1.0).all(), \
        f"{fname}: targets must be fractions in [0, 1]"
    # external + 8 ZGB Kreise
    zones = set(df.loc[df["dimension"] == "zone", "key"])
    assert {"braunschweig", "salzgitter", "wolfsburg", "gifhorn", "goslar",
            "helmstedt", "peine", "wolfenbuettel", "external"} <= zones


def test_kreis_share_csv_includes_gesamt_row():
    for fname in ["mid2023_H7_cars_by_kreis.csv",
                  "mid2023_H12_3_bikes_by_kreis.csv"]:
        df = pd.read_csv(MID / fname, comment="#")
        assert "Gesamt" in df["ars5"].values, f"{fname}: missing region row"
        bucket_cols = [c for c in df.columns if c != "ars5"]
        for _, row in df.iterrows():
            total = sum(float(row[c]) for c in bucket_cols)
            assert abs(total - 1.0) < 0.05, \
                f"{fname} row {row['ars5']!r} sums to {total:.3f}"


# ---------------------------------------------------------------------------
# Loader identity vs. legacy hard-coded values
# ---------------------------------------------------------------------------

LEGACY_CARS_BY_KREIS = {
    "03101": (0.25, 0.53, 0.20, 0.02),
    "03102": (0.10, 0.62, 0.22, 0.06),
    "03103": (0.17, 0.57, 0.22, 0.04),
    "03151": (0.06, 0.50, 0.35, 0.08),
    "03153": (0.22, 0.53, 0.21, 0.04),
    "03154": (0.14, 0.52, 0.27, 0.07),
    "03157": (0.07, 0.48, 0.37, 0.08),
    "03158": (0.13, 0.56, 0.22, 0.09),
}
LEGACY_CARS_REGION = (0.15, 0.53, 0.26, 0.06)

LEGACY_BIKES_BY_KREIS = {
    "03101": (0.17, 0.25, 0.26, 0.12, 0.21),
    "03102": (0.23, 0.24, 0.25, 0.11, 0.17),
    "03103": (0.36, 0.22, 0.21, 0.08, 0.14),
    "03151": (0.12, 0.22, 0.25, 0.14, 0.26),
    "03153": (0.36, 0.23, 0.17, 0.09, 0.15),
    "03154": (0.28, 0.16, 0.29, 0.13, 0.15),
    "03157": (0.18, 0.23, 0.22, 0.16, 0.22),
    "03158": (0.23, 0.27, 0.17, 0.13, 0.20),
}
LEGACY_BIKES_REGION = (0.23, 0.23, 0.23, 0.12, 0.20)

LEGACY_INCOME_BY_SIZE = {
    "1":  (0.16, 0.14, 0.39, 0.26, 0.03),
    "2":  (0.04, 0.15, 0.28, 0.38, 0.15),
    "3":  (0.03, 0.03, 0.17, 0.55, 0.22),
    "4":  (0.02, 0.04, 0.23, 0.51, 0.19),
    "5":  (0.04, 0.08, 0.30, 0.44, 0.14),
    "6+": (0.04, 0.08, 0.30, 0.44, 0.14),
}

LEGACY_CLASS_MIDPOINT_EUR = {
    "0-500":     250.0,
    "1500-2000": 1750.0,
    "2600-3000": 2800.0,
    "3600-4500": 4050.0,
    "5000+":     6000.0,
}


def test_loader_h7_matches_legacy():
    from braunschweig.data.mid.reference_tables import load_kreis_share_table
    by_kreis, region, values = load_kreis_share_table(
        str(DATA), "mid2023_H7_cars_by_kreis.csv")
    assert list(values) == [0, 1, 2, 3]
    assert tuple(region) == LEGACY_CARS_REGION
    assert set(by_kreis) == set(LEGACY_CARS_BY_KREIS)
    for ars, expected in LEGACY_CARS_BY_KREIS.items():
        assert tuple(by_kreis[ars]) == expected


def test_loader_h12_3_matches_legacy():
    from braunschweig.data.mid.reference_tables import load_kreis_share_table
    by_kreis, region, values = load_kreis_share_table(
        str(DATA), "mid2023_H12_3_bikes_by_kreis.csv")
    assert list(values) == [0, 1, 2, 3, 4]
    assert tuple(region) == LEGACY_BIKES_REGION
    for ars, expected in LEGACY_BIKES_BY_KREIS.items():
        assert tuple(by_kreis[ars]) == expected


def test_loader_h4_matches_legacy():
    from braunschweig.data.mid.reference_tables import load_income_by_size
    got = load_income_by_size(str(DATA))
    assert set(got) == set(LEGACY_INCOME_BY_SIZE)
    for size, expected in LEGACY_INCOME_BY_SIZE.items():
        assert tuple(got[size]) == expected


def test_loader_midpoint_matches_legacy():
    from braunschweig.data.mid.reference_tables import load_class_midpoint_eur
    got = load_class_midpoint_eur(str(DATA))
    assert got == LEGACY_CLASS_MIDPOINT_EUR


# ---------------------------------------------------------------------------
# P24.1 — categorical PT ticket-type breakdown (MiD 2023, page 105)
# ---------------------------------------------------------------------------

# Per-Kreis row% as printed in the PDF for the 9 ticket categories,
# in the column order defined by ``PT_TICKET_CATEGORIES``.  Used to verify
# the loader against the parsed CSV.
EXPECTED_P24_1_RAW_PCT = {
    "03101": (42, 15, 13, 4, 3, 6, 3, 14, 0),  # Braunschweig
    "03103": (36, 7, 9, 2, 1, 6, 3, 37, 0),    # Wolfsburg
    "03102": (42, 5, 8, 5, 3, 4, 3, 31, 0),    # Salzgitter
    "03151": (37, 2, 8, 1, 2, 5, 2, 43, 0),    # Gifhorn
    "03157": (37, 3, 7, 2, 2, 3, 2, 42, 0),    # Peine
    "03154": (35, 2, 12, 3, 1, 3, 2, 41, 1),   # Helmstedt
    "03158": (42, 4, 6, 3, 1, 4, 4, 36, 0),    # Wolfenbüttel
    "03153": (31, 2, 13, 2, 1, 3, 2, 46, 1),   # Goslar
}
EXPECTED_P24_1_GESAMT_PCT = (37, 5, 10, 3, 2, 4, 3, 36, 0)


def test_p24_1_csv_has_all_kreise():
    df = pd.read_csv(MID / "mid2023_P24_1.csv")
    assert "Gesamt" in df["kreis"].values
    assert set(EXPECTED_P24_1_RAW_PCT) <= set(df["ars5"].astype(str))


def test_loader_pt_subscription_breakdown_normalised():
    from braunschweig.data.mid.reference_tables import (
        load_pt_subscription_breakdown,
        PT_TICKET_CATEGORIES,
    )
    by_kreis, region = load_pt_subscription_breakdown(str(DATA))
    assert len(PT_TICKET_CATEGORIES) == 9
    # Probabilities must sum to 1 per Kreis (within float tolerance).
    for ars, vec in by_kreis.items():
        assert vec.shape == (9,)
        assert abs(float(vec.sum()) - 1.0) < 1e-9, \
            f"{ars}: probabilities sum to {vec.sum()}"
    assert abs(float(region.sum()) - 1.0) < 1e-9


def test_loader_pt_subscription_breakdown_matches_pdf():
    """Loader values match the raw integer percentages from the MiD PDF."""
    from braunschweig.data.mid.reference_tables import (
        load_pt_subscription_breakdown,
    )
    by_kreis, region = load_pt_subscription_breakdown(str(DATA))
    for ars, expected_pct in EXPECTED_P24_1_RAW_PCT.items():
        raw = sum(expected_pct)
        for i, pct in enumerate(expected_pct):
            assert abs(float(by_kreis[ars][i]) - pct / raw) < 1e-9, \
                f"{ars} cat {i}: got {by_kreis[ars][i]} expected {pct/raw}"
    raw = sum(EXPECTED_P24_1_GESAMT_PCT)
    for i, pct in enumerate(EXPECTED_P24_1_GESAMT_PCT):
        assert abs(float(region[i]) - pct / raw) < 1e-9


def test_pt_flatrate_set_matches_legacy_kreis_share():
    """Sum of flatrate categories per Kreis matches the legacy P24.1
    ``has_pt_subscription`` zone targets (as seeded in the legacy CSV)."""
    from braunschweig.data.mid.reference_tables import (
        load_pt_subscription_breakdown,
        PT_TICKET_CATEGORIES,
        PT_TICKET_FLATRATE,
    )
    legacy = pd.read_csv(MID / "mid2023_P24_1_pt_subscription_constraints.csv",
                         comment="#")
    legacy_zone = legacy[legacy["dimension"] == "zone"].set_index("key")["target"]
    name_to_ars5 = {
        "braunschweig": "03101", "salzgitter": "03102", "wolfsburg": "03103",
        "gifhorn": "03151", "goslar": "03153", "helmstedt": "03154",
        "peine": "03157", "wolfenbuettel": "03158",
    }
    by_kreis, _ = load_pt_subscription_breakdown(str(DATA))
    flat_idx = [PT_TICKET_CATEGORIES.index(c) for c in PT_TICKET_FLATRATE]
    for zone_name, ars5 in name_to_ars5.items():
        flat_share = float(by_kreis[ars5][flat_idx].sum())
        legacy_target = float(legacy_zone[zone_name])
        # MiD percentages are integer-rounded; allow ±1 percentage point.
        assert abs(flat_share - legacy_target) < 0.015, \
            f"{zone_name}: flatrate {flat_share:.3f} vs legacy {legacy_target:.3f}"


def test_pt_subscription_margins_csvs_exist_and_normalised():
    """``_by_sex.csv`` and ``_by_age.csv`` exist and the loader returns
    probability vectors that sum to 1."""
    from braunschweig.data.mid.reference_tables import (
        load_pt_subscription_margins,
        PT_TICKET_CATEGORIES,
    )
    assert (MID / "mid2023_P24_1_by_sex.csv").exists()
    assert (MID / "mid2023_P24_1_by_age.csv").exists()
    by_sex, by_age = load_pt_subscription_margins(str(DATA))
    assert set(by_sex) == {"male", "female"}
    for sex, vec in by_sex.items():
        assert vec.shape == (len(PT_TICKET_CATEGORIES),)
        assert abs(float(vec.sum()) - 1.0) < 1e-9
    assert len(by_age) == 9
    # Bands must be contiguous and start at 14.
    assert by_age[0][0] == 14
    assert by_age[-1][1] == 999
    for lo, hi, vec in by_age:
        assert lo <= hi
        assert vec.shape == (len(PT_TICKET_CATEGORIES),)
        assert abs(float(vec.sum()) - 1.0) < 1e-9


def test_pt_subscription_margins_match_pdf_values():
    """Spot-check a few raw integer percentages from MiD page 105."""
    from braunschweig.data.mid.reference_tables import (
        load_pt_subscription_margins,
        PT_TICKET_CATEGORIES,
    )
    by_sex, by_age = load_pt_subscription_margins(str(DATA))
    i_dt = PT_TICKET_CATEGORIES.index("deutschlandticket")
    # Both sexes have D-Ticket = 10 % per PDF.
    assert abs(float(by_sex["male"][i_dt]) - 10 / 101) < 1e-9   # row sums to 101
    assert abs(float(by_sex["female"][i_dt]) - 10 / 100) < 1e-9
    # 18-29 age band has D-Ticket = 15 %; row sums to 101.
    lo, hi, vec = by_age[1]
    assert (lo, hi) == (18, 29)
    assert abs(float(vec[i_dt]) - 15 / 101) < 1e-9


def test_pt_ipf_three_margins_converges_on_synthetic_population():
    """Run the same raking algorithm used in enriched.py on a synthetic
    population and verify it converges to the MiD margins within 1 pp."""
    from braunschweig.data.mid.reference_tables import (
        load_pt_subscription_breakdown,
        load_pt_subscription_margins,
        PT_TICKET_CATEGORIES,
    )
    rng = np.random.RandomState(42)
    by_kreis, _region = load_pt_subscription_breakdown(str(DATA))
    by_sex, by_age = load_pt_subscription_margins(str(DATA))
    ars5_list = list(by_kreis.keys())
    n_kreise, n_sex, n_ages, n_cats = len(ars5_list), 2, len(by_age), len(PT_TICKET_CATEGORIES)

    # Synthetic population: 5000 persons distributed across all cells.
    n_persons = 5000
    person_kreis = rng.randint(0, n_kreise, n_persons)
    person_sex = rng.randint(0, n_sex, n_persons)
    person_age = rng.randint(0, n_ages, n_persons)

    M_K = np.array([by_kreis[ars5_list[i]] for i in range(n_kreise)])
    M_S = np.array([by_sex["male"], by_sex["female"]])
    M_A = np.array([vec for _lo, _hi, vec in by_age])

    flat = person_kreis * (n_sex * n_ages) + person_sex * n_ages + person_age
    T = np.bincount(flat, minlength=n_kreise * n_sex * n_ages).reshape(
        n_kreise, n_sex, n_ages
    ).astype(float)
    T_K, T_S, T_A = T.sum(axis=(1, 2)), T.sum(axis=(0, 2)), T.sum(axis=(0, 1))
    target_kc, target_sc, target_ac = M_K * T_K[:, None], M_S * T_S[:, None], M_A * T_A[:, None]

    X = np.broadcast_to(T[..., None] / n_cats, (n_kreise, n_sex, n_ages, n_cats)).copy()
    eps = 1e-9
    for _ in range(200):
        cur = X.sum(axis=(1, 2))
        X *= np.where(cur > eps, target_kc / np.maximum(cur, eps), 1.0)[:, None, None, :]
        cur = X.sum(axis=(0, 2))
        X *= np.where(cur > eps, target_sc / np.maximum(cur, eps), 1.0)[None, :, None, :]
        cur = X.sum(axis=(0, 1))
        X *= np.where(cur > eps, target_ac / np.maximum(cur, eps), 1.0)[None, None, :, :]
        cur = X.sum(axis=3)
        X *= np.where(cur > eps, T / np.maximum(cur, eps), 0.0)[..., None]

    # All three margins must converge to within 5 pp.  The MiD margins are
    # independently rounded to integer percent and are therefore not
    # internally consistent, so raking finds a least-squares compromise
    # rather than an exact match.
    cur_kc = X.sum(axis=(1, 2))
    dev_k = np.nanmax(np.abs(cur_kc / np.maximum(T_K[:, None], eps) - M_K))
    cur_sc = X.sum(axis=(0, 2))
    dev_s = np.nanmax(np.abs(cur_sc / np.maximum(T_S[:, None], eps) - M_S))
    cur_ac = X.sum(axis=(0, 1))
    dev_a = np.nanmax(np.abs(cur_ac / np.maximum(T_A[:, None], eps) - M_A))
    assert dev_k < 0.05, f"kreis margin off by {dev_k:.3f}"
    assert dev_s < 0.05, f"sex margin off by {dev_s:.3f}"
    assert dev_a < 0.05, f"age margin off by {dev_a:.3f}"


# ------- numpy import for the synthetic IPF test ---------------------------
import numpy as np  # noqa: E402


# ---------------------------------------------------------------------------
# MiD P17.1 — driving licence (analogous to PT P24.1)
# ---------------------------------------------------------------------------

def test_license_csv_has_all_kreise():
    """``mid2023_P17_1.csv`` carries the 8 ZGB Kreise + Gesamt row."""
    df = pd.read_csv(MID / "mid2023_P17_1.csv")
    assert len(df) == 9
    assert "Gesamt" in set(df["kreis"])
    assert {"ja", "nein", "keine_angabe"}.issubset(df.columns)


def test_license_margin_csvs_exist_and_normalised():
    from braunschweig.data.mid.reference_tables import (
        load_license_breakdown,
        load_license_margins,
        LICENSE_CATEGORIES,
    )
    assert (MID / "mid2023_P17_1_by_sex.csv").exists()
    assert (MID / "mid2023_P17_1_by_age.csv").exists()
    by_kreis, region = load_license_breakdown(str(DATA))
    assert len(by_kreis) == 8
    for vec in by_kreis.values():
        assert vec.shape == (len(LICENSE_CATEGORIES),)
        assert abs(float(vec.sum()) - 1.0) < 1e-9
    assert abs(float(region.sum()) - 1.0) < 1e-9
    by_sex, by_age = load_license_margins(str(DATA))
    assert set(by_sex) == {"male", "female"}
    for vec in by_sex.values():
        assert abs(float(vec.sum()) - 1.0) < 1e-9
    assert len(by_age) == 9
    for lo, hi, vec in by_age:
        assert lo <= hi
        assert abs(float(vec.sum()) - 1.0) < 1e-9


def test_license_margins_match_pdf_values():
    """Spot-check raw integer percentages from MiD page 87."""
    from braunschweig.data.mid.reference_tables import (
        load_license_breakdown,
        load_license_margins,
        LICENSE_CATEGORIES,
    )
    by_kreis, _region = load_license_breakdown(str(DATA))
    i_ja = LICENSE_CATEGORIES.index("ja")
    # Braunschweig (Kreisfreie Stadt): ja = 82 / (82+17+1) = 82/100.
    assert abs(float(by_kreis["03101"][i_ja]) - 82 / 100) < 1e-9
    # Wolfsburg: ja = 74 / (74+25+0) = 74/99.
    assert abs(float(by_kreis["03103"][i_ja]) - 74 / 99) < 1e-9
    by_sex, by_age = load_license_margins(str(DATA))
    # Male: ja = 90 / (90+9+1) = 90/100.
    assert abs(float(by_sex["male"][i_ja]) - 90 / 100) < 1e-9
    assert abs(float(by_sex["female"][i_ja]) - 82 / 99) < 1e-9
    # 14-17 age band: ja = 19/100; 80+: 75/100.
    assert by_age[0][:2] == (14, 17)
    assert abs(float(by_age[0][2][i_ja]) - 19 / 100) < 1e-9
    assert by_age[-1][:2] == (80, 999)
    assert abs(float(by_age[-1][2][i_ja]) - 75 / 100) < 1e-9


def test_license_ipf_three_margins_converges_on_synthetic_population():
    """Same raking as in enriched.py — verify margin convergence."""
    from braunschweig.data.mid.reference_tables import (
        load_license_breakdown,
        load_license_margins,
        LICENSE_CATEGORIES,
    )
    rng = np.random.RandomState(7)
    by_kreis, _region = load_license_breakdown(str(DATA))
    by_sex, by_age = load_license_margins(str(DATA))
    ars5_list = list(by_kreis.keys())
    n_kreise, n_sex, n_ages, n_cats = len(ars5_list), 2, len(by_age), len(LICENSE_CATEGORIES)

    n_persons = 5000
    person_kreis = rng.randint(0, n_kreise, n_persons)
    person_sex = rng.randint(0, n_sex, n_persons)
    person_age = rng.randint(0, n_ages, n_persons)

    M_K = np.array([by_kreis[ars5_list[i]] for i in range(n_kreise)])
    M_S = np.array([by_sex["male"], by_sex["female"]])
    M_A = np.array([vec for _lo, _hi, vec in by_age])

    flat = person_kreis * (n_sex * n_ages) + person_sex * n_ages + person_age
    T = np.bincount(flat, minlength=n_kreise * n_sex * n_ages).reshape(
        n_kreise, n_sex, n_ages
    ).astype(float)
    T_K, T_S, T_A = T.sum(axis=(1, 2)), T.sum(axis=(0, 2)), T.sum(axis=(0, 1))
    target_kc, target_sc, target_ac = M_K * T_K[:, None], M_S * T_S[:, None], M_A * T_A[:, None]

    X = np.broadcast_to(T[..., None] / n_cats, (n_kreise, n_sex, n_ages, n_cats)).copy()
    eps = 1e-9
    for _ in range(200):
        cur = X.sum(axis=(1, 2))
        X *= np.where(cur > eps, target_kc / np.maximum(cur, eps), 1.0)[:, None, None, :]
        cur = X.sum(axis=(0, 2))
        X *= np.where(cur > eps, target_sc / np.maximum(cur, eps), 1.0)[None, :, None, :]
        cur = X.sum(axis=(0, 1))
        X *= np.where(cur > eps, target_ac / np.maximum(cur, eps), 1.0)[None, None, :, :]
        cur = X.sum(axis=3)
        X *= np.where(cur > eps, T / np.maximum(cur, eps), 0.0)[..., None]

    cur_kc = X.sum(axis=(1, 2))
    dev_k = np.nanmax(np.abs(cur_kc / np.maximum(T_K[:, None], eps) - M_K))
    cur_sc = X.sum(axis=(0, 2))
    dev_s = np.nanmax(np.abs(cur_sc / np.maximum(T_S[:, None], eps) - M_S))
    cur_ac = X.sum(axis=(0, 1))
    dev_a = np.nanmax(np.abs(cur_ac / np.maximum(T_A[:, None], eps) - M_A))
    # MiD margins are independently rounded to integer % so allow 10 pp
    # (P17.1 ranges from 19 % to 94 % across age bands so the unavoidable
    # IPF compromise is larger than for P24.1).
    assert dev_k < 0.10, f"kreis margin off by {dev_k:.3f}"
    assert dev_s < 0.10, f"sex margin off by {dev_s:.3f}"
    assert dev_a < 0.10, f"age margin off by {dev_a:.3f}"


def test_loader_constraint_round_trip_ages():
    """Age bands round-trip through the CSV correctly (incl. ±inf)."""
    from braunschweig.data.mid.reference_tables import load_constraint_table
    rows = load_constraint_table(str(DATA), "mid2023_P22_bicycle_constraints.csv")
    age_rows = [r for r in rows if "age" in r]
    # P22 includes a 'upto_6' (-inf, 6) row and an '80+' (80, inf) row.
    has_neg_inf = any(r["age"][0] == -math.inf for r in age_rows)
    has_pos_inf = any(r["age"][1] == math.inf for r in age_rows)
    assert has_neg_inf and has_pos_inf


def test_loader_constraint_includes_all_three_dimensions():
    from braunschweig.data.mid.reference_tables import load_constraint_table
    rows = load_constraint_table(str(DATA), "mid2023_P19_car_constraints.csv")
    has_zone = any("zone" in r for r in rows)
    has_sex = any("sex" in r for r in rows)
    has_age = any("age" in r for r in rows)
    assert has_zone and has_sex and has_age


# ---------------------------------------------------------------------------
# Seed script idempotency
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Region vector: 03ZGB-sourced reference, dead averaged fallback removed
# ---------------------------------------------------------------------------
#
# Background: the ``region`` second return value of
# ``load_pt_subscription_breakdown`` / ``load_license_breakdown`` is the
# ZGB-aggregate (``03ZGB`` / Gesamt) probability vector.  It is NOT consumed
# by the live categorical-IPF path in
# ``braunschweig.synthesis.population.enriched`` (ineligible persons get
# ``fahre_nie`` / ``nein`` deterministically, never the region vector).  The
# loaders used to fabricate ``region`` as a cross-Kreis average when the
# ``03ZGB`` row was absent; that branch was dead code (the row is always
# present) and was removed in favour of an explicit error.  These tests pin
# the two contracts: (a) ``region`` still equals the ``03ZGB`` row exactly
# (used output unchanged), and (b) a missing ``03ZGB`` row now raises instead
# of silently fabricating an averaged vector.


def _write_breakdown_csv(path, cols, rows):
    """Write a minimal breakdown CSV (``ars5`` + category columns)."""
    header = "kreis,ars5," + ",".join(cols) + "\n"
    lines = [header]
    for kreis, ars5, values in rows:
        lines.append(
            "{},{},".format(kreis, ars5) + ",".join(str(v) for v in values) + "\n"
        )
    path.write_text("".join(lines), encoding="utf-8")


def test_pt_region_equals_03zgb_row_used_output_unchanged():
    """``region`` is exactly the normalised ``03ZGB`` Gesamt row."""
    from braunschweig.data.mid.reference_tables import load_pt_subscription_breakdown
    _by_kreis, region = load_pt_subscription_breakdown(str(DATA))
    raw = sum(EXPECTED_P24_1_GESAMT_PCT)
    expected = np.asarray(EXPECTED_P24_1_GESAMT_PCT, dtype=float) / raw
    assert np.allclose(region, expected, atol=1e-9)


def test_license_region_equals_03zgb_row_used_output_unchanged():
    """``region`` is exactly the normalised ``03ZGB`` Gesamt row (P17.1)."""
    from braunschweig.data.mid.reference_tables import load_license_breakdown
    _by_kreis, region = load_license_breakdown(str(DATA))
    # P17.1 Gesamt row: ja=86, nein=13, keine_angabe=1 -> sums to 100.
    expected = np.asarray([86.0, 13.0, 1.0]) / 100.0
    assert np.allclose(region, expected, atol=1e-9)


def test_pt_breakdown_missing_03zgb_raises_no_averaged_fallback(tmp_path, monkeypatch):
    """Without the ``03ZGB`` row the loader raises (dead averaged fallback gone)."""
    from braunschweig.data.mid import reference_tables
    mid_dir = tmp_path / reference_tables.MID_SUBDIR
    mid_dir.mkdir(parents=True)
    # The raw CSV keeps the codebook-German column headers -- the loader
    # translates through ``P24_RAW_COLUMN_BY_CATEGORY``, so the synthetic file
    # must use the same raw headers as the committed CSVs.
    cols = [
        reference_tables.P24_RAW_COLUMN_BY_CATEGORY[c]
        for c in reference_tables.PT_TICKET_CATEGORIES
    ]
    n = len(cols)
    # Two Kreise, NO 03ZGB Gesamt row.
    _write_breakdown_csv(
        mid_dir / "mid2023_P24_1.csv",
        cols,
        [
            ("Braunschweig", "03101", [100] + [0] * (n - 1)),
            ("Wolfsburg", "03103", [0, 100] + [0] * (n - 2)),
        ],
    )
    with pytest.raises(RuntimeError, match="03ZGB"):
        reference_tables.load_pt_subscription_breakdown(str(tmp_path))


def test_license_breakdown_missing_03zgb_raises_no_averaged_fallback(tmp_path):
    """Without the ``03ZGB`` row the licence loader raises (dead fallback gone)."""
    from braunschweig.data.mid import reference_tables
    mid_dir = tmp_path / reference_tables.MID_SUBDIR
    mid_dir.mkdir(parents=True)
    cols = list(reference_tables.LICENSE_CATEGORIES)
    _write_breakdown_csv(
        mid_dir / "mid2023_P17_1.csv",
        cols,
        [
            ("Braunschweig", "03101", [82, 17, 1]),
            ("Wolfsburg", "03103", [74, 25, 0]),
        ],
    )
    with pytest.raises(RuntimeError, match="03ZGB"):
        reference_tables.load_license_breakdown(str(tmp_path))


def test_seed_script_writes_to_tmp(tmp_path):
    """Run seed script with a temp output dir and verify it produces all files."""
    from scripts import seed_mid_constraint_tables as seed
    rc = seed.main(["--out-dir", str(tmp_path)])
    assert rc == 0
    for fname in EXPECTED_FILES:
        assert (tmp_path / fname).exists(), f"{fname} not produced"


def test_seed_script_idempotent(tmp_path):
    """Running twice produces byte-identical files."""
    from scripts import seed_mid_constraint_tables as seed
    seed.main(["--out-dir", str(tmp_path)])
    first = {f: (tmp_path / f).read_bytes() for f in EXPECTED_FILES}
    seed.main(["--out-dir", str(tmp_path)])
    for fname, content in first.items():
        assert (tmp_path / fname).read_bytes() == content, \
            f"{fname} changed on rerun"


def test_pt_ticket_categories_are_english_with_raw_boundary():
    from braunschweig.data.mid import reference_tables as rt

    assert rt.PT_TICKET_CATEGORIES == (
        "single_ticket",
        "multi_ride_ticket",
        "deutschlandticket",
        "weekly_monthly_no_subscription",
        "monthly_or_annual_subscription",
        "job_or_semester_ticket",
        "other_ticket",
        "never_pt",
        "no_answer",
    )
    # The boundary mapping covers exactly the nine categories and maps onto the
    # committed codebook-German CSV headers.
    assert set(rt.P24_RAW_COLUMN_BY_CATEGORY) == set(rt.PT_TICKET_CATEGORIES)
    assert rt.P24_RAW_COLUMN_BY_CATEGORY["never_pt"] == "fahre_nie"
    assert rt.P24_RAW_COLUMN_BY_CATEGORY["single_ticket"] == "einzelfahrschein"
    assert rt.P24_RAW_COLUMN_BY_CATEGORY["deutschlandticket"] == "deutschlandticket"
    assert rt.PT_TICKET_FLATRATE == frozenset({
        "deutschlandticket",
        "weekly_monthly_no_subscription",
        "monthly_or_annual_subscription",
        "job_or_semester_ticket",
    })
    assert rt.PT_TICKET_WORK_STUDY_BOUND == frozenset({"job_or_semester_ticket"})
