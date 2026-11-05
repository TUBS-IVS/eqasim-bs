"""Observation of the in-commuter fleet log on the committed tables (run manifest
prod100-expected-message-levels-2026-11-04).

Prints which Kreise the committed Gemeinde EV table covers, then runs
braunschweig.synthesis.incommuters.build_incommuter_fleet (the function the incommuters stage calls) for 400
synthetic car agents whose origin Kreise lie outside the ZGB, and prints the fleet log lines about the Kreis
lookup and the Gemeinde tilt. The agents are a fixed illustration, not a model population; the point is the
accounting of cars with no home-Gemeinde label and a Kreis outside the table. Writes nothing.

Usage: python observe_incommuter_fleet_log.py <repo root>
"""
import logging
import sys

import numpy as np
import pandas as pd

repo = sys.argv[1]
sys.path.insert(0, repo)

from braunschweig.synthesis import incommuters  # noqa: E402

kba = repo + "/eqasim-data/data/braunschweig/kba/derived/"
table_kreise = sorted(pd.read_csv(kba + "kba_gemeinde_ev.csv", dtype=str)["kreis_ags5"].unique())
all_kreise = pd.read_csv(kba + "kba_kreis_fuel.csv", dtype=str)["kreis_ags5"].unique()
print("Gemeinde EV table (kba_gemeinde_ev.csv) covers %d Kreise: %s" % (len(table_kreise), ", ".join(table_kreise)))
print("Kreis fuel table (kba_kreis_fuel.csv) covers %d Kreise" % len(all_kreise))

records = []


class Collect(logging.Handler):
    def emit(self, record):
        records.append((record.levelname, record.getMessage()))


logging.getLogger().addHandler(Collect())
logging.getLogger().setLevel(logging.INFO)
n = 400
origins = np.array(["03241", "03159", "03254", "03155", "03257"] * (n // 5))
assert not set(origins) & set(table_kreise)       # every origin Kreis lies outside the Gemeinde table
fleet = incommuters.build_incommuter_fleet(
    person_ids=np.arange(n), modes=np.array(["car"] * n), orig_ars=origins,
    income_eur=np.full(n, 2800.0), ages=np.full(n, 40.0),
    data_path=repo + "/eqasim-data/data", rng=np.random.default_rng(7))
print("vehicles drawn:", len(fleet[1]))
for level, message in records:
    if "powertrain Gemeinde tilt" in message or "powertrain Kreis lookup" in message:
        print("%-7s %s" % (level, message))
