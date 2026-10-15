import pandas as pd
import pytest

from braunschweig.popsim import zensus_employment_age as za


def test_age_shares_sum_to_one_and_use_national_for_landkreis(tmp_path):
    ref = tmp_path / "ref.csv"
    pd.DataFrame([
        # region, age_band, total, erwerbstaetige, rate
        # 03102: 20-29->16_29(70), 30-39->30_39(80), 40-49->40_49(50), 50-59->50_59(20), 60-69->60plus(30)
        ("03102", "20-29", 100, 70, 0.70),
        ("03102", "30-39", 100, 80, 0.80),
        ("03102", "40-49", 100, 50, 0.50),
        ("03102", "50-59", 100, 20, 0.20),
        ("03102", "60-69", 100, 30, 0.30),
        # DE_large_gemeinden: 20-29->16_29(73), 30-39->30_39(81), 60-69->60plus(44)
        ("DE_large_gemeinden", "20-29", 100, 73, 0.73),
        ("DE_large_gemeinden", "30-39", 100, 81, 0.81),
        ("DE_large_gemeinden", "60-69", 100, 44, 0.44),
    ], columns=["region", "age_band", "total", "erwerbstaetige", "rate"]).to_csv(ref, index=False)
    sz = za.load_age_shares(str(ref), "03102")
    # 03102: groups 16_29=70, 30_39=80, 40_49=50, 50_59=20, 60plus=30 -> total 250
    total_03102 = 70 + 80 + 50 + 20 + 30
    assert round(sz["16_29"] + sz["30_39"] + sz["40_49"] + sz["50_59"] + sz["60plus"], 6) == 1.0
    assert round(sz["16_29"], 4) == round(70 / total_03102, 4)
    assert round(sz["30_39"], 4) == round(80 / total_03102, 4)
    gf = za.load_age_shares(str(ref), "03151")           # not exact -> national fallback
    # DE_large_gemeinden has only 20-29->16_29(73), 30-39->30_39(81), 60-69->60plus(44)
    # 40_49 and 50_59 map to 0 (no rows for those bands)
    total_de = 73 + 81 + 44
    assert round(gf["30_39"], 4) == round(81 / total_de, 4)
    assert round(gf["16_29"] + gf["30_39"] + gf["40_49"] + gf["50_59"] + gf["60plus"], 6) == 1.0


# --- per Kreis and sex, from the Zensus 2022 Regionaltabelle (ADR-0137) ----------------------

def _kreis_reference(tmp_path, rows):
    """A reference CSV in the layout scripts/extract_zensus2022_employed_by_age.py writes."""
    path = tmp_path / "zensus2022_employed_by_age_kreis.csv"
    frame = pd.DataFrame(rows, columns=["region", "region_name", "age_class", "sex", "employed_persons"])
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write("# provenance line, skipped by the reader\n")
        frame.to_csv(handle, index=False)
    return str(path)


def _classes(region, name, sex, counts):
    return [(region, name, age_class, sex, count) for age_class, count in zip(za.KREIS_AGE_CLASSES, counts)]


def _gifhorn_and_germany():
    # 15-19, 20-29, 30-39, 40-49, 50-59, 60-67, 68+
    return (_classes("03151", "Gifhorn", "M", [10, 50, 80, 90, 120, 40, 10])
            + _classes("03151", "Gifhorn", "F", [10, 40, 70, 80, 110, 30, 10])
            + _classes("00", "Deutschland", "M", [5, 35, 45, 40, 50, 20, 5])
            + _classes("00", "Deutschland", "F", [5, 30, 40, 40, 50, 20, 5]))


def test_kreis_shares_are_exact_per_sex_and_merge_the_outer_classes(tmp_path):
    reference = _kreis_reference(tmp_path, _gifhorn_and_germany())

    shares, method = za.load_kreis_sex_age_shares(reference, "03151")

    assert method == za.SHAPE_EXACT
    men = 10 + 50 + 80 + 90 + 120 + 40 + 10
    assert shares["M"]["16_29"] == (10 + 50) / men       # 15-19 and 20-29 form 16_29
    assert shares["M"]["30_39"] == 80 / men
    assert shares["M"]["60plus"] == (40 + 10) / men      # 60-67 and 68+ form 60plus
    women = 10 + 40 + 70 + 80 + 110 + 30 + 10
    assert shares["F"]["50_59"] == 110 / women
    for sex in ("M", "F"):
        assert abs(sum(shares[sex].values()) - 1.0) < 1e-12
        assert list(shares[sex]) == [name for name, _lo, _hi in za.AGE_GROUPS]


def test_a_kreis_without_rows_takes_the_national_shape_and_says_so(tmp_path):
    reference = _kreis_reference(tmp_path, _gifhorn_and_germany())

    shares, method = za.load_kreis_sex_age_shares(reference, "03157")

    assert method == za.SHAPE_NATIONAL
    national = 5 + 35 + 45 + 40 + 50 + 20 + 5
    assert shares["M"]["40_49"] == 40 / national


def test_the_shape_comparison_reports_the_change_per_kreis_sex_and_group(tmp_path):
    """What ADR-0137 changes, per Kreis: the previous (sex-pooled) share next to the new share
    per sex, in percentage points."""
    legacy = tmp_path / "legacy.csv"
    pd.DataFrame([("DE_large_gemeinden", band, 100, count, count / 100)
                  for band, count in (("20-29", 20), ("30-39", 20), ("40-49", 20), ("50-59", 20),
                                      ("60-69", 20))],
                 columns=["region", "age_band", "total", "erwerbstaetige", "rate"]).to_csv(legacy, index=False)
    reference = _kreis_reference(tmp_path, _gifhorn_and_germany())

    table = za.compare_age_shapes(str(legacy), reference, ["03151"])

    assert list(table.columns) == ["kreis", "sex", "group", "previous_share_percent",
                                   "new_share_percent", "change_percentage_points", "new_method"]
    row = table[(table["sex"] == "M") & (table["group"] == "50_59")].iloc[0]
    assert row["previous_share_percent"] == pytest.approx(20.0)
    assert row["new_share_percent"] == pytest.approx(100 * 120 / 400)
    assert row["change_percentage_points"] == pytest.approx(100 * 120 / 400 - 20.0)
    assert set(table["new_method"]) == {za.SHAPE_EXACT}
    assert len(table) == 2 * 5


def test_no_national_row_either_is_an_error_not_a_guess(tmp_path):
    reference = _kreis_reference(tmp_path, _classes("03151", "Gifhorn", "M", [1, 1, 1, 1, 1, 1, 1])
                                 + _classes("03151", "Gifhorn", "F", [1, 1, 1, 1, 1, 1, 1]))
    with pytest.raises(ValueError, match="03157"):
        za.load_kreis_sex_age_shares(reference, "03157")
