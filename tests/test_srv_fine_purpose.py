"""Unit tests for the SrV 2023 fine-purpose reference and the MiD-vs-SrV comparison (#242 Task 6).

Synthetic SrV-shaped frames only -- the raw scientific-use microdata is local-only and must
never be read by a test. The base frame carries one trip per measured ``V_ZWECK`` fine code plus
two out-of-scope trips (a home trip and a work trip), so every test can reason about exact
counts and exact weighted shares.

The tests cover the three properties the task brief names for the pure builder (shares within a
coarse purpose sum to 1; GIS-invalid rows are excluded from the percentiles but still counted; a
fine code outside the map raises) plus the universe guards and the comparison join.
"""
import numpy as np
import pandas as pd
import pytest

from braunschweig.calibration import srv_fine_purpose as F
from scripts.compare_purpose_subtypes_srv import build_comparison


def _trips(**overrides) -> pd.DataFrame:
    """One trip per measured fine code (weight 1.0, 1 km, 10 min) plus two out-of-scope trips."""
    fine_codes = sorted(F.COARSE_BY_FINE)
    out_of_scope = [19, 1]
    codes = fine_codes + out_of_scope
    frame = pd.DataFrame({
        "HHNR": range(1, len(codes) + 1),
        "PNR": [1] * len(codes),
        "WNR": range(1, len(codes) + 1),
        "V_ZWECK": codes,
        "GEWICHT_W_ZENSUS": [1.0] * len(codes),
        "GIS_LAENGE_GUELTIG": [1.0] * len(codes),
        "E_DAUER": [10] * len(codes),
        "MITTL_WERKTAG": [F.AVERAGE_WEEKDAY] * len(codes),
    })
    for column, values in overrides.items():
        frame[column] = values
    return frame


def _row(table: pd.DataFrame, fine_code: int) -> pd.Series:
    return table[table["fine_code"] == fine_code].iloc[0]


# --------------------------------------------------------------------------- code maps


def test_every_measured_fine_code_has_a_codebook_label():
    assert sorted(F.FINE_LABELS) == sorted(F.COARSE_BY_FINE)
    assert all(isinstance(label, str) and label for label in F.FINE_LABELS.values())


def test_measured_and_out_of_scope_codes_do_not_overlap():
    assert not set(F.COARSE_BY_FINE) & set(F.OUT_OF_SCOPE_V_ZWECK)


def test_subtype_map_only_references_measured_fine_codes():
    for group, (codes, exactness) in F.SUBTYPE_TO_SRV_FINE.items():
        assert exactness in F.EXACTNESS_VALUES, group
        assert set(codes) <= set(F.COARSE_BY_FINE), group
        # Every mapped group must stay inside ONE coarse purpose; a group spanning two coarse
        # purposes would make share_srv a share of two different denominators at once.
        assert len({F.coarse_of(code) for code in codes}) <= 1, group


def test_complementary_group_pairs_carry_the_same_exactness_grade():
    """Ruling C-R16: where a purpose has exactly two groups and both are mapped, the two shares
    are complements, so ``delta_a == -delta_b`` identically. One grade must then cover both --
    otherwise the same number would be trustworthy under one name and not under the other."""
    by_purpose = {}
    for group, (codes, exactness) in F.SUBTYPE_TO_SRV_FINE.items():
        if codes:
            by_purpose.setdefault(F.coarse_of(codes[0]), []).append((group, exactness))
    for purpose, entries in by_purpose.items():
        if len(entries) == 2:
            grades = {exactness for _, exactness in entries}
            assert len(grades) == 1, (purpose, entries)


def test_other_errand_pair_is_graded_approximate():
    """Pinned separately from the structural rule above because the LABELS also overlap: MiD
    W_ZWD 602 "Behoerde, Bank, Post" feeds other_errand_short while SrV 11
    "Dienstleistungseinrichtung (z. B. Post, Bank, ...)" feeds other_errand_long."""
    assert F.SUBTYPE_TO_SRV_FINE["other_errand_short"] == ((10,), "approximate")
    assert F.SUBTYPE_TO_SRV_FINE["other_errand_long"] == ((11,), "approximate")


