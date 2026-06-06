"""Population marginals for the ZGB Braunschweig region.

Strategy: combine two sources for best data quality.

* **DESTATIS 12411-0018** (``12411-0018_de.csv``) — authoritative Kreis-level
  counts in 17 age classes (0, 3, 6, 10, 15, 18, 20, 25, 30, 35, 40, 45, 50,
  55, 60, 65, 75), Stichtag 31.12.2024. Provides the official Kreis totals
  per (sex, age_class).

* **urbistat** (``urbistat_age_gemeinden.csv``) — Gemeinde-level counts in 11
  age classes. Used only to compute the *share* each Gemeinde contributes to
  its Kreis, per (sex, coarse_age_class).

The final Gemeinde × sex × age weight is::

    weight(gemeinde, sex, D_age) =
        DESTATIS(kreis, sex, D_age) * urbistat_share(gemeinde | kreis, sex, U_age)

where ``U_age`` is the urbistat class that overlaps the DESTATIS class most.
This keeps the spatial distribution from urbistat while adopting the official
DESTATIS totals and the Bavaria-compatible 17-class age scheme.

Output schema (matches ``braunschweig.data.census.population``):
    (commune_id, sex, age_class, weight)  with ARS-12 commune_id.
"""

import os
import re
import zipfile

import geopandas as gpd
import pandas as pd


# DESTATIS 12411-0018 age class lower bounds (17 classes).
DESTATIS_AGES: list[int] = [0, 3, 6, 10, 15, 18, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 75]

# Urbistat age class label -> lower bound.
URBISTAT_AGES: dict[str, int] = {
    "0 - 2 anni":    0,
    "3 - 5 anni":    3,
    "6 - 11 anni":   6,
    "12 - 17 anni": 12,
    "18 - 24 anni": 18,
    "25 - 34 anni": 25,
    "35 - 44 anni": 35,
    "45 - 54 anni": 45,
    "55 - 64 anni": 55,
    "65 - 74 anni": 65,
    "75 e più":     75,
}

# Map each DESTATIS age class to the urbistat class that overlaps it most.
DESTATIS_TO_URBISTAT_AGE: dict[int, int] = {
    0: 0, 3: 3, 6: 6, 10: 12, 15: 12, 18: 18, 20: 18,
    25: 25, 30: 25, 35: 35, 40: 35, 45: 45, 50: 45,
    55: 55, 60: 55, 65: 65, 75: 75,
}

# Zensus 2022 1000A-3082 ALTKL2 band lower bounds (11 bands).
ZENSUS_AGES: list[int] = [0, 5, 10, 15, 20, 25, 30, 40, 50, 60, 75]

# Map each DESTATIS age class to the Zensus 1000A-3082 band that CONTAINS its
# lower bound. The Zensus band edges (5/10/15/20/25/30/40/50/60/75) align with
# the DESTATIS edges far better than urbistat's coarse Italian bands: in
# particular DESTATIS 10 (ages 10-14) maps to the native Zensus 10-15 band
# instead of urbistat's 12-17, removing the school-age spatial smear flagged in
# the model review (item #5).
DESTATIS_TO_ZENSUS_AGE: dict[int, int] = {
    0: 0, 3: 0, 6: 5, 10: 10, 15: 15, 18: 15, 20: 20,
    25: 25, 30: 30, 35: 30, 40: 40, 45: 40, 50: 50,
    55: 50, 60: 60, 65: 60, 75: 75,
}


def configure(context):
    context.config("data_path")
    context.config("braunschweig.destatis_population_path",
                   "braunschweig/12411-0018_de.csv")
    context.config("braunschweig.urbistat_gemeinden_path",
                   "braunschweig/urbistat_age_gemeinden.csv")
    context.stage("eqasim_common.data.population.raw")

    # Optional: take the Gemeinde-within-Kreis population shares from open
    # Zensus 2022 (1000A-3082) instead of the scraped, non-redistributable
    # urbistat table. Default False -> urbistat path, byte-identical (item #5).
    context.config("braunschweig.census.use_zensus_gemeinde_shares", False)
    if context.config("braunschweig.census.use_zensus_gemeinde_shares"):
        context.stage("braunschweig.data.census.households_size_age")


