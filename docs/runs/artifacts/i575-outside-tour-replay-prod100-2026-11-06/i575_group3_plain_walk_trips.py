"""Check the 58 group-3 legs whose first prepared leg is a plain walk: what does the WHOLE prepared trip look like?"""
import collections
import gzip
import sys
import xml.etree.ElementTree as ET

import pandas as pd

sys.path.insert(0, "/home/felix/prod100_20261031/eqasim-bs")
from braunschweig.matsim.simulation import plan_check as C  # noqa: E402

CACHE = "/home/felix/prod100_20261031/cache/"
UNCUT = CACHE + "braunschweig.matsim.scenario.population__d66536c43b770db3cd52e1a76a1c4449.cache/population.xml.gz"
PREP = (CACHE + "braunschweig.matsim.simulation.prepare__57fd011e45464705ad66d48134be0b63.cache/"
        "braunschweig_100pct_prod20261031_population.xml.gz")
f = pd.read_csv("/home/felix/scratch_i575/group3_cause/group3_frozen_cause.csv.gz")
rows = f[f["prepared_mode"].eq("walk")]
persons = {i.split(":")[0] for i in rows["identifier"]}


def read(path):
    out = {}
    for _, el in ET.iterparse(gzip.open(path, "rb")):
        if el.tag == "person":
            if el.get("id") in persons:
                plan = next((p for p in el.findall("plan") if p.get("selected") == "yes"), el.find("plan"))
                acts, legs = [], []
                for c in plan:
                    if c.tag == "activity":
                        acts.append((c.get("type"), c.get("end_time"), round(float(c.get("x")), 1),
                                     round(float(c.get("y")), 1)))
                    elif c.tag == "leg":
                        rm = next((a.text for a in c.iter("attribute") if a.get("name") == "routingMode"), None)
                        legs.append((c.get("mode"), rm))
                out[el.get("id")] = (acts, legs)
            el.clear()
    return out


uncut, prep = read(UNCUT), read(PREP)
kinds = collections.Counter()
for ident in rows["identifier"]:
    pid, i = ident.split(":")
    acts_u, _ = uncut[pid]
    origin = acts_u[int(i)]
    acts_p, legs_p = prep[pid]
    ks = [k for k, a in enumerate(acts_p[:-1]) if a[0] == origin[0] and a[2:] == origin[2:]]
    if len(ks) != 1:
        kinds["unmatched"] += 1
        continue
    trip = next(t for t in C.split_trips([(a[0],) for a in acts_p], len(legs_p)) if t[0] == ks[0])
    modes = tuple(legs_p[j][0] for j in trip)
    routing = {legs_p[j][1] for j in trip}
    end = acts_p[trip[-1] + 1][0]
    kinds[(("has_pt_leg" if "pt" in modes else "walk_only"), ",".join(sorted(r or "-" for r in routing)), end)] += 1
for k, v in kinds.most_common():
    print(v, k)
