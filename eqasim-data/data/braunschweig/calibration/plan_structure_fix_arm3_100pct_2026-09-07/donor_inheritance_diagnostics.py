"""Are short work activities / same-purpose repeats inherited from the MiD donor diaries?"""
import sys, pickle, numpy as np, pandas as pd
path = sys.argv[1]
with open(path, "rb") as f:
    obj = pickle.load(f)
trips = obj["trips"] if isinstance(obj, dict) else obj
t = trips.sort_values(["person_id", "trip_index"]).reset_index(drop=True)
same = t["person_id"].values[1:] == t["person_id"].values[:-1]
def nxt(col, fill):
    a = np.full(len(t), fill, dtype=object); a[:-1][same] = t[col].values[1:][same]; return a
t["nxt_pur"] = nxt("following_purpose", None)
t["nxt_dep"] = pd.to_numeric(pd.Series(nxt("departure_time", np.nan)), errors="coerce").values
t["nxt_zweck"] = pd.to_numeric(pd.Series(nxt("W_ZWECK", np.nan)), errors="coerce").values
t["nxt_clo"] = pd.Series(nxt("is_synthetic_closure", False)).fillna(False).astype(bool).values
t["dur_h"] = (t["nxt_dep"] - t["arrival_time"]) / 3600.0
def report(d, label):
    w = d[(d["following_purpose"] == "work") & d["dur_h"].notna()]
    pairs = d[d["nxt_pur"].notna()]
    rep = pairs[pairs["following_purpose"] == pairs["nxt_pur"]]
    print(f"{label}: persons {d.person_id.nunique()}, trips {len(d)}, work acts {len(w)}, "
          f"<2h {(w.dur_h<2).mean():.3f}, mean {w.dur_h.mean():.2f}; same-purpose repeats {len(rep)/len(pairs):.3f} of pairs, {len(rep)/len(d):.3f} of trips")
    ww = w[w["nxt_pur"] == "work"]
    print(f"   work->work pairs {len(ww)} ({len(ww)/len(w):.3f} of work acts), <2h {(ww.dur_h<2).mean():.3f}")
    comp = ww.groupby([pd.to_numeric(ww["W_ZWECK"], errors="coerce"), ww["nxt_zweck"]]).agg(
        n=("dur_h", "size"), lt2h=("dur_h", lambda s: (s < 2).mean()), mean_h=("dur_h", "mean")).sort_values("n", ascending=False).head(8)
    print("   by (W_ZWECK of trip to work, W_ZWECK of next trip):"); print(comp.round(3).to_string())
    print("   repeats by purpose:", rep["following_purpose"].value_counts().to_dict())
    print("   repeats by purpose, share of that purpose's trips:",
          (rep["following_purpose"].value_counts() / d["following_purpose"].value_counts()).round(3).dropna().to_dict())
report(t, "ALL SYNTHETIC")
# donor-level: one realised chain per unique MiD donor person (unweighted) -> what the MiD pool itself looks like
first_pid = t.drop_duplicates(["source_H_ID", "source_P_ID"])["person_id"]
report(t[t["person_id"].isin(first_pid)], "UNIQUE DONORS (MiD pool as realised)")
# how many work->work with both W_ZWECK==1 (Arbeit -> Arbeit) and their raw MiD times
ww = t[(t["following_purpose"] == "work") & (t["nxt_pur"] == "work")]
z = pd.to_numeric(ww["W_ZWECK"], errors="coerce")
print("work->work: W_RBW of trip", ww["W_RBW"].value_counts(dropna=False).to_dict())
print("work->work sample of 12 (person_id, trip_index, dep, arr, nxt_dep, W_ZWECK, nxt_zweck, W_SZ, W_AZ, wegmin):")
print(ww[["person_id","trip_index","departure_time","arrival_time","nxt_dep","W_ZWECK","nxt_zweck","W_SZ","W_AZ","wegmin","is_synthetic_closure"]].head(12).to_string())
