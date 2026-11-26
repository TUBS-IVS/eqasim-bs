"""Emissions view of a run archive (#688): the archive stays untouched, the view is complete.

The archive is a small synthetic one in the exact layout MATSim writes (tab indentation,
``engineInformation`` before ``costInformation``, links with and without attributes).
"""
from __future__ import annotations

import gzip
import hashlib
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from braunschweig.emissions.view import EmissionsViewSettings, build_emissions_view

_NS = {"m": "http://www.matsim.org/files/dtd"}

_ENGINE = """\t\t<engineInformation>
\t\t\t<attributes>
\t\t\t\t<attribute name="HbefaEmissionsConcept" class="java.lang.String">{concept}</attribute>
\t\t\t\t<attribute name="HbefaSizeClass" class="java.lang.String">medium</attribute>
\t\t\t\t<attribute name="HbefaTechnology" class="java.lang.String">{technology}</attribute>
\t\t\t\t<attribute name="HbefaVehicleCategory" class="java.lang.String">PASSENGER_CAR</attribute>
\t\t\t</attributes>
\t\t</engineInformation>
"""

_TYPE = """\t<vehicleType id="{type_id}">

\t\t<capacity seats="4" standingRoomInPersons="0">

\t\t</capacity>
\t\t<length meter="7.5"/>
\t\t<width meter="1.0"/>
{engine}\t\t<costInformation>

\t\t</costInformation>
\t\t<passengerCarEquivalents pce="1.0"/>
\t\t<networkMode networkMode="{mode}"/>
\t\t<flowEfficiencyFactor factor="1.0"/>
\t</vehicleType>

"""

_HEAD = (
    '<?xml version="1.0" encoding="UTF-8"?>\n\n'
    '<vehicleDefinitions xmlns="http://www.matsim.org/files/dtd" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
    'xsi:schemaLocation="http://www.matsim.org/files/dtd '
    'http://www.matsim.org/files/dtd/vehicleDefinitions_v2.0.xsd">\n\n'
)

# A transit type as MATSim writes it: own top-level attributes, a maximum velocity, no engine.
_TRANSIT_TYPE = """\t<vehicleType id="Bus">
\t\t<attributes>
\t\t\t<attribute name="accessTimeInSecondsPerPerson" class="java.lang.Double">0.5</attribute>
\t\t</attributes>
\t\t<capacity seats="70" standingRoomInPersons="0">

\t\t</capacity>
\t\t<length meter="18.0"/>
\t\t<width meter="2.5"/>
\t\t<maximumVelocity meterPerSecond="22.2"/>
\t\t<costInformation>

\t\t</costInformation>
\t\t<passengerCarEquivalents pce="2.8"/>
\t\t<networkMode networkMode="car"/>
\t\t<flowEfficiencyFactor factor="1.0"/>
\t</vehicleType>

"""