# A DESTATIS (sex, age_class) cell that fails to parse as an integer is dropped
# (the cell is not added to the marginal). This is a silent population shrink:
# the cell is real demand that disappears with no count. We therefore count the
# PRIMARY (parsed) vs FALLBACK (skipped) cells and surface the skipped share.
#
# A non-zero skip rate is expected and benign on the genuine DESTATIS export
# because the suppressed-value placeholder ("-") and trailing summary/footer rows
# do not carry counts; the warning threshold is set above that natural floor so it
# never fires on valid data. A high skipped share almost always means the table
# format changed (wrong column offsets, shifted header) and the population is
# being silently shrunk -- that must fail loudly rather than run quietly.
DESTATIS_SKIP_WARN_FRACTION: float = 0.05
DESTATIS_SKIP_RAISE_FRACTION: float = 0.50


def _load_destatis(path: str, scope_kreise: set[str]) -> pd.DataFrame:
    raw = pd.read_csv(path, sep=";", encoding="utf-8-sig",
                      header=None, dtype=str, skiprows=6)
    rows: list[dict] = []
    parsed_cells = 0   # PRIMARY: (sex, age_class) cells parsed to an integer count
    skipped_cells = 0  # FALLBACK: cells dropped because they did not parse
    for _, r in raw.iterrows():
        ags = str(r.iloc[0]).strip()
        if ags not in scope_kreise:
            continue
        for i, age in enumerate(DESTATIS_AGES):
            m_val = r.iloc[2 + 2 * i]
            f_val = r.iloc[36 + 2 * i]
            try:
                m = int(m_val); f = int(f_val)
            except (TypeError, ValueError):
                # One non-integer cell drops both the male and female count for
                # this (kreis, age_class). Count it so a partial parse failure
                # cannot silently shrink the population (the all-empty case is
                # still caught by the `if not rows` raise below).
                skipped_cells += 1
                continue
            parsed_cells += 1
            rows.append({"kreis": ags, "sex": "male",   "age_class": age, "weight": m})
            rows.append({"kreis": ags, "sex": "female", "age_class": age, "weight": f})
    if not rows:
        raise RuntimeError(
            f"No DESTATIS rows matched ZGB Kreise in {path}. Scope={sorted(scope_kreise)}"
        )

    total_cells = parsed_cells + skipped_cells
    skipped_fraction = skipped_cells / total_cells if total_cells else 0.0
    print(f"[braunschweig.population] DESTATIS cells: primary {parsed_cells}/{total_cells} "
          f"({100.0 * parsed_cells / total_cells:.1f}%), "
          f"skipped {skipped_cells} ({100.0 * skipped_fraction:.1f}%)")
    if skipped_fraction >= DESTATIS_SKIP_RAISE_FRACTION:
        raise RuntimeError(
            f"DESTATIS parse skipped {skipped_cells}/{total_cells} cells "
            f"({100.0 * skipped_fraction:.1f}%) in {path}, above the "
            f"{100.0 * DESTATIS_SKIP_RAISE_FRACTION:.0f}% limit. This implausibly "
            f"high drop rate would silently shrink the population and almost "
            f"certainly means the table format/column offsets changed -- refusing "
            f"to continue with a corrupted marginal."
        )
    if skipped_fraction >= DESTATIS_SKIP_WARN_FRACTION:
        print(f"[braunschweig.population] WARNING: DESTATIS dropped {skipped_cells}/"
              f"{total_cells} unparseable (sex, age_class) cells "
              f"({100.0 * skipped_fraction:.1f}%); each dropped cell removes a real "
              f"population count. Verify the export is complete and the column "
              f"offsets still match.")
    return pd.DataFrame(rows)


def _norm(s: str) -> str:
    s = s.lower()
    s = re.sub(
        r",\s*(stadt|flecken|gemfr\.?\s*geb\.?|kreisfreie\s*stadt|"
        r"landkreis|berg-\s*und\s*universitaetsstadt|berg-\s*und\s*universit[aä]tsstadt|"
        r"samtgemeinde)",
        "", s,
    )
    s = (s.replace("ä", "ae").replace("ö", "oe")
          .replace("ü", "ue").replace("ß", "ss"))
    s = re.sub(r"[^a-z0-9]", "", s)
    return s


