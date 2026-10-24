"""The facilities at the cordon road gates are pinned to the carriageway that feeds the inward gate link (#465, ADR-0167).

Synthetic scene: the cut extent is the square (0, 0)-(1000, 1000); a dual-carriageway motorway crosses its east edge
at y = 490 (westbound, inward) and y = 510 (eastbound, outward). Closer to the gate points than the motorway lie a
600 veh/h residential street that also crosses inward, and a pt link. The defect this guards against is the one
measured in the 1 % rerun: the facilities ended on the nearest residential street (or on the outward carriageway)
instead of the motorway.
"""
import gzip
import logging
from xml.etree import ElementTree

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point, box

from braunschweig.data.cordon import gate_facility_links as gfl

CRS = "EPSG:25832"
EXTENT = box(0.0, 0.0, 1000.0, 1000.0)

#: link_id, from (x, y), to (x, y), capacity veh/h, modes
_LINKS = [
    ("feed_in", (1600.0, 490.0), (1200.0, 490.0), 4000.0, "car"),      # outside, feeds gate_in: the expected link
    ("gate_in", (1200.0, 490.0), (800.0, 490.0), 4000.0, "car"),       # crosses inward
    ("next_in", (800.0, 490.0), (400.0, 490.0), 4000.0, "car"),        # inside
    ("gate_out", (800.0, 510.0), (1200.0, 510.0), 4000.0, "car"),      # crosses outward
    ("away_out", (1200.0, 510.0), (1600.0, 510.0), 4000.0, "car"),     # outside, leaves
    ("ramp_on", (1250.0, 600.0), (1200.0, 490.0), 1500.0, "car"),      # outside on-ramp, lower capacity
    ("inside_ramp", (900.0, 700.0), (1200.0, 490.0), 5000.0, "car"),   # feeds gate_in from INSIDE: never eligible
    ("street_in", (1050.0, 500.0), (950.0, 500.0), 600.0, "car"),      # residential, crosses inward, closest
    ("rail_in", (1200.0, 494.0), (800.0, 494.0), 9999.0, "pt"),        # crosses inward, not a car link
    ("north_street", (500.0, 1100.0), (500.0, 900.0), 600.0, "car"),   # the only crossing at the north gate
]


def _links():
    rows = []
    for link_id, (fx, fy), (tx, ty), capacity, modes in _LINKS:
        rows.append({"link_id": link_id, "from_node": "n%.0f_%.0f" % (fx, fy), "to_node": "n%.0f_%.0f" % (tx, ty),
                     "from_x": fx, "from_y": fy, "to_x": tx, "to_y": ty, "capacity_veh_per_h": capacity,
                     "allows_car": modes == "car"})
    return pd.DataFrame(rows)


def _gates():
    return gpd.GeoDataFrame({"gate_id": ["gate_0004", "gate_0006", "gate_0009"]},
                            geometry=[Point(1000.0, 494.0), Point(1000.0, 510.0), Point(500.0, 1000.0)], crs=CRS)


def _select():
    return gfl.select_gate_facility_links(_gates(), _links(), EXTENT, minimum_capacity_veh_per_h=1000.0,
                                          maximum_distance_m=300.0)


def test_both_carriageway_gates_get_the_link_feeding_the_inward_motorway_and_a_weak_gate_falls_back():
    selection = _select().set_index("gate_id")

    # The inward gate (next to a pt link and a residential street) and the OUTWARD carriageway gate both resolve to
    # the outside link that feeds the inward carriageway, not to the higher-capacity link from inside, not to the
    # lower-capacity ramp, and never to the outward carriageway.
    for gate_id in ("gate_0004", "gate_0006"):
        assert selection.loc[gate_id, "status"] == gfl.STATUS_LINKED
        assert selection.loc[gate_id, "facility_link_id"] == "feed_in"
        assert selection.loc[gate_id, "entry_link_id"] == "gate_in"
        assert selection.loc[gate_id, "facility_link_capacity_veh_per_h"] == 4000.0
    assert selection.loc["gate_0004", "entry_distance_m"] == pytest.approx(4.0)
    assert selection.loc["gate_0006", "entry_distance_m"] == pytest.approx(20.0)

    # The north gate has only a 600 veh/h crossing: no link is pinned, the reason is named.
    assert selection.loc["gate_0009", "status"] == gfl.STATUS_FALLBACK
    assert pd.isna(selection.loc["gate_0009", "facility_link_id"])
    assert selection.loc["gate_0009", "reason"] == gfl.REASON_NO_ENTRY_LINK


