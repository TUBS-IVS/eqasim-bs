#!/usr/bin/env python3
"""Wiring-test helper for issue #688 (post-run emissions). NOT pipeline code.

Builds an "emissions view" of a MATSim run archive for
org.matsim.application.analysis.emissions.AirPollutionAnalysis.

The archive is opened read-only. Everything is written into --view, which must
not exist yet. Unmodified large files (events, transit schedule) are symlinked.

Classification of anything computed from this view: wiring_proof only. The road
type is one constant for every link because the MATSim sample tables hold a
single traffic situation family; this is not a road-type mapping.
"""
import argparse
import collections
import gzip
import os
import re
import sys

HBEFA_ATTRIBUTES = (
    "HbefaEmissionsConcept",
    "HbefaSizeClass",
    "HbefaTechnology",
    "HbefaVehicleCategory",
)

TYPE_ID = re.compile(r'<vehicleType id="([^"]+)"')
ATTRIBUTE = re.compile(r'(<attribute name="(Hbefa[A-Za-z]+)"[^>]*>)([^<]*)(</attribute>)')


def engine_block(category, indent="\t\t"):
    values = {name: "average" for name in HBEFA_ATTRIBUTES}
    values["HbefaVehicleCategory"] = category
    lines = [indent + "<engineInformation>\n", indent + "\t<attributes>\n"]
    for name in HBEFA_ATTRIBUTES:
        lines.append(
            '%s\t\t<attribute name="%s" class="java.lang.String">%s</attribute>\n'
            % (indent, name, values[name])
        )
    lines += [indent + "\t</attributes>\n", indent + "</engineInformation>\n"]
    return lines


def rewrite_vehicles(source, target, add_rules, override_category, add_default=None):
    """Rewrite vehicle TYPES only; vehicles are copied unchanged.

    add_rules: type id -> HBEFA category for types WITHOUT engine information.
    override_category: type id -> HBEFA category replacing an existing one.
    add_default: category for every type without engine information and without
    a rule (transit file); None means such a type raises.
    """
    actions = collections.OrderedDict()
    current = None
    has_engine = False
    with gzip.open(source, "rt", encoding="utf-8") as reader, gzip.open(
        target, "wt", encoding="utf-8"
    ) as writer:
        for line in reader:
            match = TYPE_ID.search(line)
            if match:
                current = match.group(1)
                has_engine = False
                actions[current] = "kept"
            elif current is not None:
                if "<engineInformation" in line:
                    has_engine = True
                if current in override_category:
                    attribute = ATTRIBUTE.search(line)
                    if attribute and attribute.group(2) == "HbefaVehicleCategory":
                        line = ATTRIBUTE.sub(
                            lambda m: m.group(1) + override_category[current] + m.group(4),
                            line,
                        )
                        actions[current] = "category overridden: " + override_category[current]
                if "<costInformation" in line and not has_engine:
                    category = add_rules.get(current, add_default)
                    if category is None:
                        raise SystemExit(
                            "vehicle type %r has no engineInformation and no rule" % current
                        )
                    writer.writelines(engine_block(category))
                    has_engine = True
                    actions[current] = "engine information added: " + category
                if "</vehicleType>" in line:
                    if not has_engine:
                        raise SystemExit(
                            "vehicle type %r: no place found for engineInformation" % current
                        )
                    current = None
            writer.write(line)
    for type_id, category in override_category.items():
        if not actions.get(type_id, "").startswith("category overridden"):
            raise SystemExit("override rule for %r did not apply" % type_id)
    return actions


def rewrite_network(source, target, road_type):
    attribute = (
        '\t\t\t\t<attribute name="hbefa_road_type" class="java.lang.String">%s</attribute>\n'
        % road_type
    )
    links = 0
    tagged = 0
    pending = False
    with gzip.open(source, "rt", encoding="utf-8") as reader, gzip.open(
        target, "wt", encoding="utf-8"
    ) as writer:
        for line in reader:
            if "<link " in line:
                if pending:
                    raise SystemExit("link without closing tag before the next link")
                links += 1
                if line.rstrip().endswith("/>"):
                    head = line.rstrip()[:-2].rstrip() + " >\n"
                    writer.write(head)
                    writer.write("\t\t\t<attributes>\n" + attribute + "\t\t\t</attributes>\n")
                    writer.write("\t\t</link>\n")
                    tagged += 1
                    continue
                pending = True
            elif pending and "<attributes>" in line:
                writer.write(line)
                writer.write(attribute)
                tagged += 1
                pending = False
                continue
            elif pending and "</link>" in line:
                writer.write("\t\t\t<attributes>\n" + attribute + "\t\t\t</attributes>\n")
                tagged += 1
                pending = False
            writer.write(line)
    if links != tagged:
        raise SystemExit("network: %d links but %d tagged" % (links, tagged))
    return links


