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

if TYPE_CHECKING:
    from braunschweig.analysis.population_validation.population_source import PopulationFrames

LOGGER = logging.getLogger("braunschweig.analysis.popsim_validation.controls")

# Age-band bounds matching the Tier-0 backbone controls (ten-year bands).
BACKBONE_AGE_BOUNDS: tuple[int, ...] = (10, 20, 30, 40, 50, 60, 70, 80)

# Prepared parquet path relative to data root.
_PARQUET_SUBPATH = "braunschweig/popsim/cells/zensus2022_grid_100m_de_prepared.parquet"

# ARS column name in the prepared parquet (after clean_col_name transliteration of ü→u).
_ARS_COLUMN_CLEAN = "RegionalSchlussel_ARS"


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
    else:
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
