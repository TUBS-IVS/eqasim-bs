"""Build the emissions view of a MATSim run archive (post-run emissions, #688).

``org.matsim.application.analysis.emissions.AirPollutionAnalysis`` loads a whole scenario from a
folder: it finds ``output_vehicles``, ``output_transitVehicles``, ``output_network``,
``output_transitSchedule``, ``output_events`` and ``output_config.xml`` by name, and takes the
HBEFA table paths and settings only from the config module ``emissions``. The archive itself
is read-only and must stay byte-identical (other stages and the analysis read it), so this module
writes a separate *view* folder:

* ``output_vehicles`` / ``output_transitVehicles``: every vehicle type gets the engine
  information resolved by :mod:`braunschweig.emissions.vehicle_types`; the vehicles themselves
  are copied unchanged;
* ``output_network``: every link gets the attribute ``hbefa_road_type`` from a caller-supplied
  function (the road-type method is decided elsewhere);
* ``output_config.xml``: archive config with the ``emissions`` module added and
  ``households.inputFile`` nulled (the tool loads the scenario from the config);
* ``output_events`` and ``output_transitSchedule``: linked, not copied (events are GBs).

The files are rewritten line by line rather than through an XML library so that everything the
rewrite does not touch stays byte-for-byte as MATSim wrote it. Output is gzip with a fixed
header timestamp, so a rebuilt view is byte-identical for identical inputs.

Scientific content (which type emits what) lives in the committed tables, not here.
"""
from __future__ import annotations

import contextlib
import gzip
import io
import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator, Mapping

from braunschweig.emissions.vehicle_types import TypeResolution, resolve_vehicle_type

logger = logging.getLogger("braunschweig.emissions.view")

#: Table keys required in :attr:`EmissionsViewSettings.tables`.
TABLE_KEYS = ("cold_average", "warm_average", "cold_detailed", "warm_detailed")

#: Link attribute the contrib reads the HBEFA road type from.
ROAD_TYPE_ATTRIBUTE = "hbefa_road_type"

_TABLE_PARAMETERS = {
    "cold_average": "averageFleetColdEmissionFactorsFile",
    "warm_average": "averageFleetWarmEmissionFactorsFile",
    "cold_detailed": "detailedColdEmissionFactorsFile",
    "warm_detailed": "detailedWarmEmissionFactorsFile",
}

_ENGINE_ATTRIBUTES = (
    ("HbefaEmissionsConcept", "emissions_concept"),
    ("HbefaSizeClass", "size_class"),
    ("HbefaTechnology", "technology"),
    ("HbefaVehicleCategory", "category"),
)

# Children of a vehicleType that the MATSim schema places AFTER engineInformation.
_AFTER_ENGINE_TAGS = (
    "<costInformation",
    "<passengerCarEquivalents",
    "<networkMode",
    "<flowEfficiencyFactor",
)

_VEHICLE_TYPE_OPEN = re.compile(r'<vehicleType id="([^"]+)"')
_ATTRIBUTE_LINE = re.compile(r'<attribute name="([^"]+)"[^>]*>([^<]*)</attribute>')
_XML_ATTRIBUTE = re.compile(r'(\w+)="([^"]*)"')


@dataclass(frozen=True)
class EmissionsViewSettings:
    """Settings written into the ``emissions`` config module (no defaults for method choices)."""

    tables: Mapping[str, str]
    lookup_behavior: str
    consistency_check_level: str
    computation_method: str
    handle_high_average_speeds: bool
    vehicle_description_source: str = "asEngineInformationAttributes"
    non_scenario_vehicles: str = "abort"
    coordinate_system: str | None = None


@dataclass(frozen=True)
class LinkRecord:
    """The parts of a network link a road-type method may use."""

    link_id: str
    length_meters: float
    freespeed_meters_per_second: float
    modes: str
    attributes: Mapping[str, str]


@dataclass
class ViewReport:
    """Counts of what the view builder saw and changed (feeds the stage report)."""

    vehicle_types: list[TypeResolution] = field(default_factory=list)
    transit_vehicle_types: list[TypeResolution] = field(default_factory=list)
    links: int = 0
    events_link_mode: str = ""


