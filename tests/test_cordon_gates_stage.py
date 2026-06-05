"""Test the cordon gates stage's pure end-to-end builder (network ∩ cordon -> gravity)."""
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, box, Point

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.synthesis.cordon_gates import build_gates_with_volume  # noqa: E402


def test_build_gates_with_volume_end_to_end():
    # Municipalities = one square; network = links crossing its buffered boundary.
    muni = gpd.GeoDataFrame({"commune_id": ["03101000"]},
                            geometry=[box(600000, 5800000, 610000, 5810000)],
                            crs="EPSG:25832")
    links = gpd.GeoDataFrame(
        {"link_id": ["a", "b"], "capacity": [8000.0, 1000.0],
         "road_class": ["motorway", "secondary"]},
        geometry=[LineString([(605000, 5805000), (605000, 5830000)]),   # crosses N
                  LineString([(605000, 5805000), (630000, 5805000)])],  # crosses E
        crs="EPSG:25832")
    gemeinden = gpd.GeoDataFrame({"ars5": ["03241"], "ewz": [500000]},
                                 geometry=[Point(605000, 5840000)], crs="EPSG:25832")
    flows = pd.DataFrame([("03241", "03101", 1000), ("03101", "03241", 800)],
                         columns=["orig_ars", "dest_ars", "flow"])
    gates, assignment = build_gates_with_volume(
        muni, links, gemeinden, flows, zgb_kreise={"03101"},
        buffer_fraction=0.10, road_classes=("motorway", "trunk", "primary"),
        beta=-0.05, capacity_exponent=1.0)
    # Only the motorway link qualifies (secondary excluded), one gate, with a gate_id.
    assert len(gates) == 1
    assert gates.iloc[0]["road_class"] == "motorway"
    assert gates.iloc[0]["gate_id"] == "gate_0000"
    # The single external Kreis routes its inbound+outbound to that gate.
    assert set(assignment["gate_id"]) == {"gate_0000"}
    assert int(assignment["inbound"].sum()) == 1000
    assert int(assignment["outbound"].sum()) == 800
