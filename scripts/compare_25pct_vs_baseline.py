"""Detailed comparison: current 25% (Phase-2) vs baseline_pre_phase2 vs MiD 2023."""
from __future__ import annotations

import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "eqasim-data" / "output_bs_25pct"
BASE = OUT / "baseline_pre_phase2"
MID = REPO / "eqasim-data" / "data" / "braunschweig" / "mid"

ZGB8 = {
    "03101": "SK Braunschweig", "03102": "SK Salzgitter", "03103": "SK Wolfsburg",
    "03151": "LK Gifhorn", "03153": "LK Goslar", "03154": "LK Helmstedt",
    "03157": "LK Peine", "03158": "LK Wolfenbüttel",
}

def rd(name, sep=";"):
    return pd.read_csv(name, sep=sep, low_memory=False)

# ---------------------------------------------------------------- load
print("Loading current 25%...")
cur_p  = rd(OUT / "braunschweig_25pct_persons.csv")
cur_h  = rd(OUT / "braunschweig_25pct_households.csv")
cur_t  = rd(OUT / "braunschweig_25pct_trips.csv")
cur_homes = gpd.read_file(OUT / "braunschweig_25pct_homes.gpkg")
cur_act = gpd.read_file(OUT / "braunschweig_25pct_activities.gpkg")

print("Loading baseline...")
base_p = rd(BASE / "persons.csv")
base_h = rd(BASE / "households.csv")
base_t = rd(BASE / "trips.csv")

print("Loading MiD...")
mid = {c: pd.read_csv(MID / f"mid2023_{c}.csv") for c in ("P9", "P12_1", "P13", "P17_1")}
for v in mid.values():
    v["ars5"] = v["ars5"].astype(str)

# ---------------------------------------------------------------- VG250 → Kreis
print("Joining VG250...")
VG = REPO / "eqasim-data" / "data" / "germany" / "vg250-ew_12-31.utm32s.gpkg.ebenen.zip"
INNER = "vg250-ew_12-31.utm32s.gpkg.ebenen/vg250-ew_ebenen_1231/DE_VG250.gpkg"
with zipfile.ZipFile(VG) as z, z.open(INNER) as f:
    vg = gpd.read_file(f, layer="vg250_gem")
vg["ars5"] = vg["ARS"].astype(str).str[:5]
kreise = (vg[vg["ars5"].isin(ZGB8)][["ars5", "geometry"]]
          .dissolve(by="ars5", as_index=False)
          .to_crs(cur_homes.crs))

homes_kreis = gpd.sjoin(cur_homes, kreise, how="left", predicate="within")[
    ["household_id", "ars5"]].drop_duplicates("household_id")
cur_p_k = cur_p.merge(homes_kreis, on="household_id", how="left")
cur_h_k = cur_h.merge(homes_kreis, on="household_id", how="left")

# ---------------------------------------------------------------- print helpers
def hr(t):
    print("\n" + "=" * 78); print(t); print("=" * 78)

# ---------------------------------------------------------------- 1. Counts
hr("1. POPULATION COUNTS")
print(f"{'':25} {'baseline':>12} {'current':>12} {'delta':>10} {'delta%':>8}")
for label, b, c in [
    ("households", len(base_h), len(cur_h)),
    ("persons", len(base_p), len(cur_p)),
    ("trips", len(base_t), len(cur_t)),
]:
    d = c - b
    print(f"{label:25} {b:>12,} {c:>12,} {d:>+10,} {100*d/b:>+7.2f}%")

# ---------------------------------------------------------------- 2. Demographics
hr("2. DEMOGRAPHICS — baseline vs current (ZGB-8 total)")
def pct(s, dropna=True):
    return (s.value_counts(normalize=True, dropna=dropna).sort_index() * 100).round(2)
print("\n[sex]"); print(pd.DataFrame({"baseline": pct(base_p["sex"]), "current": pct(cur_p["sex"])}))
print("\n[employed]"); print(pd.DataFrame({"baseline": pct(base_p["employed"]), "current": pct(cur_p["employed"])}))
print("\n[has_driving_license]")
print(pd.DataFrame({"baseline": pct(base_p["has_driving_license"]), "current": pct(cur_p["has_driving_license"])}))

print("\n[age stats]")
print(pd.DataFrame({"baseline": base_p["age"].describe(), "current": cur_p["age"].describe()}).round(2))

print("\n[age bands %]")
def age_band(s):
    bins = [0, 6, 14, 17, 24, 34, 44, 54, 64, 74, 200]
    lab = ["0-5", "6-13", "14-16", "17-24", "25-34", "35-44", "45-54", "55-64", "65-74", "75+"]
    return pd.cut(s, bins=bins, labels=lab, right=False).value_counts(normalize=True).reindex(lab) * 100
print(pd.DataFrame({"baseline": age_band(base_p["age"]).round(2), "current": age_band(cur_p["age"]).round(2)}))

