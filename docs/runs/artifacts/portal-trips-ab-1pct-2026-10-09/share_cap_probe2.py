"""Decompose the share_out cap of the portal stays by cause (eqasim-bs#442). Read-only probe.

Rebuilds the stays from the cached pre-portal trips with the stage's own classification, joins
the gates from the anchors, and for every stay computes the quantities the timing rule uses.
Primary (work/education) stays: point = assigned location, exact. Drawn stays: point lies in the
band [0.8, 1.2] x reported outbound distance from the origin, so only bounds are available.
"""
import glob
import os
import pickle
import sys

import numpy as np
import pandas as pd

from braunschweig.synthesis.portal_trips import classification as cls

cache = sys.argv[1]
run_log = sys.argv[2]
import re
_used = {}
for line in open(run_log, encoding="utf-8", errors="replace"):
    m = re.search(r"(?:Loading cache for|Writing cache for|Executing stage) ([A-Za-z0-9_.]+)__([0-9a-f]{32})", line)
    if m:
        _used[m.group(1)] = m.group(2)
THRESHOLD_M = 45000.0
TOL = 0.2


def load(stage):
    f = os.path.join(cache, f"{stage}__{_used[stage]}.p")
    with open(f, "rb") as fh:
        return pickle.load(fh)


pre = load("braunschweig.synthesis.commute_day.trips_day_stage")
core = load("braunschweig.synthesis.portal_trips.stage")
persons = load("braunschweig.popsim.enriched_adapter")
homes = load("braunschweig.synthesis.locations.home_cell")
df_work, df_education = load("braunschweig.locations.synthesis.replacement_education_gravity")

trips = pre.sort_values(["person_id", "trip_index"]).reset_index(drop=True)
home_xy = cls.person_home_xy(persons, homes)
primary = cls.primary_xy(df_work, df_education)
frame = cls.classification_distance_frame(trips, home_xy, primary)
is_portal = cls.portal_flags(frame["classification_distance_m"], trips["following_purpose"], THRESHOLD_M)
stays = cls.find_outside_stays(trips, is_portal)
print("stays rebuilt:", len(stays), "| core report stays:", core["report"]["n_stays"])

key = trips.set_index(["person_id", "trip_index"])
out_idx = pd.MultiIndex.from_arrays([stays["person_id"], stays["outbound_trip_index"]])
s = stays.copy()
s["purpose"] = key["following_purpose"].reindex(out_idx).to_numpy()
s["origin_purpose"] = key["preceding_purpose"].reindex(out_idx).to_numpy()
s["reported_out"] = key["euclidean_distance"].reindex(out_idx).to_numpy()
has_ret = s["return_trip_index"].notna()
ret_idx = pd.MultiIndex.from_arrays([s["person_id"], s["return_trip_index"].fillna(-1).astype(int)])
s["reported_ret"] = key["euclidean_distance"].reindex(ret_idx).to_numpy()
s["ret_purpose"] = key["following_purpose"].reindex(ret_idx).to_numpy()

# Gate of each stay: anchors are keyed by (person, activity_index) in the post table; match by order.
anchors = core["anchors"].sort_values(["person_id", "activity_index"]).reset_index(drop=True)
s = s.sort_values(["person_id", "outbound_trip_index"]).reset_index(drop=True)
assert (anchors["person_id"].to_numpy() == s["person_id"].to_numpy()).all(), "anchor/stay order mismatch"
s["gx"] = anchors.geometry.x.to_numpy()
s["gy"] = anchors.geometry.y.to_numpy()
s["kind"] = anchors["kind"].to_numpy()
hx = home_xy.reindex(s["person_id"])
s["hx"], s["hy"] = hx["x"].to_numpy(), hx["y"].to_numpy()
s["home_gate_m"] = np.hypot(s["gx"] - s["hx"], s["gy"] - s["hy"])

is_primary = s["purpose"].isin(["work", "education"])
px = np.full(len(s), np.nan)
py = np.full(len(s), np.nan)
for purpose in ("work", "education"):
    m = (s["purpose"] == purpose).to_numpy()
    loc = primary[purpose].reindex(s.loc[m, "person_id"])
    px[m], py[m] = loc["x"].to_numpy(), loc["y"].to_numpy()
s["outside_primary_m"] = np.hypot(px - s["gx"], py - s["gy"])
s["assigned_m"] = np.hypot(px - s["hx"], py - s["hy"])

missing = ~np.isfinite(s["reported_ret"]) | (s["reported_ret"] <= 0)
cap_primary = is_primary & ~missing & (s["outside_primary_m"] > s["reported_ret"])
# drawn: outside >= (1 - TOL) * reported_out - |origin - gate| (lower bound, origin proxied by home)
lb = np.maximum(0.0, (1 - TOL) * s["reported_out"] - s["home_gate_m"])
cap_drawn_certain = ~is_primary & ~missing & (lb > s["reported_ret"])

print("\nstays:", len(s), "| with return:", int(has_ret.sum()))
print("missing/zero reported return distance:", int(missing.sum()),
      "| their return purpose:", s.loc[missing, "ret_purpose"].value_counts().to_dict())
print("primary stays:", int(is_primary.sum()), "| capped (outside gate->assigned > reported return):",
      int(cap_primary.sum()))
pp = s[is_primary & ~missing]
print("  primary: assigned home->location km  median %.1f | donor reported return km median %.1f | "
      "ratio assigned/reported median %.2f p90 %.2f" % (
          pp["assigned_m"].median() / 1e3, pp["reported_ret"].median() / 1e3,
          (pp["assigned_m"] / pp["reported_ret"]).median(), (pp["assigned_m"] / pp["reported_ret"]).quantile(.9)))
