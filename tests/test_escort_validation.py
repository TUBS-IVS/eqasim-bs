"""Escort purpose in the MiD validation crosswalks (issue #201)."""
import pandas as pd
import pytest

from braunschweig.analysis.population_validation import trip_coherence as tc


def test_escort_maps_to_begleitung_without_raise():
    mapped = tc.mid_purpose_from_eqasim(pd.Series(["work", "escort", "home"]))
    assert list(mapped) == ["arbeit", "begleitung", "heimweg"]


def test_scored_purposes_selection_is_presence_based():
    with_escort = {"arbeit": 0.3, "ausbildung": 0.1, "einkauf": 0.2,
                   "freizeit": 0.3, "begleitung": 0.1}
    without = {"arbeit": 0.4, "ausbildung": 0.1, "einkauf": 0.2, "freizeit": 0.3}
    assert tc.scored_mid_purposes(with_escort) == tc.SCORED_MID_PURPOSES_WITH_ESCORT
    assert tc.scored_mid_purposes(without) == tc.SCORED_MID_PURPOSES


def test_renormalize_scored_with_escort_sums_to_one():
    dist = {"arbeit": 0.3, "ausbildung": 0.1, "einkauf": 0.1, "freizeit": 0.3,
            "begleitung": 0.1, "sonstiges": 0.1}
    out = tc.renormalize_scored(dist, scored_purposes=tc.SCORED_MID_PURPOSES_WITH_ESCORT)
    assert set(out) == set(tc.SCORED_MID_PURPOSES_WITH_ESCORT)
    assert sum(out.values()) == pytest.approx(1.0)


def test_w12_map_with_escort():
    assert tc.W12_PURPOSE_BY_MID_WITH_ESCORT["Begleitung"] == "escort"
    assert "Begleitung" not in tc.W12_PURPOSE_BY_MID
