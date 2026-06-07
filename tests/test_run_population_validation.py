import pandas as pd
import pytest
from braunschweig.analysis.population_validation import run_population_validation as R


def test_parser_requires_exactly_one_source():
    with pytest.raises(SystemExit):
        R._parse_args(["--prefix", "p_"])  # neither source
    with pytest.raises(SystemExit):
        R._parse_args(["--run-output-dir", "a", "--sim-cache", "b"])  # both


def test_deviation_wide_frame_pivots_delta_pp():
    long = pd.DataFrame({
        "control": ["cars_per_hh"], "category": ["0"], "geography": ["kreis"],
        "geo_id": ["03101"], "delta_pp": [2.5],
    })
    wide = R._deviation_wide(long, geography="kreis", id_name="ars5")
    assert "cars_per_hh__0_delta_pp" in wide.columns
    assert wide.loc[0, "ars5"] == "03101"


def test_interpretation_sections_split_good_and_bad():
    quality = pd.DataFrame({
        "control": ["good_one", "bad_one"], "family": ["mid_person", "mid_household"],
        "grade": ["very good", "needs improvement"], "mean_abs_delta_pp": [0.2, 7.0],
        "srmse": [0.01, 0.3], "cause_hint": ["", "structural offset"],
    })
    md = R._interpretation_markdown(quality)
    assert "good_one" in md and "bad_one" in md
    assert "structural offset" in md
