"""Measure the REALISED driving-licence and PT-subscription shares of a synthetic
population, per Kreis and by age x sex, against the committed MiD / SrV references
(issue #307, decision basis ADR-0079).

Why a separate measurement script and not the validation stage: the
``population_validation`` stage reports ONE universe per control (the licence control
is registered with ``age_min=14``) and no age x sex breakdown. #307 asks precisely
for the universe sensitivity -- the known trap is that the committed references live
on DIFFERENT bases (MiD P17.1 / P24.1 are 14+, the SrV licence table is 17+), so a
single realised number cannot be compared to all of them. This script therefore
reports every realised share together with the explicit universe it was computed on
and never compares across bases silently.

Universes reported (person-level, home Kreis from the VG250 spatial join):
  all      -- every synthetic person
  14plus   -- age >= 14  (MiD P17.1 / P24.1 survey base)
  17plus   -- age >= 17  (SrV car-licence table base)
  18plus   -- age >= 18  (synthesis licence floor, RT.LICENSE_MIN_AGE)

References (all committed, no invented targets):
  eqasim-data/data/braunschweig/mid/mid2023_P17_1{,_by_age,_by_sex}.csv     (14+)
  eqasim-data/data/braunschweig/mid/mid2023_P24_1{,_by_age,_by_sex}.csv     (14+)
  eqasim-data/data/braunschweig/srv/srv2023_car_license_17plus_by_kreis.csv (17+)
  eqasim-data/data/braunschweig/srv/srv2023_dticket_by_kreis.csv            (all ages)

Usage (conda env eqasim, from the repository root):
    python scripts/measure_license_pt_shares.py \
        --run-output-dir eqasim-data/output_bs_100pct_allfeat_popsim \
        --label 100pct_allfeat_popsim_2026-07-23 \
        --output-dir eqasim-data/analysis/i307_license_pt
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from braunschweig.analysis import spatial  # noqa: E402
from braunschweig.data.mid import reference_tables as RT  # noqa: E402

LOGGER = logging.getLogger("measure_license_pt_shares")

# Universe definitions: name -> inclusive minimum age (None = no age filter).
UNIVERSES: dict[str, int | None] = {
    "all": None,
    "14plus": 14,
    "17plus": 17,
    "18plus": 18,
}

# eqasim truthy tokens, mirroring
# braunschweig.analysis.population_validation.controls._is_truthy.
TRUTHY_TOKENS = {"true", "1", "yes"}

ZGB_KEY = "03ZGB"


def _truthy(series: pd.Series) -> np.ndarray:
    return series.astype(str).str.strip().str.lower().isin(TRUTHY_TOKENS).to_numpy()


def _mid_path(data_path: Path, name: str) -> Path:
    return data_path / "braunschweig" / "mid" / name


def _srv_path(data_path: Path, name: str) -> Path:
    return data_path / "braunschweig" / "srv" / name


def _read_p24_wide(path: Path) -> pd.DataFrame:
    """Read a committed P24.1 wide table and translate its raw codebook-German
    ticket-type column headers to the English PT_TICKET_CATEGORIES names.

    The committed ``mid2023_P24_1*.csv`` files keep the codebook-German raw
    headers (the single raw-CSV boundary, see
    ``braunschweig.data.mid.reference_tables.P24_RAW_COLUMN_BY_CATEGORY``); every
    downstream computation in this script (row sums, flatrate aggregation,
    per-category selection) assumes the English category names, so the
    translation must happen HERE, at the read boundary, not by indexing the raw
    frame with English keys directly (issue #329 -- this exact KeyError class was
    found twice elsewhere: scripts/extract_mid_p24_by_car_availability.py and
    tests/test_key_matching_leading_zeros.py).
    """
    df = pd.read_csv(path)
    return df.rename(columns={raw: cat for cat, raw in
                              RT.P24_RAW_COLUMN_BY_CATEGORY.items()})


def load_persons_with_kreis(run_dir: Path, prefix: str) -> pd.DataFrame:
    """Load the person table and attach the home Kreis (ars5) and commune id.

    The Kreis comes from the same VG250 spatial join the validation stage uses
    (``spatial.assign_geographies``), so the geography is identical and the
    numbers here are comparable to ``population_validation/controls_long.csv``.
    """
    persons = pd.read_csv(run_dir / f"{prefix}persons.csv", sep=";")
    homes = gpd.read_file(run_dir / f"{prefix}homes.gpkg")
    geo = spatial.assign_geographies(homes)
    geo = (geo[["household_id", "ars5", "commune_id"]]
           .drop_duplicates("household_id"))
    merged = persons.merge(geo, on="household_id", how="left")

    # Fallback transparency (CLAUDE.md): a person without a Kreis match cannot
    # enter any per-Kreis comparison, so the loss must be an explicit rate.
    n = len(merged)
    n_no_kreis = int(merged["ars5"].isna().sum())
    LOGGER.info("persons=%d; Kreis matched %d (%.3f%%), unmatched %d (%.3f%%)",
                n, n - n_no_kreis, 100.0 * (n - n_no_kreis) / max(n, 1),
                n_no_kreis, 100.0 * n_no_kreis / max(n, 1))
    if n and n_no_kreis / n > 0.02:
        LOGGER.warning("%.2f%% of persons have no Kreis match -- per-Kreis rows "
                       "are not trustworthy; check the home CRS vs VG250.",
                       100.0 * n_no_kreis / n)

    merged["age"] = pd.to_numeric(merged["age"], errors="coerce")
    if merged["age"].isna().any():
        raise ValueError("Non-numeric age values in the person table; the age "
                         "universes cannot be formed.")
    merged["license"] = _truthy(merged["has_driving_license"])
    merged["pt_flag"] = _truthy(merged["has_pt_subscription"])
    merged["pt_type"] = merged["pt_subscription_type"].astype(str)
    merged["pt_flatrate_from_type"] = merged["pt_type"].isin(RT.PT_TICKET_FLATRATE)
    return merged


def check_boolean_type_consistency(persons: pd.DataFrame) -> dict:
    """``has_pt_subscription`` must be exactly the flatrate subset of
    ``pt_subscription_type``. A mismatch would mean the boolean and the
    categorical attribute disagree, i.e. the eqasim fare model (which reads the
    boolean) and this measurement would describe different populations."""
    mismatch = int((persons["pt_flag"] != persons["pt_flatrate_from_type"]).sum())
    out = {"n_persons": int(len(persons)),
           "n_boolean_vs_type_mismatch": mismatch,
           "mismatch_pct": 100.0 * mismatch / max(len(persons), 1)}
    if mismatch:
        LOGGER.warning("has_pt_subscription disagrees with the flatrate subset of "
                       "pt_subscription_type for %d persons (%.4f%%)",
                       mismatch, out["mismatch_pct"])
    else:
        LOGGER.info("has_pt_subscription == flatrate(pt_subscription_type) for all "
                    "%d persons (consistent)", len(persons))
    return out


def _universe(persons: pd.DataFrame, age_min: int | None) -> pd.DataFrame:
    if age_min is None:
        return persons
    return persons[persons["age"] >= age_min]


def _share_rows(df: pd.DataFrame, flag_col: str, universe: str,
                geo_col: str = "ars5") -> pd.DataFrame:
    """Realised share of a boolean flag per Kreis plus a ZGB total row."""
    rows = []
    per_kreis = df.dropna(subset=[geo_col])
    for ars5, grp in per_kreis.groupby(geo_col):
        rows.append({"ars5": str(ars5), "universe": universe,
                     "n_persons": int(len(grp)),
                     "n_flag": int(grp[flag_col].sum()),
                     "share_pct": 100.0 * float(grp[flag_col].mean())})
    rows.append({"ars5": ZGB_KEY, "universe": universe,
                 "n_persons": int(len(per_kreis)),
                 "n_flag": int(per_kreis[flag_col].sum()),
                 "share_pct": 100.0 * float(per_kreis[flag_col].mean())})
    return pd.DataFrame(rows)


def realised_by_universe(persons: pd.DataFrame, flag_col: str) -> pd.DataFrame:
    return pd.concat(
        [_share_rows(_universe(persons, age_min), flag_col, name)
         for name, age_min in UNIVERSES.items()],
        ignore_index=True)


# ---------------------------------------------------------------------------
# Reference-side helpers
# ---------------------------------------------------------------------------

def mid_license_reference(data_path: Path) -> pd.DataFrame:
    """MiD P17.1 per Kreis, on its own 14+ base.

    Two reference variants are reported because the synthetic side is a BOOLEAN
    and therefore has no ``keine_angabe`` category:
      ``ja_pct``          -- the published share of the full 14+ base;
      ``ja_excl_ka_pct``  -- ja/(ja+nein), i.e. the share among persons who
                             answered. The synthesis maps ``keine_angabe`` to
                             False, so ``ja_pct`` is the like-for-like target and
                             ``ja_excl_ka_pct`` is the upper reading of the same
                             table (this is the 86.9 % figure quoted in ADR-0079).
    """
    df = pd.read_csv(_mid_path(data_path, "mid2023_P17_1.csv"))
    df["ars5"] = df["ars5"].astype(str)
    df["ja_pct"] = df["ja"] / (df["ja"] + df["nein"] + df["keine_angabe"]) * 100.0
    df["ja_excl_ka_pct"] = df["ja"] / (df["ja"] + df["nein"]) * 100.0
    return df[["ars5", "kreis", "n_unweighted", "ja", "nein", "keine_angabe",
               "ja_pct", "ja_excl_ka_pct"]]


def mid_license_18plus(data_path: Path) -> dict:
    """Re-aggregate MiD P17.1 to an 18+ base from the published age bands.

    Quantifies how much of the MiD-vs-SrV licence gap is a pure UNIVERSE effect
    rather than a measurement difference: the P17.1 total is a 14+ figure (its
    age table starts at the 14-17 band and the band weights sum to the total),
    whereas the SrV table is 17+. Dropping the 14-17 band gives the 18+ reading
    of the SAME MiD table. 17+ is not reachable from the published bands (14-17
    cannot be split), so 18+ is the closest recomputable base.
    """
    df = pd.read_csv(_mid_path(data_path, "mid2023_P17_1_by_age.csv"))
    w = df["n_weighted"]
    total = {"base": "14plus",
             "ja_pct": float((df["ja"] * w).sum() / w.sum()),
             "ja_excl_ka_pct": float((df["ja"] * w).sum()
                                     / ((df["ja"] + df["nein"]) * w).sum() * 100.0)}
    adult = df[df["age_lo"] >= 18]
    wa = adult["n_weighted"]
    over18 = {"base": "18plus",
              "ja_pct": float((adult["ja"] * wa).sum() / wa.sum()),
              "ja_excl_ka_pct": float((adult["ja"] * wa).sum()
                                      / ((adult["ja"] + adult["nein"]) * wa).sum() * 100.0)}
    return {"mid_p17_1_recomputed": [total, over18]}


def srv_license_reference(data_path: Path) -> pd.DataFrame:
    df = pd.read_csv(_srv_path(data_path, "srv2023_car_license_17plus_by_kreis.csv"),
                     comment="#")
    kreis = df[df["level"] == "kreis"].copy()
    kreis["ars5"] = kreis["code"].astype(str).str.zfill(5)
    total = df[df["level"] == "total"].copy()
    total["ars5"] = ZGB_KEY
    out = pd.concat([kreis, total], ignore_index=True)
    out["srv_license_17plus_pct"] = out["share_with_license"] * 100.0
    return out[["ars5", "name", "n_unweighted", "srv_license_17plus_pct"]]


def srv_dticket_reference(data_path: Path) -> pd.DataFrame:
    df = pd.read_csv(_srv_path(data_path, "srv2023_dticket_by_kreis.csv"), comment="#")
    kreis = df[df["level"] == "kreis"].copy()
    kreis["ars5"] = kreis["code"].astype(str).str.zfill(5)
    total = df[df["level"] == "total"].copy()
    total["ars5"] = ZGB_KEY
    out = pd.concat([kreis, total], ignore_index=True)
    out["srv_dticket_pct"] = out["share_deutschlandticket"] * 100.0
    return out[["ars5", "name", "n_unweighted", "srv_dticket_pct"]]


def mid_pt_reference(data_path: Path) -> pd.DataFrame:
    """MiD P24.1 per Kreis: the 9 ticket categories plus the flatrate aggregate."""
    df = _read_p24_wide(_mid_path(data_path, "mid2023_P24_1.csv"))
    df["ars5"] = df["ars5"].astype(str)
    cats = list(RT.PT_TICKET_CATEGORIES)
    row_sum = df[cats].sum(axis=1)
    flat = [c for c in cats if c in RT.PT_TICKET_FLATRATE]
    df["mid_flatrate_pct"] = df[flat].sum(axis=1) / row_sum * 100.0
    df["published_row_sum_pct"] = row_sum
    return df[["ars5", "kreis", "n_unweighted", "mid_flatrate_pct",
               "published_row_sum_pct"] + cats]


# ---------------------------------------------------------------------------
# Structure (age x sex)
# ---------------------------------------------------------------------------

def by_age_band(persons: pd.DataFrame, flag_col: str, bands: pd.DataFrame,
                ref_cols: list[str]) -> pd.DataFrame:
    """Realised flag share inside the published MiD age bands (14+ base)."""
    rows = []
    for _, band in bands.iterrows():
        lo, hi = int(band["age_lo"]), int(band["age_hi"])
        grp = persons[(persons["age"] >= lo) & (persons["age"] <= hi)]
        row = {"age_lo": lo, "age_hi": hi, "label": band["label"],
               "n_persons_synth": int(len(grp)),
               "realised_pct": 100.0 * float(grp[flag_col].mean()) if len(grp) else np.nan,
               "mid_n_unweighted": float(band["n_unweighted"])}
        for c in ref_cols:
            row[f"mid_{c}"] = float(band[c])
        rows.append(row)
    return pd.DataFrame(rows)


def by_sex(persons: pd.DataFrame, flag_col: str, ref: pd.DataFrame,
           ref_cols: list[str], age_min: int = 14) -> pd.DataFrame:
    """Realised flag share by sex on the MiD 14+ base."""
    base = _universe(persons, age_min)
    rows = []
    for sex_value, grp in base.groupby(base["sex"].astype(str)):
        row = {"sex": sex_value, "n_persons_synth": int(len(grp)),
               "realised_pct": 100.0 * float(grp[flag_col].mean())}
        match = ref[ref["sex"].astype(str) == sex_value]
        for c in ref_cols:
            row[f"mid_{c}"] = float(match[c].iloc[0]) if len(match) else np.nan
        row["mid_n_unweighted"] = (float(match["n_unweighted"].iloc[0])
                                   if len(match) else np.nan)
        rows.append(row)
    return pd.DataFrame(rows)


def pt_type_distribution(persons: pd.DataFrame, mid_ref: pd.DataFrame,
                         age_min: int = 14) -> pd.DataFrame:
    """Realised 9-category ticket distribution per Kreis vs the MiD P24.1 margins.

    Published MiD rows are integer percentages that sum to 99-101; they are
    renormalised per Kreis so that both sides sum to 100 (same convention as
    ``population_validation.controls._renormalized_by_kreis``).
    """
    base = _universe(persons, age_min).dropna(subset=["ars5"])
    cats = list(RT.PT_TICKET_CATEGORIES)
    rows = []
    for ars5, grp in list(base.groupby("ars5")) + [(ZGB_KEY, base)]:
        ref = mid_ref[mid_ref["ars5"] == str(ars5)]
        ref_sum = float(ref[cats].sum(axis=1).iloc[0]) if len(ref) else np.nan
        counts = grp["pt_type"].value_counts()
        for cat in cats:
            realised = 100.0 * float(counts.get(cat, 0)) / max(len(grp), 1)
            target = (float(ref[cat].iloc[0]) / ref_sum * 100.0
                      if len(ref) and ref_sum else np.nan)
            rows.append({"ars5": str(ars5), "category": cat,
                         "n_persons": int(len(grp)),
                         "realised_pct": realised,
                         "mid_target_pct": target,
                         "delta_pp": realised - target})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-output-dir", required=True,
                        help="Directory holding <prefix>persons.csv and <prefix>homes.gpkg.")
    parser.add_argument("--prefix", default=None,
                        help="File prefix; auto-detected from *_persons.csv when omitted.")
    parser.add_argument("--label", required=True,
                        help="Run label recorded in the report (e.g. the run id).")
    parser.add_argument("--output-dir", required=True,
                        help="Directory for the CSV/JSON outputs.")
    parser.add_argument("--data-path", default=str(REPO_ROOT / "eqasim-data" / "data"),
                        help="Committed reference-data root (default: eqasim-data/data).")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")

    run_dir = Path(args.run_output_dir)
    data_path = Path(args.data_path)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    prefix = args.prefix
    if prefix is None:
        candidates = sorted(run_dir.glob("*_persons.csv"))
        if len(candidates) != 1:
            raise SystemExit(f"Expected exactly one *_persons.csv in {run_dir}, "
                             f"found {len(candidates)}; pass --prefix.")
        prefix = candidates[0].name[: -len("persons.csv")]
    LOGGER.info("Run %s: prefix %r in %s", args.label, prefix, run_dir)

    persons = load_persons_with_kreis(run_dir, prefix)
    consistency = check_boolean_type_consistency(persons)

    # --- Driving licence ---------------------------------------------------
    lic = realised_by_universe(persons, "license")
    lic.to_csv(out / "license_realised_by_universe.csv", index=False)

    mid_lic = mid_license_reference(data_path)
    srv_lic = srv_license_reference(data_path)
    wide = (lic.pivot(index="ars5", columns="universe", values="share_pct")
            .add_prefix("realised_").reset_index())
    cmp_lic = (wide
               .merge(mid_lic[["ars5", "kreis", "ja_pct", "ja_excl_ka_pct"]],
                      on="ars5", how="left")
               .merge(srv_lic[["ars5", "srv_license_17plus_pct"]],
                      on="ars5", how="left"))
    cmp_lic["delta_14plus_vs_mid_ja_pp"] = cmp_lic["realised_14plus"] - cmp_lic["ja_pct"]
    cmp_lic["delta_14plus_vs_mid_ja_excl_ka_pp"] = (cmp_lic["realised_14plus"]
                                                    - cmp_lic["ja_excl_ka_pct"])
    cmp_lic["delta_17plus_vs_srv_pp"] = (cmp_lic["realised_17plus"]
                                         - cmp_lic["srv_license_17plus_pct"])
    cmp_lic.to_csv(out / "license_reference_comparison.csv", index=False)

    bands = pd.read_csv(_mid_path(data_path, "mid2023_P17_1_by_age.csv"))
    lic_age = by_age_band(persons, "license", bands, ["ja", "nein", "keine_angabe"])
    lic_age["delta_pp"] = lic_age["realised_pct"] - lic_age["mid_ja"]
    lic_age.to_csv(out / "license_by_age_band.csv", index=False)

    sex_ref = pd.read_csv(_mid_path(data_path, "mid2023_P17_1_by_sex.csv"))
    lic_sex = by_sex(persons, "license", sex_ref, ["ja", "nein", "keine_angabe"])
    lic_sex["delta_pp"] = lic_sex["realised_pct"] - lic_sex["mid_ja"]
    lic_sex.to_csv(out / "license_by_sex.csv", index=False)

    # --- PT subscription ---------------------------------------------------
    pt = realised_by_universe(persons, "pt_flag")
    pt.to_csv(out / "pt_flatrate_realised_by_universe.csv", index=False)

    mid_pt = mid_pt_reference(data_path)
    pt_wide = (pt.pivot(index="ars5", columns="universe", values="share_pct")
               .add_prefix("realised_").reset_index())
    cmp_pt = pt_wide.merge(mid_pt[["ars5", "kreis", "mid_flatrate_pct"]],
                           on="ars5", how="left")
    cmp_pt["delta_14plus_vs_mid_pp"] = (cmp_pt["realised_14plus"]
                                        - cmp_pt["mid_flatrate_pct"])
    cmp_pt.to_csv(out / "pt_flatrate_reference_comparison.csv", index=False)

    pt_types = pt_type_distribution(persons, mid_pt)
    pt_types.to_csv(out / "pt_type_by_kreis.csv", index=False)

    pt_bands = _read_p24_wide(_mid_path(data_path, "mid2023_P24_1_by_age.csv"))
    flat_cols = [c for c in RT.PT_TICKET_CATEGORIES if c in RT.PT_TICKET_FLATRATE]
    pt_bands["flatrate"] = pt_bands[flat_cols].sum(axis=1)
    pt_bands["row_sum"] = pt_bands[list(RT.PT_TICKET_CATEGORIES)].sum(axis=1)
    pt_bands["flatrate_pct"] = pt_bands["flatrate"] / pt_bands["row_sum"] * 100.0
    pt_age = by_age_band(persons, "pt_flag", pt_bands, ["flatrate_pct"])
    pt_age["delta_pp"] = pt_age["realised_pct"] - pt_age["mid_flatrate_pct"]
    pt_age.to_csv(out / "pt_flatrate_by_age_band.csv", index=False)

    pt_sex_ref = _read_p24_wide(_mid_path(data_path, "mid2023_P24_1_by_sex.csv"))
    pt_sex_ref["flatrate"] = pt_sex_ref[flat_cols].sum(axis=1)
    pt_sex_ref["row_sum"] = pt_sex_ref[list(RT.PT_TICKET_CATEGORIES)].sum(axis=1)
    pt_sex_ref["flatrate_pct"] = pt_sex_ref["flatrate"] / pt_sex_ref["row_sum"] * 100.0
    pt_sex = by_sex(persons, "pt_flag", pt_sex_ref, ["flatrate_pct"])
    pt_sex["delta_pp"] = pt_sex["realised_pct"] - pt_sex["mid_flatrate_pct"]
    pt_sex.to_csv(out / "pt_flatrate_by_sex.csv", index=False)

    # Deutschlandticket alone: the ONLY PT reference from a second, independent
    # survey (SrV). Its universe is all persons with a valid weight, so the
    # like-for-like realised figure is the all-persons one; the 14+ figure is
    # reported next to it to bound the universe effect.
    srv_dt = srv_dticket_reference(data_path)
    dticket_rows = []
    for name, age_min in (("all", None), ("14plus", 14)):
        base = _universe(persons, age_min).dropna(subset=["ars5"])
        base = base.assign(dticket=base["pt_type"].eq("deutschlandticket"))
        dticket_rows.append(_share_rows(base, "dticket", name))
    dticket = pd.concat(dticket_rows, ignore_index=True)
    dticket = (dticket.pivot(index="ars5", columns="universe", values="share_pct")
               .add_prefix("realised_dticket_").reset_index()
               .merge(srv_dt[["ars5", "srv_dticket_pct"]], on="ars5", how="left")
               .merge(mid_pt[["ars5", "deutschlandticket"]], on="ars5", how="left")
               .rename(columns={"deutschlandticket": "mid_dticket_14plus_pct"}))
    dticket["delta_all_vs_srv_pp"] = (dticket["realised_dticket_all"]
                                      - dticket["srv_dticket_pct"])
    dticket["delta_14plus_vs_mid_pp"] = (dticket["realised_dticket_14plus"]
                                         - dticket["mid_dticket_14plus_pct"])
    dticket.to_csv(out / "pt_dticket_two_survey_comparison.csv", index=False)

    report = {
        "label": args.label,
        "run_output_dir": str(run_dir),
        "prefix": prefix,
        "data_path": str(data_path),
        "universes": {k: ("all ages" if v is None else f"age >= {v}")
                      for k, v in UNIVERSES.items()},
        "pt_flatrate_categories": sorted(RT.PT_TICKET_FLATRATE),
        "boolean_vs_type_consistency": consistency,
        "kreis_join": {
            "n_persons": int(len(persons)),
            "n_without_kreis": int(persons["ars5"].isna().sum()),
        },
        "license_zgb_pct": {
            u: float(lic[(lic["ars5"] == ZGB_KEY) & (lic["universe"] == u)]
                     ["share_pct"].iloc[0]) for u in UNIVERSES},
        "pt_flatrate_zgb_pct": {
            u: float(pt[(pt["ars5"] == ZGB_KEY) & (pt["universe"] == u)]
                     ["share_pct"].iloc[0]) for u in UNIVERSES},
        "references": {
            "mid_p17_1_zgb_ja_pct": float(
                mid_lic[mid_lic["ars5"] == ZGB_KEY]["ja_pct"].iloc[0]),
            "mid_p17_1_zgb_ja_excl_ka_pct": float(
                mid_lic[mid_lic["ars5"] == ZGB_KEY]["ja_excl_ka_pct"].iloc[0]),
            "srv_license_17plus_total_pct": float(
                srv_lic[srv_lic["ars5"] == ZGB_KEY]["srv_license_17plus_pct"].iloc[0]),
            "mid_p24_1_zgb_flatrate_pct": float(
                mid_pt[mid_pt["ars5"] == ZGB_KEY]["mid_flatrate_pct"].iloc[0]),
            "srv_dticket_total_pct": float(
                srv_dt[srv_dt["ars5"] == ZGB_KEY]["srv_dticket_pct"].iloc[0]),
        },
    }
    report.update(mid_license_18plus(data_path))
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    LOGGER.info("Wrote %s", out / "report.json")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
