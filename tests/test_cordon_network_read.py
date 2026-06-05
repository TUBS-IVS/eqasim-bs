"""Test the importable MATSim-network link reader for the cordon module."""
import gzip
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.data.cordon.network import (  # noqa: E402
    read_matsim_links,
    read_transit_stops_routes,
)

SCHEDULE = b"""<?xml version="1.0"?><transitSchedule><transitStops>
<stopFacility id="s1" x="600000" y="5800000"/>
<stopFacility id="s2" x="650000" y="5800000"/></transitStops>
<transitLine><transitRoute><transportMode>rail</transportMode>
<routeProfile><stop refId="s1"/><stop refId="s2"/></routeProfile>
</transitRoute></transitLine></transitSchedule>"""

NETWORK = b"""<?xml version="1.0"?><network><nodes>
<node id="1" x="600000" y="5800000"/><node id="2" x="601000" y="5800000"/></nodes>
<links><link id="L1" from="1" to="2" capacity="8000">
<attributes><attribute name="osm:way:highway" class="java.lang.String">motorway</attribute></attributes>
</link></links></network>"""


def test_read_matsim_links_parses_highway_and_capacity(tmp_path):
    p = tmp_path / "network.xml.gz"
    with gzip.open(p, "wb") as fh:
        fh.write(NETWORK)
    links = read_matsim_links(str(p), crs="EPSG:25832")
    assert list(links.columns) == ["link_id", "capacity", "road_class", "geometry"]
    row = links.iloc[0]
    assert row["road_class"] == "motorway" and row["capacity"] == 8000.0
    assert str(links.crs) == "EPSG:25832"


def test_read_transit_stops_routes(tmp_path):
    p = tmp_path / "schedule.xml.gz"
    with gzip.open(p, "wb") as fh:
        fh.write(SCHEDULE)
    stops, routes = read_transit_stops_routes(str(p))
    assert stops == {"s1": (600000.0, 5800000.0), "s2": (650000.0, 5800000.0)}
    assert routes == [("rail", ["s1", "s2"])]
