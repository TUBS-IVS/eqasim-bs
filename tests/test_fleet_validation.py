# tests/test_fleet_validation.py
import sys; from pathlib import Path
REPO = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(REPO))
import pandas as pd
from braunschweig.synthesis.vehicles import fleet_validation as V

def _df(pt):  # minimal df_spec
    return pd.DataFrame({"powertrain": pt, "euro_class": ["euro6"]*len(pt),
                         "age_band": ["5_to_9"]*len(pt), "segment": ["kompaktklasse"]*len(pt),
                         "kreis_ags5": ["03101"]*len(pt)})

def test_matching_margins_not_flagged():
    df = _df(["petrol"]*60 + ["diesel"]*40)
    exp = {"powertrain": {"petrol": 0.6, "diesel": 0.4}}
    r = V.validate_realised_margins(df, exp)
    assert r["dimensions"]["powertrain"]["flagged"] is False
    assert r["any_flagged"] is False

def test_drift_is_flagged():
    df = _df(["petrol"]*95 + ["diesel"]*5)      # realised 95/5
    exp = {"powertrain": {"petrol": 0.6, "diesel": 0.4}}  # expected 60/40
    r = V.validate_realised_margins(df, exp)
    assert r["dimensions"]["powertrain"]["flagged"] is True
    assert r["any_flagged"] is True
    assert r["dimensions"]["powertrain"]["max_abs_pp"] > 30.0