def test_coarse_of_and_label_of_raise_for_an_unmapped_code():
    with pytest.raises(ValueError, match="12"):
        F.coarse_of(12)
    with pytest.raises(ValueError, match="12"):
        F.label_of(12)


# --------------------------------------------------------------------------- builder


def test_shares_within_each_coarse_purpose_sum_to_one():
    table, _ = F.build_fine_purpose_reference(_trips())
    sums = table.groupby("coarse")["share_within_coarse"].sum()
    for coarse in F.COARSE_PURPOSES:
        assert sums[coarse] == pytest.approx(1.0)


def test_every_measured_fine_code_gets_a_row_in_a_stable_order():
    table, _ = F.build_fine_purpose_reference(_trips())
    assert list(table.columns) == F.REFERENCE_COLUMNS
    assert list(table["coarse"]) == [F.coarse_of(code) for code in table["fine_code"]]
    assert list(table["fine_code"]) == [8, 9, 10, 11, 13, 14, 15, 16, 17, 18]
    assert list(table["label"]) == [F.FINE_LABELS[code] for code in table["fine_code"]]


def test_shares_use_the_trip_weight_not_the_row_count():
    """Doubling one shop code's GEWICHT_W_ZENSUS must move the share, not just the count."""
    weights = [1.0] * 12
    weights[0] = 3.0  # fine code 8 (first row of the frame)
    table, _ = F.build_fine_purpose_reference(_trips(GEWICHT_W_ZENSUS=weights))
    assert _row(table, 8)["share_within_coarse"] == pytest.approx(0.75)
    assert _row(table, 9)["share_within_coarse"] == pytest.approx(0.25)
    assert int(_row(table, 8)["n_unweighted"]) == 1


def test_empty_fine_code_is_emitted_rather_than_dropped():
    """A fine code with no trip inside a non-empty coarse purpose has a share of exactly 0 -- a
    real measurement, not a missing value -- while its percentiles are NaN (undefined, never 0)."""
    trips = _trips()
    table, _ = F.build_fine_purpose_reference(trips[trips["V_ZWECK"] != 13])
    row = _row(table, 13)
    assert int(row["n_unweighted"]) == 0
    assert row["share_within_coarse"] == pytest.approx(0.0)
    assert np.isnan(row["gis_km_p50"]) and np.isnan(row["duration_min_p50"])


def test_an_entirely_empty_coarse_purpose_yields_nan_shares_not_zero():
    trips = _trips()
    table, _ = F.build_fine_purpose_reference(trips[~trips["V_ZWECK"].isin([8, 9])])
    shop = table[table["coarse"] == "shop"]
    assert list(shop["n_unweighted"]) == [0, 0]
    assert shop["share_within_coarse"].isna().all()


def test_gis_invalid_rows_are_excluded_from_the_percentiles_but_still_counted():
    """Fine code 8 gets a second trip whose GIS length is the -7 'not computable' code: the
    percentiles must be computed from the ONE valid 5 km trip, while n_unweighted counts both
    and the diagnostics report the invalid trip."""
    trips = _trips()
    extra = trips[trips["V_ZWECK"] == 8].copy()
    extra["GIS_LAENGE_GUELTIG"] = F.GIS_INVALID_CODE
    trips.loc[trips["V_ZWECK"] == 8, "GIS_LAENGE_GUELTIG"] = 5.0
    table, diagnostics = F.build_fine_purpose_reference(pd.concat([trips, extra], ignore_index=True))
    row = _row(table, 8)
    assert int(row["n_unweighted"]) == 2
    assert row["gis_km_p25"] == pytest.approx(5.0)
    assert row["gis_km_p50"] == pytest.approx(5.0)
    assert row["gis_km_p75"] == pytest.approx(5.0)
    assert diagnostics["n_trips_gis_invalid"] == 1
    # The share still uses BOTH trips: only the distance is unknown, not the trip.
    assert row["share_within_coarse"] == pytest.approx(2.0 / 3.0)


