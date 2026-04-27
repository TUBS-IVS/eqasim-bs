"""Quick diagnostic — does Pendleratlas include intra-Kreis flows?"""
import sys, pandas as pd
sys.path.insert(0, '.')
from scripts.calibrate_gravity_decay import (
    _read_pendler_csv, EIN_CSV, AUS_CSV, ZGB8,
)

ein = _read_pendler_csv(EIN_CSV, "ein")
aus = _read_pendler_csv(AUS_CSV, "aus")
print(f"EIN raw rows: {len(ein):,}  AUS raw rows: {len(aus):,}")
print(f"EIN intra (orig==dest): {(ein['orig_ars']==ein['dest_ars']).sum()}")
print(f"AUS intra (orig==dest): {(aus['orig_ars']==aus['dest_ars']).sum()}")

df = (
    pd.concat([ein, aus], ignore_index=True)
      .groupby(["orig_ars", "dest_ars"], as_index=False)
      .agg(flow=("flow", "max"))
)
print(f"\nTotal OD pairs: {len(df):,}")
print(f"  intra-Kreis pairs: {(df['orig_ars']==df['dest_ars']).sum():,}")
print(f"  cross-Kreis pairs: {(df['orig_ars']!=df['dest_ars']).sum():,}")

# ZGB-origin breakdown
df_zgb = df[df["orig_ars"].isin(ZGB8)].copy()
intra = df_zgb[df_zgb["orig_ars"] == df_zgb["dest_ars"]]
inter = df_zgb[df_zgb["orig_ars"] != df_zgb["dest_ars"]]
intra_sum = int(intra["flow"].sum())
inter_sum = int(inter["flow"].sum())
print("\n=== ZGB-origin commuters (BA Pendleratlas) ===")
print(f"  Intra-Kreis flow sum: {intra_sum:>10,}")
print(f"  Cross-Kreis flow sum: {inter_sum:>10,}")
total = intra_sum + inter_sum
if total:
    print(f"  Cross share: {inter_sum/total:.1%}")

print("\nPer-Kreis split:")
for k in ZGB8:
    i = int(intra[intra["orig_ars"] == k]["flow"].sum())
    o = int(inter[inter["orig_ars"] == k]["flow"].sum())
    t = i + o
    if t:
        print(f"  {k}: intra={i:>7,}  cross={o:>7,}  intra-share={i/t:.1%}")
    else:
        print(f"  {k}: empty")
