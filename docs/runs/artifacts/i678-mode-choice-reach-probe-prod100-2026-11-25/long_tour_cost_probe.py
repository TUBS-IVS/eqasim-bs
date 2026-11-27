"""Read-only probe on prod100_20261031: how many mode chains would the tour-based mode choice have to enumerate for
the home-based tours with more than 6 trips, given the modes each person may use?

The discrete mode choice enumerates (available modes) ** (trips of the tour) chains per tour. Available modes as in
``BraunschweigModeAvailability``: walk and pt always, car_passenger with availability some/all, car with a car and a
licence, bicycle with a bicycle. Tours are split at ``home``; tours with an ``outside`` purpose are left out.
An ESTIMATE of the enumeration size, not a timing. Usage: python long_tour_cost_probe.py ~/prod100_20261031
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from distance_probe import TRIPS  # noqa: E402

pd.set_option("display.width", 220)
pd.set_option("display.float_format", lambda v: f"{v:,.2f}")


def main() -> int:
    with open(TRIPS, "rb") as handle:
        trips = pickle.load(handle)
    columns = ["person_id", "trip_index", "preceding_purpose", "following_purpose", "has_license",
               "car_availability", "bicycle_availability", "car_passenger_availability"]
    trips = trips[columns].sort_values(["person_id", "trip_index"]).copy()
    for column in ("car_availability", "bicycle_availability", "car_passenger_availability"):
        print(column, trips[column].astype(str).value_counts(dropna=False).head(6).to_dict())
    print("has_license", trips["has_license"].astype(str).value_counts(dropna=False).to_dict())
    trips["tour"] = (trips["preceding_purpose"].eq("home")).groupby(trips["person_id"]).cumsum()
    trips["outside"] = trips["preceding_purpose"].eq("outside") | trips["following_purpose"].eq("outside")
    licence = trips["has_license"].astype(str).str.lower().isin(["true", "1", "1.0"])
    trips["n_modes"] = (2 + trips["car_passenger_availability"].astype(str).isin(["some", "all"]).astype(int)
                        + (~trips["car_availability"].astype(str).isin(["none", "nan", "None"]) & licence).astype(int)
                        + (~trips["bicycle_availability"].astype(str).isin(["none", "nan", "None"])).astype(int))
    tours = trips.groupby(["person_id", "tour"]).agg(n=("trip_index", "size"), outside=("outside", "any"),
                                                     modes=("n_modes", "first")).reset_index()
    tours = tours[~tours["outside"]]
    print("tours without outside:", len(tours), "; modes per person of all tours:",
          tours["modes"].value_counts().sort_index().to_dict())
    long = tours[tours["n"] > 6].copy()
    long["chains"] = long["modes"].astype(float) ** long["n"]
    table = long.groupby("n").agg(tours=("chains", "size"), mean_modes=("modes", "mean"),
                                  chains_total=("chains", "sum"), chains_max=("chains", "max"))
    table["cum_chains"] = table["chains_total"].cumsum()
    print(table.to_string())
    print("modes of the tours with the most trips:",
          long[long["n"] == long["n"].max()]["modes"].value_counts().sort_index().to_dict())
    base = tours[tours["n"] <= 6]
    print(f"for comparison, all tours of up to 6 trips: {len(base)} tours, "
          f"{(base['modes'].astype(float) ** base['n']).sum():,.0f} chains")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