# ---------------------------------------------------------------- 3. Household size
hr("3. HOUSEHOLD SIZE — baseline vs current (this is the IPF key indicator)")
def hh_dist(df):
    s = df["household_size"].astype(str)
    return (s.value_counts(normalize=True).reindex(["1","2","3","4","5","5+","6"]).fillna(0) * 100).round(2)
print(pd.DataFrame({"baseline": hh_dist(base_h), "current": hh_dist(cur_h)}))

base_h["_n"] = pd.to_numeric(base_h["household_size"].astype(str).replace({"5+":"5"}), errors="coerce")
cur_h["_n"] = pd.to_numeric(cur_h["household_size"].astype(str).replace({"5+":"5"}), errors="coerce")
print(f"\nMean HH size  base={base_h['_n'].mean():.3f}   cur={cur_h['_n'].mean():.3f}   "
      f"delta={cur_h['_n'].mean() - base_h['_n'].mean():+.3f}")

# Census reference (Zensus 2022 Niedersachsen aggregate share - approx)
# Niedersachsen 2022 Zensus HH size share %: 1=39.5, 2=33.1, 3=12.0, 4=10.6, 5+=4.8
ns_ref = pd.Series({"1": 39.5, "2": 33.1, "3": 12.0, "4": 10.6, "5+": 4.8}, name="NS_zensus")
cur_dist_top = cur_h["household_size"].astype(str).value_counts(normalize=True)
cur_5plus = cur_dist_top.reindex(["5","5+","6"]).fillna(0).sum()
cur_top = pd.Series({
    "1": cur_dist_top.get("1", 0)*100,
    "2": cur_dist_top.get("2", 0)*100,
    "3": cur_dist_top.get("3", 0)*100,
    "4": cur_dist_top.get("4", 0)*100,
    "5+": cur_5plus*100,
}).round(2)
base_dist_top = base_h["household_size"].astype(str).value_counts(normalize=True)
base_5plus = base_dist_top.reindex(["5","5+","6"]).fillna(0).sum()
base_top = pd.Series({
    "1": base_dist_top.get("1", 0)*100,
    "2": base_dist_top.get("2", 0)*100,
    "3": base_dist_top.get("3", 0)*100,
    "4": base_dist_top.get("4", 0)*100,
    "5+": base_5plus*100,
}).round(2)
hh_compare = pd.DataFrame({"NS_zensus": ns_ref, "baseline": base_top, "current": cur_top})
hh_compare["base_dev_pp"] = (hh_compare["baseline"] - hh_compare["NS_zensus"]).round(2)
hh_compare["cur_dev_pp"]  = (hh_compare["current"]  - hh_compare["NS_zensus"]).round(2)
print("\nHH-size vs NS-Zensus (% pts deviation):")
print(hh_compare)
print(f"|Δ| sum:  baseline={hh_compare['base_dev_pp'].abs().sum():.2f}   current={hh_compare['cur_dev_pp'].abs().sum():.2f}")

# ---------------------------------------------------------------- 4. Cars / income
hr("4. HOUSEHOLD AMENITIES")
for col in ("number_of_cars", "number_of_bicycles", "household_income_eur"):
    if col in cur_h.columns and col in base_h.columns:
        print(f"{col}:  base mean={pd.to_numeric(base_h[col], errors='coerce').mean():.3f}  "
              f"cur mean={pd.to_numeric(cur_h[col], errors='coerce').mean():.3f}")

# ---------------------------------------------------------------- 5. HH-size per Kreis
hr("5. HH-SIZE per Kreis (current) — IPF marginal evaluation")
hh_by_k = cur_h_k.dropna(subset=["ars5"]).groupby("ars5").apply(
    lambda g: (g["household_size"].astype(str).value_counts(normalize=True)
               .reindex(["1","2","3","4","5","5+","6"]).fillna(0) * 100).round(2)
)
print(hh_by_k)

# ---------------------------------------------------------------- 6. Trips
hr("6. TRIPS — baseline vs current")
print(f"trips/person   base={len(base_t)/len(base_p):.3f}   cur={len(cur_t)/len(cur_p):.3f}")
print("\nTrip purpose pairs (top 10, current):")
print(cur_t.groupby(["preceding_purpose", "following_purpose"]).size()
      .sort_values(ascending=False).head(10))
