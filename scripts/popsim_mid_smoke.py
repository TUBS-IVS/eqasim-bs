"""Smoke run for the popsim_mid workflow: build ONE small PopulationSim folder
from the real prepared data and run PopulationSim on it.

This validates the cells -> controls -> seed -> PopulationSim -> output chain on a
tiny subset (a few 1 km parents) before the logic is folded into the
``braunschweig.popsim`` modules + the synpp graph. It deliberately reuses the
committed popsimprep PopulationSim config (``settings.yaml`` / ``logging.yaml``)
and control spec (``_prep3_controls.csv``) so the run is faithful to the notebook.

Usage (from the eqasim-bs repo root, with the eqasim env python)::

    python scripts/popsim_mid_smoke.py --n-parents 3

The PopulationSim subprocess uses the popsimprep uv environment.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

# Make the repo root importable when run as a script (python scripts/...).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from braunschweig.popsim import cells as cellmod
from braunschweig.popsim import controls as ctrl
from braunschweig.popsim import folders
from braunschweig.popsim import prepared_cells

REPO = Path(__file__).resolve().parents[1]
POP = REPO / "eqasim-data" / "data" / "braunschweig" / "popsim"
CELLS_100M = POP / "cells" / "zensus2022_grid_100m_de_prepared.parquet"
MID = POP / "mid2023_raw"

POPSIMPREP = Path(r"C:\Users\bienzeisler\Documents\GitHub\popsimprep")
CONFIGS = POPSIMPREP / "popsim" / "configs"
UV = Path(r"C:\Users\bienzeisler\.local\bin\uv.exe")

GEO_SUFFIX_100M = "_ZENSUS100m"
GEO_SUFFIX_1KM = "_ZENSUS1km"


def load_cells_columns(path: Path, needed_clean: list[str]) -> pd.DataFrame:
    """Read only the needed (cleaned) columns + ids from the prepared parquet.

    Avoids loading all 570 columns x 3.1 M rows. Matches each needed cleaned name
    back to its raw parquet column via ``clean_col_name`` and reads just those.
    """
    raw_cols = pq.ParquetFile(path).schema.names
    clean_to_raw: dict[str, str] = {}
    for raw in raw_cols:
        clean_to_raw.setdefault(prepared_cells.clean_col_name(raw), raw)

    id100_raw = raw_cols[0]  # GITTER_ID_100m is the first column
    raw_needed = [id100_raw]
    for clean in needed_clean:
        raw = clean_to_raw.get(clean)
        if raw is not None and raw not in raw_needed:
            raw_needed.append(raw)

    df = pd.read_parquet(path, columns=raw_needed)
    df.columns = [prepared_cells.clean_col_name(c) for c in df.columns]
    df = df.rename(columns={prepared_cells.clean_col_name(id100_raw): "ZENSUS100m"})
    df["ZENSUS1km"] = df["ZENSUS100m"].map(cellmod.derive_1km_parent_id)
    df["STAAT"] = 1
    df["WELT"] = 1
    return df


def base_cols_for_geography(controls_csv: Path, geography: str) -> list[str]:
    """Return the control_field base column names for a geography (suffix stripped)."""
    df = pd.read_csv(controls_csv, sep=";")
    df = df[df["geography"] == geography]
    suffix = f"_{geography}"
    bases = []
    for control_field in df["control_field"]:
        if control_field.endswith(suffix):
            bases.append(control_field[: -len(suffix)])
        else:
            bases.append(control_field)
    return list(dict.fromkeys(bases))


def build_control_totals(sub_cells: pd.DataFrame, base_cols: list[str]) -> dict[str, pd.DataFrame]:
    """Build the four control-total tables with the notebook's per-geography suffixing."""
    # Integerize each base column within its 1 km parent (largest-remainder).
    df100 = pd.DataFrame({"ZENSUS100m": sub_cells["ZENSUS100m"].to_numpy()})
    work = sub_cells[["ZENSUS100m", "ZENSUS1km", *base_cols]].copy()
    for col in base_cols:
        df100[f"{col}{GEO_SUFFIX_100M}"] = ctrl.integerize_within_parents(
            work, value_col=col, parent_col="ZENSUS1km"
        ).to_numpy()

    df100["ZENSUS1km"] = sub_cells["ZENSUS1km"].to_numpy()
    cols_100m = [f"{c}{GEO_SUFFIX_100M}" for c in base_cols]
    df1km = (
        df100.groupby("ZENSUS1km", sort=False)[cols_100m].sum().reset_index()
    )
    df1km = df1km.rename(
        columns={f"{c}{GEO_SUFFIX_100M}": f"{c}{GEO_SUFFIX_1KM}" for c in base_cols}
    )

    df100_out = df100.drop(columns=["ZENSUS1km"])
    df_staat = pd.DataFrame([{"STAAT": 1, "WELT": 1}])
    df_welt = pd.DataFrame([{"WELT": 1}])
    return {
        "ZENSUS100m": df100_out,
        "ZENSUS1km": df1km,
        "STAAT": df_staat,
        "WELT": df_welt,
    }


