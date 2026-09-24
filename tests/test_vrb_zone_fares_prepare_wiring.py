"""Flag-gated wiring of the VRB zone fare inputs in braunschweig.matsim.simulation.prepare."""
import gzip
import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import box

from braunschweig.matsim.simulation import prepare


class _Context:
    def __init__(self, tmp_path, values):
        self.values = values
        self.declared_config, self.declared_stages = [], []
        self._path = tmp_path / "prepare"
        self._path.mkdir()
        self._cleaned = tmp_path / "cleaned"
        (self._cleaned / "output").mkdir(parents=True)
        self.stages = {}

    def config(self, key, default=None, **options):  # synpp's configure-time config also takes volatile=...
        self.declared_config.append(key)
        return self.values.get(key, default)

    def stage(self, name):
        self.declared_stages.append(name)
        return self.stages.get(name)

    def path(self, name=None):
        return str(self._cleaned if name == "data.gtfs.cleaned" else self._path)


ON_VALUES = {
    "vrb_zone_fares_enabled": True, "cordon_enabled": False, "freight_enabled": False, "output_prefix": "bs_",
    "vrb_fare_snapshot_date": "2026-06-20", "vrb_fare_day_ticket_cap_enabled": True,
    "vrb_fare_unsupported_fallback_cents": 370, "vrb_fare_maximum_unsupported_share": 0.05,
    "vrb_fare_external_local_single_cents": 370, "vrb_fare_rail_distance_factor": 1.0,
    "vrb_fare_minimum_line_scope_coverage": 0.99, "vrb_fare_minimum_facility_zone_coverage": 0.10,
}
MODEL_NAME = "bs_vrb_fare_model_2026-06-20.json"


def _configure(tmp_path, enabled):
    context = _Context(tmp_path, {"vrb_zone_fares_enabled": enabled, "cordon_enabled": False, "freight_enabled": False})
    prepare.configure(context)
    return context


def test_off_declares_legacy_zone_stage_and_no_fare_keys(tmp_path):
    context = _configure(tmp_path, False)
    assert "braunschweig.data.vrb.zones" in context.declared_stages
    assert "braunschweig.data.vrb.zone_polygons" not in context.declared_stages
    assert "vrb_fare_snapshot_date" not in context.declared_config


def test_on_declares_polygon_stage_cleaned_gtfs_and_assumption_keys(tmp_path):
    context = _configure(tmp_path, True)
    assert "braunschweig.data.vrb.zone_polygons" in context.declared_stages
    assert "data.gtfs.cleaned" in context.declared_stages
    assert "braunschweig.data.vrb.zones" not in context.declared_stages
    for key in ("vrb_fare_snapshot_date", "vrb_fare_day_ticket_cap_enabled", "vrb_fare_unsupported_fallback_cents",
                "vrb_fare_maximum_unsupported_share", "vrb_fare_external_local_single_cents",
                "vrb_fare_rail_distance_factor", "vrb_fare_minimum_line_scope_coverage",
                "vrb_fare_minimum_facility_zone_coverage"):
        assert key in context.declared_config


def _schedule(line_ids, zoned, facilities=10):
    """A MATSim transit schedule (v2 layout) with ``zoned`` of ``facilities`` stop facilities in VRB zone 40."""
    stops = []
    for index in range(facilities):
        attributes = ('<attributes><attribute name="vrbTariffZone" class="java.lang.String">40</attribute></attributes>'
                      if index < zoned else '<attributes><attribute name="other" class="java.lang.String">x</attribute>'
                      '</attributes>')
        stops.append(f'<stopFacility id="s{index}" x="{index}.0" y="0.0">{attributes}</stopFacility>')
    lines = "".join(f'<transitLine id="{line}"><transitRoute id="{line}_r"><transportMode>bus</transportMode>'
                    f'<routeProfile><stop refId="s0"/></routeProfile></transitRoute></transitLine>' for line in line_ids)
    return f'<transitSchedule><transitStops>{"".join(stops)}</transitStops>{lines}</transitSchedule>'


def _on_context(tmp_path, monkeypatch, line_ids=("L1",), zoned=9, tool_zoned=9):
    context = _Context(tmp_path, dict(ON_VALUES))
    context.stages["braunschweig.data.vrb.zone_polygons"] = gpd.GeoDataFrame(
        {"zone_id": ["40", "70"], "geometry": [box(0, 0, 1, 1), box(1, 0, 2, 1)]}, crs="EPSG:25832")
    pd.DataFrame([{"route_id": "L1", "agency_id": "a", "route_type": 3, "route_short_name": "1"}]).to_csv(
        context._cleaned / "output" / "routes.txt", index=False)
    pd.DataFrame([{"agency_id": "a", "agency_name": "KVG"}]).to_csv(context._cleaned / "output" / "agency.txt", index=False)
    (Path(context.path()) / "bs_config.xml").write_text('<?xml version="1.0" encoding="utf-8"?>\n<config>\n</config>\n',
                                                         encoding="utf-8")
    (Path(context.path()) / "bs_transit_schedule.xml.gz").write_bytes(gzip.compress(_schedule(line_ids, zoned).encode()))
    calls = []

    def fake_run(ctx, command, arguments):
        calls.append((command, list(arguments)))
        (Path(ctx.path()) / "vrb_tariff_zone_report.json").write_text(json.dumps(
            {"facilities": 12, "zoned": tool_zoned, "unzoned": 12 - tool_zoned, "boundary_ties": 0, "by_mode": {}}))

    monkeypatch.setattr(prepare.eqasim, "run", fake_run)
    monkeypatch.setattr(prepare.delegate, "execute", lambda ctx: "bs_config.xml")
    return context, calls