def test_an_entry_link_without_an_eligible_feeding_link_falls_back_with_its_own_reason():
    links = _links()
    links = links[~links["link_id"].isin(["feed_in", "ramp_on"])]
    selection = gfl.select_gate_facility_links(_gates().iloc[:1], links, EXTENT, minimum_capacity_veh_per_h=1000.0,
                                               maximum_distance_m=300.0)
    row = selection.iloc[0]
    assert row["status"] == gfl.STATUS_FALLBACK
    assert row["entry_link_id"] == "gate_in"
    assert row["reason"] == gfl.REASON_NO_FEEDING_LINK


def _homes_and_portals():
    # Two in-commuter homes at gate points, one resident home elsewhere; portal facilities of two gates and one
    # rail exit station (not a road gate: never touched).
    homes = gpd.GeoDataFrame({"household_id": [1, 2, 3]},
                             geometry=[Point(1000.0, 510.0), Point(300.0, 300.0), Point(500.0, 1000.0)], crs=CRS)
    portals = gpd.GeoDataFrame({"location_id": ["portal_gate_0004", "portal_gate_0009", "portal_rail_17"]},
                               geometry=[Point(800.0, 490.0), Point(500.0, 900.0), Point(200.0, 200.0)], crs=CRS)
    return homes, portals


def test_gate_homes_and_portal_gates_get_the_pinned_link_and_the_fallback_rate_is_logged(caplog):
    homes, portals = _homes_and_portals()
    with caplog.at_level(logging.INFO):
        homes, portals, summary = gfl.attach_gate_facility_links(homes, portals, _gates(), _select(),
                                                                 maximum_fallback_share=0.6)

    assert homes["link_id"].tolist()[0] == "feed_in"
    assert pd.isna(homes["link_id"].tolist()[1])          # resident home: placed by eqasim as before
    assert pd.isna(homes["link_id"].tolist()[2])          # gate home at the fallback gate
    links_by_portal = dict(zip(portals["location_id"], portals["link_id"]))
    assert links_by_portal["portal_gate_0004"] == "feed_in"
    assert pd.isna(links_by_portal["portal_gate_0009"])
    assert pd.isna(links_by_portal["portal_rail_17"])

    assert (summary["n_facilities"], summary["n_linked"], summary["n_fallback"]) == (4, 2, 2)
    assert summary["fallback_share"] == pytest.approx(0.5)
    # A fallback never passes silently: it is logged as a warning.
    assert any(record.levelno == logging.WARNING for record in caplog.records)


def test_a_fallback_share_above_the_bound_fails():
    homes, portals = _homes_and_portals()
    with pytest.raises(RuntimeError, match="fallback"):
        gfl.attach_gate_facility_links(homes, portals, _gates(), _select(), maximum_fallback_share=0.05)


def test_the_car_link_reader_converts_the_capacity_period_and_flags_car_links(tmp_path):
    path = tmp_path / "network.xml.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write('<?xml version="1.0" encoding="utf-8"?><network><nodes>'
                     '<node id="a" x="0.0" y="0.0"/><node id="b" x="100.0" y="0.0"/></nodes>'
                     '<links capperiod="00:30:00">'
                     '<link id="1" from="a" to="b" length="100" freespeed="10" capacity="1000" permlanes="1"'
                     ' modes="car,car_passenger"/>'
                     '<link id="2" from="b" to="a" length="100" freespeed="10" capacity="1000" permlanes="1"'
                     ' modes="pt"/></links></network>')
    links = gfl.read_car_links(str(path)).set_index("link_id")
    assert links.loc["1", "capacity_veh_per_h"] == pytest.approx(2000.0)
    assert bool(links.loc["1", "allows_car"]) and not bool(links.loc["2", "allows_car"])
    assert (links.loc["2", "from_x"], links.loc["2", "to_x"]) == (100.0, 0.0)