def test_a_fine_code_with_no_valid_gis_length_yields_nan_percentiles_not_zero():
    trips = _trips()
    trips.loc[trips["V_ZWECK"] == 9, "GIS_LAENGE_GUELTIG"] = F.GIS_INVALID_CODE
    table, _ = F.build_fine_purpose_reference(trips)
    row = _row(table, 9)
    assert int(row["n_unweighted"]) == 1
    assert np.isnan(row["gis_km_p25"]) and np.isnan(row["gis_km_p50"]) and np.isnan(row["gis_km_p75"])


def test_invalid_duration_rows_are_excluded_from_the_median_but_still_counted():
    trips = _trips()
    extra = trips[trips["V_ZWECK"] == 10].copy()
    extra["E_DAUER"] = F.DURATION_INVALID_CODES[0]
    trips.loc[trips["V_ZWECK"] == 10, "E_DAUER"] = 42
    table, diagnostics = F.build_fine_purpose_reference(pd.concat([trips, extra], ignore_index=True))
    row = _row(table, 10)
    assert int(row["n_unweighted"]) == 2
    assert row["duration_min_p50"] == pytest.approx(42.0)
    assert diagnostics["n_trips_duration_invalid"] == 1


def test_out_of_scope_purposes_are_dropped_and_reported_as_a_rate(caplog):
    """No-silent-fallback rule (CLAUDE.md, MANDATORY): the dropped rows must be logged as
    n / total (rate), never as a bare count. 2 of 12 trips are out of scope (home, work)."""
    caplog.set_level("INFO")
    table, diagnostics = F.build_fine_purpose_reference(_trips())
    assert diagnostics["n_trips_raw"] == 12
    assert diagnostics["n_trips_out_of_scope"] == 2
    assert diagnostics["n_trips_measured"] == 10
    assert int(table["n_unweighted"].sum()) == 10
    messages = [record.getMessage() for record in caplog.records]
    assert any("2/12" in message and "16.67%" in message for message in messages), messages


def test_an_unmapped_v_zweck_code_raises():
    """A fine code that is in neither COARSE_BY_FINE nor OUT_OF_SCOPE_V_ZWECK must raise rather
    than land in a silent bucket -- the same guard idea as purpose_subtype.code_coverage_guard."""
    trips = _trips()
    trips.loc[0, "V_ZWECK"] = 99
    with pytest.raises(ValueError, match="99"):
        F.build_fine_purpose_reference(trips)


def test_raises_when_the_delivery_is_not_the_average_weekday_universe():
    trips = _trips()
    trips.loc[0, "MITTL_WERKTAG"] = 2
    with pytest.raises(ValueError, match="MITTL_WERKTAG"):
        F.build_fine_purpose_reference(trips)


def test_raises_on_a_non_positive_trip_weight():
    trips = _trips()
    trips.loc[0, "GEWICHT_W_ZENSUS"] = 0.0
    with pytest.raises(ValueError, match="GEWICHT_W_ZENSUS"):
        F.build_fine_purpose_reference(trips)


def test_raises_on_a_missing_required_column():
    trips = _trips().drop(columns=["E_DAUER"])
    with pytest.raises(ValueError, match="E_DAUER"):
        F.build_fine_purpose_reference(trips)


def test_check_invariants_rejects_a_share_outside_the_unit_interval():
    table, _ = F.build_fine_purpose_reference(_trips())
    F.check_invariants(table)
    broken = table.copy()
    broken.loc[0, "share_within_coarse"] = 1.5
    with pytest.raises(ValueError, match="share_within_coarse"):
        F.check_invariants(broken)


# --------------------------------------------------------------------------- comparison join


