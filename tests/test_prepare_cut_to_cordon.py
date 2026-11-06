"""prepare's cordon cut uses the cut-timetable mode, hubs and extent, verifies both cuts agree, reads the summary (#574)."""
import gzip
import logging

import geopandas as gpd
import pytest
from shapely.geometry import box

from braunschweig.data.spatial import cordon as spatial_cordon
from braunschweig.data.transit import cut_schedule as CS
from braunschweig.matsim.simulation import prepare

HUBS = ["Hannover Hbf", "Göttingen"]
SUMMARY_METRICS = ("stops_kept_inside", "stops_kept_hub", "stops_kept_other_outside", "routes_kept", "routes_dropped",
                   "pt_trips_with_crossings", "collapsed_inside_inside", "collapsed_inside_inside_walked",
                   "collapsed_outside_origin", "collapsed_outside_origin_walked", "collapsed_outside_destination",
                   "collapsed_outside_destination_walked", "collapsed_through", "collapsed_through_walked")


def _schedule_xml(routes):
    """A minimal MATSim schedule: routes is {(line, route): [stop ids]}."""
    stop_ids = sorted({s for stops in routes.values() for s in stops})
    facilities = "".join(f'<stopFacility id="{s}" x="{i}" y="0"/>' for i, s in enumerate(stop_ids))
    lines = ""
    for (line, route), stops in routes.items():
        refs = "".join(f'<stop refId="{s}" departureOffset="00:0{i}:00"/>' for i, s in enumerate(stops))
        lines += (f'<transitLine id="{line}"><transitRoute id="{route}"><transportMode>rail</transportMode>'
                  f'<routeProfile>{refs}</routeProfile><departures><departure id="d" departureTime="08:00:00"/>'
                  f'</departures></transitRoute></transitLine>')
    return f'<?xml version="1.0"?><transitSchedule><transitStops>{facilities}</transitStops>{lines}</transitSchedule>'


def _write_schedule(path, routes):
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(_schedule_xml(routes))


class _Context:
    def __init__(self, tmp_path, values):
        self.root = tmp_path
        (tmp_path / "prepare").mkdir(exist_ok=True)
        self.values = {"output_prefix": "bs_", "matsim_threads": 2, "processes": 2,
                       "cordon_network_buffer_fraction": 0.1, CS.KEY_TRANSIT_CUT_MODE: "kept-stops",
                       CS.KEY_HUB_STOP_NAMES: HUBS, **values}
        self.municipalities = gpd.GeoDataFrame({"id": [1]}, geometry=[box(0, 0, 1000, 2000)], crs="EPSG:25832")

    def config(self, key, default=None):
        return self.values.get(key, default)

    def stage(self, name):
        if name == "data.spatial.municipalities":
            return self.municipalities
        return {"schedule_path": "cut_schedule.xml.gz", "mode": "kept-stops"}

    def path(self, name=None):
        directory = self.root / (name or "prepare")
        directory.mkdir(exist_ok=True)
        return str(directory)


@pytest.fixture
def cutter_calls(monkeypatch):
    """Replace the Java call: record the arguments, write the cut config and a cut schedule like the cutter."""
    calls = []
    routes = {("L1", "R1"): ["a", "b"]}

    def fake_run(context, class_name, arguments):
        calls.append((class_name, list(arguments)))
        (context.root / "prepare" / "bs_config.xml").write_text("<config/>")
        _write_schedule(context.root / "prepare" / "bs_transit_schedule.xml.gz", routes)
        if "kept-stops" in arguments:  # the cutter writes its summary in kept-stops mode only
            _write_summary(context.root / "prepare" / "bs_transit_cut_summary.csv", _summary_values())

    monkeypatch.setattr(prepare.eqasim, "run", fake_run)
    monkeypatch.setattr(prepare, "_verify_cut_timetable_matches_stage", lambda context, prefix: None)
    return calls


def test_kept_stops_mode_writes_the_hub_file_and_passes_mode_hubs_and_the_shared_extent(tmp_path, cutter_calls):
    context = _Context(tmp_path, {})
    assert prepare._cut_to_cordon(context) == "bs_config.xml"
    ((class_name, arguments),) = cutter_calls
    assert class_name == "org.eqasim.core.scenario.cutter.RunScenarioCutter"
    option = lambda name: arguments[arguments.index(name) + 1]
    assert option("--transit-cut-mode") == "kept-stops"
    assert option("--transit-hub-stop-names-path") == CS.HUB_NAMES_FILE_NAME
    assert (tmp_path / "prepare" / CS.HUB_NAMES_FILE_NAME).read_text(encoding="utf-8").splitlines() == HUBS
    written = gpd.read_file(tmp_path / "prepare" / "cordon_extent.gpkg")
    expected = spatial_cordon.cordon_polygon_from_fraction(context.municipalities, 0.1)
    assert written.geometry.iloc[0].symmetric_difference(expected).area < 1e-6 * expected.area