def test_the_writer_emits_the_pinned_link_and_nothing_else_changes(tmp_path):
    # Pin a link on one facility of each pinnable kind; the other facilities must be written exactly as before.
    import contextlib

    import matsim.scenario.facilities as base

    class _Context:
        def config(self, key, default=None):
            return {"escort_purpose": False}.get(key, default)

        def progress(self, total, label):
            class _P:
                def update(self):
                    pass
            return contextlib.nullcontext(_P())

    homes, portals = _homes_and_portals()
    homes["link_id"] = pd.Series(["feed_in", None, None], dtype=object)
    portals["link_id"] = pd.Series(["feed_in", None, None], dtype=object)
    primary = gpd.GeoDataFrame({"location_id": ["work_1"], "is_work": [True]}, geometry=[Point(1, 1)], crs=CRS)[base.PRIMARY_FIELDS]
    secondary = gpd.GeoDataFrame({"location_id": ["sec_1"], "offers_leisure": [True], "offers_shop": [False],
                                  "offers_other": [False], "offers_escort": [False]}, geometry=[Point(2, 2)], crs=CRS)[base.SECONDARY_FIELDS]
    with_links = tmp_path / "with.xml.gz"
    without_links = tmp_path / "without.xml.gz"
    base.write_facilities(str(with_links), homes, primary, secondary, _Context(), df_portal=portals)
    base.write_facilities(str(without_links), homes.drop(columns="link_id"), primary, secondary, _Context(),
                          df_portal=portals.drop(columns="link_id"))

    with gzip.open(with_links, "rb") as handle:
        root = ElementTree.fromstring(handle.read())
    link_by_facility = {f.get("id"): f.get("linkId") for f in root.findall("facility")}
    assert {k: v for k, v in link_by_facility.items() if v is not None} == {
        "home_1": "feed_in", "portal_gate_0004": "feed_in"}

    def _lines(path):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return [line for line in handle.read().splitlines() if "linkId" not in line]
    with gzip.open(without_links, "rt", encoding="utf-8") as handle:
        unchanged = handle.read().splitlines()
    assert _lines(with_links) == [line for line in unchanged
                                  if 'id="home_1"' not in line and 'id="portal_gate_0004"' not in line]


# ---------------------------------------------------------------------------------------------------------------------
# End to end through the REAL facilities stage (fake synpp context): network file, extent and gates wired as in a run.
# ---------------------------------------------------------------------------------------------------------------------

def _write_network(path):
    nodes = {}
    for _link_id, start, end, _capacity, _modes in _LINKS:
        for x, y in (start, end):
            nodes["n%.0f_%.0f" % (x, y)] = (x, y)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write('<?xml version="1.0" encoding="utf-8"?><network><nodes>')
        for node_id, (x, y) in nodes.items():
            handle.write('<node id="%s" x="%f" y="%f"/>' % (node_id, x, y))
        handle.write('</nodes><links capperiod="01:00:00">')
        for link_id, (fx, fy), (tx, ty), capacity, modes in _LINKS:
            handle.write('<link id="%s" from="n%.0f_%.0f" to="n%.0f_%.0f" length="1" freespeed="1" capacity="%f"'
                         ' permlanes="1" modes="%s"/>' % (link_id, fx, fy, tx, ty, capacity, modes))
        handle.write('</links></network>')


