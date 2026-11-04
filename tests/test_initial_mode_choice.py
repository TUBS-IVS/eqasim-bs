"""The initial mode choice runs before iteration 0 (eqasim-bs#542, ADR-0178)."""
from __future__ import annotations

import gzip
import json
import os

import pandas as pd
import pytest

from braunschweig.matsim import initial_mode_choice as I
import matsim.runtime.eqasim as eqasim


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
    with pytest.raises(ValueError, match="ADR-0178"):
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


def test_summarise_is_json_serialisable():
    before = _trips([("1", "0", "walk"), ("2", "0", "car")])
    after = _trips([("1", "0", "pt"), ("2", "0", "car")])
    summary = I.summarise(before, after)
    assert json.loads(json.dumps(summary)) == summary


def test_summarise_refuses_duplicate_trip_keys():
    # Equal row counts and a full inner join would hide a duplicated key; the key check must catch it.
    duplicated = _trips([("1", "0", "walk"), ("1", "0", "car")])
    clean = _trips([("1", "0", "walk"), ("1", "1", "walk")])
    with pytest.raises(ValueError, match="duplicate"):
        I.summarise(duplicated, clean)
    with pytest.raises(ValueError, match="duplicate"):
        I.summarise(clean, duplicated)


def test_summarise_refuses_a_missing_mode():
    before = _trips([("1", "0", "walk")])
    after = _trips([("1", "0", "walk")])
    after["mode"] = None
    with pytest.raises(ValueError, match="mode"):
        I.summarise(before, after)


_CONFIG = """<?xml version="1.0" ?>
<!DOCTYPE config SYSTEM "http://www.matsim.org/files/dtd/config_v2.dtd">
<config>
\t<module name="plans" >
\t\t<param name="inputPlansFile" value="bs_population.xml.gz" />
\t</module>
</config>
"""


class _Context:
    def __init__(self, path, threads=None, processes=4):
        self._path, self._values = str(path), {"matsim_threads": threads, "processes": processes}

    def path(self):
        return self._path

    def config(self, key):
        return self._values[key]


def _write_trips(path, rows):
    _trips(rows).to_csv(path, sep=";", index=False, compression="gzip")


def _fake_java(stage, persons_after, skip=None):
    """A stand-in for eqasim.run that writes what RunStandaloneModeChoice writes, except the file named ``skip``."""
    def fake(context, command, arguments):
        fake.calls.append((command, list(arguments)))
        out = stage / "mode_choice"
        out.mkdir()
        if skip != I.OUTPUT_PLANS:
            with gzip.open(out / I.OUTPUT_PLANS, "wb") as handle:
                handle.write(_plans(persons_after))
        if skip != I.INPUT_TRIPS:
            _write_trips(out / I.INPUT_TRIPS, [("1", "0", "walk"), ("2", "0", "car")])
        if skip != I.OUTPUT_TRIPS:
            _write_trips(out / I.OUTPUT_TRIPS, [("1", "0", "pt"), ("2", "0", "car")])
    fake.calls = []
    return fake


def _stage(tmp_path):
    (tmp_path / "bs_config.xml").write_text(_CONFIG, encoding="utf-8")
    with gzip.open(tmp_path / "bs_population.xml.gz", "wb") as handle:
        handle.write(_plans(["1", "2"]))
    return tmp_path


def test_run_calls_eqasim_on_the_population_the_config_names_and_replaces_it(tmp_path, monkeypatch, caplog):
    stage = _stage(tmp_path)
    fake = _fake_java(stage, ["1", "2"])
    monkeypatch.setattr(eqasim, "run", fake)
    with caplog.at_level("INFO"):
        summary = I.run(_Context(stage, threads=8), "bs_config.xml")
    (command, arguments), = fake.calls
    assert command == I.RUNNER
    assert arguments == I.arguments("bs_config.xml", os.path.join(str(stage), "bs_population.xml.gz"), 8)
    assert summary["persons_before"] == summary["persons_after"] == 2
    assert summary["n_trips"] == 2 and summary["share_mode_changed"] == pytest.approx(0.5)
    assert summary["travel_times"] == "free_flow"
    # the chosen plans replaced the population under its name; no second copy remains
    with gzip.open(stage / "bs_population.xml.gz", "rb") as handle:
        assert handle.read() == _plans(["1", "2"])
    assert not (stage / "mode_choice" / "output_plans.xml.gz").exists()
    assert json.loads((stage / "initial_mode_choice.json").read_text(encoding="utf-8")) == summary
    assert any(I._LOG_TAG in record.message and "50.0" in record.message for record in caplog.records)


def test_run_uses_processes_when_matsim_threads_is_unset(tmp_path, monkeypatch):
    stage = _stage(tmp_path)
    fake = _fake_java(stage, ["1", "2"])
    monkeypatch.setattr(eqasim, "run", fake)
    I.run(_Context(stage, threads=None, processes=4), "bs_config.xml")
    assert "4" == fake.calls[0][1][fake.calls[0][1].index("--config:global.numberOfThreads") + 1]