def _srv_reference() -> pd.DataFrame:
    """Minimal SrV reference: shop 60/40, other_errand 30/70, leisure 10/20/30/25/10/5."""
    shares = {8: 0.6, 9: 0.4, 10: 0.3, 11: 0.7,
              13: 0.10, 14: 0.20, 15: 0.30, 16: 0.25, 17: 0.10, 18: 0.05}
    return pd.DataFrame({
        "coarse": [F.coarse_of(code) for code in shares],
        "fine_code": list(shares),
        "label": [F.FINE_LABELS[code] for code in shares],
        "n_unweighted": [100] * len(shares),
        "share_within_coarse": list(shares.values()),
        "gis_km_p25": [1.0] * len(shares),
        "gis_km_p50": [float(code) for code in shares],
        "gis_km_p75": [9.0] * len(shares),
        "duration_min_p50": [10.0] * len(shares),
    })


def _mid_reference() -> pd.DataFrame:
    """Two-variant MiD reference. Leisure is asymmetric on both sides in the SrV fixture (code 18
    unmapped) and here (leisure_excursion unmapped); the 'codeplan' variant moves leisure_visit
    just below the raw threshold while the comparable delta stays above it."""
    default = {"shop_daily": 0.8, "shop_non_daily": 0.2,
               "other_errand_short": 0.4, "other_errand_long": 0.6,
               "leisure_local": 0.30, "leisure_visit": 0.35,
               "leisure_activity": 0.25, "leisure_excursion": 0.10}
    codeplan = dict(default, leisure_visit=0.33, leisure_activity=0.27)
    purposes = {"shop_daily": "shop", "shop_non_daily": "shop",
                "other_errand_short": "other_errand", "other_errand_long": "other_errand",
                "leisure_local": "leisure", "leisure_visit": "leisure",
                "leisure_activity": "leisure", "leisure_excursion": "leisure"}
    rows = []
    for variant, shares in (("default", default), ("codeplan", codeplan)):
        for group, share in shares.items():
            rows.append({"purpose": purposes[group], "spec_variant": variant, "group": group,
                         "n_unweighted": 50, "share_within_purpose": share, "km_p25": 1.0,
                         "km_p50": 2.0, "km_p75": 9.0, "n_missing_distance": 0})
    return pd.DataFrame(rows)


def test_comparison_sums_the_srv_shares_of_a_multi_code_group():
    comparison = build_comparison(_srv_reference(), _mid_reference())
    local = comparison[comparison["subtype_group"] == "leisure_local"].iloc[0]
    assert local["share_srv"] == pytest.approx(0.20 + 0.25)     # fine codes 14 + 16
    assert local["exactness"] == "approximate"
    assert local["srv_fine_codes"] == "14|16"
    # A pooled median over several fine codes cannot be recomputed from committed percentile
    # rows, so the cell stays empty and the per-code medians are carried instead.
    assert np.isnan(local["median_km_srv"])
    assert local["median_km_srv_components"] == "14:14.000|16:16.000"


def test_comparison_flags_only_exact_groups_beyond_the_threshold():
    comparison = build_comparison(_srv_reference(), _mid_reference())
    default = comparison[comparison["spec_variant"] == "default"].set_index("subtype_group")
    # shop_daily: MiD 0.80 vs SrV 0.60 -> +20.0 pp; shop is symmetric, so the comparable delta is
    # the raw one and the exact group is flagged.
    assert default.loc["shop_daily", "delta_pp"] == pytest.approx(20.0)
    assert bool(default.loc["shop_daily", "candidate_for_reestimation"])
    # leisure_visit: MiD 0.35 vs SrV 0.30 -> +5.0 pp raw, exact but below the threshold on the
    # comparable universe too (+7.3 pp default, +5.1 pp codeplan).
    assert default.loc["leisure_visit", "delta_pp"] == pytest.approx(5.0)
    assert not bool(default.loc["leisure_visit", "candidate_for_reestimation"])
    # other_errand_short/long: +10.0 / -10.0 pp, at the threshold and only "approximate"
    # (ruling C-R16), so neither is flagged.
    assert default.loc["other_errand_short", "delta_pp"] == pytest.approx(10.0)
    assert default.loc["other_errand_long", "delta_pp"] == pytest.approx(-10.0)
    assert not bool(default.loc["other_errand_short", "candidate_for_reestimation"])
    assert not bool(default.loc["other_errand_long", "candidate_for_reestimation"])


