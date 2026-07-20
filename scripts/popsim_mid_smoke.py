"""Smoke run for the popsim_mid workflow: build ONE small PopulationSim folder
from the real prepared data and run PopulationSim on it.

Thin driver over ``braunschweig.popsim.mid`` -- validates that the folded modules
reproduce the validated end-to-end run on real data (a few 1 km parents):
prepared cells -> control totals -> consistent MiD seed -> PopulationSim -> output.

Usage (from the eqasim-bs repo root, with the eqasim env python)::

    python scripts/popsim_mid_smoke.py --n-parents 2
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd

# Make the repo root importable when run as a script (python scripts/...).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from braunschweig.popsim import mid

REPO = Path(__file__).resolve().parents[1]
POP = REPO / "eqasim-data" / "data" / "braunschweig" / "popsim"
CELLS_100M = POP / "cells" / "zensus2022_grid_100m_de_prepared.parquet"
MID = POP / "mid2023_raw"

# Portable: popsimprep is a sibling of the eqasim-bs repo (reproduces the previous
# Windows-only hardcoded path on Windows AND resolves on the felix server); override
# with the POPSIMPREP env var if it lives elsewhere. uv comes from PATH, falling back
# to the standard ~/.local/bin install (mirrors braunschweig.popsim.batch which uses
# bare "uv" from PATH).
POPSIMPREP = Path(os.environ.get("POPSIMPREP") or (REPO.parent / "popsimprep"))
CONFIGS = POPSIMPREP / "popsim" / "configs"
UV = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-parents", type=int, default=2,
                        help="number of 1 km parents (by population) to include")
    parser.add_argument("--out", default=str(POPSIMPREP / "popsim_smoke"))
    parser.add_argument("--no-run", action="store_true")
    args = parser.parse_args()

    controls_df = pd.read_csv(CONFIGS / "_prep3_controls.csv", sep=";")
    base_100m = mid.control_base_columns(controls_df, "ZENSUS100m")
    print(f"[smoke] {len(base_100m)} control base columns")

    print(f"[smoke] loading cells (targeted): {CELLS_100M}")
    cells = mid.load_control_cells(CELLS_100M, base_100m)

    parent_pop = cells.groupby("ZENSUS1km")["POP_TOTAL_100m_adj"].sum().sort_values(ascending=False)
    chosen = parent_pop.head(args.n_parents).index.tolist()
    sub = cells[cells["ZENSUS1km"].isin(chosen)].copy()
    print(f"[smoke] {len(chosen)} parents, {len(sub)} cells, pop ~{sub['POP_TOTAL_100m_adj'].sum():.0f}")

    print("[smoke] loading MiD seed (complete-household filtered) ...")
    seed_hh, seed_persons, report = mid.load_mid_seed(MID)
    print(f"[smoke] seed: {report.n_households_complete}/{report.n_households_in} hh "
          f"({report.completeness_rate:.1%}), {len(seed_persons)} persons")

    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    written = mid.assemble_batch_folder(
        out, sub, base_100m, controls_df, seed_hh, seed_persons,
        settings_yaml=(CONFIGS / "settings.yaml").read_text(encoding="utf-8"),
        logging_yaml=(CONFIGS / "logging.yaml").read_text(encoding="utf-8"),
    )
    print(f"[smoke] wrote folder {out} ({len(written)} files)")
    if args.no_run:
        return 0

    print("[smoke] running PopulationSim via uv ...")
    result = subprocess.run(
        [str(UV), "run", "--no-sync", "populationsim", "-w", str(out)],
        cwd=str(POPSIMPREP), capture_output=True, text=True, timeout=1800,
    )
    print(f"[smoke] populationsim exit code: {result.returncode}")
    if result.returncode != 0:
        print("---- stderr tail ----\n" + "\n".join((result.stderr or "").splitlines()[-20:]))
    output_csv = out / "output" / "final_expanded_household_ids.csv"
    print(f"[smoke] output exists: {output_csv.is_file()}")
    if output_csv.is_file():
        df = pd.read_csv(output_csv)
        print(f"[smoke] expanded households: {len(df)}, cells: {df['ZENSUS100m'].nunique()}")
    return 0 if (result.returncode == 0 and output_csv.is_file()) else 2


if __name__ == "__main__":
    sys.exit(main())