def _load_urbistat_shares(path: str, df_vg: pd.DataFrame) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"kreis_ars": str})
    df["kreis_ars"] = df["kreis_ars"].str.zfill(5)

    df = df[(df["age_class"] != "Totale") & (df["sex"] != "total")].copy()
    df = df[~df["name"].str.contains("gemfr", case=False, na=False)].copy()

    df["u_age"] = df["age_class"].map(URBISTAT_AGES).astype(int)
    df["norm"] = df["name"].map(_norm)

    df_vg = df_vg.copy()
    df_vg["kreis_ars"] = df_vg["ARS"].str[:5]
    df_vg["norm"] = df_vg["GEN"].map(_norm)
    df_vg = (df_vg.sort_values("EWZ", ascending=False)
                   .drop_duplicates(["kreis_ars", "norm"]))

    merged = df.merge(df_vg[["kreis_ars", "norm", "ARS"]],
                      on=["kreis_ars", "norm"], how="left")

    lost = merged[merged["ARS"].isna()]["name"].unique()
    if len(lost):
        print(f"[braunschweig.population] Dropping {len(lost)} urbistat entries "
              f"not in VG250: {list(lost)}")
        merged = merged[~merged["ARS"].isna()].copy()

    merged = merged.rename(columns={"ARS": "commune_id",
                                    "kreis_ars": "kreis",
                                    "count": "weight"})

    agg = (merged.groupby(["kreis", "commune_id", "sex", "u_age"],
                          as_index=False)["weight"].sum())

    tot = (agg.groupby(["kreis", "sex", "u_age"], as_index=False)["weight"]
              .sum().rename(columns={"weight": "kreis_total"}))
    agg = agg.merge(tot, on=["kreis", "sex", "u_age"])

    agg["share"] = 0.0
    mask = agg["kreis_total"] > 0
    agg.loc[mask, "share"] = agg.loc[mask, "weight"] / agg.loc[mask, "kreis_total"]

    return agg[["kreis", "commune_id", "sex", "u_age", "share"]]


def _load_zensus_shares(df_zensus: pd.DataFrame,
                        scope_kreise: set[str]) -> pd.DataFrame:
    """Gemeinde-within-Kreis population shares from Zensus 2022 1000A-3082.

    Unlike urbistat, the Zensus loader carries the authoritative 12-digit ARS
    ``commune_id`` directly, so no fuzzy Gemeinde-name matching against VG250 is
    needed. Persons are aggregated over household size (and the input is already
    per sex x ALTKL2 age band), then the share each Gemeinde contributes to its
    Kreis is computed per (sex, Zensus age band).

    Returns ``[kreis, commune_id, sex, z_age, share]`` where ``z_age`` is the
    ALTKL2 lower bound and the shares sum to 1 per (kreis, sex, z_age).
    """
    df = df_zensus.copy()
    df["commune_id"] = df["commune_id"].astype(str)
    df["kreis"] = df["commune_id"].str[:5]
    df = df[df["kreis"].isin(scope_kreise)].copy()
    df["z_age"] = df["lower_age"].astype(int)
    df["sex"] = df["sex"].astype(str)

    agg = (df.groupby(["kreis", "commune_id", "sex", "z_age"],
                      as_index=False)["weight"].sum())
    tot = (agg.groupby(["kreis", "sex", "z_age"], as_index=False)["weight"]
              .sum().rename(columns={"weight": "kreis_total"}))
    agg = agg.merge(tot, on=["kreis", "sex", "z_age"])

    agg["share"] = 0.0
    mask = agg["kreis_total"] > 0
    agg.loc[mask, "share"] = agg.loc[mask, "weight"] / agg.loc[mask, "kreis_total"]

    return agg[["kreis", "commune_id", "sex", "z_age", "share"]]