print("\nDeparture hour distribution (current):")
cur_t["dep_hr"] = (cur_t["departure_time"] // 3600).astype(int) % 24
print((cur_t["dep_hr"].value_counts(normalize=True).sort_index() * 100).round(2))

# ---------------------------------------------------------------- 7. Activities
hr("7. ACTIVITIES (current)")
purposes = cur_act["purpose"].value_counts()
print(purposes)
print(f"\nactivities/person = {len(cur_act)/cur_act['person_id'].nunique():.3f}")

# Spatial leak
print("\nActivities outside ZGB-8 polygon (% per purpose):")
act_in = gpd.sjoin(cur_act, kreise[["ars5", "geometry"]], how="left", predicate="within")
leak = (act_in["ars5"].isna().groupby(act_in["purpose"]).mean() * 100).round(2)
print(leak)

# ---------------------------------------------------------------- 8. Commute distance (MiD P13)
hr("8. COMMUTE DISTANCE per Kreis vs MiD 2023 P13")
work = cur_act[cur_act["purpose"] == "work"].drop_duplicates("person_id")[["person_id", "household_id", "geometry"]].rename(columns={"geometry":"work"})
home_geom = cur_homes[["household_id", "geometry"]].rename(columns={"geometry":"home"}).drop_duplicates("household_id")
cm = work.merge(home_geom, on="household_id", how="inner")
cm["d_km"] = cm.apply(lambda r: r["home"].distance(r["work"]) / 1000.0, axis=1)
cm = cm.merge(cur_p_k[["person_id", "ars5"]], on="person_id", how="left")
print(f"commute pairs: {len(cm)}")
print(cm["d_km"].describe().round(2))

BANDS = [(0,0.5,"d_0"),(0.5,5,"d_0_5"),(5,10,"d_5_10"),(10,20,"d_10_20"),
         (20,30,"d_20_30"),(30,50,"d_30_50"),(50,100,"d_50_100"),(100,np.inf,"d_100p")]
p13 = mid["P13"].set_index("ars5")
rows = []
for ars5, lab in ZGB8.items():
    sub = cm[cm["ars5"] == ars5]
    if sub.empty: continue
    syn = {n: 100.0 * np.mean((sub["d_km"] >= lo) & (sub["d_km"] < hi)) for lo,hi,n in BANDS}
    ref = p13.loc[ars5]
    for lo,hi,n in BANDS:
        rows.append({"kreis": lab, "band": n, "synth": round(syn[n],1),
                     "MiD": round(float(ref.get(n, np.nan)),1) if not pd.isna(ref.get(n)) else np.nan})
cmp = pd.DataFrame(rows).pivot(index="kreis", columns="band", values=["synth", "MiD"])
print(cmp)

mean_syn = cm.groupby("ars5")["d_km"].mean()
mean_mid = p13["mittel"]
sm = pd.DataFrame({"kreis": mean_syn.index.map(ZGB8), "syn_km": mean_syn.round(2),
                   "mid_km": mean_mid.reindex(mean_syn.index).round(2)})
sm["delta_km"] = (sm["syn_km"] - sm["mid_km"]).round(2)
print("\nMean commute km:")
print(sm.set_index("kreis"))
print(f"\nWeighted MAE (km, by Kreis size): {(sm['delta_km'].abs()).mean():.2f}")

# ---------------------------------------------------------------- 9. License rate vs MiD P17_1
hr("9. DRIVER LICENSE per Kreis vs MiD P17_1")
lic_pop = cur_p_k[cur_p_k["age"] >= 17].dropna(subset=["ars5"])
lic = (lic_pop.groupby("ars5")["has_driving_license"]
       .apply(lambda s: 100.0 * s.astype(str).str.lower().isin(["true","1","yes"]).mean()))
lic_mid = mid["P17_1"].set_index("ars5")["ja"]
lt = pd.DataFrame({"kreis": lic.index.map(ZGB8), "syn_pct": lic.round(1),
                   "mid_pct": lic_mid.reindex(lic.index).round(1)})
lt["delta_pp"] = (lt["syn_pct"] - lt["mid_pct"]).round(1)
print(lt.set_index("kreis"))
print(f"\n|Δ| MAE (pp): {lt['delta_pp'].abs().mean():.2f}")

# ---------------------------------------------------------------- 10. Employment vs MiD P9
hr("10. EMPLOYMENT per Kreis vs MiD P9")
work_pop = cur_p_k[cur_p_k["age"].between(15, 74)].dropna(subset=["ars5"])
emp = (work_pop.groupby("ars5")["employed"]
       .apply(lambda s: 100.0 * s.astype(str).str.lower().isin(["true","1","yes"]).mean()))
p9 = mid["P9"].set_index("ars5")
emp_mid = (p9.get("vollzeit", 0).fillna(0) + p9.get("teilzeit", 0).fillna(0) +
           p9.get("geringfuegig", 0).fillna(0) + p9.get("sonstiges", 0).fillna(0) +
           p9.get("erwerbstaetig_unspec", pd.Series(0, index=p9.index)).fillna(0))
et = pd.DataFrame({"kreis": emp.index.map(ZGB8), "syn_pct": emp.round(1),
                   "mid_pct": emp_mid.reindex(emp.index).round(1)})
et["delta_pp"] = (et["syn_pct"] - et["mid_pct"]).round(1)
print(et.set_index("kreis"))
print(f"\n|Δ| MAE (pp): {et['delta_pp'].abs().mean():.2f}")

print("\n" + "=" * 78)
print("DONE")
print("=" * 78)