def replace_once(text, pattern, replacement, what):
    text, count = re.subn(pattern, replacement, text)
    if count != 1:
        raise SystemExit("config: expected exactly one %s, found %d" % (what, count))
    return text


def rewrite_config(source, target, tables, crs, handle_high_speeds):
    with open(source, encoding="utf-8") as reader:
        text = reader.read()
    if '<module name="emissions"' in text:
        raise SystemExit("config already has an emissions module")
    text = replace_once(
        text,
        r'(<module name="households" >\s*<param name="inputFile" value=")[^"]*(")',
        r"\1null\2",
        "households input file",
    )
    if crs:
        text = replace_once(
            text,
            r'(<param name="coordinateSystem" value=")[^"]*(")',
            r"\g<1>%s\2" % crs,
            "global coordinate system",
        )
    params = [
        ("averageFleetColdEmissionFactorsFile", tables["cold_average"]),
        ("averageFleetWarmEmissionFactorsFile", tables["warm_average"]),
        ("detailedColdEmissionFactorsFile", tables["cold_detailed"]),
        ("detailedWarmEmissionFactorsFile", tables["warm_detailed"]),
        ("detailedVsAverageLookupBehavior", "tryDetailedThenTechnologyAverageThenAverageTable"),
        ("hbefaTableConsistencyCheckingLevel", "none"),
        ("emissionsComputationMethod", "StopAndGoFraction"),
        ("hbefaVehicleDescriptionSource", "asEngineInformationAttributes"),
        ("nonScenarioVehicles", "abort"),
        ("handleHighAverageSpeeds", "true" if handle_high_speeds else "false"),
    ]
    module = ['\t<module name="emissions" >\n']
    module += ['\t\t<param name="%s" value="%s" />\n' % item for item in params]
    module.append("\t</module>\n")
    text = replace_once(text, r"</config>", "".join(module) + "</config>", "config end tag")
    with open(target, "w", encoding="utf-8") as writer:
        writer.write(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--view", required=True)
    parser.add_argument("--tables", required=True, help="folder with the HBEFA csv tables")
    parser.add_argument("--road-type", default="URB/Local/50")
    parser.add_argument("--crs", default=None, help="replace global.coordinateSystem")
    parser.add_argument("--handle-high-speeds", action="store_true")
    args = parser.parse_args()

    archive = os.path.abspath(args.archive)
    view = os.path.abspath(args.view)
    tables_dir = os.path.abspath(args.tables)
    os.makedirs(view)  # raises when the view exists: never overwrite

    tables = {
        "cold_average": "sample_41_EFA_ColdStart_vehcat_2020average.csv",
        "warm_average": "sample_41_EFA_HOT_vehcat_2020average.csv",
        "cold_detailed": "sample_41_EFA_ColdStart_SubSegm_2020detailed.csv",
        "warm_detailed": "sample_41_EFA_HOT_SubSegm_2020detailed.csv",
    }
    for key, name in tables.items():
        path = os.path.join(tables_dir, name)
        if not os.path.isfile(path):
            raise SystemExit("missing table: " + path)
        tables[key] = path

    for name in ("output_events.xml.gz", "output_transitSchedule.xml.gz"):
        os.symlink(os.path.join(archive, name), os.path.join(view, name))

    actions = rewrite_vehicles(
        os.path.join(archive, "output_vehicles.xml.gz"),
        os.path.join(view, "output_vehicles.xml.gz"),
        add_rules={"heavy_truck": "HEAVY_GOODS_VEHICLE"},
        override_category={"default_car_passenger": "NON_HBEFA_VEHICLE"},
    )
    summary = collections.Counter(actions.values())
    print("[vehicles] %d types: %s" % (len(actions), dict(summary)))

    actions = rewrite_vehicles(
        os.path.join(archive, "output_transitVehicles.xml.gz"),
        os.path.join(view, "output_transitVehicles.xml.gz"),
        add_rules={},
        override_category={},
        add_default="NON_HBEFA_VEHICLE",
    )
    print("[transit vehicles] %d types: %s" % (len(actions), dict(actions)))

    links = rewrite_network(
        os.path.join(archive, "output_network.xml.gz"),
        os.path.join(view, "output_network.xml.gz"),
        args.road_type,
    )
    print("[network] %d links tagged with hbefa_road_type=%s" % (links, args.road_type))

    rewrite_config(
        os.path.join(archive, "output_config.xml"),
        os.path.join(view, "output_config.xml"),
        tables,
        args.crs,
        args.handle_high_speeds,
    )
    print("[config] emissions module added, households input removed, crs=%s" % args.crs)


if __name__ == "__main__":
    sys.exit(main())