def test_extent_mode_passes_no_hub_option_and_writes_no_file(tmp_path, cutter_calls):
    context = _Context(tmp_path, {CS.KEY_TRANSIT_CUT_MODE: "extent", CS.KEY_HUB_STOP_NAMES: []})
    prepare._cut_to_cordon(context)
    ((_, arguments),) = cutter_calls
    assert arguments[arguments.index("--transit-cut-mode") + 1] == "extent"
    assert "--transit-hub-stop-names-path" not in arguments
    assert not (tmp_path / "prepare" / CS.HUB_NAMES_FILE_NAME).exists()


def _guard_context(tmp_path, stage_routes, cut_routes):
    context = _Context(tmp_path, {})
    _write_schedule(tmp_path / "prepare" / "bs_transit_schedule.xml.gz", cut_routes)
    stage_dir = tmp_path / "braunschweig.data.transit.cut_schedule"
    stage_dir.mkdir()
    _write_schedule(stage_dir / "cut_schedule.xml.gz", stage_routes)
    context.path("prepare")
    return context


def test_guard_passes_when_both_cuts_have_the_same_routes_and_stop_sequences(tmp_path):
    routes = {("L1", "R1"): ["a", "b", "c"], ("L2", "R2"): ["b", "c"]}
    prepare._verify_cut_timetable_matches_stage(_guard_context(tmp_path, routes, dict(routes)), "bs_")


@pytest.mark.parametrize("cut_routes", [
    {("L1", "R1"): ["a", "b", "x"], ("L2", "R2"): ["b", "c"]},   # one route ends on another stop
    {("L1", "R1"): ["a", "b", "c"]},                              # one route missing
])
def test_guard_raises_naming_the_differing_route(tmp_path, cut_routes):
    stage_routes = {("L1", "R1"): ["a", "b", "c"], ("L2", "R2"): ["b", "c"]}
    context = _guard_context(tmp_path, stage_routes, cut_routes)
    with pytest.raises(RuntimeError, match=r"1 route") as error:
        prepare._verify_cut_timetable_matches_stage(context, "bs_")
    assert "L1" in str(error.value) or "L2" in str(error.value)


def _write_summary(path, values):
    path.write_text("metric,value\n" + "".join(f"{k},{v}\n" for k, v in values.items()), encoding="utf-8")


def _summary_values(**overrides):
    values = {metric: 0 for metric in SUMMARY_METRICS}
    values.update(stops_kept_inside=80, stops_kept_hub=7, stops_kept_other_outside=13, routes_kept=50,
                  routes_dropped=50, pt_trips_with_crossings=1000, collapsed_inside_inside=100,
                  collapsed_inside_inside_walked=2)
    values.update(overrides)
    return values


def test_summary_reader_returns_ints_and_logs_each_collapsed_case(tmp_path, caplog):
    path = tmp_path / "bs_transit_cut_summary.csv"
    _write_summary(path, _summary_values())
    with caplog.at_level(logging.INFO, logger=prepare.log.name):
        summary = prepare._read_transit_cut_summary(path)
    assert summary == _summary_values()
    assert "collapsed inside_inside walked 2/100 (2.0 %)" in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_summary_reader_warns_when_the_walked_share_exceeds_the_threshold(tmp_path, caplog):
    path = tmp_path / "bs_transit_cut_summary.csv"
    _write_summary(path, _summary_values(collapsed_through=100, collapsed_through_walked=6))
    with caplog.at_level(logging.INFO, logger=prepare.log.name):
        prepare._read_transit_cut_summary(path)
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "through" in warnings[0]
    assert prepare.COLLAPSED_WALK_WARN_SHARE == 0.05


def test_summary_reader_raises_naming_a_missing_metric(tmp_path):
    values = _summary_values()
    del values["collapsed_through_walked"]
    path = tmp_path / "bs_transit_cut_summary.csv"
    _write_summary(path, values)
    with pytest.raises(ValueError, match="collapsed_through_walked"):
        prepare._read_transit_cut_summary(path)
