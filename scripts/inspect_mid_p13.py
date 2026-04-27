"""Inspect MiD P13 — band shares + implied means + long-distance shares."""
import pandas as pd
import numpy as np

df = pd.read_csv("eqasim-data/data/braunschweig/mid/mid2023_P13.csv")
print("P13 columns:", list(df.columns))
print()
print(df.to_string(index=False))

bands = [
    ("d_0", 0.25), ("d_0_5", 2.5), ("d_5_10", 7.5), ("d_10_20", 15.0),
    ("d_20_30", 25.0), ("d_30_50", 40.0), ("d_50_100", 75.0),
    ("d_100p", 150.0),
]
centres = np.array([m for _, m in bands])
print("\n=== Band-midpoint mean per Kreis (P13) ===")
for _, r in df.iterrows():
    vals = np.array(
        [r[c] if not pd.isna(r[c]) else 0.0 for c, _ in bands], float
    )
    if vals.sum() <= 0:
        continue
    mean = (vals * centres).sum() / vals.sum()
    far = vals[-2:].sum() / vals.sum()
    very_far = vals[-1] / vals.sum()
    name = str(r["kreis"]).strip()
    print(
        f"  {name:35s} ars5={str(r['ars5']):>5s}  "
        f"mean={mean:6.2f} km   "
        f">=50km={far:5.1%}   >=100km={very_far:5.1%}"
    )

# Population-weighted overall mean (use n_total if present)
if "n_total" in df.columns:
    print("\n=== Population-weighted ZGB-8 mean (using n_total) ===")
    sub = df[df["ars5"].astype(str).isin([
        "03101", "03102", "03103", "03151",
        "03153", "03154", "03157", "03158",
    ])].copy()
    means = []
    weights = []
    for _, r in sub.iterrows():
        vals = np.array(
            [r[c] if not pd.isna(r[c]) else 0.0 for c, _ in bands], float
        )
        if vals.sum() <= 0:
            continue
        mean = (vals * centres).sum() / vals.sum()
        means.append(mean)
        weights.append(float(r["n_total"]) if not pd.isna(r["n_total"]) else 0)
    means = np.array(means)
    weights = np.array(weights)
    print(f"  n-weighted mean: {(means*weights).sum()/weights.sum():.2f} km")
    print(f"  unweighted mean: {means.mean():.2f} km")
