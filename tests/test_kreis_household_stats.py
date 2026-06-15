import pandas as pd

from braunschweig.data.census import household_size as hs


def _write_fixture(tmp_path):
    rows = [
        ("031010000000", "PERSON01", "1 Person",            "",        "100"),
        ("031010000000", "PERSON02", "2 Personen",          "",        "50"),
        ("031010000000", "",         "Insgesamt",           "",        "150"),
        ("031020000000", "PERSON01", "1 Person",            "",        "40"),
        ("031020000000", "PERSON06", "6 und mehr Personen", "",        "10"),
    ]
    p = tmp_path / "5000H.csv"
    header = ("1_variable_attribute_code;2_variable_attribute_code;"
              "2_variable_attribute_label;3_variable_attribute_code;value\n")
    with open(p, "w", encoding="utf-8") as f:
        f.write(header)
        for ars12, sc, sl, tc, v in rows:
            f.write(f"{ars12};{sc};{sl};{tc};{v}\n")
    return str(p)


def test_kreis_household_stats_per_kreis(tmp_path):
    path = _write_fixture(tmp_path)
    out = hs.kreis_household_stats(path, ["03101", "03102"]).set_index("ars5")
    # 03101: 100x1P + 50x2P -> hh=150, persons=100*1+50*2=200, mean=200/150
    assert out.loc["03101", "hh_count"] == 150.0
    assert out.loc["03101", "mean_size"] == 200.0 / 150.0
    # 03102: 40x1P + 10x6+P(=6.5) -> hh=50, persons=40+65=105, mean=105/50
    assert out.loc["03102", "hh_count"] == 50.0
    assert out.loc["03102", "mean_size"] == 105.0 / 50.0