_NETWORK = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE network SYSTEM "http://www.matsim.org/files/dtd/network_v2.dtd">
<network>
\t<attributes>
\t\t<attribute name="coordinateReferenceSystem" class="java.lang.String">NETWORK_CRS</attribute>
\t</attributes>
\t<nodes>
\t\t<node id="1" x="0.0" y="0.0" >
\t\t</node>
\t</nodes>
\t<links effectivecellsize="7.5" capperiod="01:00:00" >
\t\t<link id="10" from="1" to="2" length="100.0" freespeed="13.9" capacity="600.0" permlanes="1.0" oneway="1" modes="car,truck" >
\t\t\t<attributes>
\t\t\t\t<attribute name="osm:way:highway" class="java.lang.String">residential</attribute>
\t\t\t</attributes>
\t\t</link>
\t\t<link id="11" from="2" to="1" length="50.0" freespeed="27.8" capacity="600.0" permlanes="1.0" oneway="1" modes="car" >
\t\t</link>
\t\t<link id="12" from="2" to="1" length="50.0" freespeed="27.8" capacity="600.0" permlanes="1.0" oneway="1" modes="pt" />
\t\t<link id="13" from="2" to="1" length="50.0" freespeed="8.3" capacity="600.0" permlanes="1.0" oneway="1" modes="car" >
\t\t\t<attributes>
\t\t\t\t<attribute name="hbefa_road_type" class="java.lang.String">OLD/Type/1</attribute>
\t\t\t\t<attribute name="osm:way:name" class="java.lang.String">A &amp; B</attribute>
\t\t\t</attributes>
\t\t</link>
\t</links>
</network>
"""

_CONFIG = """<?xml version="1.0" ?>
<config>
\t<module name="global" >
\t\t<param name="coordinateSystem" value="Atlantis" />
\t</module>
\t<module name="households" >
\t\t<param name="inputFile" value="households.xml.gz" />
\t</module>
</config>
"""


def _gz(path: Path, text: str) -> None:
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        handle.write(text)


# As MATSim writes it when no coordinate system was configured: the placeholder "Atlantis".
_SCHEDULE = """<transitSchedule>
\t<attributes>
\t\t<attribute name="coordinateReferenceSystem" class="java.lang.String">Atlantis</attribute>
\t</attributes>
</transitSchedule>
"""


def _write_archive(archive: Path, *, extra_vehicle_type: str = "", network_crs: str = "Atlantis") -> None:
    archive.mkdir()
    vehicles = (
        _HEAD
        + _TYPE.format(type_id="car_x_medium_pc_hybrid", mode="car",
                       engine=_ENGINE.format(concept="PC P-Hybrid", technology="petrol (hybrid)"))
        + _TYPE.format(type_id="default_car", mode="car",
                       engine=_ENGINE.format(concept="average", technology="average"))
        + _TYPE.format(type_id="default_car_passenger", mode="car_passenger",
                       engine=_ENGINE.format(concept="average", technology="average"))
        + _TYPE.format(type_id="heavy_truck", mode="truck", engine="")
        + extra_vehicle_type
        + '\t<vehicle id="1:car:1" type="car_x_medium_pc_hybrid">\n\t</vehicle>\n\n</vehicleDefinitions>\n'
    )
    _gz(archive / "output_vehicles.xml.gz", vehicles)
    _gz(archive / "output_transitVehicles.xml.gz",
        _HEAD + _TRANSIT_TYPE + "</vehicleDefinitions>\n")
    _gz(archive / "output_network.xml.gz", _NETWORK.replace("NETWORK_CRS", network_crs))
    _gz(archive / "output_transitSchedule.xml.gz", _SCHEDULE)
    _gz(archive / "output_events.xml.gz", "<events/>\n")
    (archive / "output_config.xml").write_text(_CONFIG, encoding="utf-8", newline="")


def _digest(folder: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.iterdir())}


def _settings(tmp_path: Path) -> EmissionsViewSettings:
    return EmissionsViewSettings(
        tables={key: str(tmp_path / f"{key}.csv")
                for key in ("cold_average", "warm_average", "cold_detailed", "warm_detailed")},
        lookup_behavior="tryDetailedThenTechnologyAverageThenAverageTable",
        consistency_check_level="none",
        computation_method="StopAndGoFraction",
        handle_high_average_speeds=False,
        coordinate_system="EPSG:25832",
    )


def _read_gz(path: Path) -> str:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return handle.read()


def test_view_is_complete_and_the_archive_stays_byte_identical(tmp_path):
    archive = tmp_path / "matsim_output"
    _write_archive(archive)
    before = _digest(archive)
    seen_links = []

    def road_type_of_link(link):
        seen_links.append(link)
        return "URB/Access/30" if link.attributes.get("osm:way:highway") == "residential" else "RUR/MW/100"

    view = tmp_path / "view"
    report = build_emissions_view(archive, view, _settings(tmp_path), road_type_of_link)

    assert _digest(archive) == before

    # The road-type function saw every link shape: with attributes, without, self-closing, and
    # one that already carried a road type (replaced and counted, never duplicated).
    assert [(l.link_id, l.attributes.get("osm:way:highway")) for l in seen_links] == [
        ("10", "residential"), ("11", None), ("12", None), ("13", None)]
    assert seen_links[1].freespeed_meters_per_second == pytest.approx(27.8)
    assert seen_links[3].attributes["osm:way:name"] == "A & B"
    assert report.links == 4
    assert report.road_types_replaced == 1

    # Vehicle types: engine information resolved, sitting before costInformation.
    types = {
        t.get("id"): t
        for t in ET.fromstring(_read_gz(view / "output_vehicles.xml.gz")).findall("m:vehicleType", _NS)
    }
    assert len(types) == 4
    for vehicle_type in types.values():
        children = [child.tag.split("}")[1] for child in vehicle_type]
        assert children.index("engineInformation") < children.index("costInformation")

    def engine(type_id):
        node = types[type_id].find("m:engineInformation/m:attributes", _NS)
        return {a.get("name"): a.text for a in node}

    assert engine("car_x_medium_pc_hybrid")["HbefaTechnology"] == "petrol (4S)"
    assert engine("car_x_medium_pc_hybrid")["HbefaEmissionsConcept"] == "PC P-Hybrid"
    assert engine("default_car_passenger")["HbefaVehicleCategory"] == "NON_HBEFA_VEHICLE"
    assert engine("heavy_truck")["HbefaVehicleCategory"] == "HEAVY_GOODS_VEHICLE"
    assert [r.excluded for r in report.transit_vehicle_types] == [True]
    bus = ET.fromstring(_read_gz(view / "output_transitVehicles.xml.gz")).find("m:vehicleType", _NS)
    assert [child.tag.split("}")[1] for child in bus] == [
        "attributes", "capacity", "length", "width", "maximumVelocity", "engineInformation",
        "costInformation", "passengerCarEquivalents", "networkMode", "flowEfficiencyFactor"]
    assert bus.find("m:attributes/m:attribute", _NS).get("name") == "accessTimeInSecondsPerPerson"
    # Vehicles themselves are copied unchanged.
    assert '<vehicle id="1:car:1" type="car_x_medium_pc_hybrid">' in _read_gz(view / "output_vehicles.xml.gz")

    # Network: every link carries the road type; untouched parts survive.
    network = _read_gz(view / "output_network.xml.gz")
    assert network.count('name="hbefa_road_type"') == 4
    assert "OLD/Type/1" not in network
    assert "A &amp; B" in network
    assert 'hbefa_road_type" class="java.lang.String">URB/Access/30<' in network
    assert 'hbefa_road_type" class="java.lang.String">RUR/MW/100<' in network
    assert 'name="osm:way:highway"' in network
    ET.fromstring(network.split("\n", 2)[2])  # still well-formed after the DOCTYPE line

    # Config: emissions module added, households nulled, CRS replaced, nothing else lost.
    config = (view / "output_config.xml").read_text(encoding="utf-8")
    assert '<param name="inputFile" value="null" />' in config
    assert '<param name="coordinateSystem" value="EPSG:25832" />' in config
    assert '<param name="emissionsComputationMethod" value="StopAndGoFraction" />' in config
    assert f'value="{tmp_path / "warm_average.csv"}"' in config
    assert "</config>" in config

    # A rebuild from the same archive is byte-identical (fixed gzip header, no timestamps).
    second = tmp_path / "view_again"
    build_emissions_view(archive, second, _settings(tmp_path), road_type_of_link)
    for name in ("output_vehicles.xml.gz", "output_transitVehicles.xml.gz", "output_network.xml.gz",
                 "output_config.xml"):
        assert (second / name).read_bytes() == (view / name).read_bytes(), name

    # Events are reachable under their archive name.
    assert _read_gz(view / "output_events.xml.gz") == "<events/>\n"
    assert report.events_link_mode in {"symlinked", "copied"}

    # The tool transforms every file from its own label to the config's system and stops on the
    # placeholder "Atlantis": network, schedule and config have to name one system.
    label = '<attribute name="coordinateReferenceSystem" class="java.lang.String">EPSG:25832</attribute>'
    schedule = _read_gz(view / "output_transitSchedule.xml.gz")
    assert label in network and label in schedule
    assert "Atlantis" not in network + schedule + config
    assert report.coordinate_system_labels == {
        "output_network.xml.gz": "Atlantis", "output_transitSchedule.xml.gz": "Atlantis",
        "output_config.xml": "Atlantis"}


def test_a_file_in_another_coordinate_system_is_refused_not_relabelled(tmp_path):
    archive = tmp_path / "matsim_output"
    _write_archive(archive, network_crs="EPSG:4326")
    view = tmp_path / "view"

    # Relabelling would declare WGS84 degrees to be metres; the view never transforms coordinates.
    with pytest.raises(ValueError, match=r"output_network.*EPSG:4326.*EPSG:25832"):
        build_emissions_view(archive, view, _settings(tmp_path), lambda link: "URB/Local/50")
    assert not view.exists()


def test_unknown_vehicle_type_aborts_and_leaves_no_half_view(tmp_path):
    archive = tmp_path / "matsim_output"
    _write_archive(archive, extra_vehicle_type=_TYPE.format(type_id="mystery_van", mode="car", engine=""))
    view = tmp_path / "view"

    with pytest.raises(ValueError, match="mystery_van"):
        build_emissions_view(archive, view, _settings(tmp_path), lambda link: "URB/Local/50")
    assert not view.exists()


def test_existing_view_is_never_overwritten(tmp_path):
    archive = tmp_path / "matsim_output"
    _write_archive(archive)
    view = tmp_path / "view"
    view.mkdir()
    (view / "keep.txt").write_text("precious")

    with pytest.raises(FileExistsError):
        build_emissions_view(archive, view, _settings(tmp_path), lambda link: "URB/Local/50")
    assert (view / "keep.txt").read_text() == "precious"
