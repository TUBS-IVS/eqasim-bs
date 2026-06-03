"""
Load the BMV/BBSR RegioStaR Gemeinde reference table (TASK-004).

Source data
-----------
``eqasim-data/data/regiostar/regiostar_referenzdatei.xlsx`` (sheet
``ReferenzGebietsstand2020``), fetched via
``scripts/download_regiostar.py``.

Output
------
``DataFrame`` with one row per Gemeinde:

    - ``commune_id``   8-digit AGS as zero-padded string (matches the
                       ``commune_id`` keys used downstream in
                       ``synthesis.locations`` etc.).
    - ``ars5``         5-digit Kreis prefix.
    - ``name``         Gemeindename.
    - ``regiostar7``   integer 71–77 RegioStaR-7 type.
    - ``regiostar17``  integer 3-digit RegioStaR-17 type (finer).
    - ``regiostar_gem7`` integer Gemeinde-typ (RegioStaRGem7).

The frame is filtered to the configured ``braunschweig.political_prefix``
(ZGB-8 by default) so downstream stages do not pay for full-Germany
overhead. A small ``RegioStaR-7`` legend is exposed as ``REGIOSTAR7_LABELS``
for reporting.

REGIOSTAR-7 codes (BMV 2020):
    71 — Metropole
    72 — Regiopole, Großstadt
    73 — Mittelstadt, städtischer Raum
    74 — Kleinstädtischer, dörflicher Raum
    75 — Zentrale Stadt
    76 — Mittelstadt, städtischer Raum (ländlich)
    77 — Kleinstädtischer, dörflicher Raum (ländlich)
"""

from __future__ import annotations

import os

import geopandas as gpd
import numpy as np
import pandas as pd

REGIOSTAR7_LABELS = {
    71: "Metropole",
    72: "Regiopole, Großstadt",
    73: "Mittelstadt/städtischer Raum",
    74: "Kleinstädtischer, dörflicher Raum",
    75: "Zentrale Stadt (ländlich)",
    76: "Mittelstadt/städtischer Raum (ländlich)",
    77: "Kleinstädtischer, dörflicher Raum (ländlich)",
}

SHEET = "ReferenzGebietsstand2020"


def ars_to_ags8(commune_id):
    """Convert a 12-digit ARS to the 8-digit AGS this table keys on.

    ``data.spatial.municipalities`` carries the full 12-character ARS
    (Land(2)+RB(1)+Kreis(2)+VG(4)+Gem(3)) while the RegioStaR-7 table keys on the
    8-digit AGS = ARS[0:5] + ARS[9:12] (the 4-digit Verbandsgemeinde block is
    dropped). An 8-digit input is returned unchanged so the helper is idempotent.
    """
    s = str(commune_id)
    if len(s) == 12:
        return s[0:5] + s[9:12]
    return s


def fill_missing_rs7_nearest_neighbour(df_known, df_expected):
    """Return RS7 for every Gemeinde in ``df_expected``.

    ``df_known`` has [commune_id, ars5, name, regiostar7, x, y] for Gemeinden
    that already have an RS7 code (x/y are centroid coords in a metric CRS).
    ``df_expected`` has [commune_id, x, y] for all Gemeinden downstream stages
    need. Gemeinden in ``df_known`` keep their code (``rs7_filled=False``);
    Gemeinden missing from it get the RS7 of the nearest known Gemeinde by
    Euclidean centroid distance (``rs7_filled=True``).
    """
    known_ids = set(df_known["commune_id"])
    kx = df_known["x"].to_numpy()
    ky = df_known["y"].to_numpy()
    kcode = df_known["regiostar7"].to_numpy()
    rows = []
    for _, r in df_expected.iterrows():
        cid = r["commune_id"]
        if cid in known_ids:
            kr = df_known[df_known["commune_id"] == cid].iloc[0]
            rows.append({"commune_id": cid, "regiostar7": int(kr["regiostar7"]),
                         "rs7_filled": False})
        else:
            d2 = (kx - r["x"]) ** 2 + (ky - r["y"]) ** 2
            nn = int(np.argmin(d2))
            rows.append({"commune_id": cid, "regiostar7": int(kcode[nn]),
                         "rs7_filled": True})
    return pd.DataFrame(rows)


