"""Diagnose short work activities and same-purpose repeats on a trips_stage cache pickle."""
import sys, pickle, numpy as np, pandas as pd
path = sys.argv[1]; label = sys.argv[2]
with open(path, "rb") as f:
    obj = pickle.load(f)
trips = obj["trips"] if isinstance(obj, dict) else obj
print(label, "rows", len(trips), "cols", list(trips.columns))
t = trips.sort_values(["person_id", "trip_index"]).reset_index(drop=True)
same = t["person_id"].values[1:] == t["person_id"].values[:-1]
nxt_dep = np.full(len(t), np.nan); nxt_dep[:-1][same] = t["departure_time"].values[1:][same]
nxt_pur = np.full(len(t), None, dtype=object); nxt_pur[:-1][same] = t["following_purpose"].values[1:][same]
clo = t["is_synthetic_closure"].values if "is_synthetic_closure" in t.columns else np.zeros(len(t), bool)
nxt_clo = np.zeros(len(t), bool); nxt_clo[:-1][same] = clo[1:][same]
t["dur_h"] = (nxt_dep - t["arrival_time"].values) / 3600.0
t["nxt_pur"] = nxt_pur; t["nxt_clo"] = nxt_clo
w = t[(t["following_purpose"] == "work") & t["dur_h"].notna()]
print(f"{label}: work activities {len(w)}, mean {w.dur_h.mean():.2f} h, <2h {(w.dur_h<2).mean():.3f}")
for name, m in [("next=closure", w.nxt_clo), ("next=work (repeat)", w.nxt_pur == "work"),
                ("next=home, not closure", (w.nxt_pur == "home") & ~w.nxt_clo),
                ("next=other purpose", (w.nxt_pur != "home") & (w.nxt_pur != "work"))]:
    s = w[m]
    print(f"  {name:24s} n={len(s):7d} share={len(s)/len(w):.3f} <2h={(s.dur_h<2).mean():.3f} mean={s.dur_h.mean():.2f} contrib_to_<2h={(s.dur_h<2).sum()/len(w):.3f}")
print("  dur_h quantiles", w.dur_h.quantile([.05,.1,.25,.5,.75,.9]).round(2).to_dict())
rep = t[t["following_purpose"] == t["nxt_pur"]]
print(f"{label}: same-purpose repeats {len(rep)} / {same.sum()} pairs = {len(rep)/same.sum():.3f}; of all trips {len(rep)/len(t):.3f}")
print(rep["following_purpose"].value_counts().to_string())
# repeats by dwell
print("  repeat dwell <0.5h share", (rep.dur_h < 0.5).mean().round(3), " <2h", (rep.dur_h<2).mean().round(3))
# home->home trips
hh = t[(t["preceding_purpose"] == "home") & (t["following_purpose"] == "home")]
print(f"{label}: home->home trips {len(hh)} ({len(hh)/len(t):.3f}), of which closure {int(clo[hh.index].sum())}")
