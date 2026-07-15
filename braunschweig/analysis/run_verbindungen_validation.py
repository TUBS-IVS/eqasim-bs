"""Re-run the VerBindungen validation against an existing synpp cache.

Loads the cached stage pickles from a completed pipeline run (no pipeline
re-execution) and writes the five validation CSVs into ``--output-dir``.

Usage::

    python -m braunschweig.analysis.run_verbindungen_validation \
        --working-directory eqasim-data/cache_bs_popsim_mid \
        --output-dir eqasim-data/output_bs_popsim_mid/verbindungen_validation
"""
from __future__ import annotations

import argparse
import glob
import os
import pickle

from braunschweig.analysis.verbindungen_validation import (
    _PROVENANCE_HEADER, build_validation_outputs,
)


def _load_stage(working_directory: str, stage_name: str):
    pattern = os.path.join(working_directory, f"{stage_name}__*.p")
    hits = glob.glob(pattern)
    if not hits:
        direct = os.path.join(working_directory, f"{stage_name}.p")
        if os.path.exists(direct):
            hits = [direct]
    if not hits:
        raise RuntimeError(
            f"[run_verbindungen_validation] no cached pickle for stage "
            f"'{stage_name}' in '{working_directory}'; run the pipeline first."
        )
    path = max(hits, key=os.path.getmtime)
    print(f"  LOAD {stage_name} <- {os.path.basename(path)}")
    with open(path, "rb") as fh:
        return pickle.load(fh)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--working-directory", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)

    wd = args.working_directory
    df_home = _load_stage(wd, "synthesis.population.spatial.home.locations")
    df_work, _ = _load_stage(wd, "synthesis.population.spatial.primary.locations")
    df_persons = _load_stage(
        wd, "synthesis.population.spatial.primary.candidates")["persons"]
    df_cells, _ = _load_stage(wd, "braunschweig.data.verbindungen.zones")
    df_ref_od = _load_stage(wd, "braunschweig.data.verbindungen.work_od")
    df_margins = _load_stage(wd, "braunschweig.data.verbindungen.margins")
    df_pendler = _load_stage(wd, "braunschweig.data.census.pendler")

    outputs = build_validation_outputs(
        df_home, df_work, df_persons, df_cells, df_ref_od, df_margins,
        df_pendler)

    os.makedirs(args.output_dir, exist_ok=True)
    names = dict(
        summary="verbindungen_validation_summary.csv",
        margin="verbindungen_margin_check.csv",
        od_per_origin="verbindungen_od_check.csv",
        od_by_kreis_pair="verbindungen_od_check_by_kreis_pair.csv",
        vintage_drift="verbindungen_vintage_drift.csv",
    )
    for key, name in names.items():
        target = os.path.join(args.output_dir, name)
        with open(target, "w", encoding="utf-8", newline="") as f:
            f.write(_PROVENANCE_HEADER)
            outputs[key].to_csv(f, index=False)
        print(f"  WROTE {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