def build_emissions_view(
    archive: Path,
    view_dir: Path,
    settings: EmissionsViewSettings,
    road_type_of_link: Callable[[LinkRecord], str],
) -> ViewReport:
    """Write the emissions view of ``archive`` into the new folder ``view_dir``.

    ``view_dir`` must not exist (never overwritten). On any failure the half-written view is
    removed and the error re-raised. The archive is only read.
    """
    archive = Path(archive)
    view_dir = Path(view_dir)
    _validate_settings(settings)
    for name in (
        "output_vehicles.xml.gz",
        "output_transitVehicles.xml.gz",
        "output_network.xml.gz",
        "output_transitSchedule.xml.gz",
        "output_events.xml.gz",
        "output_config.xml",
    ):
        if not (archive / name).is_file():
            raise FileNotFoundError(f"archive {archive} lacks {name}; cannot build an emissions view")
    view_dir.mkdir(parents=True, exist_ok=False)
    report = ViewReport()
    try:
        report.vehicle_types = _rewrite_vehicle_types(
            archive / "output_vehicles.xml.gz", view_dir / "output_vehicles.xml.gz"
        )
        report.transit_vehicle_types = _rewrite_vehicle_types(
            archive / "output_transitVehicles.xml.gz", view_dir / "output_transitVehicles.xml.gz"
        )
        report.links = _rewrite_network(
            archive / "output_network.xml.gz", view_dir / "output_network.xml.gz", road_type_of_link
        )
        _rewrite_config(archive / "output_config.xml", view_dir / "output_config.xml", settings)
        report.events_link_mode = _link_or_copy(archive / "output_events.xml.gz", view_dir)
        _link_or_copy(archive / "output_transitSchedule.xml.gz", view_dir)
    except BaseException:
        shutil.rmtree(view_dir, ignore_errors=True)
        raise
    logger.info(
        "[emissions.view] %s: %d vehicle types (%d excluded), %d transit types, %d links tagged, "
        "events %s",
        view_dir,
        len(report.vehicle_types),
        sum(r.excluded for r in report.vehicle_types),
        len(report.transit_vehicle_types),
        report.links,
        report.events_link_mode,
    )
    return report


def _validate_settings(settings: EmissionsViewSettings) -> None:
    missing = [key for key in TABLE_KEYS if key not in settings.tables]
    if missing:
        raise ValueError(f"emissions settings lack table paths {missing}")


def _open_gz_read(path: Path):
    return gzip.open(path, "rt", encoding="utf-8", newline="")


@contextlib.contextmanager
def _open_gz_write(path: Path):
    # mtime=0 and no embedded file name: identical input gives a byte-identical file.
    with open(path, "wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8", newline="") as text:
                yield text


def _blocks(reader, open_pattern: str, close_pattern: str) -> Iterator[tuple[bool, list[str]]]:
    """Yield ``(is_block, lines)``: lines inside an open..close block, or single other lines.

    A line holding the opening pattern that also ends in ``/>`` is a one-line block.
    """
    block: list[str] | None = None
    for line in reader:
        if block is None:
            if open_pattern in line:
                if line.rstrip().endswith("/>") or close_pattern in line:
                    yield True, [line]
                else:
                    block = [line]
            else:
                yield False, [line]
        else:
            block.append(line)
            if close_pattern in line:
                yield True, block
                block = None
    if block is not None:
        raise ValueError(f"unterminated block starting with {block[0].strip()!r}")


def _engine_block(resolution: TypeResolution) -> list[str]:
    keys = resolution.keys
    lines = ["\t\t<engineInformation>\n", "\t\t\t<attributes>\n"]
    for attribute, field_name in _ENGINE_ATTRIBUTES:
        lines.append(
            f'\t\t\t\t<attribute name="{attribute}" class="java.lang.String">'
            f"{getattr(keys, field_name)}</attribute>\n"
        )
    lines += ["\t\t\t</attributes>\n", "\t\t</engineInformation>\n"]
    return lines


def _rewrite_vehicle_type(block: list[str]) -> tuple[list[str], TypeResolution]:
    type_id = _VEHICLE_TYPE_OPEN.search(block[0]).group(1)
    engine_attributes: dict[str, str] = {}
    kept: list[str] = []
    in_engine = False
    for line in block:
        if "<engineInformation" in line:
            in_engine = True
        if in_engine:
            match = _ATTRIBUTE_LINE.search(line)
            if match:
                engine_attributes[match.group(1)] = match.group(2)
        else:
            kept.append(line)
        if "</engineInformation>" in line:
            in_engine = False
    resolution = resolve_vehicle_type(type_id, engine_attributes)
    insert_at = next(
        (i for i, line in enumerate(kept) if any(tag in line for tag in _AFTER_ENGINE_TAGS)),
        len(kept) - 1,
    )
    return kept[:insert_at] + _engine_block(resolution) + kept[insert_at:], resolution


def _rewrite_vehicle_types(source: Path, target: Path) -> list[TypeResolution]:
    resolutions: list[TypeResolution] = []
    with _open_gz_read(source) as reader, _open_gz_write(target) as writer:
        for is_block, lines in _blocks(reader, "<vehicleType ", "</vehicleType>"):
            if is_block:
                if len(lines) == 1:
                    raise ValueError(f"{source.name}: one-line vehicleType not supported: {lines[0]!r}")
                lines, resolution = _rewrite_vehicle_type(lines)
                resolutions.append(resolution)
            writer.writelines(lines)
    if not resolutions:
        raise ValueError(f"{source} holds no vehicle types")
    return resolutions