def execute(context):
    data_path = context.config("data_path")
    destatis_path = os.path.join(data_path,
                                 context.config("braunschweig.destatis_population_path"))
    urbistat_path = os.path.join(data_path,
                                 context.config("braunschweig.urbistat_gemeinden_path"))

    df_vg = context.stage("eqasim_common.data.population.raw")
    if "municipality_code" in df_vg.columns:
        df_vg = df_vg.rename(columns={"municipality_code": "ARS", "population": "EWZ"})
    if "GEN" not in df_vg.columns:
        pop_zip = os.path.join(data_path,
                               "germany/vg250-ew_12-31.utm32s.gpkg.ebenen.zip")
        inner = ("vg250-ew_12-31.utm32s.gpkg.ebenen/"
                 "vg250-ew_ebenen_1231/DE_VG250.gpkg")
        with zipfile.ZipFile(pop_zip) as z, z.open(inner) as f:
            df_vg_full = gpd.read_file(f, layer="vg250_gem")[["ARS", "GEN", "EWZ"]]
        df_vg = df_vg.merge(df_vg_full[["ARS", "GEN"]], on="ARS", how="left")

    df_vg = df_vg[df_vg["EWZ"] > 0].copy()
    scope_kreise = set(df_vg["ARS"].str[:5].unique())

    df_destatis = _load_destatis(destatis_path, scope_kreise)
    print(f"[braunschweig.population] DESTATIS: {len(df_destatis)} rows, "
          f"{df_destatis['kreis'].nunique()} Kreise, "
          f"total={df_destatis['weight'].sum():,}")

    use_zensus = context.config("braunschweig.census.use_zensus_gemeinde_shares")
    df_destatis = df_destatis.rename(columns={"age_class": "d_age"})
    if use_zensus:
        df_zensus = context.stage("braunschweig.data.census.households_size_age")
        df_shares = _load_zensus_shares(df_zensus, scope_kreise)
        df_destatis["z_age"] = df_destatis["d_age"].map(DESTATIS_TO_ZENSUS_AGE)
        merged = df_shares.merge(df_destatis, on=["kreis", "sex", "z_age"])
        print(f"[braunschweig.population] Gemeinde shares from Zensus 1000A-3082 "
              f"({df_shares['commune_id'].nunique()} communes)")
    else:
        df_shares = _load_urbistat_shares(urbistat_path, df_vg)
        df_destatis["u_age"] = df_destatis["d_age"].map(DESTATIS_TO_URBISTAT_AGE)
        merged = df_shares.merge(df_destatis, on=["kreis", "sex", "u_age"])
    merged["weight"] = merged["share"] * merged["weight"]

    df_out = merged.rename(columns={"d_age": "age_class"})[
        ["commune_id", "sex", "age_class", "weight"]
    ]
    df_out["commune_id"] = df_out["commune_id"].astype("category")
    df_out["sex"] = df_out["sex"].astype("category")
    df_out["age_class"] = df_out["age_class"].astype(int)
    df_out["weight"] = df_out["weight"].round().astype(int)

    check = df_out.copy()
    check["kreis"] = check["commune_id"].astype(str).str[:5]
    got = check.groupby("kreis")["weight"].sum()
    expected = df_destatis.groupby("kreis")["weight"].sum()
    diff = (got - expected).abs().max()
    print(f"[braunschweig.population] Gemeinde->Kreis rounding diff max: {diff}")

    print(f"[braunschweig.population] {len(df_out)} rows, "
          f"{df_out['commune_id'].nunique()} communes, "
          f"17 age classes, total={df_out['weight'].sum():,}")

    return df_out[["commune_id", "sex", "age_class", "weight"]]


def validate(context):
    data_path = context.config("data_path")
    dest = os.path.join(data_path, context.config("braunschweig.destatis_population_path"))
    required = [dest]
    # urbistat is only needed for the legacy share path; the Zensus path
    # validates its own input via the households_size_age stage.
    if not context.config("braunschweig.census.use_zensus_gemeinde_shares"):
        required.append(
            os.path.join(data_path, context.config("braunschweig.urbistat_gemeinden_path"))
        )
    for p in required:
        if not os.path.exists(p):
            raise RuntimeError(f"Missing input: {p}")
    return sum(os.path.getsize(p) for p in required)
