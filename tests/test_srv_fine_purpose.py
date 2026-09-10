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
from scripts.compare_purpose_subtypes_srv import _render_sensitivity_section, build_comparison


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


#: MiD purpose of every subtype group the fixtures below measure.
_MID_PURPOSES = {"shop_daily": "shop", "shop_non_daily": "shop",
                 "other_errand_short": "other_errand", "other_errand_long": "other_errand",
                 "leisure_local": "leisure", "leisure_visit": "leisure",
                 "leisure_activity": "leisure", "leisure_excursion": "leisure",
                 "leisure_unspecified": "leisure"}


def _mid_rows(variant: str, shares: dict) -> list:
    """One MiD-reference row per (variant, group), with constant counts and percentiles."""
    return [{"purpose": _MID_PURPOSES[group], "spec_variant": variant, "group": group,
             "n_unweighted": 50, "share_within_purpose": share, "km_p25": 1.0,
             "km_p50": 2.0, "km_p75": 9.0, "n_missing_distance": 0}
            for group, share in shares.items()]


def _mid_reference() -> pd.DataFrame:
    """Three-variant MiD reference. Leisure is asymmetric on both sides in the SrV fixture (code
    18 is residual) and here (leisure_excursion unmapped); the 'codeplan' variant moves
    leisure_visit just below the raw threshold while the comparable delta stays above it, and
    'codeplan_unspecified' adds the fifth, W_ZWECK-defined leisure group `leisure_unspecified`
    that only that variant's spec defines.

    The third variant's leisure shares are deliberately NOT proportional to the codeplan ones, so
    the brief's arithmetic (comparable MiD mass 0.20 + 0.23 + 0.17 = 0.60) stays readable; the
    proportionality the REAL extraction produces is pinned separately by
    :func:`test_the_residual_group_leaves_the_named_groups_comparable_shares_unchanged`.
    """
    default = {"shop_daily": 0.8, "shop_non_daily": 0.2,
               "other_errand_short": 0.4, "other_errand_long": 0.6,
               "leisure_local": 0.30, "leisure_visit": 0.35,
               "leisure_activity": 0.25, "leisure_excursion": 0.10}
    codeplan = dict(default, leisure_visit=0.33, leisure_activity=0.27)
    codeplan_unspecified = dict(codeplan, leisure_local=0.20, leisure_visit=0.23,
                                leisure_activity=0.17, leisure_excursion=0.07,
                                leisure_unspecified=0.33)
    rows = []
    for variant, shares in (("default", default), ("codeplan", codeplan),
                            ("codeplan_unspecified", codeplan_unspecified)):
        rows.extend(_mid_rows(variant, shares))
    return pd.DataFrame(rows)


def _mid_reference_without_the_residual_variant() -> pd.DataFrame:
    """The two W_ZWD-only variants of :func:`_mid_reference`.

    Used by the tests whose documented arithmetic pins the raw-vs-comparable reading of exactly
    ONE pair of variants: the third variant carries a different leisure mix, so leaving it in
    would add a second crossing variant and the pinned numbers would describe only part of the
    result. A residual group absent from EVERY variant is legitimate in its own right (it is
    emitted only where a spec defines it), so this is also a valid input.
    """
    mid = _mid_reference()
    return mid[mid["spec_variant"] != "codeplan_unspecified"].reset_index(drop=True)


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
    mid = _mid_reference_without_the_residual_variant()
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
    mid = _mid_reference_without_the_residual_variant()
    mid.loc[mid["group"] == "leisure_excursion", "share_within_purpose"] = 0.30
    mid.loc[mid["group"] == "leisure_local", "share_within_purpose"] = 0.10
    comparison = build_comparison(srv, mid)
    visit = comparison[(comparison["subtype_group"] == "leisure_visit")
                       & (comparison["spec_variant"] == "default")].iloc[0]
    assert abs(visit["delta_pp"]) < 10.0
    assert visit["delta_pp_comparable"] > 10.0
    assert bool(visit["candidate_for_reestimation"])


def test_a_comparable_grade_without_srv_codes_raises_instead_of_renormalising_to_nan(monkeypatch):
    """A grade in COMPARABLE_EXACTNESS must map to at least one SrV fine code. Otherwise its
    share_srv is NaN, which a plain sum would SKIP: the block would renormalise against a
    denominator that omits the group, while the group's own delta_pp_comparable fell back to the
    raw delta. That is a crosswalk defect and must raise, naming the purpose and the group."""
    broken = dict(F.SUBTYPE_TO_SRV_FINE, leisure_activity=((), "approximate"))
    monkeypatch.setattr(F, "SUBTYPE_TO_SRV_FINE", broken)
    with pytest.raises(ValueError, match="leisure_activity"):
        build_comparison(_srv_reference(), _mid_reference())


