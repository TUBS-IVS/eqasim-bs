import runpy, pandas as pd
from braunschweig.data.bosserhof_purpose import load_mapping, OTHER_CLASSES


def test_seed_writes_all_44_classes_and_valid_mapping(tmp_path, monkeypatch):
    out = tmp_path / "bosserhof_class_to_purpose.csv"
    import scripts.seed_bosserhof_class_to_purpose as seed
    seed.write_mapping(str(out))
    m = load_mapping(str(out))            # must pass validation (incl. consistency)
    assert len(m) == 44
    assert set(m.loc[m["other_destination"], "bosserhof_class"]) == set(OTHER_CLASSES)
    # border cases are NOT other
    assert not m.set_index("bosserhof_class").loc["research institutes", "other_destination"]
    assert not m.set_index("bosserhof_class").loc["car dealerships", "other_destination"]
