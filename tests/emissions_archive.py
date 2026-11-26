"""A tiny synthetic MATSim run archive for the emissions stage tests (#688).

Layout as MATSim writes it (tab indentation, ``engineInformation`` before ``costInformation``).
Not a test module (no ``test_`` prefix).

Content: one fleet type that matches the petrol sub-segment of ``tests/emissions_tables.py``,
``default_car``, ``default_car_passenger``, ``heavy_truck`` (no engine information) and a transit
type; optionally a diesel fleet type the tables only answer below the detailed level. Three links:
a tertiary one at 50 km/h (1000 m, ``URB/Local/50``), a residential one at 30 km/h (500 m,
``URB/Access/30``) and a transit-only one. Driving: the petrol car 2500 m (1000 m of it after
24:00), ``default_car`` 1000 m, the truck 1000 m.
"""
from __future__ import annotations

import gzip
from pathlib import Path

_ENGINE = """\t\t<engineInformation>
\t\t\t<attributes>
\t\t\t\t<attribute name="HbefaEmissionsConcept" class="java.lang.String">{concept}</attribute>
\t\t\t\t<attribute name="HbefaSizeClass" class="java.lang.String">{size}</attribute>
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

_HEAD = '<?xml version="1.0" encoding="UTF-8"?>\n\n<vehicleDefinitions xmlns="http://www.matsim.org/files/dtd">\n\n'

_NETWORK = """<?xml version="1.0" encoding="UTF-8"?>
<network>
\t<links effectivecellsize="7.5" capperiod="01:00:00" >
\t\t<link id="1" from="1" to="2" length="1000.0" freespeed="13.9" capacity="600.0" permlanes="1.0" oneway="1" modes="car,truck" >
\t\t\t<attributes>
\t\t\t\t<attribute name="osm:way:highway" class="java.lang.String">tertiary</attribute>
\t\t\t</attributes>
\t\t</link>
\t\t<link id="2" from="2" to="1" length="500.0" freespeed="8.33" capacity="600.0" permlanes="1.0" oneway="1" modes="car" >
\t\t\t<attributes>
\t\t\t\t<attribute name="osm:way:highway" class="java.lang.String">residential</attribute>
\t\t\t</attributes>
\t\t</link>
\t\t<link id="3" from="2" to="1" length="300.0" freespeed="20.0" capacity="600.0" permlanes="1.0" oneway="1" modes="pt" >
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

_EVENTS = [
    (100.0, "1", "v_petrol"),
    (200.0, "2", "v_petrol"),
    (300.0, "1", "v_default"),
    (400.0, "1", "v_truck"),
    (90000.0, "1", "v_petrol"),
]


def _gz(path: Path, text: str) -> None:
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        handle.write(text)


def _car(type_id, technology, size, concept, mode="car"):
    return _TYPE.format(type_id=type_id, mode=mode,
                        engine=_ENGINE.format(technology=technology, size=size, concept=concept))


def write_archive(output_path: Path, *, with_diesel: bool = False) -> Path:
    """Write ``<output_path>/matsim_output`` and return it."""
    archive = Path(output_path) / "matsim_output"
    archive.mkdir(parents=True)
    types = _car("car_petrol", "petrol (4S)", "not specified", "PC P Euro-6")
    vehicles = ["v_petrol:car_petrol", "v_default:default_car", "v_truck:heavy_truck"]
    if with_diesel:
        types += _car("car_diesel", "diesel", "not specified", "PC D Euro-6")
        vehicles.append("v_diesel:car_diesel")
    types += _car("default_car", "average", "average", "average")
    types += _car("default_car_passenger", "average", "average", "average", mode="car_passenger")
    types += _TYPE.format(type_id="heavy_truck", mode="truck", engine="")
    body = "".join(
        '\t<vehicle id="{}" type="{}">\n\t</vehicle>\n'.format(*vehicle.split(":")) for vehicle in vehicles)
    _gz(archive / "output_vehicles.xml.gz", _HEAD + types + body + "\n</vehicleDefinitions>\n")
    _gz(archive / "output_transitVehicles.xml.gz",
        _HEAD + _TYPE.format(type_id="Bus", mode="car", engine="")
        + '\t<vehicle id="bus_1" type="Bus"/>\n\n</vehicleDefinitions>\n')
    _gz(archive / "output_network.xml.gz", _NETWORK)
    _gz(archive / "output_transitSchedule.xml.gz", "<transitSchedule/>\n")
    _gz(archive / "output_events.xml.gz", '<?xml version="1.0" encoding="utf-8"?>\n<events version="1.0">\n' + "".join(
        f'\t<event time="{time}" type="entered link" link="{link}" vehicle="{vehicle}"  />\n'
        for time, link, vehicle in _EVENTS) + "</events>\n")
    (archive / "output_config.xml").write_text(_CONFIG, encoding="utf-8")
    return archive
