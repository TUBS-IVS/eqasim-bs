import pandas as pd
from braunschweig.popsim import zensus_employment_age as za


def test_age_shares_sum_to_one_and_use_national_for_landkreis(tmp_path):
    ref = tmp_path / "ref.csv"
    pd.DataFrame([
        # region, age_band, total, erwerbstaetige, rate
        ("03102", "20-29", 100, 70, 0.70), ("03102", "30-39", 100, 80, 0.80), ("03102", "60-69", 100, 30, 0.30),
        ("DE_large_gemeinden", "20-29", 100, 73, 0.73), ("DE_large_gemeinden", "30-39", 100, 81, 0.81),
        ("DE_large_gemeinden", "60-69", 100, 44, 0.44),
    ], columns=["region", "age_band", "total", "erwerbstaetige", "rate"]).to_csv(ref, index=False)
    sz = za.load_age_shares(str(ref), "03102")           # 70 young / 80 prime / 30 old -> /180
    assert round(sz["young"] + sz["prime"] + sz["old"], 6) == 1.0
    assert round(sz["young"], 4) == round(70 / 180, 4)
    gf = za.load_age_shares(str(ref), "03151")           # not exact -> national 73/81/44
    assert round(gf["prime"], 4) == round(81 / 198, 4)