print("  primary: donor reported return < 45 km:", int((pp["reported_ret"] < THRESHOLD_M).sum()), "of", len(pp))
print("drawn stays:", int((~is_primary).sum()), "| certainly capped (lower bound):", int(cap_drawn_certain.sum()))
dd = s[~is_primary & ~missing]
print("  drawn: reported_out/reported_ret median %.2f | home->gate km median %.1f" % (
    (dd["reported_out"] / dd["reported_ret"]).median(), dd["home_gate_m"].median() / 1e3))
print("by kind (capped primary):", s.loc[cap_primary, "kind"].value_counts().to_dict())
print("origin purposes of stays:", s["origin_purpose"].value_counts().to_dict())

# Hypothesis B: the "return" leg of a drawn stay goes to a second far activity, and only the leg
# after it (often home-bound, never classified as portal because home legs are excluded) comes back.
nxt_idx = pd.MultiIndex.from_arrays([s["person_id"], s["return_trip_index"].fillna(-2).astype(int) + 1])
s["next_reported"] = key["euclidean_distance"].reindex(nxt_idx).to_numpy()
s["next_purpose"] = key["following_purpose"].reindex(nxt_idx).to_numpy()
d = s[~is_primary & ~missing]
far_next = d["next_reported"] > THRESHOLD_M
print("\nH-B drawn stays with a reported return distance:", len(d))
print("  return leg reported km median %.1f; return leg purpose %s" % (
    d["reported_ret"].median() / 1e3, d["ret_purpose"].value_counts().to_dict()))
print("  leg AFTER the return leg is > 45 km:", int(far_next.sum()), "| its purpose:",
      d.loc[far_next, "next_purpose"].value_counts().to_dict())
print("  of the certainly capped drawn stays, next leg > 45 km:",
      int((far_next & cap_drawn_certain.reindex(d.index)).sum()), "of", int(cap_drawn_certain.sum()))
# Same check for primary stays
p2 = s[is_primary & ~missing]
print("H-B primary: leg after return > 45 km:", int((p2["next_reported"] > THRESHOLD_M).sum()), "of", len(p2),
      "| return purpose:", p2["ret_purpose"].value_counts().to_dict())
# Hypothesis A check: donor's own OUTBOUND reported distance of primary stays
print("H-A primary: donor outbound reported km median %.1f; outbound reported > 45 km: %d of %d" % (
    p2["reported_out"].median() / 1e3, int((p2["reported_out"] > THRESHOLD_M).sum()), len(p2)))

# Hypothesis C: the reported leg distance is a ROUTE length (wegkm / 1.3), not the displacement of the
# destination. Triangle inequality: the far activity's distance from home is at most the sum of the
# reported distances of the legs from it back to the next home arrival. If that sum is below the
# threshold, the diary itself says the destination lies within the threshold of home.
tr = trips[["person_id", "trip_index", "following_purpose", "euclidean_distance"]]
by_person = {pid: g.reset_index(drop=True) for pid, g in tr.groupby("person_id", sort=False)}
back_km, back_ok = [], []
for row in s.itertuples():
    g = by_person[row.person_id]
    legs = g[g["trip_index"] > row.outbound_trip_index + row.n_removed_legs]
    total, reached = 0.0, False
    for leg in legs.itertuples():
        if not np.isfinite(leg.euclidean_distance):
            total = np.nan
            break
        total += leg.euclidean_distance
        if leg.following_purpose == "home":
            reached = True
            break
    back_km.append(total)
    back_ok.append(reached)
s["back_to_home_m"] = back_km
s["back_reaches_home"] = back_ok
inconsistent = s["back_reaches_home"] & (s["back_to_home_m"] < THRESHOLD_M)
for label, mask in (("drawn", ~is_primary), ("primary", is_primary)):
    sub = s[mask & s["back_reaches_home"] & s["back_to_home_m"].notna()]
    print(f"H-C {label}: stays with a finite way home {len(sub)}; way home < 45 km (destination provably "
          f"within the threshold by the diary): {int((sub['back_to_home_m'] < THRESHOLD_M).sum())}; "
          f"median way home km {sub['back_to_home_m'].median() / 1e3:.1f}; median outbound km {sub['reported_out'].median() / 1e3:.1f}")
print("H-C capped drawn (certain) that are diary-inconsistent:",
      int((inconsistent & cap_drawn_certain).sum()), "of", int(cap_drawn_certain.sum()))
print("H-C purposes of inconsistent drawn stays:", s.loc[inconsistent & ~is_primary, "purpose"].value_counts().to_dict())

# Phase 3 test: geometric split. Return: outside = |gate - point|, inside = |gate - return proxy (home)|;
# share_out_geo = outside / (outside + inside) is in [0, 1] by construction and needs no reported
# distance. For primary stays the point is exact; for drawn stays use the band centre on the
# origin->gate ray as a stand-in point (direction = gate direction), which is what choose_gates favours.
inside_ret = s["home_gate_m"]
out_primary = s["outside_primary_m"]
geo_primary = out_primary / (out_primary + inside_ret)
centre = np.maximum(s["reported_out"] - s["home_gate_m"], 0.0)
geo_drawn = centre / (centre + inside_ret)
geo = np.where(is_primary, geo_primary, geo_drawn)
print("\nPhase 3 geometric split: share_out_geo quantiles", pd.Series(geo).describe(percentiles=[.1, .5, .9]).round(3).to_dict())
print("  stays with share_out_geo >= 0.999 (zero inside duration):", int((geo >= 0.999).sum()), "of", len(s))
print("  current rule capped (from the report): 440 of 617")
