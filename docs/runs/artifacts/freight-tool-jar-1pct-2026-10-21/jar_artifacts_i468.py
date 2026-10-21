"""List the Maven artifacts bundled in two shaded jars and diff them (#468 Phase 2 server check).

Usage: python jar_artifacts_i468.py <reference jar> <candidate jar> <out json>

A shaded jar keeps META-INF/maven/<groupId>/<artifactId>/pom.properties of every bundled artifact; the
version in that file is the version that was shaded in. Also reports whether the class
org/matsim/application/prepare/longDistanceFreightGER/tripExtraction/ExtractRelevantFreightTrips.class
has identical bytes in both jars, and the artifacts that differ only in version.
"""
import hashlib
import json
import sys
import zipfile

ENTRY = ("org/matsim/application/prepare/longDistanceFreightGER/tripExtraction/"
         "ExtractRelevantFreightTrips.class")


def artifacts(path):
    found, class_sha = {}, None
    with zipfile.ZipFile(path) as jar:
        for name in jar.namelist():
            if name.startswith("META-INF/maven/") and name.endswith("/pom.properties"):
                props = dict(line.split("=", 1) for line in jar.read(name).decode("utf-8", "replace").splitlines()
                             if "=" in line and not line.startswith("#"))
                found["%s:%s" % (props.get("groupId"), props.get("artifactId"))] = props.get("version")
            if name == ENTRY:
                class_sha = hashlib.sha256(jar.read(name)).hexdigest()
    return found, class_sha


def main():
    reference, candidate, out = sys.argv[1:4]
    ref, ref_class = artifacts(reference)
    cand, cand_class = artifacts(candidate)
    report = {
        "reference_jar": reference, "candidate_jar": candidate,
        "artifacts_reference": len(ref), "artifacts_candidate": len(cand),
        "entry_class_sha256": [ref_class, cand_class],
        "entry_class_identical": ref_class is not None and ref_class == cand_class,
        "version_differences": {k: [ref[k], cand[k]] for k in sorted(set(ref) & set(cand)) if ref[k] != cand[k]},
        "only_in_reference": sorted(set(ref) - set(cand)),
        "only_in_candidate": sorted(set(cand) - set(ref)),
    }
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2)
    print(json.dumps({k: v for k, v in report.items() if k != "only_in_reference"}, indent=1)[:4000])
    print("only_in_reference:", len(report["only_in_reference"]))


if __name__ == "__main__":
    main()
