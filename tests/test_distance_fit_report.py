import json
import pandas as pd
from braunschweig.calibration.distance_fit import report as RP


def test_write_summary_emits_json_with_provenance(tmp_path):
    summaries = {"work": {"subpop_weighted_mean": 0.086, "worst_key": "03103",
                          "worst_value": 0.19, "is_validation": True, "aggregate": 0.036}}
    prov = {"cache": "cache_bs_25pct_allfeat_popsim", "detour_factor": 1.3,
            "scope_boundaries": ["synthesis-level not routed", "residents only"]}
    path = RP.write_summary(summaries, prov, str(tmp_path))
    data = json.load(open(path, encoding="utf-8"))
    assert data["activities"]["work"]["worst_value"] == 0.19
    assert data["provenance"]["detour_factor"] == 1.3
    assert "residents only" in data["provenance"]["scope_boundaries"]


def test_write_fit_csv_roundtrips(tmp_path):
    df = pd.DataFrame({"key": ["A"], "band": [0], "emd": [0.02]})
    path = RP.write_fit_csv(df, str(tmp_path), "work_distance_fit_by_key.csv")
    assert pd.read_csv(path)["emd"].iloc[0] == 0.02
