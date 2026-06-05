"""Test the importable MATSim-network link reader for the cordon module."""
import gzip
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.data.cordon.network import read_matsim_links  # noqa: E402

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
