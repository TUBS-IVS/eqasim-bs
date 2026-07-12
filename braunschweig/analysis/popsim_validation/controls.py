"""Declarative control registry for popsim_mid control-fit validation.

Each Control couples a realized-share extractor (synthetic distribution from the
expanded popsim_mid households/persons frame) with a target loader (the census
marginal that PopulationSim actually fitted to).

Reuses the population_validation extractor builders and evaluate/assess machinery
unchanged; only the target loaders differ -- they read the prepared 100 m cell
parquet aggregated to the geography level, not the DESTATIS / MiD reference CSVs
used by the baseline population_validation.

Control families
----------------
popsim_backbone   -- age x sex (Tier-0), the structural backbone controls
popsim_hh         -- household_size, household_type, tenure, building_type (Tier-1/2)
popsim_reference  -- seniorenstatus (census-only reference, NOT a popsim control)

Geography
---------
All popsim controls are fitted at the 100 m / 1 km cell level; the validation
aggregates to Kreis (5-digit ARS) by summing realized counts and target counts
across all cells within the Kreis, so the unit is consistent with the backbone
population_validation controls (same geography="kreis" column).

Target columns in the prepared parquet
---------------------------------------
Column names use the cleaned notation (``-`` → ``_``; umlauts transliterated by
``braunschweig.popsim.prepared_cells.clean_col_name``), e.g.:
  Insgesamt_Haushalte_Groesse_des_privaten_Haushalts_100m_Gitter_adj  (HH total)
  1_Person_Groesse_des_privaten_Haushalts_100m_Gitter                 (size 1)
  EigentuemerHH_Tenure_100m_Gitter                                   (owner)
  building_type_ein_zweifamilienhaus                                  (derived)
  Insgesamt_Haushalte_Seniorenstatus_eines_privaten_Haushalts_100m_Gitter_adj  (senior total)
  HH_mitSenioren_Seniorenstatus_eines_privaten_Haushalts_100m_Gitter           (with seniors)
  HH_nurSenioren_Seniorenstatus_eines_privaten_Haushalts_100m_Gitter           (only seniors)
  HH_ohneSenioren_Seniorenstatus_eines_privaten_Haushalts_100m_Gitter          (without seniors)
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from braunschweig.analysis.population_validation.controls import (
    Control,
    banded_person_control,
    bucket_household_control,
    categorical_household_control,
    categorical_person_control,
    _geo_col,
)
from braunschweig.popsim.zensus_employment_age import AGE_GROUPS as _AGE_GROUPS3

if TYPE_CHECKING:
    from braunschweig.analysis.population_validation.population_source import PopulationFrames

LOGGER = logging.getLogger("braunschweig.analysis.popsim_validation.controls")

# Age-band bounds matching the Tier-0 backbone controls (ten-year bands).
BACKBONE_AGE_BOUNDS: tuple[int, ...] = (10, 20, 30, 40, 50, 60, 70, 80)

# Prepared parquet path relative to data root.
_PARQUET_SUBPATH = "braunschweig/popsim/cells/zensus2022_grid_100m_de_prepared.parquet"

# ARS column name in the prepared parquet (after clean_col_name transliteration of ü→u).
_ARS_COLUMN_CLEAN = "RegionalSchlussel_ARS"

# --- Tier-3 (employment + education) constants ---------------------------------
# Kreis controls dir (GENESIS per-Kreis marginals) relative to the data root.
_KREIS_CONTROLS_SUBPATH = "braunschweig/popsim/kreis_controls"

# Employment seed predicate: MiD official `erwerb` definition (P_TAET in {1,2,3,4,6,8}).
# Includes Auszubildende (8); excludes 5 (Elternzeit) and 7 (FSJ/Wehrdienst).
# Consistent with attributes.EMPLOYED_TAET and the Tier-3 control expressions.
_EMPLOYED_PTAET = frozenset({1, 2, 3, 4, 6, 8})

# Census-source column groups in the merged kreis control table (raw GENESIS
# columns). Mirror control_spec._TIER3_ENTRIES + the ERWERBSTAT universe (children
# are counted as Nichterwerbspersonen __2, so the universe ~= total population).
_EMP_EMPLOYED_COL = "ERWERBSTAT_KURZ_STP__11"   # Erwerbstaetige (employed)
_EMP_TOTAL_COL = "ERWERBSTAT_KURZ_STP"          # Insgesamt (= __11 + __12 + __2)
_SCHULABS_SOURCE = {
    "low": ("SCHULABS_STP__21", "SCHULABS_STP__22"),
    "mid": ("SCHULABS_STP__23",),
    "high": ("SCHULABS_STP__24",),
}
_BERUFABS_SOURCE = {
    "none": ("BERUFABS_AUSF_STP__2",),
    "vocational": ("BERUFABS_AUSF_STP__11", "BERUFABS_AUSF_STP__12", "BERUFABS_AUSF_STP__13"),
    "tertiary": ("BERUFABS_AUSF_STP__14", "BERUFABS_AUSF_STP__15",
                 "BERUFABS_AUSF_STP__16", "BERUFABS_AUSF_STP__17"),
}


def _load_kreis_control_table(data_path: str) -> pd.DataFrame:
    """Load the merged per-Kreis control table (erwerbsstatus + schulabschluss +
    berufl_abschluss) -- the SAME GENESIS marginals the Tier-3 KREIS controls were
    fitted to. ARS_kreis is a 5-digit zero-padded string (400 Kreise)."""
    from braunschweig.popsim.mid import load_kreis_control_table
    kreis_dir = Path(data_path) / _KREIS_CONTROLS_SUBPATH
    return load_kreis_control_table(str(kreis_dir))


def _load_cells(data_path: str) -> pd.DataFrame:
    """Load and clean the prepared 100 m cell parquet (cached per data_path call).

    Returns the cells frame with cleaned column names and ``ars5`` (5-digit
    Kreis prefix) derived from the ``RegionalSchlussel_ARS`` column.
    """
    from braunschweig.popsim.prepared_cells import clean_col_name, add_aggregated_controls
    from braunschweig.popsim.control_spec import build_aggregation_map, full_catalog, controls_for_seed

    parquet = Path(data_path) / _PARQUET_SUBPATH
    df = pd.read_parquet(parquet)
    df.columns = [clean_col_name(c) for c in df.columns]

    # Derive ars5 (5-digit Kreis) from the 12-digit ARS column.
    ars_col = clean_col_name("RegionalSchlussel_ARS")
    if ars_col in df.columns:
        df["ars5"] = df[ars_col].astype(str).str.zfill(12).str[:5]
    else:
        LOGGER.warning(
            "Cells parquet missing ARS column %r; ars5 cannot be derived. "
            "Target loaders will return empty frames.", ars_col,
        )
        df["ars5"] = pd.NA

    # Add derived building_type_3class columns (sum of Gebaeudetyp sub-columns).
    catalog = full_catalog(include_tiers=("tier2",))
    agg_map = build_aggregation_map(controls_for_seed(catalog, "mid"))
    df = add_aggregated_controls(df, agg_map)

    return df


def _multi_col_kreis_target(
    cells: pd.DataFrame,
    col_category_map: dict[str, str],
    total_col: str | None = None,
) -> pd.DataFrame:
    """Build a [geo_id, category, target_share] frame from multiple cell columns.

    Each key in ``col_category_map`` is a column name in ``cells``; its value is
    the category label for that column. Shares are per-Kreis (ars5), computed as
    count / total where total is the sum of all category counts per Kreis (or
    ``total_col`` if given). Kreise with zero total are dropped.

    Parameters
    ----------
    cells:
        Prepared cells frame with ``ars5`` and the count columns.
    col_category_map:
        ``{cell_column: category_label}`` for each category.
    total_col:
        Optional explicit total column (e.g. ``_adj`` rescaled total). When
        ``None`` the total is the row-wise sum of the category columns.

    Returns
    -------
    pandas.DataFrame
        Columns ``[geo_id, category, target_share]``.
    """
    present = {col: cat for col, cat in col_category_map.items() if col in cells.columns}
    missing_cols = [col for col in col_category_map if col not in cells.columns]
    for col in missing_cols:
        LOGGER.warning(
            "[popsim_validation] target column %r absent from prepared cells; "
            "category %r will have zero target share.", col, col_category_map[col],
        )

    # Aggregate each category column to Kreis level.
    kreis_counts: dict[str, pd.Series] = {}
    for col, cat in present.items():
        kreis_counts[cat] = cells.groupby("ars5")[col].apply(lambda s: pd.to_numeric(s, errors="coerce").fillna(0.0).sum())

    # Compute totals.
    if total_col is not None and total_col in cells.columns:
        kreis_totals = cells.groupby("ars5")[total_col].apply(
            lambda s: pd.to_numeric(s, errors="coerce").fillna(0.0).sum()
        )
        # The category counts and the dedicated total column come from
        # DIFFERENT census columns (raw vs '_adj' / M_TOTAL); when they
        # disagree, the target shares do not sum to 1 while realized shares
        # always do -- surface the ratio instead of letting the mismatch
        # masquerade as model error (no-silent-fallback rule).
        cat_sum = None
        for s in kreis_counts.values():
            cat_sum = s.copy() if cat_sum is None else cat_sum.add(s, fill_value=0.0)
        if cat_sum is not None and len(kreis_totals) > 0:
            ratio = (cat_sum.reindex(kreis_totals.index).fillna(0.0)
                     / kreis_totals.replace(0.0, np.nan))
            off = ratio[(ratio - 1.0).abs() > 0.01].dropna()
            if len(off) > 0:
                LOGGER.warning(
                    "[popsim_validation] %d Kreis(e): category sum deviates >1%% "
                    "from total column %r (ratio min %.3f, max %.3f) -- target "
                    "shares will not sum to 1 there",
                    len(off), total_col, float(off.min()), float(off.max()))
    else:
        if total_col is not None:
            # No-silent-fallback rule: a requested total column that is ABSENT
            # must not silently degrade to the category-sum denominator.
            LOGGER.warning(
                "[popsim_validation] requested total column %r is absent from "
                "the cells frame; falling back to the SUM OF CATEGORY COUNTS "
                "as the denominator", total_col)
        # Sum the category counts as the total.
        all_series = list(kreis_counts.values())
        kreis_totals = all_series[0].copy().rename("total")
        for s in all_series[1:]:
            kreis_totals = kreis_totals.add(s, fill_value=0.0)

    rows = []
    for cat, kreis_count in kreis_counts.items():
        aligned_total = kreis_totals.reindex(kreis_count.index).fillna(0.0)
        valid = aligned_total > 0
        shares = kreis_count[valid] / aligned_total[valid]
        for geo_id, share in shares.items():
            rows.append({"geo_id": str(geo_id), "category": cat, "target_share": float(share)})

    # Fill in missing categories with 0 target share (for Kreise that have a total
    # but no count in that category).
    if rows:
        df = pd.DataFrame(rows)
        all_geos = df["geo_id"].unique()
        all_cats = list(col_category_map.values())
        full = pd.MultiIndex.from_product([all_geos, all_cats], names=["geo_id", "category"])
        df = df.set_index(["geo_id", "category"]).reindex(full, fill_value=0.0).reset_index()
        return df[["geo_id", "category", "target_share"]]
    return pd.DataFrame(columns=["geo_id", "category", "target_share"])


# ---------------------------------------------------------------------------
# Target loaders (read from prepared parquet, aggregate to Kreis level)
# ---------------------------------------------------------------------------

def household_size_target(data_path: str) -> pd.DataFrame:
    """Per-Kreis household-size target from the prepared 100 m cells (Tier-1).

    Aggregates the 6 size-class columns to Kreis level and computes shares.
    Total denominator: ``Insgesamt_Haushalte_Groesse_des_privaten_Haushalts_100m_Gitter_adj``
    (the ``_adj`` rescaled total — same as what PopulationSim fitted to).
    """
    cells = _load_cells(data_path)
    col_cat = {
        "1_Person_Groesse_des_privaten_Haushalts_100m_Gitter":     "1",
        "2_Personen_Groesse_des_privaten_Haushalts_100m_Gitter":   "2",
        "3_Personen_Groesse_des_privaten_Haushalts_100m_Gitter":   "3",
        "4_Personen_Groesse_des_privaten_Haushalts_100m_Gitter":   "4",
        "5_Personen_Groesse_des_privaten_Haushalts_100m_Gitter":   "5",
        "6_Personen_und_mehr_Groesse_des_privaten_Haushalts_100m_Gitter": "6+",
    }
    total_col = "Insgesamt_Haushalte_Groesse_des_privaten_Haushalts_100m_Gitter_adj"
    return _multi_col_kreis_target(cells, col_cat, total_col=total_col)


def household_type_target(data_path: str) -> pd.DataFrame:
    """Per-Kreis household-type (5-class Familientyp) target from prepared cells (Tier-1)."""
    cells = _load_cells(data_path)
    col_cat = {
        "EinpersHH_SingleHH_Typ_priv_HH_Familie_100m_Gitter":    "einpersonen",
        "Paare_ohneKind_Typ_priv_HH_Familie_100m_Gitter":         "paar_ohne_kind",
        "Paare_mitKind_Typ_priv_HH_Familie_100m_Gitter":          "paar_mit_kind",
        "Alleinerziehende_Typ_priv_HH_Familie_100m_Gitter":       "alleinerziehend",
        "MehrpersHHohneKernfam_Typ_priv_HH_Familie_100m_Gitter":  "mehrpers_ohne_kernfamilie",
    }
    total_col = "Insgesamt_Haushalte_Typ_priv_HH_Familie_100m_Gitter"
    return _multi_col_kreis_target(cells, col_cat, total_col=total_col)


def tenure_target(data_path: str) -> pd.DataFrame:
    """Per-Kreis tenure (owner/renter) target from prepared cells (Tier-2)."""
    cells = _load_cells(data_path)
    col_cat = {
        "EigentuemerHH_Tenure_100m_Gitter": "owner",
        "MieterHH_Tenure_100m_Gitter":      "renter",
    }
    return _multi_col_kreis_target(cells, col_cat)


def building_type_target(data_path: str) -> pd.DataFrame:
    """Per-Kreis building-type (3-class) target from prepared cells (Tier-2).

    The derived columns ``building_type_ein_zweifamilienhaus`` etc. are produced
    by ``_load_cells`` via ``add_aggregated_controls``.
    """
    cells = _load_cells(data_path)
    col_cat = {
        "building_type_ein_zweifamilienhaus": "ein_zweifamilienhaus",
        "building_type_mehrfamilienhaus":     "mehrfamilienhaus",
        "building_type_sonstiges":            "sonstiges",
    }
    return _multi_col_kreis_target(cells, col_cat)


def age_sex_target(data_path: str, sex: str) -> pd.DataFrame:
    """Per-Kreis age x sex target from prepared cells (Tier-0 backbone).

    Parameters
    ----------
    sex:
        ``"male"`` or ``"female"``.
    """
    cells = _load_cells(data_path)
    from braunschweig.analysis.population_validation.controls import (
        AGE_GROUP_BOUNDS, _band_labels,
    )
    prefix = "M" if sex == "male" else "F"
    # The backbone uses 10-year bands: M_AGE_0_9_agg, M_AGE_10_19_agg, ...
    band_cols = {
        f"{prefix}_AGE_0_9_agg":    "0-9",
        f"{prefix}_AGE_10_19_agg":  "10-19",
        f"{prefix}_AGE_20_29_agg":  "20-29",
        f"{prefix}_AGE_30_39_agg":  "30-39",
        f"{prefix}_AGE_40_49_agg":  "40-49",
        f"{prefix}_AGE_50_59_agg":  "50-59",
        f"{prefix}_AGE_60_69_agg":  "60-69",
        f"{prefix}_AGE_70_79_agg":  "70-79",
        f"{prefix}_AGE_80_plus_agg": "80+",
    }
    total_col = f"{prefix}_TOTAL"
    return _multi_col_kreis_target(cells, band_cols, total_col=total_col)


def seniorenstatus_target(data_path: str) -> pd.DataFrame:
    """Per-Kreis Seniorenstatus target (REFERENCE only, not a popsim control).

    The census provides three Seniorenstatus categories:
      - HH_ohneSenioren  (no member aged >= 65)
      - HH_mitSenioren   (some members aged >= 65, not all)
      - HH_nurSenioren   (all members aged >= 65)

    The realized extractor (_realized_seniorenstatus) classifies households into
    two classes based on whether any person is aged >= 65:
      - "ohne_senioren"  (no senior present)
      - "mit_senioren"   (at least one senior present)

    To match the two realized categories, nurSenioren and mitSenioren are merged
    into "mit_senioren" here, so realized and target category labels align.

    Column names after clean_col_name (``-`` → ``_``):
      HH_ohneSenioren_Seniorenstatus_eines_privaten_Haushalts_100m_Gitter
      HH_mitSenioren_Seniorenstatus_eines_privaten_Haushalts_100m_Gitter
      HH_nurSenioren_Seniorenstatus_eines_privaten_Haushalts_100m_Gitter
      Insgesamt_Haushalte_Seniorenstatus_eines_privaten_Haushalts_100m_Gitter_adj
    """
    cells = _load_cells(data_path)

    _COL_OHNE = "HH_ohneSenioren_Seniorenstatus_eines_privaten_Haushalts_100m_Gitter"
    _COL_MIT = "HH_mitSenioren_Seniorenstatus_eines_privaten_Haushalts_100m_Gitter"
    _COL_NUR = "HH_nurSenioren_Seniorenstatus_eines_privaten_Haushalts_100m_Gitter"
    _COL_TOTAL = "Insgesamt_Haushalte_Seniorenstatus_eines_privaten_Haushalts_100m_Gitter_adj"

    # Merge nurSenioren + mitSenioren into a single "mit_senioren" column so that
    # category labels match the realized extractor (2-class: mit / ohne).
    for col in (_COL_MIT, _COL_NUR):
        if col not in cells.columns:
            LOGGER.warning(
                "[popsim_validation] seniorenstatus target column %r absent from prepared "
                "cells; mit_senioren share will be underestimated.", col,
            )
    # Build merged column (sum of mit + nur, coercing missing to 0).
    mit_series = pd.to_numeric(cells.get(_COL_MIT, 0), errors="coerce").fillna(0.0)
    nur_series = pd.to_numeric(cells.get(_COL_NUR, 0), errors="coerce").fillna(0.0)
    cells = cells.copy()
    cells["_mit_senioren_merged"] = mit_series + nur_series

    col_cat = {
        "_mit_senioren_merged":  "mit_senioren",
        _COL_OHNE:               "ohne_senioren",
    }
    return _multi_col_kreis_target(cells, col_cat, total_col=_COL_TOTAL)


# ---------------------------------------------------------------------------
# Tier-3 target loaders (merged kreis control table -> per-Kreis shares)
# ---------------------------------------------------------------------------

def _kreis_share_target(kt: pd.DataFrame, source: dict, total_col: str | None = None) -> pd.DataFrame:
    """[geo_id, category, target_share] from per-Kreis census-source column groups.

    share = sum(source cols) / denom; denom = ``total_col`` if given, else the
    row-wise sum of all category counts (so shares sum to 1 per Kreis). geo_id is
    the 5-digit ARS_kreis. Suppressed (NaN) census cells are summed as 0 -- the
    same convention as ``folders.build_kreis_control_totals``.
    """
    ars = kt["ARS_kreis"].astype(str).str.zfill(5).to_numpy()
    counts: dict[str, np.ndarray] = {}
    for cat, cols in source.items():
        s = None
        for c in cols:
            col = pd.to_numeric(kt[c], errors="coerce").fillna(0.0)
            s = col if s is None else s + col
        counts[cat] = s.to_numpy()
    if total_col is not None:
        denom = pd.to_numeric(kt[total_col], errors="coerce").fillna(0.0).to_numpy()
    else:
        denom = np.sum(list(counts.values()), axis=0)
    rows = []
    with np.errstate(divide="ignore", invalid="ignore"):
        for cat in source:
            share = np.where(denom > 0, counts[cat] / denom, 0.0)
            for g, sh in zip(ars, share):
                rows.append({"geo_id": str(g), "category": cat, "target_share": float(sh)})
    return pd.DataFrame(rows, columns=["geo_id", "category", "target_share"])


def employed_target(data_path: str) -> pd.DataFrame:
    """Per-Kreis employment target (Tier-3), 2-class {employed, not_employed} over
    the census Erwerbsstatus universe (~= total population). employed share =
    ERWERBSTAT_KURZ_STP__11 / ERWERBSTAT_KURZ_STP; not_employed = 1 - that."""
    kt = _load_kreis_control_table(data_path)
    ars = kt["ARS_kreis"].astype(str).str.zfill(5).to_numpy()
    emp = pd.to_numeric(kt[_EMP_EMPLOYED_COL], errors="coerce").fillna(0.0).to_numpy()
    total = pd.to_numeric(kt[_EMP_TOTAL_COL], errors="coerce").fillna(0.0).to_numpy()
    rows = []
    with np.errstate(divide="ignore", invalid="ignore"):
        emp_share = np.where(total > 0, emp / total, 0.0)
    for g, es in zip(ars, emp_share):
        rows.append({"geo_id": str(g), "category": "employed", "target_share": float(es)})
        rows.append({"geo_id": str(g), "category": "not_employed", "target_share": float(1.0 - es)})
    return pd.DataFrame(rows, columns=["geo_id", "category", "target_share"])


def schulabschluss_target(data_path: str) -> pd.DataFrame:
    """Per-Kreis Schulabschluss target (Tier-3, 3-class low/mid/high). Shares over
    the 'mit Schulabschluss' universe (low+mid+high == SCHULABS_STP__2)."""
    return _kreis_share_target(_load_kreis_control_table(data_path), _SCHULABS_SOURCE)


def beruflabschluss_target(data_path: str) -> pd.DataFrame:
    """Per-Kreis Berufsabschluss target (Tier-3, 3-class none/vocational/tertiary).
    Shares over the full BERUFABS_AUSF_STP universe (none+vocational+tertiary)."""
    return _kreis_share_target(_load_kreis_control_table(data_path), _BERUFABS_SOURCE)


# ---------------------------------------------------------------------------
# Realized extractors for popsim-specific attributes
# ---------------------------------------------------------------------------

def _realized_hh_type5(frames: "PopulationFrames", geo: pd.DataFrame) -> pd.DataFrame:
    """Realized household-type (hh_type5) extractor for popsim synthetic population.

    Reads ``hh_type5`` from ``frames.persons`` (set by assembly.map_mid_person_attributes).
    One value per synthetic household (all persons in a household share the same
    hh_type5); uses one row per household (the first person's value) and joins
    geo via household_id.
    """
    persons = frames.persons
    if "hh_type5" not in persons.columns:
        LOGGER.warning(
            "hh_type5 absent from persons; household_type control skipped. "
            "Ensure assembly.map_mid_person_attributes was called (popsim_mid path)."
        )
        return pd.DataFrame(columns=["geo_id", "category", "synthetic_count"])

    # One row per household (first person per household carries the hh_type5).
    hh = persons.drop_duplicates("household_id")[["household_id", "hh_type5"]].copy()
    hh = hh.merge(geo[["household_id", "ars5"]], on="household_id", how="left")
    hh = hh.dropna(subset=["ars5"]).copy()
    # Drop households where hh_type5 is NaN (not_classifiable).
    hh = hh.dropna(subset=["hh_type5"]).copy()
    hh["category"] = hh["hh_type5"].astype(str)
    out = (hh.groupby(["ars5", "category"]).size()
              .rename("synthetic_count").reset_index())
    return out.rename(columns={"ars5": "geo_id"})


def _realized_housing_tenure(frames: "PopulationFrames", geo: pd.DataFrame) -> pd.DataFrame:
    """Realized tenure (owner/renter) extractor from synthetic persons.

    Reads ``housing_tenure`` from ``frames.persons``. Only "owner" and "renter"
    categories are counted; "unknown" (H_MIETE not in {1,2}) is excluded.
    """
    persons = frames.persons
    if "housing_tenure" not in persons.columns:
        LOGGER.warning(
            "housing_tenure absent from persons; tenure control skipped. "
            "Ensure assembly carries H_MIETE from the donor households."
        )
        return pd.DataFrame(columns=["geo_id", "category", "synthetic_count"])

    hh = persons.drop_duplicates("household_id")[["household_id", "housing_tenure"]].copy()
    hh = hh.merge(geo[["household_id", "ars5"]], on="household_id", how="left")
    hh = hh.dropna(subset=["ars5"]).copy()
    # Exclude "unknown" (H_MIETE not in {1,2}).
    hh = hh[hh["housing_tenure"].isin(["owner", "renter"])].copy()
    hh["category"] = hh["housing_tenure"].astype(str)
    out = (hh.groupby(["ars5", "category"]).size()
              .rename("synthetic_count").reset_index())
    return out.rename(columns={"ars5": "geo_id"})


def _realized_building_type(frames: "PopulationFrames", geo: pd.DataFrame) -> pd.DataFrame:
    """Realized building_type_3class extractor from synthetic persons."""
    persons = frames.persons
    if "building_type_3class" not in persons.columns:
        LOGGER.warning(
            "building_type_3class absent from persons; building_type control skipped. "
            "Ensure assembly carries haustyp from the donor households."
        )
        return pd.DataFrame(columns=["geo_id", "category", "synthetic_count"])

    hh = persons.drop_duplicates("household_id")[["household_id", "building_type_3class"]].copy()
    hh = hh.merge(geo[["household_id", "ars5"]], on="household_id", how="left")
    hh = hh.dropna(subset=["ars5"]).copy()
    hh = hh.dropna(subset=["building_type_3class"]).copy()  # drop haustyp=95 (n.z.)
    hh["category"] = hh["building_type_3class"].astype(str)
    out = (hh.groupby(["ars5", "category"]).size()
              .rename("synthetic_count").reset_index())
    return out.rename(columns={"ars5": "geo_id"})


def _realized_seniorenstatus(frames: "PopulationFrames", geo: pd.DataFrame) -> pd.DataFrame:
    """Realized Seniorenstatus extractor: households by senior presence.

    A household is "mit_senioren" when at least one person has age >= 65,
    "ohne_senioren" otherwise. Uses persons age column (mapped from HP_ALTER
    by expand.map_demographics). This is a REFERENCE control: it uses the census
    Seniorenstatus column as the target but the synthetic household's senior
    presence is derived from the expanded persons, not from a popsim control.
    """
    persons = frames.persons
    if "age" not in persons.columns:
        LOGGER.warning("age absent from persons; seniorenstatus control skipped.")
        return pd.DataFrame(columns=["geo_id", "category", "synthetic_count"])

    senior_mask = pd.to_numeric(persons["age"], errors="coerce") >= 65
    has_senior = senior_mask.groupby(persons["household_id"]).any()
    hh = has_senior.rename("has_senior").reset_index()
    hh = hh.merge(geo[["household_id", "ars5"]], on="household_id", how="left")
    hh = hh.dropna(subset=["ars5"]).copy()
    hh["category"] = np.where(hh["has_senior"], "mit_senioren", "ohne_senioren")
    out = (hh.groupby(["ars5", "category"]).size()
              .rename("synthetic_count").reset_index())
    return out.rename(columns={"ars5": "geo_id"})


# ---------------------------------------------------------------------------
# Tier-3 realized extractors (person-level; key on the person's own cell ARS)
# ---------------------------------------------------------------------------

def _persons_ars5(frames: "PopulationFrames", value_col: str) -> pd.DataFrame:
    """Lean [ars5, val] frame: Kreis from the person's own 12-digit
    RegionalSchlussel_ARS (the authoritative cell ARS the KREIS control was applied
    at -- not the homes-sjoin geo, which drops the ~258 null-Kreis cells)."""
    persons = frames.persons
    ars = persons["RegionalSchlussel_ARS"].astype(str).str.zfill(12).str[:5]
    return pd.DataFrame({"ars5": ars.to_numpy(),
                         "val": pd.to_numeric(persons[value_col], errors="coerce").to_numpy()})


def _realized_employed(frames: "PopulationFrames", geo: pd.DataFrame) -> pd.DataFrame:
    """Realized employment (Tier-3): per Kreis, persons with P_TAET in {1,2,3,4,6,8}
    (= the ``_EMPLOYED_PTAET`` constant; MiD ``erwerb`` definition) are 'employed',
    all others 'not_employed' (over the whole synthetic population)."""
    persons = frames.persons
    if "P_TAET" not in persons.columns or "RegionalSchlussel_ARS" not in persons.columns:
        LOGGER.warning("P_TAET/RegionalSchlussel_ARS absent; employed control skipped.")
        return pd.DataFrame(columns=["geo_id", "category", "synthetic_count"])
    p = _persons_ars5(frames, "P_TAET")
    p["category"] = np.where(p["val"].isin(_EMPLOYED_PTAET), "employed", "not_employed")
    out = p.groupby(["ars5", "category"]).size().rename("synthetic_count").reset_index()
    return out.rename(columns={"ars5": "geo_id"})


def _realized_schulabschluss(frames: "PopulationFrames", geo: pd.DataFrame) -> pd.DataFrame:
    """Realized Schulabschluss (Tier-3): bildung1 2/3/4 -> low/mid/high per Kreis;
    codes 1,5,9 (NaN) excluded -- mirrors attributes.SCHULABS_BY_BILDUNG1."""
    persons = frames.persons
    if "bildung1" not in persons.columns or "RegionalSchlussel_ARS" not in persons.columns:
        LOGGER.warning("bildung1/RegionalSchlussel_ARS absent; schulabschluss control skipped.")
        return pd.DataFrame(columns=["geo_id", "category", "synthetic_count"])
    from braunschweig.popsim.attributes import SCHULABS_BY_BILDUNG1
    p = _persons_ars5(frames, "bildung1")
    p["category"] = p["val"].astype("Int64").map(SCHULABS_BY_BILDUNG1)
    p = p.dropna(subset=["category"])
    out = p.groupby(["ars5", "category"]).size().rename("synthetic_count").reset_index()
    return out.rename(columns={"ars5": "geo_id"})


def _realized_beruflabschluss(frames: "PopulationFrames", geo: pd.DataFrame) -> pd.DataFrame:
    """Realized Berufsabschluss (Tier-3): bildung2 1->vocational, 2/3->tertiary,
    5->none per Kreis; codes 4,9 (+ structural 206/402) excluded -- mirrors
    attributes.BERUFABS_BY_BILDUNG2."""
    persons = frames.persons
    if "bildung2" not in persons.columns or "RegionalSchlussel_ARS" not in persons.columns:
        LOGGER.warning("bildung2/RegionalSchlussel_ARS absent; beruflabschluss control skipped.")
        return pd.DataFrame(columns=["geo_id", "category", "synthetic_count"])
    from braunschweig.popsim.attributes import BERUFABS_BY_BILDUNG2
    p = _persons_ars5(frames, "bildung2")
    p["category"] = p["val"].astype("Int64").map(BERUFABS_BY_BILDUNG2)
    p = p.dropna(subset=["category"])
    out = p.groupby(["ars5", "category"]).size().rename("synthetic_count").reset_index()
    return out.rename(columns={"ars5": "geo_id"})


def employed_25_64_rate(persons):
    """Employment rate within the 25-64 age band, per Kreis (ARS[:5])."""
    band = persons[(persons["HP_ALTER"] >= 25) & (persons["HP_ALTER"] <= 64)].copy()
    band["KREIS"] = band["RegionalSchlussel_ARS"].astype(str).str[:5]
    band["is_emp"] = band["P_TAET"].isin(_EMPLOYED_PTAET)
    grp = band.groupby("KREIS")["is_emp"]
    return (grp.sum() / grp.count()).to_dict()


def employed_by_age_group(persons):
    """Employment rate per Kreis × age group (young 16-29 / prime 30-59 / old 60+).

    Uses the module-level ``_EMPLOYED_PTAET`` (MiD erwerb definition: P_TAET in
    {1,2,3,4,6,8}).  Returns a dict keyed by ``(kreis5, group_name)`` where
    kreis5 is the 5-digit ARS prefix (``RegionalSchlussel_ARS[:5]``).
    """
    df = persons.copy()
    df["K"] = df["RegionalSchlussel_ARS"].astype(str).str[:5]
    df["is_emp"] = df["P_TAET"].isin(_EMPLOYED_PTAET)
    out = {}
    for g, lo, hi in _AGE_GROUPS3:
        sub = df[(df["HP_ALTER"] >= lo) & (df["HP_ALTER"] <= hi)]
        grp = sub.groupby("K")["is_emp"]
        for k, rate in (grp.sum() / grp.count()).items():
            out[(k, g)] = rate
    return out


# ---------------------------------------------------------------------------
# Registry builder
# ---------------------------------------------------------------------------

def build_registry(data_path: str) -> list[Control]:
    """Build the popsim control-fit validation registry.

    Returns a list of Controls for:
    - household_size (Tier-1, 6 categories, geography=kreis)
    - household_type (Tier-1, 5 categories, geography=kreis)
    - tenure (Tier-2, 2 categories, geography=kreis)
    - building_type (Tier-2, 3 categories, geography=kreis)
    - age_male / age_female (Tier-0 backbone, 9 bands each, geography=kreis)
    - seniorenstatus (REFERENCE only, 2 categories, geography=kreis)

    All controls use Kreis-level geography (ars5 derived from the 12-digit ARS
    in the prepared parquet). This allows the same geo frame from
    spatial.assign_geographies to be reused.
    """
    reg: list[Control] = []

    # --- Tier-1: household_size (bucket control on household_size column) ---
    # household_size on persons is an int derived in assembly (groupby count).
    # bucket_household_control expects the column on frames.households, but the
    # popsim_mid output carries it on persons (broadcast per HH). We use
    # categorical_household_control instead with direct category matching.
    reg.append(Control(
        name="household_size",
        family="popsim_hh",
        geography="kreis",
        categories=("1", "2", "3", "4", "5", "6+"),
        realized=_make_household_size_realized(),
        target=household_size_target,
    ))

    # --- Tier-1: household_type / hh_type5 ---
    reg.append(Control(
        name="household_type",
        family="popsim_hh",
        geography="kreis",
        categories=("einpersonen", "paar_ohne_kind", "paar_mit_kind",
                    "alleinerziehend", "mehrpers_ohne_kernfamilie"),
        realized=_realized_hh_type5,
        target=household_type_target,
    ))

    # --- Tier-2: tenure ---
    reg.append(Control(
        name="tenure",
        family="popsim_hh",
        geography="kreis",
        categories=("owner", "renter"),
        realized=_realized_housing_tenure,
        target=tenure_target,
    ))

    # --- Tier-2: building_type ---
    reg.append(Control(
        name="building_type",
        family="popsim_hh",
        geography="kreis",
        categories=("ein_zweifamilienhaus", "mehrfamilienhaus", "sonstiges"),
        realized=_realized_building_type,
        target=building_type_target,
    ))

    # --- Tier-0 backbone: age x sex ---
    age_bands_9 = (
        "0-9", "10-19", "20-29", "30-39", "40-49",
        "50-59", "60-69", "70-79", "80+",
    )
    reg.append(Control(
        name="age_male",
        family="popsim_backbone",
        geography="kreis",
        categories=age_bands_9,
        realized=_make_age_sex_realized(sex="male"),
        target=lambda dp: age_sex_target(dp, sex="male"),
    ))
    reg.append(Control(
        name="age_female",
        family="popsim_backbone",
        geography="kreis",
        categories=age_bands_9,
        realized=_make_age_sex_realized(sex="female"),
        target=lambda dp: age_sex_target(dp, sex="female"),
    ))

    # --- REFERENCE: seniorenstatus (validation-only, not a popsim control) ---
    reg.append(Control(
        name="seniorenstatus",
        family="popsim_reference",
        geography="kreis",
        categories=("mit_senioren", "ohne_senioren"),
        realized=_realized_seniorenstatus,
        target=seniorenstatus_target,
    ))

    # --- Tier-3: employment (KREIS control) ---
    reg.append(Control(
        name="employed",
        family="popsim_tier3",
        geography="kreis",
        categories=("employed", "not_employed"),
        realized=_realized_employed,
        target=employed_target,
    ))

    # --- Tier-3: education / Schulabschluss (KREIS control) ---
    reg.append(Control(
        name="schulabschluss",
        family="popsim_tier3",
        geography="kreis",
        categories=("low", "mid", "high"),
        realized=_realized_schulabschluss,
        target=schulabschluss_target,
    ))

    # --- Tier-3: education / Berufsabschluss (KREIS control) ---
    reg.append(Control(
        name="beruflabschluss",
        family="popsim_tier3",
        geography="kreis",
        categories=("none", "vocational", "tertiary"),
        realized=_realized_beruflabschluss,
        target=beruflabschluss_target,
    ))

    return reg


def _make_household_size_realized():
    """Build a realized extractor for household_size from the persons frame.

    household_size on the popsim_mid persons frame is the count of persons in
    each synthetic household. We bucket it to the 6 categories (1..5, 6+) per
    Kreis. One count per *household* (not per person).
    """
    def realized(frames: "PopulationFrames", geo: pd.DataFrame) -> pd.DataFrame:
        persons = frames.persons
        if "household_size" not in persons.columns:
            LOGGER.warning(
                "household_size absent from persons; household_size control skipped."
            )
            return pd.DataFrame(columns=["geo_id", "category", "synthetic_count"])

        # One row per household (all persons share the same household_size).
        hh = persons.drop_duplicates("household_id")[["household_id", "household_size"]].copy()
        hh = hh.merge(geo[["household_id", "ars5"]], on="household_id", how="left")
        hh = hh.dropna(subset=["ars5"]).copy()
        vals = pd.to_numeric(hh["household_size"], errors="coerce")
        n_na = int(vals.isna().sum())
        if n_na:
            LOGGER.warning(
                "household_size control: %d households have non-numeric/missing "
                "household_size; excluded.", n_na,
            )
        hh = hh[vals.notna()].copy()
        hh["_size"] = vals[vals.notna()].clip(upper=6).astype(int)
        hh["category"] = hh["_size"].astype(str).where(hh["_size"] < 6, "6+")
        out = (hh.groupby(["ars5", "category"]).size()
                  .rename("synthetic_count").reset_index())
        return out.rename(columns={"ars5": "geo_id"})
    return realized


def _make_age_sex_realized(sex: str):
    """Build a realized extractor for age (10-year bands) filtered by sex."""
    _BANDS = [
        (0, 9, "0-9"), (10, 19, "10-19"), (20, 29, "20-29"),
        (30, 39, "30-39"), (40, 49, "40-49"), (50, 59, "50-59"),
        (60, 69, "60-69"), (70, 79, "70-79"), (80, None, "80+"),
    ]

    def realized(frames: "PopulationFrames", geo: pd.DataFrame) -> pd.DataFrame:
        persons = frames.persons
        if "age" not in persons.columns or "sex" not in persons.columns:
            LOGGER.warning("age or sex absent from persons; %s control skipped.", f"age_{sex}")
            return pd.DataFrame(columns=["geo_id", "category", "synthetic_count"])

        df = persons[persons["sex"] == sex].copy()
        df = df.merge(geo[["household_id", "ars5"]], on="household_id", how="left")
        df = df.dropna(subset=["ars5"]).copy()
        ages = pd.to_numeric(df["age"], errors="coerce")
        labels = []
        for lo, hi, lab in _BANDS:
            if hi is None:
                mask = ages >= lo
            else:
                mask = (ages >= lo) & (ages <= hi)
            labels.append((mask, lab))

        cats = pd.Series("unknown", index=df.index)
        for mask, lab in labels:
            cats[mask] = lab
        df["category"] = cats.to_numpy()
        df = df[df["category"] != "unknown"]
        out = (df.groupby(["ars5", "category"]).size()
                  .rename("synthetic_count").reset_index())
        return out.rename(columns={"ars5": "geo_id"})
    return realized
