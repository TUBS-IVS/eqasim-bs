"""Tests for the cut-timetable stage (#574, R9): the stage calls the Java cutter CLI (faked here)."""
from __future__ import annotations

import gzip
import sys
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import box

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.data.spatial.cordon import cordon_polygon_from_fraction  # noqa: E402
from braunschweig.data.transit import cut_schedule as stage  # noqa: E402
from matsim.runtime import eqasim  # noqa: E402

FRACTION = 0.10
HUBS = ["Göttingen Hbf", "Hannover Hbf"]
PRE_CUT_STOPS = ["a", "b", "c", "d", "e"]


def _municipalities():
    return gpd.GeoDataFrame({"commune_id": ["1", "2"]},
                            geometry=[box(600000, 5790000, 610000, 5800000), box(610000, 5790000, 620000, 5800000)],
                            crs="EPSG:25832")


def _write_schedule(path, stop_ids, routes):
    """Minimal MATSim transit schedule (gzip XML) with the given stops and one line per route."""
    stops = "".join(f'<stopFacility id="{s}" x="{600000 + i}" y="5795000"/>' for i, s in enumerate(stop_ids))
    lines = "".join(
        f'<transitLine id="L{r}"><transitRoute id="R{r}"><transportMode>rail</transportMode><routeProfile>'
        + "".join(f'<stop refId="{s}" departureOffset="00:0{i}:00"/>' for i, s in enumerate(route))
        + '</routeProfile><route/><departures><departure id="d1" departureTime="08:00:00" vehicleRefId="v"/>'
        '</departures></transitRoute></transitLine>' for r, route in enumerate(routes))
    xml = f'<?xml version="1.0"?><transitSchedule><transitStops>{stops}</transitStops>{lines}</transitSchedule>'
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(xml)


class _Context:
    """Execute-time context: the supply stage directory holds the pre-cut schedule, the stage directory is empty."""

    def __init__(self, tmp_path, mode, hubs):
        self.tmp_path = tmp_path
        self.values = {stage.KEY_TRANSIT_CUT_MODE: mode, stage.KEY_HUB_STOP_NAMES: hubs,
                       "cordon_network_buffer_fraction": FRACTION}
        (tmp_path / "supply").mkdir()
        (tmp_path / "out").mkdir()
        _write_schedule(tmp_path / "supply" / "pre.xml.gz", PRE_CUT_STOPS, [PRE_CUT_STOPS])

    def config(self, key):
        return self.values[key]

    def stage(self, name):
        if name == "matsim.scenario.supply.processed":
            return {"schedule_path": "pre.xml.gz"}
        assert name == "data.spatial.municipalities"
        return _municipalities()

    def path(self, name=None):
        return str(self.tmp_path / ("supply" if name else "out"))


@pytest.fixture
def fake_java(monkeypatch):
    """Fake the extent writer and the Java call: the fake cutter keeps a, b, c and writes ``rows`` as hub table."""
    seen = {"calls": [], "extent": [], "routes": True, "rows": None}
    monkeypatch.setattr("braunschweig.data.cordon.extent.write_cordon_extent",
                        lambda out_path, polygon, crs: seen["extent"].append((out_path, polygon, crs)))

    def fake_run(context, command, arguments):
        seen["calls"].append((command, list(arguments)))
        out = Path(context.path())
        args = dict(zip(arguments[::2], arguments[1::2]))
        _write_schedule(out / args["--output-schedule-path"], ["a", "b", "c"], [["a", "b", "c"]] if seen["routes"] else [])
        if seen["rows"] is not None:
            names = seen["rows"]
        elif "--hub-stop-names-path" in args:
            names = (out / args["--hub-stop-names-path"]).read_text(encoding="utf-8").splitlines()
        else:
            names = []
        with open(out / args["--output-hubs-path"], "w", encoding="utf-8") as handle:
            handle.write("name,stop_id,x,y,inside_extent\n")
            for i, name in enumerate(names):
                handle.write(f"{name},{'0' if i == 0 else 'd'},1.0,2.0,false\n")

    monkeypatch.setattr(eqasim, "run", fake_run)
    return seen


