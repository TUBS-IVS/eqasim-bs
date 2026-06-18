import pandas as pd
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
