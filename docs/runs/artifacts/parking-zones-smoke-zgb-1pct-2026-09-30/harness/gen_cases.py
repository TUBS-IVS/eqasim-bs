"""Random parking stays on the committed tariff release, priced by the Python reference (Python/Java differential).

Run from the eqasim-bs integration worktree root:
    python gen_cases.py <tariffs.csv> <cases.csv>
Then price the same cases in Java with DiffCheck.java against the tariff model JSON built from the same CSV:
    java -cp <braunschweig jar> DiffCheck.java <tariff model json> <cases.csv>
Columns (no header): zone_id, arrival_s, departure_s, purpose, parking_free, resident_of_zone, cents, outcome.
Seed 20260929; 60,000 stays; 10 % use the terminal-stay rule; durations mix short, long, multi-day and boundary
values (0, 1, 59, 60, 61, 1799, 1800, 1801, 10800, 10801 s).
"""
import csv
import random
import sys
from pathlib import Path

sys.path.insert(0, ".")
from braunschweig.parking import cost, tariff_export, zones  # noqa: E402


def main(tariffs_csv: str, cases_csv: str) -> None:
    tariffs = zones.load_tariffs(Path(tariffs_csv))
    objs = {row["zone_id"]: tariff_export.tariff_row_to_zone(row) for row in tariffs.to_dict(orient="records")}
    rng = random.Random(20260929)
    purposes = ["home", "work", "education", "shop", "leisure", "other", "errand"]
    with open(cases_csv, "w", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        for _ in range(60000):
            zone_id = rng.choice(sorted(objs))
            tariff = objs[zone_id]
            arrival = rng.randrange(0, 30 * 3600)
            if rng.random() < 0.1:
                departure = int(cost.terminal_departure_s(arrival, tariff.fee_end_s))
            else:
                departure = arrival + rng.choice([rng.randrange(0, 3600), rng.randrange(0, 12 * 3600),
                                                  rng.randrange(0, 40 * 3600),
                                                  rng.choice([0, 1, 59, 60, 61, 1799, 1800, 1801, 10800, 10801])])
            purpose = rng.choice(purposes)
            parking_free, resident = rng.random() < 0.1, rng.random() < 0.2
            cents, outcome = cost.parking_cost_cents(tariff, arrival, departure, purpose=purpose,
                                                     parking_free=parking_free, resident_of_zone=resident)
            writer.writerow([zone_id, arrival, departure, purpose, str(parking_free).lower(), str(resident).lower(),
                             cents, outcome])


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