class _Recording:
    def __init__(self, values=None):
        self.calls, self.stages, self._values = {}, [], values or {}

    def config(self, key, default=None, **kwargs):
        self.calls[key] = default
        return self._values.get(key, default)

    def stage(self, name, **kwargs):
        self.stages.append(name)


def test_configure_declares_the_inputs_and_keys():
    context = _Recording()
    stage.configure(context)
    assert {"data.spatial.municipalities", "matsim.scenario.supply.processed", "matsim.runtime.eqasim",
            "matsim.runtime.java"} <= set(context.stages)
    assert context.calls[stage.KEY_TRANSIT_CUT_MODE] == stage.DEFAULT_TRANSIT_CUT_MODE == "extent"
    assert context.calls[stage.KEY_HUB_STOP_NAMES] == [] and "cordon_network_buffer_fraction" in context.calls
    with pytest.raises(ValueError, match="mode"):
        stage.configure(_Recording({stage.KEY_TRANSIT_CUT_MODE: "bogus"}))
    with pytest.raises(ValueError, match="extent"):
        stage.configure(_Recording({stage.KEY_HUB_STOP_NAMES: HUBS}))
    stage.configure(_Recording({stage.KEY_TRANSIT_CUT_MODE: "kept-stops", stage.KEY_HUB_STOP_NAMES: HUBS}))


def test_execute_cuts_with_the_cli_and_returns_the_kept_set(tmp_path, fake_java):
    context = _Context(tmp_path, "kept-stops", HUBS)
    result = stage.execute(context)
    (extent_path, polygon, crs), = fake_java["extent"]
    assert extent_path.endswith("cordon_extent.gpkg") and crs == "EPSG:25832"
    assert polygon.equals(cordon_polygon_from_fraction(_municipalities(), FRACTION))
    (command, arguments), = fake_java["calls"]
    assert command == "org.eqasim.core.scenario.cutter.transit.RunTransitScheduleCutter"
    assert arguments == ["--schedule-path", str(Path(context.path("supply")) / "pre.xml.gz"),
                         "--extent-path", "cordon_extent.gpkg", "--hub-stop-names-path", "hub_stop_names.txt",
                         "--output-schedule-path", "cut_schedule.xml.gz", "--output-hubs-path", "hub_stops.csv"]
    # UTF-8 round trip: the umlaut survives the file the Java side reads.
    assert (Path(context.path()) / "hub_stop_names.txt").read_text(encoding="utf-8").splitlines() == HUBS
    assert result["kept_stop_ids"] == frozenset({"a", "b", "c"})
    assert result["n_stops_pre_cut"] == 5 and result["n_stops_kept"] == 3
    assert result["hub_stop_ids"] == frozenset({"0", "d"}) and result["n_hub_stops"] == 2
    assert result["mode"] == "kept-stops"
    assert result["schedule_path"] == "cut_schedule.xml.gz" and result["hub_stops_path"] == "hub_stops.csv"


def test_execute_raises_when_a_hub_has_no_row(tmp_path, fake_java):
    fake_java["rows"] = ["Göttingen Hbf"]
    with pytest.raises(ValueError, match="Hannover Hbf"):
        stage.execute(_Context(tmp_path, "kept-stops", HUBS))


def test_execute_raises_when_the_cut_schedule_has_no_route(tmp_path, fake_java):
    fake_java["routes"] = False
    with pytest.raises(ValueError, match="no route"):
        stage.execute(_Context(tmp_path, "extent", []))


def test_execute_in_extent_mode_passes_no_hub_names(tmp_path, fake_java):
    result = stage.execute(_Context(tmp_path, "extent", []))
    (_, arguments), = fake_java["calls"]
    assert "--hub-stop-names-path" not in arguments
    assert result["hub_stop_ids"] == frozenset() and result["mode"] == "extent"


def test_validate_token_covers_the_helpers():
    token = stage.validate(None)
    assert token == stage.validate(None)
    assert token["format"] == 2 and token["modules"]
