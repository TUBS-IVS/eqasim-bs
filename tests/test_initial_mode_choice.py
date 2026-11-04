"""The initial mode choice runs before iteration 0 (eqasim-bs#542, ADR-0177)."""
from __future__ import annotations

import gzip

import pandas as pd
import pytest

from braunschweig.matsim import initial_mode_choice as I


def test_arguments_mirror_the_eqasim_france_call_on_the_final_scenario():
    assert I.arguments("bs_config.xml", "C:/stage/bs_population.xml.gz", 8) == [
        "--config-path", "bs_config.xml",
        "--config:plans.inputPlansFile", "C:/stage/bs_population.xml.gz",
        "--config:standaloneModeChoice.outputDirectory", "mode_choice",
        "--config:standaloneModeChoice.removePersonsWithNoValidAlternatives", "true",
        "--config:global.numberOfThreads", "8",
        "--write-input-csv-trips", "true",
        "--write-output-csv-trips", "true",
        "--compression", "gzip",
        "--skip-scenario-check", "true",
        "--eqasim-configurator", "org.eqasim.braunschweig.BraunschweigConfigurator",
    ]


def test_arguments_carry_no_travel_time_option():
    joined = " ".join(I.arguments("c.xml", "p.xml.gz", 1))
    assert "travel-times" not in joined and "recorded" not in joined


@pytest.mark.parametrize("value", [1.0e6, 0.05, "1e6"])
def test_refuse_retired_weight_raises_with_the_adr(value):
    with pytest.raises(ValueError, match="ADR-0177"):
        I.refuse_retired_weight(value)


def test_refuse_retired_weight_accepts_an_absent_key():
    assert I.refuse_retired_weight(None) is None


def _plans(persons):
    body = "".join(f'\t<person id="{p}">\n\t\t<plan selected="yes"/>\n\t</person>\n' for p in persons)
    return f'<?xml version="1.0" encoding="utf-8"?>\n<population>\n{body}</population>\n'.encode("utf-8")


def test_count_persons_counts_person_elements(tmp_path):
    path = tmp_path / "population.xml.gz"
    with gzip.open(path, "wb") as handle:
        handle.write(_plans(["1", "freight_2", "3"]))
    assert I.count_persons(path) == 3


def test_count_persons_handles_a_token_across_the_chunk_boundary(tmp_path):
    path = tmp_path / "population.xml.gz"
    with gzip.open(path, "wb") as handle:
        handle.write(_plans([str(i) for i in range(200)]))
    assert I.count_persons(path, chunk_size=7) == 200


def _trips(rows):
    return pd.DataFrame(rows, columns=["person_id", "person_trip_id", "mode"]).astype(str)


def test_summarise_reports_changed_share_and_shares_before_after():
    before = _trips([("1", "0", "walk"), ("1", "1", "walk"), ("2", "0", "car"), ("2", "1", "car")])
    after = _trips([("1", "0", "pt"), ("1", "1", "walk"), ("2", "0", "car"), ("2", "1", "bicycle")])
    summary = I.summarise(before, after)
    assert summary["n_trips"] == 4 and summary["n_trips_mode_changed"] == 2
    assert summary["share_mode_changed"] == pytest.approx(0.5)
    assert summary["mode_shares_before"] == {"bicycle": 0.0, "car": 0.5, "pt": 0.0, "walk": 0.5}
    assert summary["mode_shares_after"] == {"bicycle": 0.25, "car": 0.25, "pt": 0.25, "walk": 0.25}


def test_summarise_covers_the_union_of_modes():
    before = _trips([("1", "0", "walk")])
    after = _trips([("1", "0", "car_passenger")])
    summary = I.summarise(before, after)
    assert set(summary["mode_shares_before"]) == set(summary["mode_shares_after"]) == {"car_passenger", "walk"}


def test_summarise_of_no_trips():
    summary = I.summarise(_trips([]), _trips([]))
    assert summary == {"n_trips": 0, "n_trips_mode_changed": 0, "share_mode_changed": 0.0,
                       "mode_shares_before": {}, "mode_shares_after": {}}


def test_summarise_refuses_differing_trip_sets():
    before = _trips([("1", "0", "walk"), ("1", "1", "walk")])
    after = _trips([("1", "0", "walk")])
    with pytest.raises(ValueError, match="trips"):
        I.summarise(before, after)
