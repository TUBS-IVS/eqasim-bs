"""Share of final-iteration arrivals whose destination activity ends at or before the arrival (zero-length stay).

Evidence for assumption L1 of ADR-0139 (issue #436): how often a car arrives after the planned end of its activity,
in the smoke of this directory and in older 25 % and 100 % runs. The measurement is the one of the session that
proposed L1 (2026-09-30), committed with two additions: the table is written as a CSV with a provenance header derived
from each output directory, and a trip without its destination activity fails the run instead of counting as a
stay of non-zero length.

Definition, the same as the smoke evaluation (harness/evaluate_smoke.py, zero_length_stays.csv): trip k of a person
(eqasim_trips, person_trip_id k) ends at the person's activity k + 1 (eqasim_activities, activity_index); the arrival
is that activity's start_time; the stay is zero-length when the activity has a finite end_time and
round(end_time) <= round(start_time). A terminal activity (end_time Infinity) never counts. Counted per mode for the
five eqasim travel modes, so the car figure can be compared with the others; other modes (the cordon's "outside"
stubs) are left out and counted in the header.

Per run the header records what the directory itself shows: the modification date of its eqasim_trips file (for a
copied output directory that is the date of the copy), the final iteration (the last row of scorestats.csv; eqasim's
AnalysisOutputListener.notifyShutdown copies that iteration's eqasim_trips and eqasim_activities to the output root)
and the last iteration label of eqasim_termination.csv (EqasimTerminationCriterion.prepareTerminationData labels
each row with the iteration minus one and repeats that label for the final iteration, so it is the final iteration
minus one).

Usage (paths relative to the repository root, the scratchpad masked in the header):
    python zero_length_share.py [--output CSV] [--path-alias PLACEHOLDER=DIRECTORY ...]
        [--run-note LABEL=TEXT ...] <label> <simulation_output directory> [<label> <directory> ...]
"""
from __future__ import annotations

import argparse
import datetime
import gzip
from pathlib import Path

import numpy as np
import pandas as pd

TRAVEL_MODES = ("bicycle", "car", "car_passenger", "pt", "walk")
COLUMNS = ("run_label", "mode", "arrivals", "zero_length_stays", "zero_length_share")


def read(path: Path, usecols):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as stream:
        header = stream.readline()
    sep = ";" if header.count(";") > header.count(",") else ","
    return pd.read_csv(path, sep=sep, usecols=usecols)


def first_existing(directory: Path, name: str) -> Path:
    for path in (directory / name, directory / f"{name}.gz"):
        if path.exists():
            return path
    raise FileNotFoundError(f"neither {name} nor {name}.gz in {directory}")


def display_path(directory: Path, aliases) -> str:
    for placeholder, root in aliases:
        try:
            return f"{placeholder}/{directory.resolve().relative_to(root.resolve()).as_posix()}"
        except ValueError:
            continue
    return directory.as_posix()


