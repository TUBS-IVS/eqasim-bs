"""Compare two braunschweig.freight.extraction cache directories (#468 Phase 2 server check).

Usage: python compare_freight_i468.py <reference extraction cache dir> <candidate extraction cache dir> <out json>

Checks, per trip category (internal, incoming, outgoing, transit):
1. freight_extraction_summary.csv rows (counts);
2. sha256 of the DECOMPRESSED plans XML (gzip headers carry timestamps, so compressed bytes may differ);
3. if the XML differs: a structured comparison per person id -- person attributes, and every plan element
   (activity: type, link, x, y, start/end time; leg: mode, departure/travel time, route attributes and
   route text) -- reporting the number of persons that differ and the first examples per field;
4. the plans-locations-summary.tsv files as sorted line multisets.
Nothing is written except the JSON report.
"""
import gzip
import hashlib
import json
import os
import sys
import xml.etree.ElementTree as ET

CATEGORIES = ("internal", "incoming", "outgoing", "transit")
PLANS = "zgb_freight.%s.100pct.plans.xml.gz"
TSV = "zgb_freight.%s.100pct.plans-locations-summary.tsv"


def decompressed_sha(path):
    digest = hashlib.sha256()
    with gzip.open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def persons(path):
    """person id -> (attributes tuple, plan element tuples) of the selected plan."""
    result = {}
    with gzip.open(path, "rb") as handle:
        for _event, element in ET.iterparse(handle, events=("end",)):
            if element.tag != "person":
                continue
            attributes = tuple(sorted(
                (a.get("name"), (a.text or "").strip())
                for a in element.findall("./attributes/attribute")))
            plan = element.find("./plan[@selected='yes']")
            if plan is None:
                plan = element.find("./plan")
            items = []
            for child in list(plan) if plan is not None else []:
                if child.tag == "activity":
                    items.append(("activity",) + tuple(sorted(child.attrib.items())))
                elif child.tag == "leg":
                    route = child.find("route")
                    route_part = ()
                    if route is not None:
                        route_part = (tuple(sorted(route.attrib.items())), (route.text or "").strip())
                    items.append(("leg", tuple(sorted(child.attrib.items())), route_part))
            result[element.get("id")] = (attributes, tuple(items))
            element.clear()
    return result


def compare_persons(reference, candidate, examples=5):
    ref, cand = persons(reference), persons(candidate)
    only_ref = sorted(set(ref) - set(cand))
    only_cand = sorted(set(cand) - set(ref))
    attr_diff, plan_diff = [], []
    for pid in sorted(set(ref) & set(cand)):
        if ref[pid][0] != cand[pid][0]:
            attr_diff.append(pid)
        if ref[pid][1] != cand[pid][1]:
            plan_diff.append(pid)
    first = []
    for pid in plan_diff[:examples]:
        r, c = ref[pid][1], cand[pid][1]
        for index, (a, b) in enumerate(zip(r, c)):
            if a != b:
                first.append({"person": pid, "element": index, "reference": repr(a)[:400],
                              "candidate": repr(b)[:400]})
                break
        else:
            first.append({"person": pid, "element_count": [len(r), len(c)]})
    return {
        "persons_reference": len(ref), "persons_candidate": len(cand),
        "only_in_reference": len(only_ref), "only_in_candidate": len(only_cand),
        "only_in_reference_examples": only_ref[:examples], "only_in_candidate_examples": only_cand[:examples],
        "attribute_differences": len(attr_diff), "plan_differences": len(plan_diff),
        "plan_difference_examples": first,
    }


def tsv_lines(path):
    with open(path, encoding="utf-8") as handle:
        return sorted(handle.read().splitlines())


def main():
    reference, candidate, out = sys.argv[1:4]
    report = {"reference": os.path.abspath(reference), "candidate": os.path.abspath(candidate), "categories": {}}
    for name in ("freight_extraction_summary.csv",):
        report["summary_reference"] = open(os.path.join(reference, name), encoding="utf-8").read().splitlines()
        report["summary_candidate"] = open(os.path.join(candidate, name), encoding="utf-8").read().splitlines()
        report["summary_identical"] = report["summary_reference"] == report["summary_candidate"]
    for category in CATEGORIES:
        r, c = os.path.join(reference, PLANS % category), os.path.join(candidate, PLANS % category)
        entry = {"plans_decompressed_sha256": [decompressed_sha(r), decompressed_sha(c)]}
        entry["plans_identical"] = entry["plans_decompressed_sha256"][0] == entry["plans_decompressed_sha256"][1]
        if not entry["plans_identical"]:
            entry["structured"] = compare_persons(r, c)
        rt, ct = os.path.join(reference, TSV % category), os.path.join(candidate, TSV % category)
        if os.path.exists(rt) and os.path.exists(ct):
            entry["locations_summary_identical_as_sorted_lines"] = tsv_lines(rt) == tsv_lines(ct)
        report["categories"][category] = entry
        print(category, json.dumps(entry)[:600], flush=True)
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    print("summary identical:", report["summary_identical"])


if __name__ == "__main__":
    main()
