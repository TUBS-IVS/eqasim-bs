"""Tests for mapping MiD Wege (trips) to eqasim activity chains (Phase 5g.4).

Codes grounded in the MiD 2023 codebook (Wege sheet): W_ZWECK (purpose), hvm_imp
(imputed main mode; handbook Kap. 4.2). Tiny synthetic data only.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import trips


def test_map_purpose_from_w_zweck():
    wege = pd.DataFrame({"W_ZWECK": [1, 2, 3, 4, 7, 8, 9, 5, 11, 12]})
    out = trips.map_purpose(wege)
    assert list(out["purpose"]) == [
        "work", "work", "education", "shop", "leisure",
        "home", "home", "other", "education", "education",
    ]


def test_map_mode_uses_hvm_imp_and_rejects_unknown():
    import pandas as pd, pytest
    from braunschweig.popsim import trips
    out = trips.map_mode(pd.DataFrame({"hvm_imp": [1, 2, 3, 4, 5]}))
    assert out["mode"].tolist() == ["walk", "bicycle", "car_passenger", "car", "pt"]
    with pytest.raises(ValueError, match="unmapped"):
        trips.map_mode(pd.DataFrame({"hvm_imp": [99]}))


def test_mode_bicycle_for_hvm_imp_2():
    """MiD hvm_imp=2 (Fahrrad) must map to canonical eqasim mode 'bicycle'."""
    wege = pd.DataFrame({"hvm_imp": [2]})
    out = trips.map_mode(wege)
    assert out["mode"].iloc[0] == "bicycle"


def test_expand_persons_to_trips_joins_donor_wege():
    # synthetic persons referencing donor (H_ID, P_ID)
    persons = pd.DataFrame(
        {
            "household_id": ["A_1_0", "A_1_0", "B_2_0"],
            "person_id": ["A_1_0_1", "A_1_0_2", "B_2_0_1"],
            "H_ID": [1, 1, 2],
            "P_ID": [1, 2, 1],
        }
    )
    wege = pd.DataFrame(
        {
            "H_ID": [1, 1, 1, 2],
            "P_ID": [1, 1, 2, 1],
            "W_ID": [1, 2, 1, 1],
            "W_ZWECK": [1, 8, 7, 4],
            "hvm_imp": [4, 4, 1, 5],
        }
    )
    out = trips.expand_persons_to_trips(persons, wege)
    # person A_1_0_1 (donor 1,1) has 2 trips; A_1_0_2 (donor 1,2) has 1; B_2_0_1 has 1.
    counts = out.groupby("person_id").size().to_dict()
    assert counts == {"A_1_0_1": 2, "A_1_0_2": 1, "B_2_0_1": 1}
    assert "purpose" in out.columns and "mode" in out.columns
    # trip_id from expand_persons_to_trips is the string traceability key <person_id>_<W_ID>.
    assert out["trip_id"].is_unique


def test_expand_persons_to_trips_logs_match_rate(caplog):
    """The inner join in expand_persons_to_trips must log an observable match rate.

    Two of three synthetic persons have a donor with no Wege row (silently
    dropped by the inner join); the match rate (~33.3%) must be logged and,
    since it falls below MIN_EXPECTED_TRIP_MATCH_RATE, a warning must also fire.
    """
    import logging

    persons = pd.DataFrame(
        {
            "person_id": ["A_1_0_1", "A_1_0_2", "A_1_0_3"],
            "H_ID": [1, 1, 1],
            "P_ID": [1, 2, 3],
        }
    )
    wege = pd.DataFrame(
        {"H_ID": [1], "P_ID": [1], "W_ID": [1], "W_ZWECK": [1], "hvm_imp": [4]}
    )
    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.trips"):
        out = trips.expand_persons_to_trips(persons, wege)

    assert len(out) == 1
    info_messages = [r.message for r in caplog.records if r.levelno == logging.INFO]
    assert any("1/3" in m and "33.3%" in m for m in info_messages), info_messages
    warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("below the expected minimum" in m for m in warning_messages), warning_messages


def test_expand_persons_to_trips_person_without_wege_is_dropped():
    persons = pd.DataFrame(
        {"household_id": ["A_1_0"], "person_id": ["A_1_0_9"], "H_ID": [1], "P_ID": [9]}
    )
    wege = pd.DataFrame({"H_ID": [1], "P_ID": [1], "W_ID": [1], "W_ZWECK": [1], "hvm_imp": [4]})
    out = trips.expand_persons_to_trips(persons, wege)
    assert len(out) == 0


def test_mid_time_seconds_from_hours_minutes():
    wege = pd.DataFrame({"W_SZS": [8, 17], "W_SZM": [30, 5]})
    out = trips.mid_time_seconds(wege, "W_SZS", "W_SZM")
    assert list(out) == [8 * 3600 + 30 * 60, 17 * 3600 + 5 * 60]


def test_mid_time_seconds_nans_coded_times():
    # Audited codes in MiD2023_Wege.csv (W_SZS/W_SZM/W_AZS/W_AZM): 99 (keine
    # Angabe) and 701 (design code, regelmaessige berufliche Wege). Any
    # out-of-range hour (>23) or minute (>59) must yield NaN, not a multi-day
    # timestamp. Minute 9 is a VALID minute and must stay valid.
    import numpy as np

    w = pd.DataFrame({"h": [8, 99, 701, 8], "m": [30, 0, 0, 9]})
    s = trips.mid_time_seconds(w, "h", "m")
    assert s.iloc[0] == 8 * 3600 + 30 * 60
    assert np.isnan(s.iloc[1]) and np.isnan(s.iloc[2])
    assert s.iloc[3] == 8 * 3600 + 9 * 60


def test_mid_time_seconds_nans_coded_minutes():
    # Codes also occur in the MINUTE field (99/701, audited counts match the
    # hour field row-wise); an out-of-range minute invalidates the time too.
    import numpy as np

    w = pd.DataFrame({"h": [8, 8], "m": [99, 701]})
    s = trips.mid_time_seconds(w, "h", "m")
    assert np.isnan(s.iloc[0]) and np.isnan(s.iloc[1])


def _make_two_trip_persons_and_wege():
    """Helper: one synthetic person with two sequential trips (work then home)."""
    persons = pd.DataFrame({
        "person_id": ["A_1_0_1", "A_1_0_1"],
        "H_ID": [1, 1], "P_ID": [1, 1],
    })
    wege = pd.DataFrame({
        "H_ID": [1, 1], "P_ID": [1, 1], "W_ID": [1, 2],
        "W_ZWECK": [1, 8], "hvm_imp": [4, 4],
        "W_SZS": [8, 17], "W_SZM": [0, 0],
        "W_AZS": [8, 17], "W_AZM": [30, 20],
        "wegkm": [12.0, 12.0],
        # extras (carried through):
        "W_ZWDF": [None, None], "W_ANZBEGL": [0, 1], "W_BEGL_HH": [2, 1],
    })
    return persons, wege


def test_build_trip_table_eqasim_schema_plus_extras():
    persons, wege = _make_two_trip_persons_and_wege()
    out = trips.build_trip_table(persons, wege)

    # eqasim trip-schema columns present (including trip_index added by rework):
    for col in ["person_id", "trip_id", "departure_time", "arrival_time",
                "trip_duration", "activity_duration", "preceding_purpose",
                "following_purpose", "is_first_trip", "is_last_trip", "mode",
                "trip_index"]:
        assert col in out.columns, f"column '{col}' missing from build_trip_table output"

    # trip_key holds the old string traceability ID (e.g. "A_1_0_1_1").
    assert "trip_key" in out.columns
    assert out["trip_key"].iloc[0] == "A_1_0_1_1"

    # trip_id is now an integer (global 0..n-1), still unique.
    assert pd.api.types.is_integer_dtype(out["trip_id"]), "trip_id must be integer after rework"
    assert out["trip_id"].is_unique

    first = out.iloc[0]
    assert first["departure_time"] == 8 * 3600
    assert first["arrival_time"] == 8 * 3600 + 30 * 60
    assert first["trip_duration"] == 30 * 60
    assert first["mode"] == "car"
    # preceding_purpose of trip 1 (diary starts at home) -> home; following = work.
    assert first["following_purpose"] == "work"
    assert first["preceding_purpose"] == "home"
    assert bool(first["is_first_trip"]) is True
    assert bool(out.iloc[-1]["is_last_trip"]) is True
    # extra MiD info carried:
    assert "wegkm" in out.columns and "W_ANZBEGL" in out.columns


def test_build_trip_table_has_integer_trip_index():
    """trip_index must be a per-person 0-based sequential integer."""
    persons = pd.DataFrame({
        "person_id": ["A_1_0_1", "A_1_0_1", "B_2_0_1"],
        "H_ID": [1, 1, 2], "P_ID": [1, 1, 2],
    })
    wege = pd.DataFrame({
        "H_ID": [1, 1, 2, 2, 2],
        "P_ID": [1, 1, 2, 2, 2],
        "W_ID": [1, 2, 1, 2, 3],
        "W_ZWECK": [1, 8, 4, 7, 8], "hvm_imp": [4, 4, 5, 1, 4],
        "W_SZS": [8, 17, 9, 12, 18], "W_SZM": [0, 0, 0, 30, 0],
        "W_AZS": [8, 17, 9, 12, 18], "W_AZM": [30, 20, 45, 50, 30],
        "wegkm": [12.0, 12.0, 5.0, 3.0, 8.0],
    })
    out = trips.build_trip_table(persons, wege)
    for pid, grp in out.groupby("person_id"):
        indices = sorted(grp["trip_index"].tolist())
        expected = list(range(len(grp)))
        assert indices == expected, (
            f"person {pid}: trip_index {indices} != expected 0..{len(grp)-1}"
        )


def test_build_trip_table_midnight_repair():
    """A trip departing near midnight and arriving after midnight must be repaired.

    We encode: departure at 23:30, arrival at 00:30 (next day), which appears
    in MiD as W_AZS=0, W_AZM=30 (raw seconds = 1800) while departure is
    23*3600+30*60 = 84600.  This yields arrival_time < departure_time before
    fix_trip_times; hts.fix_trip_times must shift arrival by +24h so that
    arrival_time >= departure_time.
    """
    persons = pd.DataFrame({
        "person_id": ["X_1_0_1"],
        "H_ID": [10], "P_ID": [5],
    })
    # Two-trip chain: first trip (work commute 08:00–08:30), second trip crosses midnight.
    wege = pd.DataFrame({
        "H_ID": [10, 10], "P_ID": [5, 5], "W_ID": [1, 2],
        "W_ZWECK": [1, 8], "hvm_imp": [4, 4],
        # trip 1: 08:00 -> 08:30
        # trip 2: 23:30 -> 00:30 (midnight crossing; arrival coded as 00:30)
        "W_SZS": [8, 23], "W_SZM": [0, 30],
        "W_AZS": [8, 0], "W_AZM": [30, 30],
        "wegkm": [12.0, 12.0],
    })
    out = trips.build_trip_table(persons, wege)
    trip2 = out[out["trip_index"] == 1].iloc[0]
    # After fix_trip_times, arrival_time must be >= departure_time.
    assert trip2["arrival_time"] >= trip2["departure_time"], (
        f"Midnight crossing not repaired: departure={trip2['departure_time']}, "
        f"arrival={trip2['arrival_time']}"
    )
    # The repair must have pushed arrival past midnight (>= 24h mark).
    assert trip2["arrival_time"] >= 24 * 3600, (
        f"Arrival should be pushed past midnight (>=86400s), got {trip2['arrival_time']}"
    )


def test_build_validated_trip_table_returns_report():
    persons = pd.DataFrame({"person_id": ["A_1_0_1", "A_1_0_1"], "H_ID": [1, 1], "P_ID": [1, 1]})
    wege = pd.DataFrame({
        "H_ID": [1, 1], "P_ID": [1, 1], "W_ID": [1, 2],
        "W_ZWECK": [1, 8], "hvm_imp": [4, 4],
        "W_SZS": [8, 17], "W_SZM": [0, 0], "W_AZS": [8, 17], "W_AZM": [30, 20],
    })
    table, report = trips.build_validated_trip_table(persons, wege, require_home_closure=True)
    assert "departure_time" in table.columns
    assert hasattr(report, "is_valid")


# ---------------------------------------------------------------------------
# Task 2.3 B: build_validated_trip_table resamples coded-time (NaN) persons
# from same-cell donors instead of leaving NaN chains in the table.
# ---------------------------------------------------------------------------

def test_build_validated_trip_table_resamples_coded_time_persons():
    import pandas as pd
    from braunschweig.popsim import trips as popsim_trips
    persons = pd.DataFrame({
        "person_id": ["pA", "pB"], "H_ID": [1, 2], "P_ID": [1, 1],
        "ZENSUS100m": ["c1", "c1"],
    })
    wege = pd.DataFrame({
        "H_ID":   [1, 2],
        "P_ID":   [1, 1],
        "W_ID":   [1, 1],
        "W_ZWECK": [1, 1],
        "hvm_imp": [4, 4],
        "W_SZS": [701, 8], "W_SZM": [701, 0],
        "W_AZS": [701, 9], "W_AZM": [701, 0],
        "wegkm_imp": [5.0, 5.0],
    })
    table, report = popsim_trips.build_validated_trip_table(
        persons, wege, resample=True, resample_cell_col="ZENSUS100m",
        random_seed=0,
    )
    assert set(table["person_id"].unique()) == {"pA", "pB"}
    assert table["departure_time"].notna().all()


# ---------------------------------------------------------------------------
# Issue #201: ESCORT_W_ZWECK + flag-gated escort purpose override.
# ---------------------------------------------------------------------------

def test_map_purpose_escort_flag_off_is_byte_identical():
    wege = pd.DataFrame({"W_ZWECK": [1, 4, 6, 13, 7, 99]})
    off_default = trips.map_purpose(wege)
    off_explicit = trips.map_purpose(wege, escort_purpose=False)
    assert list(off_default["purpose"]) == list(off_explicit["purpose"])
    # 6 and 13 stay "other" on the OFF path (13 via the fillna default).
    assert list(off_default["purpose"]) == ["work", "shop", "other", "other", "leisure", "other"]


def test_map_purpose_escort_flag_on_maps_6_and_13():
    wege = pd.DataFrame({"W_ZWECK": [1, 4, 6, 13, 7, 99]})
    on = trips.map_purpose(wege, escort_purpose=True)
    assert list(on["purpose"]) == ["work", "shop", "escort", "escort", "leisure", "other"]


def test_escort_w_zweck_constant():
    assert trips.ESCORT_W_ZWECK == frozenset({6, 13})
    # The internal #127 subtype constant must stay untouched (OFF-path identity).
    from braunschweig.popsim.purpose_subtype import OTHER_ESCORT_ZWECK
    assert OTHER_ESCORT_ZWECK == frozenset({6})


def test_map_purpose_escort_share_logged_w_gew_weighted(caplog):
    """The W_GEW-weighted branch of map_purpose must be exercised, not only the
    unweighted fallback (fallback-transparency rule: test the primary method,
    not just the fallback). W_GEW is the codebase's standard MiD trip weight and
    is present on real production data, so this is the branch that actually
    executes in practice; the two escort tests above only exercise the
    unweighted fallback because their fixture has no W_GEW column.

    Escort rows (W_ZWECK in {6, 13}) sit at index 2 and 3, as in the fixtures
    above; W_GEW gives them weight 2 each (sum 4) against a total weight of 8,
    so the W_GEW-weighted escort share is 4/8 = 50.00%.
    """
    import logging

    wege = pd.DataFrame({
        "W_ZWECK": [1, 4, 6, 13, 7, 99],
        "W_GEW": [1, 1, 2, 2, 1, 1],
    })
    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.trips"):
        on = trips.map_purpose(wege, escort_purpose=True)

    # The mapping itself must be unaffected by the presence of W_GEW.
    assert list(on["purpose"]) == ["work", "shop", "escort", "escort", "leisure", "other"]

    info_messages = [r.message for r in caplog.records if r.levelno == logging.INFO]
    assert any("W_GEW-weighted" in m and "50.00%" in m for m in info_messages), info_messages


# ---------------------------------------------------------------------------
# Issue #256: escort_passive_education -- W_ZWECK 13 (passive escort leg)
# becomes the escorted child's own education trip.
# ---------------------------------------------------------------------------

# Issue #256: W_ZWECK 13 is the PASSIVE side -> education at the child's own school.
def test_map_purpose_passive_education_relabels_13_only():
    wege = pd.DataFrame({
        "W_ZWECK": [1, 4, 6, 13, 7, 99],
        "W_GEW": [1.0] * 6,
    })
    out = trips.map_purpose(wege, escort_purpose=True, escort_passive_education=True)
    assert list(out["purpose"]) == [
        "work", "shop", "escort", "education", "leisure", "other"]


def test_map_purpose_passive_education_requires_escort_purpose():
    wege = pd.DataFrame({"W_ZWECK": [6, 13], "W_GEW": [1.0, 1.0]})
    with pytest.raises(ValueError, match="requires escort_purpose"):
        trips.map_purpose(wege, escort_purpose=False, escort_passive_education=True)


def test_map_purpose_passive_flag_off_keeps_201_behaviour():
    wege = pd.DataFrame({"W_ZWECK": [6, 13], "W_GEW": [1.0, 1.0]})
    on_201 = trips.map_purpose(wege, escort_purpose=True)
    explicit_off = trips.map_purpose(wege, escort_purpose=True,
                                     escort_passive_education=False)
    assert list(on_201["purpose"]) == list(explicit_off["purpose"]) == ["escort", "escort"]


def test_map_purpose_passive_education_rates_logged(caplog):
    import logging
    wege = pd.DataFrame({"W_ZWECK": [6, 6, 6, 13], "W_GEW": [1.0, 1.0, 1.0, 3.0]})
    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.trips"):
        trips.map_purpose(wege, escort_purpose=True, escort_passive_education=True)
    joined = " ".join(r.getMessage() for r in caplog.records)
    # active 3 legs weight 3.0 (50.0%), passive 1 leg weight 3.0 (50.0%)
    assert "escort_passive_education ON" in joined
    assert "passive" in joined and "education" in joined


# ---------------------------------------------------------------------------
# Issue #373 fix round 1, Important finding 3a: w_zweck_10_as_leisure must reach
# map_purpose through the FULL builder chain (build_validated_trip_table ->
# build_trip_table -> expand_persons_to_trips -> map_purpose), not just be
# exercised directly against map_purpose (test_w_zweck_hwzweck1_fold.py).
# ---------------------------------------------------------------------------

def test_build_validated_trip_table_threads_w_zweck_10_as_leisure_to_map_purpose():
    """A single W_ZWECK-10 leg must resolve to following_purpose 'leisure' when the
    flag is on and 'other' when it is off, through the full builder chain."""
    persons = pd.DataFrame({
        "person_id": ["A_1_0_1", "A_1_0_1"], "H_ID": [1, 1], "P_ID": [1, 1],
    })
    wege = pd.DataFrame({
        "H_ID": [1, 1], "P_ID": [1, 1], "W_ID": [1, 2],
        "W_ZWECK": [10, 8], "hvm_imp": [4, 4],
        "W_SZS": [8, 17], "W_SZM": [0, 0], "W_AZS": [8, 17], "W_AZM": [30, 20],
    })
    table_on, _ = trips.build_validated_trip_table(persons, wege, w_zweck_10_as_leisure=True)
    table_off, _ = trips.build_validated_trip_table(persons, wege, w_zweck_10_as_leisure=False)

    first_on = table_on[table_on["trip_index"] == 0].iloc[0]
    first_off = table_off[table_off["trip_index"] == 0].iloc[0]
    assert first_on["following_purpose"] == "leisure"
    assert first_off["following_purpose"] == "other"


# ---------------------------------------------------------------------------
# Issue #372 task 4: escort_passive_from_adult -- a PAIRED passive escort leg
# (MiD W_ZWECK 13) takes the purpose derived from the accompanying adult's
# W_ZWECK instead of the flat escort_passive_education "education" relabel.
# ---------------------------------------------------------------------------

def _household_wege():
    """Two households: H1 has a shopping/escorting/home adult plus a 5-year-old with two
    passive legs; H2 has a 7-year-old whose passive leg has no adult leg to pair with."""
    return pd.DataFrame({
        "H_ID": [1, 1, 1, 1, 1, 2], "P_ID": [1, 1, 1, 2, 2, 2], "W_ID": [1, 2, 3, 1, 2, 1],
        "W_ZWECK": [4, 6, 8, 13, 13, 13], "W_SZS": [8, 12, 13, 8, 13, 9], "W_SZM": [0, 0, 0, 0, 0, 0],
        "HP_ALTER": [35, 35, 35, 5, 5, 7], "W_GEW": [1.0] * 6,
    })


def test_passive_from_adult_takes_the_adult_purpose_home_and_keeps_the_passive_rule_for_adult_escort():
    out = trips.map_purpose(_household_wege(), escort_purpose=True, escort_passive_education=True,
                            escort_passive_from_adult=True)
    child = out[out["P_ID"] == 2].sort_values(["H_ID", "W_ID"])["purpose"].tolist()
    assert child == ["shop", "home", "education"]     # 8:00 with the shopping adult, 13:00 home with the adult, unpaired -> passive rule


def test_passive_from_adult_off_is_byte_identical_to_passive_education():
    wege = _household_wege()
    a = trips.map_purpose(wege, escort_purpose=True, escort_passive_education=True)
    b = trips.map_purpose(wege, escort_purpose=True, escort_passive_education=True,
                          escort_passive_from_adult=False)
    pd.testing.assert_frame_equal(a, b)


def test_passive_from_adult_requires_escort_purpose():
    with pytest.raises(ValueError, match="escort_passive_from_adult"):
        trips.map_purpose(_household_wege(), escort_passive_from_adult=True)


def test_adult_work_pairs_become_other_and_adult_code_10_follows_the_leisure_flag():
    codes = np.array([1, 2, 3, 10, 10, 99])
    on = trips.passive_purpose_for_pairs(codes, escort_passive_education=True, w_zweck_10_as_leisure=True)
    off = trips.passive_purpose_for_pairs(codes, escort_passive_education=True, w_zweck_10_as_leisure=False)
    assert on.tolist() == ["other", "other", "other", "leisure", "leisure", "other"]
    assert off.tolist()[3] == "other"


def test_passive_from_adult_carries_the_pairing_columns_and_logs_the_rates(caplog):
    """CLAUDE.md fallback transparency: the paired/unpaired split, the basis of the share and
    the resulting purpose distribution must be observable, not implicit in the output frame."""
    import logging
    from braunschweig.popsim.escort_pairing import PAIRING_COLUMNS, STATUS_PAIRED
    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.trips"):
        out = trips.map_purpose(_household_wege(), escort_purpose=True,
                                escort_passive_education=True, escort_passive_from_adult=True)
    for column in PAIRING_COLUMNS:
        assert column in out.columns
    assert (out["passive_pair_status"] == STATUS_PAIRED).sum() == 2
    joined = " ".join(record.getMessage() for record in caplog.records)
    assert "escort_passive_from_adult ON" in joined
    assert "2/3" in joined and "W_GEW-weighted" in joined
    assert "1 unpaired" in joined


def test_passive_from_adult_requires_the_pairing_columns_with_a_clear_message():
    """map_purpose runs on the raw Wege frame, so the household/age/time columns the pairing
    needs must be present; a missing one must name itself instead of failing deep inside."""
    wege = _household_wege().drop(columns=["HP_ALTER"])
    with pytest.raises(ValueError, match="HP_ALTER"):
        trips.map_purpose(wege, escort_purpose=True, escort_passive_education=True,
                          escort_passive_from_adult=True)


def test_passive_pair_gap_default_agrees_across_its_three_homes():
    """The 15-minute default lives in escort_pairing (the module that owns the pairing); the
    trip-build keyword default and the synpp config default repeat the literal because neither
    module can import it (leaf / cycle constraints). Pin the three together."""
    from braunschweig.popsim import escort_pairing
    from braunschweig.popsim.stage.config_keys import DEFAULT_PASSIVE_PAIR_MAX_GAP_MINUTES
    assert trips.DEFAULT_PASSIVE_PAIR_MAX_GAP_MINUTES == escort_pairing.DEFAULT_MAX_GAP_MINUTES
    assert DEFAULT_PASSIVE_PAIR_MAX_GAP_MINUTES == escort_pairing.DEFAULT_MAX_GAP_MINUTES


def test_build_validated_trip_table_threads_escort_passive_from_adult_to_map_purpose():
    """The flag must survive the FULL builder chain (build_validated_trip_table ->
    build_trip_table -> expand_persons_to_trips -> map_purpose), like w_zweck_10_as_leisure."""
    persons = pd.DataFrame({"person_id": ["A_1_0_2"], "H_ID": [1], "P_ID": [2]})
    wege = pd.DataFrame({
        "H_ID": [1, 1, 1], "P_ID": [1, 1, 2], "W_ID": [1, 2, 1],
        "W_ZWECK": [4, 8, 13], "hvm_imp": [4, 4, 3],
        "W_SZS": [8, 17, 8], "W_SZM": [0, 0, 0],
        "W_AZS": [8, 17, 8], "W_AZM": [30, 20, 30],
        "HP_ALTER": [35, 35, 5], "W_GEW": [1.0, 1.0, 1.0],
    })
    table_on, _ = trips.build_validated_trip_table(
        persons, wege, escort_purpose=True, escort_passive_education=True,
        escort_passive_from_adult=True)
    table_off, _ = trips.build_validated_trip_table(
        persons, wege, escort_purpose=True, escort_passive_education=True,
        escort_passive_from_adult=False)
    assert table_on[table_on["trip_index"] == 0].iloc[0]["following_purpose"] == "shop"
    assert table_off[table_off["trip_index"] == 0].iloc[0]["following_purpose"] == "education"


# ---------------------------------------------------------------------------
# Issue #372 fix round 1.
# ---------------------------------------------------------------------------

def test_passive_purpose_for_pairs_counts_a_missing_adult_code_instead_of_raising(caplog):
    """IMPORTANT 4: a NaN adult W_ZWECK must reach the counted, named fallback the docstring
    promises, not raise IntCastingNaNError out of an astype(int)."""
    import logging
    codes = np.array([4.0, np.nan, 77.0])
    with caplog.at_level(logging.WARNING, logger="braunschweig.popsim.trips"):
        out = trips.passive_purpose_for_pairs(codes, escort_passive_education=True,
                                              w_zweck_10_as_leisure=True)
    assert out.tolist() == ["shop", trips.DEFAULT_PURPOSE, trips.DEFAULT_PURPOSE]
    joined = " ".join(record.getMessage() for record in caplog.records)
    assert "[77]" in joined and "1 leg(s) with no adult code at all" in joined


def test_legs_kept_by_the_trip_build_matches_what_expand_persons_to_trips_keeps():
    """IMPORTANT 2/3: the seed and the reference derivations must reduce the raw Wege to
    exactly the legs the trip build turns into plan legs -- pinned against the real builder."""
    persons = pd.DataFrame({"person_id": ["A"], "H_ID": [1], "P_ID": [1]})
    wege = pd.DataFrame({
        "H_ID": [1, 1, 1], "P_ID": [1, 1, 1], "W_ID": [1, 2, 3],
        # leg 1 is a leading arrive-home leg, leg 2 an rbW summary leg, leg 3 a real one.
        "W_ZWECK": [8, 1, 4], "hvm_imp": [4, 4, 4],
        "W_SZS": [6, 8, 17], "W_SZM": [0, 0, 0], "W_AZS": [6, 8, 17], "W_AZM": [30, 30, 20],
        "W_RBW": [0, 1, 0], "W_SO1": [2, 809, 809],
    })
    kept = trips.legs_kept_by_the_trip_build(
        wege, exclude_rbw_legs=True, drop_leading_arrive_home_leg=True)
    assert kept["W_ID"].tolist() == [3]
    built = trips.expand_persons_to_trips(
        persons, wege, exclude_rbw_legs=True, drop_leading_arrive_home_leg=True)
    assert sorted(built["W_ID"].tolist()) == sorted(kept["W_ID"].tolist())


def test_legs_kept_by_the_trip_build_is_a_no_op_with_both_flags_off():
    wege = pd.DataFrame({
        "H_ID": [1], "P_ID": [1], "W_ID": [1], "W_ZWECK": [8],
        "W_RBW": [1], "W_SO1": [2],
    })
    pd.testing.assert_frame_equal(
        trips.legs_kept_by_the_trip_build(
            wege, exclude_rbw_legs=False, drop_leading_arrive_home_leg=False),
        wege)


# ---------------------------------------------------------------------------
# Issue #373 task 2 (ruling C-R20/C-R21): map_purpose's pairing_candidate_mask.
#
# The trip build pairs passive escort legs on the legs it has ALREADY dropped
# (exclude_rbw_legs / drop_leading_arrive_home_leg run before map_purpose inside
# expand_persons_to_trips). A caller that must keep every leg for its own purpose
# (distance_distributions.run needs the full distance pool) has no such pre-filter
# to rely on, so it needs a way to restrict the PAIRING's candidate universe
# without dropping any row from its own output.
# ---------------------------------------------------------------------------

def _pairing_mask_fixture():
    """One household: the adult's FIRST leg is a leading arrive-home leg (dropped by
    drop_leading_arrive_home_leg) departing the SAME minute as the child's passive leg;
    the adult's SECOND leg is the real active escort (Bringen/Holen) leg 5 minutes later.
    Unrestricted, the nearest-in-time candidate for the child's passive leg is the
    (about to be dropped) leading arrive-home leg."""
    return pd.DataFrame({
        "H_ID": [1, 1, 1], "P_ID": [1, 1, 2], "W_ID": [1, 2, 1],
        "W_ZWECK": [8, 6, 13], "W_SZS": [8, 8, 8], "W_SZM": [0, 5, 0],
        "W_SO1": [2, 809, 809], "HP_ALTER": [35, 35, 5], "W_GEW": [1.0, 1.0, 1.0],
    })


def test_map_purpose_pairing_candidate_mask_restricts_the_candidate_universe():
    """Without the mask, the child pairs with the leading arrive-home leg (gap 0 min,
    W_ZWECK 8 -> 'home'). With the mask reduced to legs_kept_by_the_trip_build (which
    drops that leading arrive-home leg), only the active escort leg (W_ZWECK 6) survives
    as a candidate, and escort_passive_education gives the child 'education' instead --
    matching what the trip build itself would realise for the same Wege."""
    wege = _pairing_mask_fixture()
    unrestricted = trips.map_purpose(
        wege, escort_purpose=True, escort_passive_education=True,
        escort_passive_from_adult=True)
    child_unrestricted = unrestricted[unrestricted["P_ID"] == 2].iloc[0]["purpose"]
    assert child_unrestricted == "home"

    kept = trips.legs_kept_by_the_trip_build(
        wege, exclude_rbw_legs=False, drop_leading_arrive_home_leg=True)
    mask = pd.Series(wege.index.isin(kept.index), index=wege.index)
    restricted = trips.map_purpose(
        wege, escort_purpose=True, escort_passive_education=True,
        escort_passive_from_adult=True, pairing_candidate_mask=mask)
    child_restricted = restricted[restricted["P_ID"] == 2].iloc[0]["purpose"]
    assert child_restricted == "education"


def test_map_purpose_pairing_candidate_mask_none_is_byte_identical_to_omitting_it():
    """None (the default) must be indistinguishable from not passing the keyword at all --
    the OFF path for every existing caller of map_purpose."""
    wege = _pairing_mask_fixture()
    a = trips.map_purpose(wege, escort_purpose=True, escort_passive_education=True,
                          escort_passive_from_adult=True)
    b = trips.map_purpose(wege, escort_purpose=True, escort_passive_education=True,
                          escort_passive_from_adult=True, pairing_candidate_mask=None)
    pd.testing.assert_frame_equal(a, b)


def test_map_purpose_pairing_candidate_mask_index_mismatch_raises():
    wege = _pairing_mask_fixture()
    bad_mask = pd.Series([True, True], index=[0, 1])
    with pytest.raises(ValueError, match="pairing_candidate_mask"):
        trips.map_purpose(wege, escort_purpose=True, escort_passive_education=True,
                          escort_passive_from_adult=True, pairing_candidate_mask=bad_mask)


def test_map_purpose_pairing_candidate_mask_with_nan_raises():
    """MINOR 6 (cleanup wave fix round): pd.Series.astype(bool) casts NaN to True, which would
    silently ADD a leg to the pairing's candidate universe instead of failing loudly -- pinned
    against the real (pre-fix) behaviour: mask.astype(bool) on [True, nan, False] gives
    [True, True, False], no exception at all."""
    wege = _pairing_mask_fixture()
    mask = pd.Series([True, np.nan, False], index=wege.index)
    with pytest.raises(ValueError, match=r"\[popsim\.trips\].*[Nn]a[Nn]"):
        trips.map_purpose(wege, escort_purpose=True, escort_passive_education=True,
                          escort_passive_from_adult=True, pairing_candidate_mask=mask)


def test_map_purpose_pairing_candidate_mask_duplicate_index_raises():
    """MINOR 7 (cleanup wave fix round): a duplicate-labelled Wege index used to raise an
    opaque pandas error ('Must have equal len keys and value when setting with an iterable')
    deep inside the masked branch's .loc assignment; it must instead raise a clear,
    [popsim.trips]-tagged ValueError naming the number of duplicate labels up front."""
    wege = _pairing_mask_fixture()
    wege.index = [0, 0, 2]  # duplicate label 0
    mask = pd.Series([True, True, False], index=wege.index)
    with pytest.raises(ValueError, match=r"\[popsim\.trips\].*duplicate"):
        trips.map_purpose(wege, escort_purpose=True, escort_passive_education=True,
                          escort_passive_from_adult=True, pairing_candidate_mask=mask)


def test_map_purpose_pairing_candidate_mask_excludes_the_passive_leg_itself_keeps_the_passive_rule():
    """A passive leg OUTSIDE the mask is not considered for pairing at all -- it keeps the
    existing passive rule (the escort_passive_education relabel), exactly like an unpaired
    leg, rather than the mask only narrowing which ADULT legs it may pair with."""
    wege = _pairing_mask_fixture()
    mask = pd.Series([True, True, False], index=wege.index)  # child's own leg excluded
    out = trips.map_purpose(wege, escort_purpose=True, escort_passive_education=True,
                            escort_passive_from_adult=True, pairing_candidate_mask=mask)
    child = out[out["P_ID"] == 2].iloc[0]
    assert child["purpose"] == "education"
    assert pd.isna(child["passive_pair_status"])


def test_map_purpose_masked_branch_share_paired_uses_the_same_denominator_as_n_passive(caplog):
    """MINOR 5 (cleanup wave fix round): the masked branch's summary log mixed denominators --
    the W_GEW-weighted share_paired's denominator (is_passive_leg) covered ALL passive legs in
    the frame, while n_passive (the mask-internal count pair_passive_legs reports) covered only
    the ones inside the mask. A passive leg OUTSIDE the mask (never even a pairing candidate)
    silently dragged the reported share DOWN. Both must be computed over the SAME set -- the
    pairing's candidate universe -- and the message must name that set."""
    import logging

    wege = pd.DataFrame({
        "H_ID": [1, 1, 1], "P_ID": [1, 2, 3], "W_ID": [1, 1, 1],
        "W_ZWECK": [6, 13, 13], "W_SZS": [8, 8, 8], "W_SZM": [0, 0, 0],
        "HP_ALTER": [35, 5, 5], "W_GEW": [1.0, 2.0, 3.0],
    })
    # Only the FIRST passive leg (P_ID=2) sits inside the pairing's candidate universe; the
    # second (P_ID=3) is excluded entirely, e.g. a leg the trip build would drop.
    mask = pd.Series([True, True, False], index=wege.index)
    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.trips"):
        out = trips.map_purpose(wege, escort_purpose=True, escort_passive_education=True,
                                escort_passive_from_adult=True, pairing_candidate_mask=mask)
    # P_ID=2 pairs with the adult's active escort leg (same minute); P_ID=3 is not even a
    # candidate and keeps the escort_passive_education rule.
    assert out.loc[out["P_ID"] == 2, "purpose"].iloc[0] == "education"
    joined = " ".join(record.getMessage() for record in caplog.records)
    assert "1/1 passive legs" in joined  # n_paired/n_passive, BOTH mask-internal
    assert "100.00%" in joined
    assert "inside the pairing's candidate universe" in joined