def test_the_crossing_list_reports_a_row_the_raw_reading_would_have_flagged():
    """The two readings can disagree in BOTH directions, so the crossing list must be the XOR of
    the two threshold tests. Here SrV codes a large unmapped residual (18 = 0.30, comparable mass
    0.70) while MiD maps 0.90: leisure_visit is 0.40 - 0.25 = +15.0 pp raw (above the threshold)
    but 0.40/0.90 - 0.25/0.70 = +8.7 pp comparable (below), so the flag does NOT fire and the
    section must list the row rather than claim that no row changes side."""
    srv = _srv_reference()
    for code, share in ((13, 0.05), (14, 0.15), (15, 0.25), (16, 0.15), (17, 0.10), (18, 0.30)):
        srv.loc[srv["fine_code"] == code, "share_within_coarse"] = share
    mid = _mid_reference_without_the_residual_variant()
    mid.loc[mid["group"] == "leisure_visit", "share_within_purpose"] = 0.40
    mid.loc[mid["group"] == "leisure_local", "share_within_purpose"] = 0.25
    comparison = build_comparison(srv, mid)
    visit = comparison[(comparison["subtype_group"] == "leisure_visit")
                       & (comparison["spec_variant"] == "default")].iloc[0]
    assert abs(visit["delta_pp"]) > F.CANDIDATE_DELTA_PP_THRESHOLD
    assert abs(visit["delta_pp_comparable"]) < F.CANDIDATE_DELTA_PP_THRESHOLD
    assert not bool(visit["candidate_for_reestimation"])
    section = "\n".join(_render_sensitivity_section(comparison))
    assert "No row changes side" not in section
    assert "* `leisure_visit` (default): raw +15.0 pp (above) -> comparable +8.7 pp (below)." \
        in section


def test_the_crossing_list_is_empty_when_both_readings_agree():
    """Base fixture: leisure_visit is below the threshold on both readings (+5.0 pp raw, +7.3 pp
    comparable), and the approximate rows never enter the list."""
    section = "\n".join(_render_sensitivity_section(build_comparison(_srv_reference(),
                                                                     _mid_reference())))
    assert "No row changes side of the 10 pp threshold between the two readings." in section


def test_comparison_covers_every_subtype_group_and_variant():
    mid = _mid_reference()
    comparison = build_comparison(_srv_reference(), mid)
    # One comparison row per MEASURED (variant, group) pair: the residual group exists only in
    # the variant whose spec defines it, so the count is the MiD reference's own row count
    # rather than variants x groups.
    assert len(comparison) == len(mid)
    assert set(comparison["subtype_group"]) == set(F.SUBTYPE_TO_SRV_FINE)
    assert set(comparison["spec_variant"]) == {"default", "codeplan", "codeplan_unspecified"}


def test_comparison_raises_when_a_subtype_group_is_missing_from_the_mid_reference():
    """Only a ``residual`` group may be absent from a variant (see the two tests below); every
    other grade must be measured in every variant, otherwise the comparison would silently omit
    it. `leisure_visit` is `exact`, so dropping it must still raise."""
    mid = _mid_reference()
    with pytest.raises(ValueError, match="leisure_visit"):
        build_comparison(_srv_reference(), mid[mid["group"] != "leisure_visit"])


# ------------------------------------------------------------------- residual grade (issue #373)


def test_residual_pair_is_reported_but_never_comparable_nor_a_candidate():
    comparison = build_comparison(_srv_reference(), _mid_reference())
    row = comparison[(comparison["subtype_group"] == "leisure_unspecified")].iloc[0]
    assert row["exactness"] == "residual" and row["srv_fine_codes"] == "18"
    assert row["share_srv"] == pytest.approx(0.05) and row["share_mid"] == pytest.approx(0.33)
    assert np.isfinite(row["delta_pp"])                       # reported
    assert np.isnan(row["share_mid_renormalised"])            # not part of the comparable mass
    assert not bool(row["candidate_for_reestimation"])
    third = comparison[comparison["spec_variant"] == "codeplan_unspecified"].set_index("subtype_group")
    # comparable MiD mass = 0.20 + 0.23 + 0.17 = 0.60 ; SrV = 0.95 (18 excluded)
    assert third.loc["leisure_visit", "share_mid_renormalised"] == pytest.approx(0.23 / 0.60)
    assert third.loc["leisure_visit", "share_srv_renormalised"] == pytest.approx(0.30 / 0.95)


def test_a_residual_row_carries_the_srv_counterparts_counts_and_median():
    """The pair is REPORTED in full -- share, unweighted n and median on both sides -- because
    the point of the grade is to show the two residuals side by side; only the comparable
    universe and the flag exclude it. `delta_pp_comparable` is EMPTY, not the raw delta: a row
    outside the comparable universe has no comparable reading."""
    comparison = build_comparison(_srv_reference(), _mid_reference())
    row = comparison[comparison["subtype_group"] == "leisure_unspecified"].iloc[0]
    assert int(row["n_srv_unweighted"]) == 100          # the SrV fixture's per-code row count
    assert int(row["n_mid_unweighted"]) == 50
    assert row["median_km_srv"] == pytest.approx(18.0)  # the fixture sets gis_km_p50 = fine code
    assert row["median_km_mid"] == pytest.approx(2.0)
    assert row["delta_pp"] == pytest.approx(100.0 * (0.33 - 0.05))
    assert np.isnan(row["share_srv_renormalised"]) and np.isnan(row["delta_pp_renormalised"])
    assert np.isnan(row["delta_pp_comparable"])
    assert row["candidate_variants"] == ""