class _StageContext:
    def __init__(self, tmp_path, links_enabled):
        import contextlib

        self._contextlib = contextlib
        self._root = tmp_path
        (tmp_path / "supply").mkdir()
        (tmp_path / "facilities").mkdir()
        _write_network(str(tmp_path / "supply" / "network.xml.gz"))
        self._links_enabled = links_enabled
        empty_secondary = gpd.GeoDataFrame({"person_id": [], "activity_index": [],
                                            "location_id": pd.Series([], dtype=object)}, geometry=[], crs=CRS)
        realised = gpd.GeoDataFrame({"person_id": [9], "activity_index": [1],
                                     "location_id": pd.Series(["portal_gate_0004"], dtype=object)},
                                    geometry=[Point(800.0, 490.0)], crs=CRS)
        incommuter_locations = gpd.GeoDataFrame({"person_id": ["ic_1", "ic_1"], "activity_index": [0, 1],
                                                 "location_id": ["home_ic", "ic_work_ic_1"]},
                                                geometry=[Point(1000.0, 510.0), Point(500.0, 500.0)], crs=CRS)
        self._stages = {
            "synthesis.population.spatial.home.locations": gpd.GeoDataFrame(
                {"household_id": [1]}, geometry=[Point(300.0, 300.0)], crs=CRS),
            "synthesis.population.spatial.primary.locations": (
                gpd.GeoDataFrame({"location_id": ["work_1"]}, geometry=[Point(100.0, 0.0)], crs=CRS),
                gpd.GeoDataFrame({"location_id": ["edu_1"]}, geometry=[Point(200.0, 0.0)], crs=CRS)),
            "synthesis.locations.secondary": gpd.GeoDataFrame({
                "location_id": ["sec_b_1"], "offers_leisure": [False], "offers_shop": [True],
                "offers_other": [False], "offers_escort": [False]}, geometry=[Point(10.0, 0.0)], crs=CRS),
            "synthesis.population.spatial.secondary.locations": (realised, None),
            "braunschweig.synthesis.incommuters": {
                "locations": incommuter_locations,
                "persons": pd.DataFrame({"person_id": ["ic_1"], "household_id": ["ic_hh_1"]})},
            "braunschweig.synthesis.student_incommuters": {"locations": empty_secondary, "persons": pd.DataFrame()},
            "braunschweig.synthesis.cordon_gates": {"gates": _gates()},
            "matsim.scenario.supply.processed": {"network_path": "network.xml.gz"},
            "data.spatial.municipalities": gpd.GeoDataFrame({"municipality_id": ["m"]}, geometry=[EXTENT], crs=CRS),
        }

    def config(self, key, default=None, volatile=False):
        values = {"secondary_building_potentials": False, "cordon_enabled": True, "escort_household_link": False,
                  "escort_purpose": False, "cordon_gate_facility_links_enabled": self._links_enabled,
                  "cordon_gate_facility_link_minimum_capacity_veh_per_h": 1000.0,
                  "cordon_gate_facility_link_maximum_distance_m": 300.0,
                  "cordon_gate_facility_link_maximum_fallback_share": 0.05,
                  "cordon_network_buffer_fraction": 0.0}
        return values.get(key, default)

    def stage(self, name):
        return self._stages[name]

    def path(self, name=None):
        return str(self._root / ("supply" if name == "matsim.scenario.supply.processed" else "facilities"))

    def progress(self, total, label):
        class _P:
            def update(self):
                pass
        return self._contextlib.nullcontext(_P())


@pytest.mark.parametrize("links_enabled", [True, False])
def test_the_facilities_stage_pins_the_gate_facilities_and_writes_nothing_when_off(tmp_path, links_enabled):
    from braunschweig.matsim.scenario import facilities as bs_facilities

    context = _StageContext(tmp_path, links_enabled)
    bs_facilities.execute(context)
    with gzip.open(tmp_path / "facilities" / "facilities.xml.gz", "rb") as handle:
        root = ElementTree.fromstring(handle.read())
    links = {f.get("id"): f.get("linkId") for f in root.findall("facility")}

    if links_enabled:
        # The in-commuter gate home sits on the OUTWARD carriageway gate point, the portal on the inward one: both
        # resolve to the outside link feeding the inward motorway; the resident home and every other facility
        # keep eqasim's placement.
        assert {k: v for k, v in links.items() if v is not None} == {
            "home_ic_hh_1": "feed_in", "portal_gate_0004": "feed_in"}
        selection = pd.read_csv(tmp_path / "facilities" / "gate_facility_links.csv")
        assert sorted(selection["gate_id"]) == ["gate_0004", "gate_0006", "gate_0009"]
    else:
        assert all(v is None for v in links.values())
        assert not (tmp_path / "facilities" / "gate_facility_links.csv").exists()


def test_the_preparation_check_fails_when_the_jar_replaced_a_pinned_link(tmp_path):
    # The written file pins one gate facility; a prepared file that kept it passes, one in which eqasim's old
    # FacilityPlacement put it on the nearest street fails (the silent Java-side fallback of an outdated jar).
    def _write(path, home_link):
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            handle.write('<facilities>\n<facility id="home_1" linkId="%s" x="1.0" y="2.0">\n</facility>\n'
                         '<facility id="home_2" linkId="street" x="3.0" y="4.0">\n</facility>\n</facilities>\n'
                         % home_link)

    written = tmp_path / "written.xml.gz"
    with gzip.open(written, "wt", encoding="utf-8") as handle:
        handle.write('<facilities>\n<facility id="home_1" linkId="feed_in" x="1.0" y="2.0">\n</facility>\n'
                     '<facility id="home_2" x="3.0" y="4.0">\n</facility>\n</facilities>\n')
    kept, replaced = tmp_path / "kept.xml.gz", tmp_path / "replaced.xml.gz"
    _write(kept, "feed_in")
    _write(replaced, "street")

    assert gfl.verify_preset_links_kept(str(written), str(kept)) == 1
    with pytest.raises(RuntimeError, match="does not keep preset facility links"):
        gfl.verify_preset_links_kept(str(written), str(replaced))