def _link_record(block: list[str]) -> LinkRecord:
    head = dict(_XML_ATTRIBUTE.findall(block[0]))
    attributes = {m.group(1): m.group(2) for line in block[1:] if (m := _ATTRIBUTE_LINE.search(line))}
    return LinkRecord(
        link_id=head["id"],
        length_meters=float(head["length"]),
        freespeed_meters_per_second=float(head["freespeed"]),
        modes=head.get("modes", ""),
        attributes=attributes,
    )


def _rewrite_link(block: list[str], road_type: str) -> list[str]:
    attribute = f'\t\t\t\t<attribute name="{ROAD_TYPE_ATTRIBUTE}" class="java.lang.String">{road_type}</attribute>\n'
    head = block[0]
    body = [line for line in block[1:] if f'name="{ROAD_TYPE_ATTRIBUTE}"' not in line]
    if head.rstrip().endswith("/>"):
        return [head.rstrip()[:-2].rstrip() + " >\n", "\t\t\t<attributes>\n", attribute,
                "\t\t\t</attributes>\n", "\t\t</link>\n"]
    for i, line in enumerate(body):
        if "</attributes>" in line:
            return [head] + body[:i] + [attribute] + body[i:]
    return [head, "\t\t\t<attributes>\n", attribute, "\t\t\t</attributes>\n"] + body


def _rewrite_network(source: Path, target: Path, road_type_of_link: Callable[[LinkRecord], str]) -> int:
    links = 0
    with _open_gz_read(source) as reader, _open_gz_write(target) as writer:
        for is_block, lines in _blocks(reader, "<link ", "</link>"):
            if is_block:
                road_type = road_type_of_link(_link_record(lines))
                if not road_type:
                    raise ValueError(f"road type function returned an empty value for link {lines[0].strip()!r}")
                lines = _rewrite_link(lines, road_type)
                links += 1
            writer.writelines(lines)
    if links == 0:
        raise ValueError(f"{source} holds no links")
    return links


def _replace_exactly_once(text: str, pattern: str, replacement: str, what: str) -> str:
    text, count = re.subn(pattern, replacement, text)
    if count != 1:
        raise ValueError(f"output_config.xml: expected exactly one {what}, found {count}")
    return text


def _rewrite_config(source: Path, target: Path, settings: EmissionsViewSettings) -> None:
    text = source.read_text(encoding="utf-8")
    if '<module name="emissions"' in text:
        raise ValueError(f"{source} already has an emissions module; refusing to overwrite its settings")
    text = _replace_exactly_once(
        text,
        r'(<module name="households" >\s*<param name="inputFile" value=")[^"]*(")',
        r"\1null\2",
        "households input file",
    )
    if settings.coordinate_system:
        text = _replace_exactly_once(
            text,
            r'(<module name="global" >\s*(?:<!--.*?-->\s*)*<param name="coordinateSystem" value=")[^"]*(")',
            lambda m: m.group(1) + settings.coordinate_system + m.group(2),
            "global coordinate system",
        )
    params = [(_TABLE_PARAMETERS[key], str(settings.tables[key])) for key in TABLE_KEYS]
    params += [
        ("detailedVsAverageLookupBehavior", settings.lookup_behavior),
        ("hbefaTableConsistencyCheckingLevel", settings.consistency_check_level),
        ("emissionsComputationMethod", settings.computation_method),
        ("hbefaVehicleDescriptionSource", settings.vehicle_description_source),
        ("nonScenarioVehicles", settings.non_scenario_vehicles),
        ("handleHighAverageSpeeds", "true" if settings.handle_high_average_speeds else "false"),
    ]
    module = '\t<module name="emissions" >\n'
    module += "".join(f'\t\t<param name="{name}" value="{value}" />\n' for name, value in params)
    module += "\t</module>\n"
    text = _replace_exactly_once(text, r"</config>", lambda m: module + "</config>", "config end tag")
    target.write_text(text, encoding="utf-8", newline="")


def _link_or_copy(source: Path, view_dir: Path) -> str:
    """Symlink ``source`` into the view; copy and warn where symlinks are not permitted."""
    destination = view_dir / source.name
    try:
        os.symlink(source.resolve(), destination)
        return "symlinked"
    except OSError as error:
        logger.warning(
            "[emissions.view] cannot symlink %s (%s); COPYING it into the view instead", source.name, error
        )
        shutil.copyfile(source, destination)
        return "copied"