def configure(context):
    context.config("data_path")
    context.config(
        "regiostar.path",
        "regiostar/regiostar_referenzdatei.xlsx",
    )
    context.config("braunschweig.political_prefix")


def _resolve_path(context) -> str:
    return os.path.join(
        context.config("data_path"),
        context.config("regiostar.path"),
    )


def execute(context) -> pd.DataFrame:
    path = _resolve_path(context)
    raw = pd.read_excel(path, sheet_name=SHEET, header=0)

    expected_cols = {
        "gem_20", "name_20", "RegioStaR7", "RegioStaR17", "RegioStaRGem7",
    }
    missing = expected_cols - set(raw.columns)
    if missing:
        raise RuntimeError(
            "[braunschweig.data.bbsr.regiostar] expected columns missing "
            f"from {path}::{SHEET}: {sorted(missing)}"
        )

    df = pd.DataFrame({
        "commune_id": raw["gem_20"].astype("Int64").astype(str).str.zfill(8),
        "name": raw["name_20"].astype(str),
        "regiostar7": pd.to_numeric(raw["RegioStaR7"], errors="coerce")
                       .astype("Int64"),
        "regiostar17": pd.to_numeric(raw["RegioStaR17"], errors="coerce")
                        .astype("Int64"),
        "regiostar_gem7": pd.to_numeric(raw["RegioStaRGem7"], errors="coerce")
                            .astype("Int64"),
    })
    df["ars5"] = df["commune_id"].str[:5]
    df = df.dropna(subset=["regiostar7"]).copy()

    scope = [str(p) for p in context.config("braunschweig.political_prefix")]
    df = df[df["ars5"].isin(scope)].reset_index(drop=True)

    if df.empty:
        raise RuntimeError(
            "[braunschweig.data.bbsr.regiostar] no Gemeinden matched scope "
            f"{scope}; check ``braunschweig.political_prefix`` and source file."
        )

    # Expected universe: all Gemeinden in scope, from VG250 (gives coords too).
    vg250 = os.path.join(context.config("data_path"),
                         "germany", "vg250-ew_12-31.utm32s.gpkg.ebenen.zip")
    vsi = ("/vsizip/" + vg250 +
           "/vg250-ew_12-31.utm32s.gpkg.ebenen/vg250-ew_ebenen_1231/DE_VG250.gpkg")
    gem = gpd.read_file(vsi, layer="vg250_gem", columns=["AGS", "GEN"])
    gem["commune_id"] = gem["AGS"].astype(str).str.zfill(8)
    gem = gem[gem["commune_id"].str[:5].isin(scope)].copy()
    gem["x"] = gem.geometry.centroid.x
    gem["y"] = gem.geometry.centroid.y

    known = df.merge(gem[["commune_id", "x", "y"]], on="commune_id", how="inner")
    expected = gem[["commune_id", "x", "y"]].drop_duplicates("commune_id")
    filled = fill_missing_rs7_nearest_neighbour(known, expected)

    n_filled = int(filled["rs7_filled"].sum())
    if n_filled:
        ids = filled[filled["rs7_filled"]]["commune_id"].tolist()
        print(f"[braunschweig.data.bbsr.regiostar] filled RS7 for {n_filled} "
              f"Gemeinde(n) via nearest-neighbour: {ids}")

    meta = df[["commune_id", "ars5", "name", "regiostar17", "regiostar_gem7"]]
    df = filled.merge(meta, on="commune_id", how="left")
    df["ars5"] = df["ars5"].fillna(df["commune_id"].str[:5])
    df["regiostar7"] = df["regiostar7"].astype("Int64")

    df = df[[
        "commune_id", "ars5", "name",
        "regiostar7", "regiostar17", "regiostar_gem7",
    ]]

    counts = df["regiostar7"].value_counts().sort_index()
    print(
        "[braunschweig.data.bbsr.regiostar] "
        f"{len(df)} Gemeinden across {df['ars5'].nunique()} Kreise; "
        f"RegioStaR7 distribution: {dict(counts)}"
    )
    return df


def validate(context):
    path = _resolve_path(context)
    if not os.path.exists(path):
        raise RuntimeError(
            f"RegioStaR reference file not found: {path}\n"
            "Run scripts/download_regiostar.py to fetch it."
        )
    return os.path.getsize(path)
