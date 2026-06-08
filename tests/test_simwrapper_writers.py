from pathlib import Path

import pandas as pd
import yaml

from braunschweig.analysis.simwrapper import writers as w


def test_write_csv_roundtrip(tmp_path: Path):
    df = pd.DataFrame({"mode": ["car", "pt"], "share_pct": [60.0, 40.0]})
    name = w.write_csv(tmp_path, "mode_share.csv", df)
    assert name == "mode_share.csv"
    back = pd.read_csv(tmp_path / "mode_share.csv")
    assert list(back.columns) == ["mode", "share_pct"]
    assert back.shape == (2, 2)


def test_write_yaml_is_valid(tmp_path: Path):
    obj = {"header": {"tab": "Test", "title": "T"}, "layout": {"row": []}}
    w.write_yaml(tmp_path, "dashboard-1-test.yaml", obj)
    loaded = yaml.safe_load((tmp_path / "dashboard-1-test.yaml").read_text(encoding="utf-8"))
    assert loaded["header"]["tab"] == "Test"


def test_card_bar_shape():
    card = w.card_bar("Mode share", "mode_share.csv", x="mode",
                      columns=["share_pct"], y_axis_name="%")
    assert card["type"] == "bar"
    assert card["dataset"] == "mode_share.csv"
    assert card["x"] == "mode"
    assert card["columns"] == ["share_pct"]


def test_card_table_and_tile():
    t = w.card_table("Headline", "kpi.csv")
    assert t["type"] == "csv" and t["dataset"] == "kpi.csv"
    tile = w.card_tile("KPIs", "kpi.csv")
    assert tile["type"] == "tile" and tile["dataset"] == "kpi.csv"
