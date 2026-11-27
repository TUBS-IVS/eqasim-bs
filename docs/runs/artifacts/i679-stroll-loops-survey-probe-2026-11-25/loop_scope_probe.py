"""Read-only probe on prod100_20261031: do the synthetic trips tables carry the donor's detailed purpose, and how many
synthetic leisure legs on foot or by bicycle would be loops by label (W_ZWD 710, 711) or need a draw (no detail)?

Model output of one run, NOT a validation. Usage on felix: python loop_scope_probe.py ~/prod100_20261031
"""
from __future__ import annotations

import glob
import os
import pickle
import sys
from pathlib import Path

import pandas as pd

RUN = Path(sys.argv[1]).expanduser()
STAGES = ["braunschweig.popsim.trips_stage", "braunschweig.synthesis.commute_day.trips_day_stage",
          "braunschweig.synthesis.portal_trips.trips_final"]
PRIMARY = {"home", "work", "education"}


def newest(stage: str) -> str:
    return max(glob.glob(str(RUN / "cache" / f"{stage}__*.p")), key=os.path.getsize)


def main() -> int:
    frame = None
    for stage in STAGES:
        path = newest(stage)
        with open(path, "rb") as handle:
            loaded = pickle.load(handle)
        if isinstance(loaded, (tuple, list)):
            loaded = next(item for item in loaded if isinstance(item, pd.DataFrame))
        present = {column: column in loaded.columns for column in ("W_ZWD", "W_ZWECK", "mode", "trip_index")}
        print(f"{stage}: {loaded.shape}, columns present {present}", flush=True)
        if stage.endswith("trips_stage"):
            frame = loaded[["person_id", "trip_index", "mode", "W_ZWD", "W_ZWECK", "preceding_purpose",
                            "following_purpose"]].copy()
        del loaded
    for column in ("W_ZWD", "W_ZWECK"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    leisure = frame[frame["following_purpose"].eq("leisure") & frame["mode"].isin(["walk", "bicycle"])].copy()
    valid_detail = leisure["W_ZWD"].between(700, 799) & ~leisure["W_ZWD"].isin([799])
    leisure["class"] = "other coarse purpose"
    leisure.loc[leisure["W_ZWECK"].eq(7) & ~valid_detail, "class"] = "W_ZWECK 7 without detail (draw)"
    leisure.loc[leisure["W_ZWECK"].eq(7) & valid_detail, "class"] = "W_ZWECK 7 with detail, not a loop"
    leisure.loc[leisure["W_ZWECK"].eq(7) & leisure["W_ZWD"].isin([710, 711]), "class"] = "loop by label (710, 711)"
    leisure.loc[leisure["W_ZWECK"].eq(10), "class"] = "W_ZWECK 10 other purpose"
    leisure.loc[leisure["W_ZWECK"].isin([14, 15, 16]), "class"] = "W_ZWECK 14-16 child purposes"
    print("leisure legs on foot or by bicycle:", len(leisure), "of all trips", len(frame))
    print(pd.crosstab(leisure["class"], leisure["mode"], margins=True).to_string())
    loops = leisure[leisure["class"].eq("loop by label (710, 711)")]
    origin = loops["preceding_purpose"].where(loops["preceding_purpose"].isin(PRIMARY), "secondary")
    print("labelled loops by the activity before them:", origin.value_counts().to_dict())
    print("persons with a labelled loop:", loops["person_id"].nunique())
    draw = leisure[leisure["class"].eq("W_ZWECK 7 without detail (draw)")]
    print("legs that need a draw, W_ZWD values:", draw["W_ZWD"].value_counts(dropna=False).head(8).to_dict())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