# ---------------------------------------------------------------------------
# The WEEKDAY DIARY universe (issue #373, ADR-0116): the one definition of the
# leg universe every MiD-based estimation of a weekday quantity must use -- the
# seed's own day filter plus the rbW exclusion, in ONE function shared by the
# secondary distance layers, the three MiD subtype deciders and the two
# committed-reference extraction scripts.
# ---------------------------------------------------------------------------


def test_weekday_diary_kernwo_is_the_seed_day_filter_not_a_retyped_copy():
    """The model has ONE weekday definition; the helper must READ it from the seed
    (MID_SEED_COLUMNS.day_filter_values, the value PopulationSim actually filters the
    seed on) rather than re-type a literal that cannot notice a change there."""
    from braunschweig.popsim.seed import MID_SEED_COLUMNS, WEEKDAY_KERNWO

    assert trips.WEEKDAY_DIARY_KERNWO == tuple(MID_SEED_COLUMNS.day_filter_values)
    # The seed's own alias for the same value set; pinned equal so a future edit that
    # touches only one of the two homes is caught here.
    assert tuple(WEEKDAY_KERNWO) == trips.WEEKDAY_DIARY_KERNWO


def test_weekday_diary_leg_mask_keeps_weekday_non_rbw_legs_only():
    wege = pd.DataFrame({"kernwo": [1, 2, 3, 4, 7, 2], "W_RBW": [0, 0, 1, 0, 0, 0]})
    mask = trips.weekday_diary_leg_mask(wege)
    assert list(mask) == [True, True, False, False, False, True]