def measure(label: str, directory: Path):
    """Rows of the travel modes and the provenance facts of one simulation_output directory."""
    trips_path = first_existing(directory, "eqasim_trips.csv")
    trips = read(trips_path, ["person_id", "person_trip_id", "mode"])
    acts = read(first_existing(directory, "eqasim_activities.csv"),
                ["person_id", "activity_index", "start_time", "end_time"])
    trips["activity_index"] = trips["person_trip_id"] + 1
    merged = trips.merge(acts, on=["person_id", "activity_index"], how="left", validate="one_to_one")
    missing = int(merged["start_time"].isna().sum())
    if missing:
        raise SystemExit(f"[{label}] {missing} of {len(trips)} trips have no destination activity in {directory}")
    end = pd.to_numeric(merged["end_time"], errors="coerce")
    start = pd.to_numeric(merged["start_time"], errors="coerce")
    finite = np.isfinite(end)
    merged["zero_length"] = finite & (np.round(end) <= np.round(start))
    rows = []
    for mode, group in merged.groupby("mode"):
        if mode in TRAVEL_MODES:
            rows.append({"run_label": label, "mode": mode, "arrivals": len(group),
                         "zero_length_stays": int(group["zero_length"].sum()),
                         "zero_length_share": round(float(group["zero_length"].mean()), 4)})
    other_modes = trips.loc[~trips["mode"].isin(TRAVEL_MODES), "mode"].value_counts().sort_index()
    facts = {
        "outputs_dated": datetime.date.fromtimestamp(trips_path.stat().st_mtime).isoformat(),
        "final_iteration": int(read(directory / "scorestats.csv", ["iteration"])["iteration"].iloc[-1]),
        "last_termination_label": int(read(directory / "eqasim_termination.csv", ["iteration"])["iteration"].iloc[-1]),
        "trips": len(trips),
        "other_modes": ", ".join(f"{mode} {count}" for mode, count in other_modes.items()) or "none",
    }
    print(f"[{label}] trips {len(trips)}, destination activity missing for {missing}")
    return rows, facts


def header_lines(runs, aliases, notes, output_name: str) -> list[str]:
    """The ``#`` header: what the table is, how it was generated (run directories as shown below), the columns and
    one line of provenance facts per run."""
    command = " ".join(["python zero_length_share.py --output", output_name,
                        *(f"{label} {display_path(directory, aliases)}" for label, directory, _ in runs)])
    lines = ["Zero-length stays among the final-iteration arrivals per travel mode (ADR-0139 assumption L1, issue #436).",
             "Generated by zero_length_share.py in this directory, run from the repository root (the run notes below "
             f"given with --run-note, <placeholders> with --path-alias): {command}",
             "Definition and the meaning of the run facts below: the docstring of zero_length_share.py.",
             "Columns: run_label; mode; arrivals (trips of the mode in the final iteration); zero_length_stays;",
             "zero_length_share (zero_length_stays / arrivals, rounded to 4 decimals)."]
    for label, directory, facts in runs:
        line = (f"run {label}: {display_path(directory, aliases)}; outputs dated {facts['outputs_dated']}; final "
                f"iteration {facts['final_iteration']} (scorestats.csv); last eqasim_termination.csv label "
                f"{facts['last_termination_label']}; {facts['trips']} trips, other modes left out: "
                f"{facts['other_modes']}")
        if label in notes:
            line += f"; {notes[label]}"
        lines.append(line)
    return [f"# {line}" for line in lines]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output", type=Path, help="CSV to write; without it the table is only printed")
    parser.add_argument("--path-alias", action="append", default=[], metavar="PLACEHOLDER=DIRECTORY",
                        help="show run directories below DIRECTORY as PLACEHOLDER/... in the header")
    parser.add_argument("--run-note", action="append", default=[], metavar="LABEL=TEXT",
                        help="provenance text appended to the header line of the run LABEL")
    parser.add_argument("runs", nargs="+", metavar="LABEL DIRECTORY")
    args = parser.parse_args(argv)
    if len(args.runs) % 2:
        parser.error("runs must be <label> <directory> pairs")
    aliases = [(placeholder, Path(root)) for placeholder, root in
               (alias.split("=", 1) for alias in args.path_alias)]
    notes = dict(note.split("=", 1) for note in args.run_note)
    rows, runs = [], []
    for label, directory in zip(args.runs[0::2], args.runs[1::2]):
        run_rows, facts = measure(label, Path(directory))
        rows.extend(run_rows)
        runs.append((label, Path(directory), facts))
    table = pd.DataFrame(rows, columns=list(COLUMNS))
    print(table.to_string(index=False))
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w", encoding="utf-8", newline="\n") as stream:
            stream.write("\n".join(header_lines(runs, aliases, notes, args.output.name)) + "\n")
            table.to_csv(stream, index=False, lineterminator="\n")
        print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