def test_comparison_leaves_an_aggregate_only_group_unflagged_with_no_srv_share():
    comparison = build_comparison(_srv_reference(), _mid_reference())
    default = comparison[comparison["spec_variant"] == "default"].set_index("subtype_group")
    row = default.loc["leisure_excursion"]
    assert row["exactness"] == "aggregate_only"
    assert np.isnan(row["share_srv"]) and np.isnan(row["delta_pp"])
    # EMPTY, not 0: SrV does not code the activity separately at all.
    assert np.isnan(row["n_srv_unweighted"])
    assert np.isnan(row["share_mid_renormalised"]) and np.isnan(row["delta_pp_renormalised"])
    assert np.isnan(row["delta_pp_comparable"])
    assert not bool(row["candidate_for_reestimation"])
    assert row["candidate_variants"] == ""


def test_renormalised_columns_stay_empty_where_the_crosswalk_covers_the_whole_mass():
    """shop and other_errand map every group on both sides, so renormalising is the identity and
    a filled column would only invite reading the same number twice."""
    comparison = build_comparison(_srv_reference(), _mid_reference())
    symmetric = comparison[comparison["purpose"].isin(["shop", "other_errand"])]
    assert symmetric["share_mid_renormalised"].isna().all()
    assert symmetric["share_srv_renormalised"].isna().all()
    assert symmetric["delta_pp_renormalised"].isna().all()


def test_renormalised_columns_divide_each_side_by_its_own_mapped_mass():
    """leisure is asymmetric: MiD maps 0.90 of its mass (leisure_excursion is unmapped), SrV maps
    0.95 (fine code 18 is unmapped). Each side must be divided by ITS OWN mapped mass."""
    comparison = build_comparison(_srv_reference(), _mid_reference())
    default = comparison[comparison["spec_variant"] == "default"].set_index("subtype_group")
    row = default.loc["leisure_visit"]
    assert row["share_mid_renormalised"] == pytest.approx(0.35 / 0.90)
    assert row["share_srv_renormalised"] == pytest.approx(0.30 / 0.95)
    assert row["delta_pp_renormalised"] == pytest.approx(100.0 * (0.35 / 0.90 - 0.30 / 0.95))
    mapped = default[default["purpose"].eq("leisure") & default["srv_fine_codes"].astype(bool)]
    assert mapped["share_mid_renormalised"].sum() == pytest.approx(1.0)
    assert mapped["share_srv_renormalised"].sum() == pytest.approx(1.0)


def test_comparable_mass_excludes_only_non_comparable_grades():
    comparison = build_comparison(_srv_reference(), _mid_reference())
    default = comparison[comparison["spec_variant"] == "default"].set_index("subtype_group")
    # MiD comparable mass = 0.30 + 0.35 + 0.25 = 0.90 (excursion is aggregate_only); SrV = 0.95.
    assert default.loc["leisure_visit", "share_mid_renormalised"] == pytest.approx(0.35 / 0.90)
    assert default.loc["leisure_visit", "share_srv_renormalised"] == pytest.approx(0.30 / 0.95)
    assert np.isnan(default.loc["leisure_excursion", "share_mid_renormalised"])
    assert np.isnan(default.loc["shop_daily", "share_mid_renormalised"])   # symmetric: identity


def test_delta_pp_comparable_is_the_renormalised_delta_where_filled_else_raw():
    comparison = build_comparison(_srv_reference(), _mid_reference())
    default = comparison[comparison["spec_variant"] == "default"].set_index("subtype_group")
    assert default.loc["leisure_visit", "delta_pp_comparable"] == \
        pytest.approx(default.loc["leisure_visit", "delta_pp_renormalised"])
    assert default.loc["shop_daily", "delta_pp_comparable"] == \
        pytest.approx(default.loc["shop_daily", "delta_pp"])
    assert np.isnan(default.loc["leisure_excursion", "delta_pp_comparable"])