def test_the_residual_row_is_excluded_from_the_comparable_mass_on_both_sides():
    """SrV code 18 was already outside the comparable mass while it was mapped to nothing, and
    the MiD residual group must be outside it too: the renormalised shares of the three
    comparable leisure groups must sum to 1 on each side even though a residual row exists."""
    comparison = build_comparison(_srv_reference(), _mid_reference())
    third = comparison[comparison["spec_variant"] == "codeplan_unspecified"]
    leisure = third[third["purpose"] == "leisure"]
    assert leisure["share_mid_renormalised"].sum() == pytest.approx(1.0)
    assert leisure["share_srv_renormalised"].sum() == pytest.approx(1.0)
    # The three summands are exactly the comparable rows; the residual and the aggregate_only
    # row contribute nothing because their renormalised shares are NaN.
    assert int(leisure["share_mid_renormalised"].notna().sum()) == 3
    assert set(leisure[leisure["share_mid_renormalised"].notna()]["exactness"]) \
        == set(F.COMPARABLE_EXACTNESS)


def test_every_subtype_group_of_the_crosswalk_has_a_row_in_every_variant():
    comparison = build_comparison(_srv_reference(), _mid_reference())
    for variant, block in comparison.groupby("spec_variant"):
        assert set(block["subtype_group"]) >= set(F.SUBTYPE_TO_SRV_FINE) - ({"leisure_unspecified"} if variant != "codeplan_unspecified" else set())


def test_a_residual_group_absent_from_a_variant_is_skipped_and_logged(caplog):
    """A residual group is emitted only where the MiD spec defines it, so its absence from the
    `default` / `codeplan` variants is legitimate -- but it must not be silent: the skipped
    (variant, group) pair is logged with its grade."""
    caplog.set_level("INFO", logger="compare_purpose_subtypes_srv")
    comparison = build_comparison(_srv_reference(), _mid_reference())
    for variant in ("default", "codeplan"):
        block = comparison[comparison["spec_variant"] == variant]
        assert set(block["subtype_group"]) == set(F.SUBTYPE_TO_SRV_FINE) - {"leisure_unspecified"}
    messages = [record.getMessage() for record in caplog.records]
    for variant in ("default", "codeplan"):
        assert any("leisure_unspecified" in message and variant in message
                   and "residual" in message for message in messages), messages


def test_a_missing_group_of_any_other_grade_still_raises():
    """The exemption is grade-specific, not "any group may be absent": dropping the
    `aggregate_only` group from ONE variant must still raise."""
    mid = _mid_reference()
    dropped = mid[~((mid["spec_variant"] == "default") & (mid["group"] == "leisure_excursion"))]
    with pytest.raises(ValueError, match="leisure_excursion"):
        build_comparison(_srv_reference(), dropped)


def test_the_residual_group_leaves_the_named_groups_comparable_shares_unchanged():
    """On the real data the third variant differs from `codeplan` ONLY by a larger leisure
    denominator (the labelled W_ZWECK 7 legs plus all W_ZWECK 10 legs): the four W_ZWD groups
    keep exactly their legs, so their raw shares all shrink by ONE common factor and their
    COMPARABLE shares are therefore identical to the codeplan variant's. Pinned on a fixture
    built that way -- the module fixture is deliberately not proportional (see its docstring)."""
    mid = _mid_reference()
    unspecified_share = 0.25
    proportional = mid[mid["spec_variant"] == "codeplan"].copy()
    proportional["spec_variant"] = "codeplan_unspecified"
    proportional.loc[proportional["purpose"] == "leisure", "share_within_purpose"] *= \
        1.0 - unspecified_share
    proportional = pd.concat(
        [proportional,
         pd.DataFrame(_mid_rows("codeplan_unspecified",
                                {"leisure_unspecified": unspecified_share}))],
        ignore_index=True)
    rebuilt = pd.concat([mid[mid["spec_variant"] != "codeplan_unspecified"], proportional],
                        ignore_index=True)
    comparison = build_comparison(_srv_reference(), rebuilt)
    codeplan = comparison[comparison["spec_variant"] == "codeplan"].set_index("subtype_group")
    third = comparison[comparison["spec_variant"] == "codeplan_unspecified"].set_index("subtype_group")
    for group in ("leisure_local", "leisure_visit", "leisure_activity"):
        assert third.loc[group, "share_mid"] < codeplan.loc[group, "share_mid"]      # raw shrinks
        assert third.loc[group, "share_mid_renormalised"] == \
            pytest.approx(codeplan.loc[group, "share_mid_renormalised"])              # comparable does not
        assert third.loc[group, "delta_pp_comparable"] == \
            pytest.approx(codeplan.loc[group, "delta_pp_comparable"])
    # leisure_excursion is aggregate_only: it has no comparable reading under either variant.
    assert np.isnan(third.loc["leisure_excursion", "share_mid_renormalised"])
    assert np.isnan(codeplan.loc["leisure_excursion", "share_mid_renormalised"])
