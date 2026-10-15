"""Why is share_out capped for 71 % of portal stays in the ON smoke? Read-only probe on felix.

Recomputes the stays from the cached pre-portal trips and the portal stage output, then splits the
capped stays by cause: (a) the return leg's reported distance is missing/zero, (b) the outside
distance (gate -> destination point) exceeds the reported return distance.
"""
import glob
import os
import pickle
import sys

import numpy as np
import pandas as pd

cache = sys.argv[1]


def load(stage):
    files = [f for f in glob.glob(os.path.join(cache, f"{stage}__*.p"))]
    f = max(files, key=os.path.getmtime)
    with open(f, "rb") as fh:
        return pickle.load(fh), f


pre, f_pre = load("braunschweig.synthesis.commute_day.trips_day_stage")
core, f_core = load("braunschweig.synthesis.portal_trips.stage")
print("pre:", f_pre, "core:", f_core)
anchors = core["anchors"]
post = core["trips"]
print("stays (anchors):", len(anchors))

# Return legs in the post table: preceding_purpose == outside
ret = post[post["preceding_purpose"] == "outside"].copy()
print("return legs:", len(ret))
# Match each return leg to its anchor (same person, activity_index == trip_index)
ret = ret.merge(anchors[["person_id", "activity_index", "geometry", "kind"]],
                left_on=["person_id", "trip_index"], right_on=["person_id", "activity_index"], how="left")
print("return legs without anchor:", int(ret["geometry"].isna().sum()))
# The pre-portal return leg's reported distance: find by person and departure time
pre_idx = pre.set_index(["person_id", "departure_time"])["euclidean_distance"]
dur = ret["arrival_time"] - ret["departure_time"]
print("return legs with zero planned duration (capped):", int((dur <= 0).sum()), "of", len(ret))
missing = pre.loc[pre["euclidean_distance"].isna() | (pre["euclidean_distance"] <= 0)]
print("pre-portal legs with missing/zero reported distance:", len(missing), "of", len(pre),
      "| following home:", int((missing["following_purpose"] == "home").sum()))
print("purpose of the portal stays' outbound legs (pre):")
out_legs = post[post["following_purpose"] == "outside"]
print(" by kind:", anchors["kind"].value_counts().to_dict())
print(" zero-duration return legs by anchor kind:", ret.loc[dur <= 0, "kind"].value_counts().to_dict())
print(" return legs: reported (post euclidean, inside part) quantiles m:",
      ret["euclidean_distance"].describe(percentiles=[.5, .9]).round(0).to_dict())