def test_on_execute_writes_inputs_module_and_report(tmp_path, monkeypatch):
    context, calls = _on_context(tmp_path, monkeypatch)
    assert prepare.execute(context) == "bs_config.xml"
    assert calls[0][0] == "org.eqasim.braunschweig.scenario.AddVrbTariffZoneInformation"
    assert "--zones-path" in calls[0][1] and "vrb_tariff_zones.shp" in calls[0][1]
    root = Path(context.path())
    assert (root / "vrb_tariff_zones.shp").is_file() and (root / "bs_vrb_line_scopes.csv").is_file()
    # The fare model name carries the output prefix and the tariff snapshot it was built for.
    assert json.loads((root / MODEL_NAME).read_text())["tariff_snapshot_date"] == "2026-06-20"
    module = prepare.fare_config_xml.read_vrb_fare_module(root / "bs_config.xml")
    assert module["enabled"] == "true" and module["fareModelPath"] == MODEL_NAME
    assert module["lineScopesPath"] == "bs_vrb_line_scopes.csv"
    assert module["maximumUnsupportedShare"] == "0.05" and module["dayTicketCapEnabled"] == "true"
    # The fallback price lives only in the fare model JSON; the Java group rejects this parameter.
    assert "unsupportedFallbackPriceCents" not in module
    report = json.loads((root / "bs_vrb_fare_inputs_report.json").read_text())
    assert report["line_scope_coverage"] == 1.0
    assert report["fare_input_files"] == [MODEL_NAME, "bs_vrb_line_scopes.csv", "bs_vrb_fare_inputs_report.json"]


def test_facility_zone_coverage_is_counted_on_the_final_schedule(tmp_path, monkeypatch):
    # The zone tool runs before the cordon cut; the report must state the post-cut coverage the run prices with.
    context, _ = _on_context(tmp_path, monkeypatch, zoned=9, tool_zoned=5)
    prepare.execute(context)
    report = json.loads((Path(context.path()) / "bs_vrb_fare_inputs_report.json").read_text())
    assert report["facilities"] == 10 and report["zoned_facilities"] == 9
    assert report["facility_zone_coverage"] == 0.9
    assert report["zoned_facilities_by_zone"] == {"40": 9}
    assert report["pre_cut_zone_tool"]["zoned"] == 5 and report["pre_cut_zone_tool"]["facilities"] == 12


def test_on_execute_fails_loudly_when_almost_no_stop_facility_carries_a_zone(tmp_path, monkeypatch):
    import pytest
    # A broken attribution (CRS mismatch, wrong zone field) turns every trip into external pricing, which the
    # run's fallback guard does not count; preparation is the place to stop it.
    context, _ = _on_context(tmp_path, monkeypatch, zoned=0)
    with pytest.raises(RuntimeError, match="stop facilities carry a VRB zone"):
        prepare.execute(context)


def test_schedule_summary_reads_lines_and_zones_in_one_pass(tmp_path):
    path = tmp_path / "schedule.xml.gz"
    path.write_bytes(gzip.compress(_schedule(("A", "B"), zoned=3, facilities=4).encode()))
    summary = prepare._schedule_summary(path)
    assert summary == {"line_ids": {"A", "B"}, "facilities": 4, "zoned_facilities": 3, "zoned_by_zone": {"40": 3}}


def test_on_execute_fails_loudly_when_schedule_lines_lack_scope_rows(tmp_path, monkeypatch):
    import pytest
    context, _ = _on_context(tmp_path, monkeypatch, line_ids=("L1", "UNKNOWN_1", "UNKNOWN_2"))
    with pytest.raises(RuntimeError, match="lines have a tariff scope row"):
        prepare.execute(context)


def test_off_execute_does_not_touch_config_or_call_the_zone_tool(tmp_path, monkeypatch):
    context = _Context(tmp_path, {"vrb_zone_fares_enabled": False, "cordon_enabled": False, "freight_enabled": False,
                                  "output_prefix": "bs_"})
    context.stages["braunschweig.data.vrb.zones"] = gpd.GeoDataFrame(
        {"zone": ["1"], "geometry": [box(0, 0, 1, 1)]}, crs="EPSG:25832")
    config = Path(context.path()) / "bs_config.xml"
    config.write_text('<?xml version="1.0" encoding="utf-8"?>\n<config>\n</config>\n', encoding="utf-8")
    before = config.read_bytes()
    calls = []
    monkeypatch.setattr(prepare.eqasim, "run", lambda ctx, command, arguments: calls.append(command))
    monkeypatch.setattr(prepare.delegate, "execute", lambda ctx: "bs_config.xml")
    prepare.execute(context)
    assert calls == ["org.eqasim.braunschweig.scenario.AddTransitZoneInformation"]
    assert config.read_bytes() == before
    assert not list(Path(context.path()).glob("*vrb_fare_model*"))


def test_prepare_token_changes_when_a_committed_tariff_input_changes(tmp_path, monkeypatch):
    # The fare model is built from committed tables at execute time; a corrected table must invalidate the cache.
    for name in ("PRICE_INPUTS", "MATRIX_CSV", "RAIL_TABLE"):
        original = getattr(prepare.fare_model_export, name)
        copy = tmp_path / original.name
        copy.write_bytes(original.read_bytes())
        monkeypatch.setattr(prepare.fare_model_export, name, copy)
        before = prepare.validate(None)
        copy.write_bytes(copy.read_bytes() + b"\n")
        assert prepare.validate(None) != before, name
