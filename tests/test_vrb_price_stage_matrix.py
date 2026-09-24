"""Pins the committed VRB price-stage matrix (printed Stand 01.01.2022, used for 2026 as an assumption)."""
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
CSV = REPO / "docs/data/vrb-price-stage-matrix-2022.csv"
ZONES = ["10", "11", "12", "13", "14", "15", "16", "17", "20", "30", "31", "32", "33", "34", "35", "36", "37",
         "38", "40", "50", "51", "52", "53", "54", "55", "56", "60", "70", "71", "72", "73", "74", "75", "76",
         "77", "78", "79", "80", "81", "82", "83", "84", "85", "86", "87", "88", "89", "90"]


def load():
    return pd.read_csv(CSV, comment="#", dtype=str, keep_default_na=False)


def test_matrix_covers_all_48_zone_pairs_once():
    df = load()
    assert list(df.columns) == ["origin_zone", "destination_zone", "price_stage", "status"]
    assert len(df) == 48 * 48
    assert sorted(df["origin_zone"].unique(), key=int) == ZONES
    assert not df.duplicated(["origin_zone", "destination_zone"]).any()


def test_matrix_stage_counts_match_the_official_pdf_export():
    counts = load()["price_stage"].value_counts().to_dict()
    assert counts == {"4": 1684, "3": 366, "2": 202, "1": 47, "ST": 3, "-": 2}


def test_city_zones_are_the_only_st_cells_and_lie_on_the_diagonal():
    df = load()
    st = df[df["price_stage"] == "ST"]
    assert sorted(st["origin_zone"]) == ["20", "40", "80"]
    assert (st["origin_zone"] == st["destination_zone"]).all()


def test_matrix_is_symmetric_and_undefined_cells_are_flagged():
    df = load()
    lookup = {(o, d): s for o, d, s in zip(df.origin_zone, df.destination_zone, df.price_stage)}
    assert all(lookup[(d, o)] == s for (o, d), s in lookup.items())
    assert set(df.loc[df["price_stage"] == "-", "status"]) == {"undefined"}
    assert set(df.loc[df["price_stage"] != "-", "status"]) == {"defined"}


def test_provenance_header_names_source_and_assumption():
    head = CSV.read_text(encoding="utf-8").splitlines()[:8]
    text = "\n".join(head)
    assert "175c58b14f2c9e94029644fa7a8a0a036af8fa438c5cb6bc4145409b99efa7e6" in text
    assert "01.01.2022" in text and "ASSUMPTION" in text
