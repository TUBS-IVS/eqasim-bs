#!/usr/bin/env python3
"""Share of persons away from home all day by absence cell, from a read-only chain run (issue #140, Phase 4).

Reads, from the working directory of ``run_stages_readonly.py``, the pickles of the day-absence stage (produced by
the chain), of ``synthesis.population.enriched`` and of ``synthesis.population.spatial.home.zones`` (both linked
from the source cache; their ids are taken from the chain manifest). Writes one CSV row per absence cell: persons,
persons away all day, the realised share and the reference rate the step drew with. It also checks the claim of
``student_flows_inputs.home_kreis_by_household`` that ``departement_id`` is the 5-digit Kreis of every household
(the function raises otherwise) and reports the persons per home area.

Model counts are unweighted persons of the synthetic population at the run's sampling rate. A measurement of the
synthesis, not a validation.

    python away_share_by_area.py --work-dir <chain work dir> --manifest <chain manifest> --out <csv>
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import sys
from pathlib import Path


def _load(work: Path, stage_id: str):
    with open(work / ("%s.p" % stage_id), "rb") as handle:
        return pickle.load(handle)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    sys.path.insert(0, os.getcwd())

    import pandas as pd
    from braunschweig.data.education import student_flows_inputs as inputs

    work = Path(args.work_dir)
    manifest = json.load(open(args.manifest, encoding="utf-8"))
    absence_ids = [entry["stage"] for entry in manifest["chain"]
                   if entry["stage"].startswith("braunschweig.synthesis.day_absence.absence_stage__")]
    if len(absence_ids) != 1:
        raise SystemExit("expected one day-absence stage in the chain, got %s" % absence_ids)
    dependencies = next(entry["dependencies"] for entry in manifest["chain"] if entry["stage"] == absence_ids[0])

    # The registry ids carry the stage the alias resolves to (configs/base_bs.yml, section aliases).
    aliases = {"synthesis.population.enriched": "braunschweig.popsim.enriched_adapter",
               "synthesis.population.spatial.home.zones": "braunschweig.synthesis.spatial.home_zones"}

    def dependency(name: str) -> str:
        prefixes = (name, aliases.get(name, name))
        found = [d for d in dependencies if d.split("__")[0] in prefixes]
        if len(found) != 1:
            raise SystemExit("dependency %s: found %s among %s" % (name, found, dependencies))
        return found[0]

    output = _load(work, absence_ids[0])
    absence = output["absence"]
    if "absence_cell" not in absence.columns:
        raise SystemExit("the absence output has no absence_cell column: were the cells switched on?")
    enriched = _load(work, dependency("synthesis.population.enriched"))
    zones = _load(work, dependency("synthesis.population.spatial.home.zones"))
    kreis = inputs.home_kreis_by_household(zones)

    frame = absence.merge(enriched[["person_id", "age", "P_TAET"]], on="person_id", how="left",
                          validate="one_to_one")
    frame["kreis"] = frame["household_id"].map(kreis)
    frame["away"] = frame["day_absence_state"] != "present"
    rows = frame.groupby("absence_cell").agg(persons=("person_id", "size"), away=("away", "sum")).reset_index()
    rows["share_away"] = rows["away"] / rows["persons"]
    by_cell = output["diagnostics"].get("by_cell", {})
    rows["reference_rate"] = rows["absence_cell"].map(
        lambda cell: by_cell.get(cell, {}).get("reference_rate", float("nan")))
    rows.to_csv(args.out, index=False, encoding="utf-8")
    print(rows.to_string(index=False))
    print("households without a Kreis:", int(frame["kreis"].isna().sum()))
    print("diagnostics keys:", sorted(k for k in output["diagnostics"] if not isinstance(output["diagnostics"][k], dict)))
    for key in ("n_adults", "n_adults_unreadable_p_taet", "share_adults_unreadable_p_taet", "n_cells_unreachable",
                "n_cells_overshoot", "n_cell_guard_hits", "share_absent_total"):
        print(key, output["diagnostics"].get(key))
    return 0


if __name__ == "__main__":
    sys.exit(main())