def test_run_raises_when_persons_were_removed(tmp_path, monkeypatch):
    stage = _stage(tmp_path)
    monkeypatch.setattr(eqasim, "run", _fake_java(stage, ["1"]))
    with pytest.raises(RuntimeError, match=r"2.*1"):
        I.run(_Context(stage), "bs_config.xml")
    with gzip.open(stage / "bs_population.xml.gz", "rb") as handle:
        assert handle.read() == _plans(["1", "2"])   # the population is untouched


@pytest.mark.parametrize("missing", ["output_plans.xml.gz", "input_trips.csv.gz", "output_trips.csv.gz"])
def test_run_raises_when_an_output_is_missing(tmp_path, monkeypatch, missing):
    stage = _stage(tmp_path)
    monkeypatch.setattr(eqasim, "run", _fake_java(stage, ["1", "2"], skip=missing))
    with pytest.raises(FileNotFoundError, match=missing.replace(".", r"\.")):
        I.run(_Context(stage), "bs_config.xml")


def test_run_refuses_an_empty_population_before_running_java(tmp_path, monkeypatch):
    stage = _stage(tmp_path)
    with gzip.open(stage / "bs_population.xml.gz", "wb") as handle:
        handle.write(_plans([]))
    fake = _fake_java(stage, [])
    monkeypatch.setattr(eqasim, "run", fake)
    with pytest.raises(RuntimeError, match="bs_population.xml.gz"):
        I.run(_Context(stage), "bs_config.xml")
    assert fake.calls == []


def test_run_refuses_a_population_outside_the_stage(tmp_path, monkeypatch):
    stage = tmp_path / "stage"
    stage.mkdir()
    outside = tmp_path / "elsewhere.xml.gz"
    with gzip.open(outside, "wb") as handle:
        handle.write(_plans(["1", "2"]))
    (stage / "bs_config.xml").write_text(_CONFIG.replace("bs_population.xml.gz", "../elsewhere.xml.gz"),
                                         encoding="utf-8")
    fake = _fake_java(stage, ["1", "2"])
    monkeypatch.setattr(eqasim, "run", fake)
    with pytest.raises(ValueError, match="stage"):
        I.run(_Context(stage), "bs_config.xml")
    assert fake.calls == []
    with gzip.open(outside, "rb") as handle:
        assert handle.read() == _plans(["1", "2"])


from braunschweig.matsim.simulation import plan_check as C
from braunschweig.matsim.simulation import prepare


class _RecordingContext:
    def __init__(self, values):
        self.calls, self._values = {}, values

    def config(self, key, default=None, **kwargs):
        self.calls[key] = default
        return self._values.get(key, default)

    def stage(self, name, **kwargs):
        pass


def test_configure_declares_enabled_and_refuses_the_retired_weight_key():
    context = _RecordingContext({"cordon_enabled": False, "freight_enabled": False})
    prepare.configure(context)
    assert context.calls[I.KEY_ENABLED] is True and I.KEY_WEIGHT_RETIRED in context.calls
    with pytest.raises(ValueError, match="ADR-0178"):
        prepare.configure(_RecordingContext({"cordon_enabled": False, "freight_enabled": False,
                                             I.KEY_WEIGHT_RETIRED: 1.0e6}))


def test_configure_refuses_the_retired_weight_key_even_when_disabled():
    with pytest.raises(ValueError, match="ADR-0178"):
        prepare.configure(_RecordingContext({"cordon_enabled": False, "freight_enabled": False,
                                             I.KEY_ENABLED: False, I.KEY_WEIGHT_RETIRED: 1.0e6}))


@pytest.mark.parametrize("enabled", [True, False])
def test_execute_runs_the_mode_choice_after_the_parking_inputs_and_before_the_plan_check(monkeypatch, enabled):
    seen = []
    monkeypatch.setattr(prepare.delegate, "execute", lambda context: "config.xml")
    monkeypatch.setattr(prepare, "_attach_vrb_tariff_zones", lambda context: None)
    monkeypatch.setattr(prepare, "_summarise_final_schedule", lambda context: {})
    monkeypatch.setattr(prepare, "_write_vrb_fare_inputs", lambda context, result, summary: seen.append("fares"))
    monkeypatch.setattr(prepare, "_write_parking_inputs", lambda context, result: seen.append("parking"))
    monkeypatch.setattr(I, "run", lambda context, result: seen.append(("initial", result)) or {})
    monkeypatch.setattr(C, "run", lambda context, result: seen.append("check"))

    class _Context:
        _values = {prepare.VRB_FARES_KEY: True, "cordon_enabled": False, "freight_enabled": False,
                   prepare.PARKING_KEY: True, C.KEY_ENABLED: True, I.KEY_ENABLED: enabled}

        def config(self, key):
            return self._values[key]

    assert prepare.execute(_Context()) == "config.xml"
    expected = ["fares", "parking", ("initial", "config.xml"), "check"] if enabled else ["fares", "parking", "check"]
    assert seen == expected


def test_base_config_pins_the_switch_and_no_retired_weight():
    import yaml
    from pathlib import Path
    base = Path(__file__).resolve().parents[1] / "configs" / "base_bs.yml"
    config = yaml.safe_load(base.read_text(encoding="utf-8"))["config"]
    assert config[I.KEY_ENABLED] is True
    assert I.KEY_WEIGHT_RETIRED not in config
