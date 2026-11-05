"""Compare the RegioStaR Gebietsstand sheets with the VG250 31.12.2024 Gemeinde universe
for the ZGB-8 scope (evidence for ADR-0180; run manifest
regiostar-gebietsstand-comparison-2026-11-05). Reads the three inputs read-only.

Usage (names printed with ascii() so their exact code points stay visible):
  python -I -X utf8 compare_regiostar_gebietsstand.py <regiostar_referenzdatei.xlsx (BMV 2020 bundle)>
      <regiostar_referenzdateien_2024.xlsx (mobilithek workbook)> <vg250-ew_12-31.utm32s.gpkg.ebenen.zip>
"""
import sys

import geopandas as gpd
import pandas as pd

ZGB = ["03101", "03102", "03103", "03151", "03153", "03154", "03157", "03158"]
rs2020_path, rs2024_path, vg250_path = sys.argv[1:4]

pd.set_option("display.width", 250)
pd.set_option("display.max_rows", 400)
pd.set_option("display.max_columns", 30)


def norm(df, code, name, rs7, rs17, gem7):
    out = pd.DataFrame({
        "commune_id": pd.to_numeric(df[code], errors="coerce").astype("Int64").astype(str).str.zfill(8),
        "name": df[name].astype(str),
        "rs7": pd.to_numeric(df[rs7], errors="coerce").astype("Int64"),
        "rs17": pd.to_numeric(df[rs17], errors="coerce").astype("Int64"),
        "gem7": (pd.to_numeric(df[gem7], errors="coerce").astype("Int64") if gem7 else pd.NA),
    })
    out = out[out["commune_id"].str[:5].isin(ZGB)].dropna(subset=["rs7"])
    return out.set_index("commune_id").sort_index()


cur = pd.read_excel(rs2020_path, sheet_name="ReferenzGebietsstand2020")
print("current bundle sheets:", pd.ExcelFile(rs2020_path).sheet_names)
cur20 = norm(cur, "gem_20", "name_20", "RegioStaR7", "RegioStaR17", "RegioStaRGem7")
print("current bundle 2020 ZGB rows:", len(cur20), "(Germany rows:", len(cur), ")")

sheets = {}
for yy in (20, 21, 22, 23, 24):
    raw = pd.read_excel(rs2024_path, sheet_name=f"ReferenzGebietsstand20{yy}")
    sheets[yy] = norm(raw, f"gem_{yy}", f"name_{yy}", "RegioStaR7", "RegioStaR17", "RegioStaRGem7")
    print(f"2024-workbook sheet 20{yy}: Germany rows {len(raw)}, ZGB rows {len(sheets[yy])}")

# Identity of the 2020 sheet across the two files (Germany-wide and ZGB).
full_cur = cur[["gem_20", "name_20", "RegioStaR7", "RegioStaR17", "RegioStaRGem7"]].reset_index(drop=True)
full_new = pd.read_excel(rs2024_path, sheet_name="ReferenzGebietsstand2020")[
    ["gem_20", "name_20", "RegioStaR7", "RegioStaR17", "RegioStaRGem7"]].reset_index(drop=True)
print("2020 sheet identical across files (5 key columns, Germany):", full_cur.equals(full_new),
      "| ZGB identical:", cur20.equals(sheets[20]))

gem = gpd.read_file(
    "/vsizip/" + vg250_path + "/vg250-ew_12-31.utm32s.gpkg.ebenen/vg250-ew_ebenen_1231/DE_VG250.gpkg",
    layer="vg250_gem", columns=["AGS", "GEN", "BEZ", "GF", "EWZ"])
gem["commune_id"] = gem["AGS"].astype(str).str.zfill(8)
gem = gem[gem["commune_id"].str[:5].isin(ZGB)]
print("VG250 ZGB rows:", len(gem), "distinct AGS:", gem["commune_id"].nunique(),
      "GF values:", sorted(gem["GF"].unique().tolist()))
vg = gem.drop_duplicates("commune_id").set_index("commune_id").sort_index()
U = set(vg.index)

for yy, df in sheets.items():
    ids = set(df.index)
    print(f"\n=== Gebietsstand 20{yy}: ZGB codes {len(ids)}; in VG250 but not sheet: "
          f"{sorted(U - ids)}; in sheet but not VG250: {sorted(ids - U)}")
    for cid in sorted(ids - U):
        print("    sheet-only", cid, df.loc[cid, "name"], int(df.loc[cid, "rs7"]))
    for cid in sorted(U - ids):
        print("    vg250-only", cid, vg.loc[cid, "GEN"], vg.loc[cid, "BEZ"], "EWZ", int(vg.loc[cid, "EWZ"]))
    if yy == 20:
        continue
    common = sorted(ids & set(cur20.index))
    a, b = cur20.loc[common], df.loc[common]
    for col in ("rs7", "rs17", "gem7"):
        if False:
            continue
        diff = a[col].astype("Int64") != b[col].astype("Int64")
        diff = diff.fillna(True)
        print(f"  {col}: {int(diff.sum())} of {len(common)} common codes differ from the 2020 bundle")
        if diff.any():
            print(pd.DataFrame({"name20": a.loc[diff, "name"], f"{col}_20": a.loc[diff, col],
                                f"name{yy}": b.loc[diff, "name"], f"{col}_{yy}": b.loc[diff, col]}))
    ndiff = a["name"] != b["name"]
    print(f"  name: {int(ndiff.sum())} of {len(common)} names differ")
    if ndiff.any():
        for cid in a.index[ndiff]:
            print("   ", cid, ascii(a.loc[cid, "name"]), "->", ascii(b.loc[cid, "name"]))
    if "03153019" in df.index:
        print("  Langelsheim 03153019:", df.loc["03153019"].to_dict())

print("\nOld Goslar codes in the 2020 bundle:")
print(cur20.loc[[c for c in ("03153006", "03153007", "03153009", "03153014") if c in cur20.index]])
print("\nVG250 Goslar rows:")
print(vg[vg.index.str.startswith("03153")][["GEN", "BEZ", "EWZ"]])