def test_candidate_flag_is_group_level_across_variants():
    """With SrV visit 0.20 and the unmapped code 18 at 0.15 (SrV comparable mass 0.85, so
    share_srv_renormalised = 0.2353), the two variants land on opposite sides of the 10 pp line:
    default 0.35/0.90 - 0.2353 = +15.4 pp, codeplan 0.25/0.90 - 0.2353 = +4.2 pp. The group is a
    candidate because ONE variant crosses, and both of its rows say so."""
    srv = _srv_reference()
    srv.loc[srv["fine_code"] == 15, "share_within_coarse"] = 0.20
    srv.loc[srv["fine_code"] == 18, "share_within_coarse"] = 0.15
    mid = _mid_reference()
    mid.loc[(mid["spec_variant"] == "codeplan") & (mid["group"] == "leisure_visit"),
            "share_within_purpose"] = 0.25
    mid.loc[(mid["spec_variant"] == "codeplan") & (mid["group"] == "leisure_activity"),
            "share_within_purpose"] = 0.35
    comparison = build_comparison(srv, mid)
    visit = comparison[comparison["subtype_group"] == "leisure_visit"].set_index("spec_variant")
    assert visit.loc["default", "delta_pp_comparable"] > 10.0
    assert visit.loc["codeplan", "delta_pp_comparable"] < 10.0
    # group-level: BOTH rows flagged, and the crossing variant is named on both.
    assert bool(visit.loc["default", "candidate_for_reestimation"])
    assert bool(visit.loc["codeplan", "candidate_for_reestimation"])
    assert visit.loc["default", "candidate_variants"] == "default"
    assert visit.loc["codeplan", "candidate_variants"] == "default"


def test_only_exact_groups_can_be_candidates_on_the_comparable_delta():
    comparison = build_comparison(_srv_reference(), _mid_reference())
    approx = comparison[comparison["exactness"] != "exact"]
    assert not approx["candidate_for_reestimation"].any()
    assert (approx["candidate_variants"] == "").all()


def test_flag_follows_the_comparable_delta_not_the_raw_one():
    """The flag reads the comparable delta, not the raw one (owner decision 2026-09-10). SrV
    leisure visit 0.27 with 16 at 0.13 and the unmapped 18 at 0.15 (comparable mass 0.80); MiD
    excursion 0.30 with local 0.10 (comparable mass 0.70). Raw: 0.35 - 0.27 = +8.0 pp, below the
    threshold. Comparable: 0.35/0.70 - 0.27/0.80 = 0.5000 - 0.3375 = +16.25 pp, above it."""
    srv = _srv_reference()
    srv.loc[srv["fine_code"] == 15, "share_within_coarse"] = 0.27
    srv.loc[srv["fine_code"] == 16, "share_within_coarse"] = 0.13
    srv.loc[srv["fine_code"] == 18, "share_within_coarse"] = 0.15
    mid = _mid_reference()
    mid.loc[mid["group"] == "leisure_excursion", "share_within_purpose"] = 0.30
    mid.loc[mid["group"] == "leisure_local", "share_within_purpose"] = 0.10
    comparison = build_comparison(srv, mid)
    visit = comparison[(comparison["subtype_group"] == "leisure_visit")
                       & (comparison["spec_variant"] == "default")].iloc[0]
    assert abs(visit["delta_pp"]) < 10.0
    assert visit["delta_pp_comparable"] > 10.0
    assert bool(visit["candidate_for_reestimation"])


def test_comparison_covers_every_subtype_group_and_variant():
    comparison = build_comparison(_srv_reference(), _mid_reference())
    assert len(comparison) == 2 * len(F.SUBTYPE_TO_SRV_FINE)
    assert set(comparison["subtype_group"]) == set(F.SUBTYPE_TO_SRV_FINE)
    assert set(comparison["spec_variant"]) == {"default", "codeplan"}


def test_comparison_raises_when_a_subtype_group_is_missing_from_the_mid_reference():
    mid = _mid_reference()
    with pytest.raises(ValueError, match="leisure_visit"):
        build_comparison(_srv_reference(), mid[mid["group"] != "leisure_visit"])