def test_weekday_diary_leg_mask_raises_when_a_universe_column_is_missing():
    """The universe must never be applied silently to a frame that cannot express it:
    a missing kernwo would otherwise read as "every leg is a weekday leg"."""
    with pytest.raises(ValueError, match="kernwo"):
        trips.weekday_diary_leg_mask(pd.DataFrame({"W_RBW": [0]}))
    with pytest.raises(ValueError, match="W_RBW"):
        trips.weekday_diary_leg_mask(pd.DataFrame({"kernwo": [1]}))


def test_weekday_diary_leg_mask_coerces_a_text_kernwo_column():
    """A CSV delivery may hand kernwo over as text; the codes must still be recognised
    (and an uncoercible value counts as non-weekday rather than raising)."""
    wege = pd.DataFrame({"kernwo": ["1", "6", "keine Angabe"], "W_RBW": [0, 0, 0]})
    assert list(trips.weekday_diary_leg_mask(wege)) == [True, False, False]


def test_restrict_to_weekday_diary_legs_logs_the_rate_and_raises_on_an_empty_result(caplog):
    import logging

    wege = pd.DataFrame({"kernwo": [1, 6, 2], "W_RBW": [0, 0, 1], "x": [1, 2, 3]})
    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.trips"):
        kept = trips.restrict_to_weekday_diary_legs(wege, log_tag="[t]")
    assert list(kept["x"]) == [1]
    assert "1/3" in caplog.text and "1 non-weekday" in caplog.text and "1 rbW" in caplog.text
    with pytest.raises(ValueError, match="weekday diary universe"):
        trips.restrict_to_weekday_diary_legs(wege[wege["kernwo"] == 6], log_tag="[t]")


def test_restrict_to_weekday_diary_legs_drop_reasons_partition_the_dropped_legs(caplog):
    """The two reported reasons must add up with the kept count to the total, so the log
    line can be read as a partition instead of two overlapping counts."""
    import logging

    wege = pd.DataFrame({"kernwo": [2, 2, 6, 6, 3], "W_RBW": [0, 1, 0, 1, 0]})
    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.trips"):
        kept = trips.restrict_to_weekday_diary_legs(wege, log_tag="[t]")
    # kept 2 (the two weekday non-rbW legs), 2 non-weekday (kernwo 6, rbW or not), 1 rbW.
    assert len(kept) == 2
    assert "kept 2/5" in caplog.text
    assert "2 non-weekday" in caplog.text and "1 rbW" in caplog.text