def build_seed(kernwo=(1, 2, 3)) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load the MiD seed (households + persons), filtered to complete households.

    Faithful to the notebook: keep only households where EVERY person reported on
    an accepted core-week day (``kernwo`` in {1,2,3}); this also guarantees seed
    consistency (every kept household has persons, every kept person a household),
    so PopulationSim's per-household incidence is never NaN (the NaN -> float
    group_id crash). Only the columns the controls need are kept, plus STAAT.
    """
    households = pd.read_csv(MID / "MiD2023_Haushalte.csv", usecols=["H_ID", "H_GEW"])
    persons = pd.read_csv(
        MID / "MiD2023_Personen.csv",
        usecols=["H_ID", "P_ID", "P_GEW", "HP_ALTER", "HP_SEX", "kernwo"],
    )

    keep = persons["kernwo"].isin(kernwo)
    n_present = persons.groupby("H_ID")["H_ID"].transform("size")
    n_kept = keep.groupby(persons["H_ID"]).transform("sum")
    complete = n_kept.eq(n_present)
    persons = persons[complete].drop(columns=["kernwo"]).copy()

    complete_hh = set(persons["H_ID"].unique())
    households = households[households["H_ID"].isin(complete_hh)].copy()

    households["STAAT"] = 1
    persons["STAAT"] = 1
    return households, persons


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-parents", type=int, default=3,
                        help="number of 1 km parents (by population) to include")
    parser.add_argument("--out", default=str(POPSIMPREP / "popsim_smoke"))
    parser.add_argument("--no-run", action="store_true", help="build only, do not run PopulationSim")
    args = parser.parse_args()

    base_100m = base_cols_for_geography(CONFIGS / "_prep3_controls.csv", "ZENSUS100m")
    print(f"[smoke] loading prepared cells (targeted columns): {CELLS_100M}")
    cells = load_cells_columns(CELLS_100M, base_100m + ["POP_TOTAL_100m_adj"])

    missing = [c for c in base_100m if c not in cells.columns]
    if missing:
        print(f"[smoke] ERROR: control base columns missing from cells: {missing}")
        return 1
    print(f"[smoke] {len(base_100m)} control base columns present")

    # Pick the most-populated 1 km parents for a meaningful smoke test.
    parent_pop = cells.groupby("ZENSUS1km")["POP_TOTAL_100m_adj"].sum().sort_values(ascending=False)
    chosen = parent_pop.head(args.n_parents).index.tolist()
    sub = cells[cells["ZENSUS1km"].isin(chosen)].copy()
    print(f"[smoke] chosen {len(chosen)} parents, {len(sub)} 100m cells, "
          f"pop ~{sub['POP_TOTAL_100m_adj'].sum():.0f}")

    geo_xwalk = folders.build_geo_crosswalk(sub, id_col_100m="ZENSUS100m", parent_col="ZENSUS1km")
    control_totals = build_control_totals(sub, base_100m)
    print(f"[smoke] loading MiD seed ...")
    seed_hh, seed_persons = build_seed()
    print(f"[smoke] seed: {len(seed_hh)} households, {len(seed_persons)} persons")

    out = Path(args.out)
    if out.exists():
        shutil.rmtree(out)
    written = folders.write_popsim_folder(
        out,
        geo_crosswalk=geo_xwalk,
        control_totals=control_totals,
        controls_csv=pd.read_csv(CONFIGS / "_prep3_controls.csv", sep=";"),
        seed_households=seed_hh,
        seed_persons=seed_persons,
        settings_yaml=(CONFIGS / "settings.yaml").read_text(encoding="utf-8"),
        logging_yaml=(CONFIGS / "logging.yaml").read_text(encoding="utf-8"),
    )
    print(f"[smoke] wrote folder {out} ({len(written)} files)")

    if args.no_run:
        return 0

    print(f"[smoke] running PopulationSim via uv ...")
    result = subprocess.run(
        [str(UV), "run", "--no-sync", "populationsim", "-w", str(out)],
        cwd=str(POPSIMPREP),
        capture_output=True,
        text=True,
        timeout=1800,
    )
    print(f"[smoke] populationsim exit code: {result.returncode}")
    tail = "\n".join((result.stdout or "").splitlines()[-25:])
    err = "\n".join((result.stderr or "").splitlines()[-25:])
    print("---- stdout tail ----\n" + tail)
    if result.returncode != 0:
        print("---- stderr tail ----\n" + err)
    output_csv = out / "output" / "final_expanded_household_ids.csv"
    print(f"[smoke] output exists: {output_csv.is_file()}")
    if output_csv.is_file():
        df = pd.read_csv(output_csv)
        print(f"[smoke] expanded households: {len(df)}, cells: {df['ZENSUS100m'].nunique()}")
    return 0 if (result.returncode == 0 and output_csv.is_file()) else 2


if __name__ == "__main__":
    sys.exit(main())
