"""Tests for optimization step (2): the trip-coherence check in
population_validation.

This is the closed-loop measurement that makes step (1) (richer HTS matching
keys) evaluable: it compares the donor-derived activity chains of the synthetic
population against real MiD 2023 targets, segmented by the same exogenous anchors
used for matching (employed / urban_class / household_size).

What is measurable at the SYNTHESIS output stage (mode_choice off, no MATSim run):
- trip-purpose distribution           vs MiD W1 (Wege je Zweck per Kreis)
- mobility rate (share of persons      vs MiD P36_1 (mobil / nicht mobil)
  with >= 1 trip)
The realised MODAL split is NOT available here (the synthesis trips.csv carries
no mode; it is written only by the MATSim mode-choice run), so it is intentionally
out of scope -- and donor modes would be French-biased anyway, which is exactly
what the planned MiD-donor replacement (step 3) addresses.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.analysis.population_validation import trip_coherence as tc  # noqa: E402
from braunschweig.analysis.population_validation.trip_coherence import (  # noqa: E402
    build_trip_coherence_report,
    mid_purpose_from_eqasim,
    mobility_rate,
    p36_mobility_target_by_kreis,
    purpose_distribution,
    purpose_participation_by_segment,
    renormalize_scored,
    segment_mobility_rate,
    synthetic_mean_length_by_purpose,
    trip_coherence_by_kreis,
    trips_per_person_by_segment,
    w12_length_coherence,
    w12_mean_length_target,
    w1_scored_target,
    w1_scored_target_by_kreis,
    p36_mobility_target,
)

DATA_PATH = str(REPO / "eqasim-data" / "data")


def test_mid_purpose_crosswalk():
    # The four unambiguous eqasim activity purposes map onto their MiD W1 Zweck;
    # home (return trips) and other are kept as explicit, separate categories so
    # they are never silently folded into a W1 purpose.
    out = mid_purpose_from_eqasim(
        pd.Series(["work", "education", "shop", "leisure", "home", "other"]))
    assert list(out) == [
        "arbeit", "ausbildung", "einkauf", "freizeit", "heimweg", "sonstiges"]


def test_mobility_rate_is_share_of_persons_with_a_trip():
    persons = pd.DataFrame({"person_id": [1, 2, 3, 4]})
    trips = pd.DataFrame({"person_id": [1, 1, 3]})  # persons 2 and 4 are immobile
    assert mobility_rate(persons, trips) == 0.5


def test_purpose_distribution_excludes_home_and_normalises():
    # Activity-purpose distribution is taken over non-home trip destinations and
    # normalised to shares over the MiD categories.
    trips = pd.DataFrame({
        "following_purpose": ["work", "work", "shop", "leisure", "home", "home"],
    })
    dist = purpose_distribution(trips)
    # 4 non-home trips: 2 arbeit, 1 einkauf, 1 freizeit
    assert dist["arbeit"] == 0.5
    assert dist["einkauf"] == 0.25
    assert dist["freizeit"] == 0.25
    assert "heimweg" not in dist


def test_renormalize_scored_restricts_to_four_purposes():
    dist = {"arbeit": 0.2, "ausbildung": 0.1, "einkauf": 0.2,
            "freizeit": 0.3, "sonstiges": 0.2}
    out = renormalize_scored(dist)
    assert set(out) == {"arbeit", "ausbildung", "einkauf", "freizeit"}
    assert abs(sum(out.values()) - 1.0) < 1e-9
    # arbeit was 0.2 of 0.8 scored mass -> 0.25 after renormalisation.
    assert abs(out["arbeit"] - 0.25) < 1e-9


def test_w1_scored_target_is_a_normalised_four_purpose_distribution():
    target = w1_scored_target(DATA_PATH)
    assert set(target) == {"arbeit", "ausbildung", "einkauf", "freizeit"}
    assert abs(sum(target.values()) - 1.0) < 1e-9
    assert all(0.0 <= v <= 1.0 for v in target.values())


def test_p36_mobility_target_is_a_plausible_share():
    rate = p36_mobility_target(DATA_PATH)
    # MiD P36_1 ZGB mobility share is ~0.80; assert a plausible (0.5, 1.0) range.
    assert 0.5 < rate < 1.0


def test_segment_mobility_rate_by_employed():
    persons = pd.DataFrame({
        "person_id": [1, 2, 3, 4],
        "employed": [True, True, False, False],
    })
    trips = pd.DataFrame({"person_id": [1, 3]})  # one employed, one not, are mobile
    out = segment_mobility_rate(persons, trips, "employed")
    rates = dict(zip(out["segment_value"].astype(str), out["mobility_rate"]))
    assert rates["True"] == 0.5
    assert rates["False"] == 0.5


def test_purpose_participation_by_segment_work():
    # Share of persons with >= 1 work trip, per employed segment. This is the
    # most direct "is the employed matching key working" KPI: employed persons
    # should have a much higher work-trip participation than non-employed.
    persons = pd.DataFrame({
        "person_id": [1, 2, 3, 4],
        "employed": [True, True, False, False],
    })
    trips = pd.DataFrame({
        "person_id": [1, 3],
        "following_purpose": ["work", "shop"],  # only person 1 (employed) works
    })
    out = purpose_participation_by_segment(persons, trips, "employed", purpose_value="work")
    rates = dict(zip(out["segment_value"].astype(str), out["participation_rate"]))
    assert rates["True"] == 0.5      # 1 of 2 employed has a work trip
    assert rates["False"] == 0.0     # 0 of 2 non-employed has a work trip


def test_trips_per_person_by_segment():
    persons = pd.DataFrame({
        "person_id": [1, 2, 3, 4],
        "employed": [True, True, False, False],
    })
    trips = pd.DataFrame({"person_id": [1, 1, 2, 3]})  # 2,1,1,0 trips per person
    out = trips_per_person_by_segment(persons, trips, "employed")
    means = dict(zip(out["segment_value"].astype(str), out["trips_per_person"]))
    assert means["True"] == 1.5      # (2 + 1) / 2
    assert means["False"] == 0.5     # (1 + 0) / 2


def test_report_exposes_work_employed_gap_kpi():
    # The headline differentiation KPI: employed work-trip participation minus
    # non-employed, in percentage points. Strongly positive = the matching gives
    # employed persons commute diaries.
    persons = pd.DataFrame({
        "person_id": [1, 2, 3, 4],
        "employed": [True, True, False, False],
    })
    trips = pd.DataFrame({
        "person_id": [1, 2, 3],
        "following_purpose": ["work", "work", "shop"],
    })
    report = build_trip_coherence_report(
        persons, trips, DATA_PATH, segment_cols=("employed",))
    # employed: 2/2 work -> 100%; non-employed: 0/2 -> 0%; gap = 100 pp.
    assert report["differentiation"]["work_share_employed_gap_pp"] == 100.0


def test_build_trip_coherence_report_structure():
    persons = pd.DataFrame({
        "person_id": [1, 2, 3, 4],
        "employed": [True, True, False, False],
    })
    trips = pd.DataFrame({
        "person_id": [1, 1, 3],
        "following_purpose": ["work", "shop", "leisure"],
    })
    report = build_trip_coherence_report(
        persons, trips, DATA_PATH, segment_cols=("employed",))

    # Mobility: persons 1 and 3 are mobile -> 0.5; target from P36_1 is present.
    assert report["mobility"]["overall_rate"] == 0.5
    assert 0.5 < report["mobility"]["target_rate"] < 1.0
    assert report["mobility"]["abs_delta"] == abs(0.5 - report["mobility"]["target_rate"])

    # Purpose: realised scored distribution over {arbeit, einkauf, freizeit} = 1/3
    # each (ausbildung 0), summing to 1; target and per-purpose |delta| present.
    realized = report["purpose"]["realized"]
    assert set(realized) == {"arbeit", "ausbildung", "einkauf", "freizeit"}
    assert abs(sum(realized.values()) - 1.0) < 1e-9
    assert abs(realized["arbeit"] - 1.0 / 3.0) < 1e-9
    assert "srmse" in report["purpose"]

    # Segment breakdown for the requested column is present.
    seg = report["mobility_by_segment"]
    assert set(seg["segment"].unique()) == {"employed"}


def test_per_kreis_targets_are_keyed_by_ars5():
    w1 = w1_scored_target_by_kreis(DATA_PATH)
    p36 = p36_mobility_target_by_kreis(DATA_PATH)
    # The ZGB-overall aggregate row must be excluded; the ZGB Kreis 03101 present.
    assert "03ZGB" not in w1 and "03ZGB" not in p36
    assert "03101" in w1 and "03101" in p36
    # Each Kreis purpose target is a normalised four-purpose distribution.
    assert abs(sum(w1["03101"].values()) - 1.0) < 1e-9
    assert 0.5 < p36["03101"] < 1.0


def test_trip_coherence_by_kreis_realised_vs_target():
    # Two Kreise; persons carry their home ars5. Per-Kreis mobility + purpose
    # shares with the matching MiD per-Kreis targets and signed deltas.
    persons = pd.DataFrame({
        "person_id": [1, 2, 3, 4],
        "ars5": ["03101", "03101", "03103", "03103"],
    })
    trips = pd.DataFrame({
        "person_id": [1, 1, 3],
        "following_purpose": ["work", "shop", "leisure"],
    })
    out = trip_coherence_by_kreis(persons, trips, DATA_PATH)
    by = out.set_index("ars5")
    # 03101: persons 1,2; only person 1 is mobile -> 0.5.
    assert by.loc["03101", "mobility_rate"] == 0.5
    # 03103: persons 3,4; only person 3 is mobile -> 0.5.
    assert by.loc["03103", "mobility_rate"] == 0.5
    # Columns for mapping are present (realised, target, signed delta).
    assert "mobility_target" in out.columns
    assert "mobility_delta_pp" in out.columns
    assert "purpose_freizeit_delta_pp" in out.columns
    assert "work_participation" in out.columns


def test_w12_mean_trip_length_target_loads_scored_purposes():
    # uses the real committed CSV under eqasim-data/.../mid/
    target = tc.w12_mean_length_target(DATA_PATH)
    # four scored eqasim purposes -> routed mean km (straight from W12 mittel_km)
    assert set(target) == {"work", "education", "shop", "leisure"}
    assert target["work"] == 15.2 and target["education"] == 5.7
    assert target["shop"] == 5.2 and target["leisure"] == 15.0


def test_w12_synthetic_mean_length_uses_detour_factor():
    # one work trip, straight-line 10 km -> routed 13 km via the 1.3 detour factor
    trips = pd.DataFrame({
        "person_id": [1],
        "following_purpose": ["work"],
        "euclidean_distance": [10000.0],  # metres
    })
    realised = synthetic_mean_length_by_purpose(trips)
    assert abs(realised["work"] - 13.0) < 1e-6


def test_w12_synthetic_mean_length_prefers_routed_distance_column():
    # When a routed_distance column exists, it is used directly (km, no detour
    # multiply): 13 km routed -> 13 km, ignoring the euclidean_distance column.
    trips = pd.DataFrame({
        "person_id": [1],
        "following_purpose": ["work"],
        "euclidean_distance": [10000.0],  # metres (would give 13 km via detour)
        "routed_distance": [13000.0],     # metres routed (used directly -> 13 km)
    })
    realised = synthetic_mean_length_by_purpose(trips)
    assert abs(realised["work"] - 13.0) < 1e-6


def test_w12_synthetic_mean_length_is_nan_safe():
    # NaN distances are skipped, not propagated into the mean.
    trips = pd.DataFrame({
        "person_id": [1, 2],
        "following_purpose": ["work", "work"],
        "euclidean_distance": [10000.0, float("nan")],
    })
    realised = synthetic_mean_length_by_purpose(trips)
    assert abs(realised["work"] - 13.0) < 1e-6


def test_w12_length_coherence_combines_target_and_realised():
    # one work trip (10 km straight-line -> 13 km routed) vs W12 target 15.2 km.
    trips = pd.DataFrame({
        "person_id": [1],
        "following_purpose": ["work"],
        "euclidean_distance": [10000.0],
    })
    rows = w12_length_coherence(trips, DATA_PATH)
    by = {r["purpose"]: r for r in rows}
    assert set(by) == {"work", "education", "shop", "leisure"}
    assert abs(by["work"]["target_km"] - 15.2) < 1e-9
    assert abs(by["work"]["realised_km"] - 13.0) < 1e-6
    assert abs(by["work"]["delta_km"] - (13.0 - 15.2)) < 1e-6
    assert abs(by["work"]["rel_delta"] - (13.0 - 15.2) / 15.2) < 1e-6


def test_segment_cols_absent_from_persons_are_skipped():
    # urban_class is only present when the matching feature is enabled; a request
    # for it on a frame that lacks it must be silently skipped (not raise), while
    # present columns are still evaluated.
    persons = pd.DataFrame({"person_id": [1, 2], "employed": [True, False]})
    trips = pd.DataFrame({"person_id": [1], "following_purpose": ["work"]})
    report = build_trip_coherence_report(
        persons, trips, DATA_PATH, segment_cols=("employed", "urban_class"))
    assert set(report["mobility_by_segment"]["segment"].unique()) == {"employed"}
