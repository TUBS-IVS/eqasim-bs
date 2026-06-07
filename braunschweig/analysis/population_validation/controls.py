"""Declarative control registry. Each Control couples a realized-share extractor
(synthetic distribution) with a target loader (the synthesis control). Targets
come from braunschweig.data.mid.reference_tables and the census stages."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

import numpy as np
import pandas as pd

from braunschweig.data.mid import reference_tables as RT
from braunschweig.data.cordon.network import ZGB_KREIS_PREFIXES

if TYPE_CHECKING:
    from braunschweig.analysis.population_validation.population_source import PopulationFrames

LOGGER = logging.getLogger("braunschweig.analysis.population_validation.controls")

# The eight ZGB Kreise (5-digit ARS prefixes) define the spatial scope of every
# census/fleet target loader below. Reused from the cordon network module so the
# scope is single-sourced (it equals braunschweig.political_prefix in the configs).
SCOPE_PREFIXES: tuple[str, ...] = ZGB_KREIS_PREFIXES

# Age-band right-open edges for the age_group control. Every edge is a native
# DESTATIS 12411-0018 age-class lower bound (0,3,6,10,15,18,20,25,30,35,40,45,50,
# 55,60,65,75), so aggregating the official Kreis age counts into these bands
# never splits a source class -> the target is exact, not an interpolation.
AGE_GROUP_BOUNDS: tuple[int, ...] = (15, 30, 45, 60, 75)

# Five BMDV economic-status classes (very_low..very_high), matching
# braunschweig.data.mid.status_by_hhtype.ECONOMIC_STATUS_CATEGORIES.
ECONOMIC_STATUS_CATEGORIES: tuple[str, ...] = (
    "very_low", "low", "medium", "high", "very_high",
)

# Three-class housing-tenure vocabulary, matching
# braunschweig.data.mid.tenure_by_income.TENURE_CATEGORIES.
HOUSING_TENURE_CATEGORIES: tuple[str, ...] = ("rent", "own", "other")

RealizedExtractor = Callable[["PopulationFrames", pd.DataFrame], pd.DataFrame]
TargetLoader = Callable[[str], pd.DataFrame]


@dataclass(frozen=True)
class Control:
    name: str
    family: str
    geography: str
    categories: tuple[str, ...]
    realized: RealizedExtractor
    target: TargetLoader | None


def _geo_col(geography: str) -> str:
    if geography == "kreis":
        return "ars5"
    if geography == "gemeinde":
        return "commune_id"
    raise ValueError(f"unknown geography {geography!r}; expected 'kreis' or 'gemeinde'")


def categorical_person_control(name, family, geography, column, categories, target):
    geo_col = _geo_col(geography)

    def realized(frames, geo) -> pd.DataFrame:
        if column not in frames.persons.columns:
            LOGGER.warning("control %s: column %r absent in persons; skipped", name, column)
            return pd.DataFrame(columns=["geo_id", "category", "synthetic_count"])
        df = frames.persons.merge(geo[["household_id", geo_col]], on="household_id", how="left")
        df = df.dropna(subset=[geo_col])
        df["category"] = df[column].astype(str)
        out = (df.groupby([geo_col, "category"]).size()
                 .rename("synthetic_count").reset_index())
        return out.rename(columns={geo_col: "geo_id"})

    return Control(name, family, geography, tuple(categories), realized, target)


def bucket_household_control(name, family, geography, column, top, target, top_label=None):
    """Bucket a numeric household column into ``[0, 1, ..., top-1, top_lab]`` categories.

    ``top_label`` is the string label for values >= ``top`` (e.g. ``"6+"``).
    When ``None`` (default), the top label is ``str(top)`` — identical to the
    previous behaviour, so existing callers (cars_per_hh, bicycles_per_hh) are
    byte-unchanged.
    """
    geo_col = _geo_col(geography)
    top_lab = top_label if top_label is not None else str(top)
    # Categories: 0 .. top-1 as strings, then top_lab for values >= top.
    cats = tuple(str(i) for i in range(top)) + (top_lab,)

    def realized(frames, geo) -> pd.DataFrame:
        if column not in frames.households.columns:
            LOGGER.warning("control %s: column %r absent in households; skipped", name, column)
            return pd.DataFrame(columns=["geo_id", "category", "synthetic_count"])
        df = frames.households.merge(geo[["household_id", geo_col]], on="household_id", how="left")
        df = df.dropna(subset=[geo_col]).copy()
        vals = pd.to_numeric(df[column], errors="coerce")
        n_na = int(vals.isna().sum())
        if n_na:
            LOGGER.warning(
                "control %s: %d household(s) have non-numeric/missing %r; excluded from the bucket distribution",
                name, n_na, column,
            )
        df = df.assign(_bucket=vals)
        df = df[df["_bucket"].notna()]
        # Clip values above top down to top (they will receive the top_lab label).
        capped = df["_bucket"].clip(upper=top)
        cat = capped.astype("int64").astype(str)
        # Values equal to top (after clipping) get the top_lab label (e.g. "6+").
        cat = cat.where(capped < top, top_lab)
        df["category"] = cat.to_numpy()
        out = (df.groupby([geo_col, "category"]).size()
                 .rename("synthetic_count").reset_index())
        return out.rename(columns={geo_col: "geo_id"})

    return Control(name, family, geography, cats, realized, target)


def _band_labels(bounds: tuple[int, ...]) -> tuple[str, ...]:
    """Human-readable, sortable band labels for a tuple of right-open edges.

    ``bounds=(15, 30)`` -> ``("0-14", "15-29", "30+")``. The last band is open
    ("<edge>+"); interior bands are "lower-upper" with ``upper = next_edge - 1``.
    """
    edges = sorted(bounds)
    labels: list[str] = []
    lower = 0
    for edge in edges:
        labels.append(f"{lower}-{edge - 1}")
        lower = edge
    labels.append(f"{lower}+")
    return tuple(labels)


def banded_person_control(name, family, geography, column, bounds, target):
    """Realized control that bins a numeric person column into age-style bands.

    ``bounds`` are the right-open band edges (e.g. ``(15, 30, 45, 60, 75)``);
    categories are the labels from :func:`_band_labels`. Persons whose value is
    non-numeric/missing are dropped from the band distribution (logged). An
    absent column logs a WARNING and returns an empty long frame, mirroring
    :func:`categorical_person_control`.
    """
    geo_col = _geo_col(geography)
    cats = _band_labels(bounds)
    edges = sorted(bounds)

    def realized(frames, geo) -> pd.DataFrame:
        if column not in frames.persons.columns:
            LOGGER.warning("control %s: column %r absent in persons; skipped", name, column)
            return pd.DataFrame(columns=["geo_id", "category", "synthetic_count"])
        df = frames.persons.merge(geo[["household_id", geo_col]], on="household_id", how="left")
        df = df.dropna(subset=[geo_col]).copy()
        vals = pd.to_numeric(df[column], errors="coerce")
        n_na = int(vals.isna().sum())
        if n_na:
            LOGGER.warning(
                "control %s: %d person(s) have non-numeric/missing %r; excluded from the band distribution",
                name, n_na, column,
            )
        df = df.assign(_val=vals)
        df = df[df["_val"].notna()]
        # np.searchsorted on the interior edges maps each value to its band index.
        idx = np.searchsorted(np.asarray(edges, dtype=float), df["_val"].to_numpy(dtype=float), side="right")
        df["category"] = [cats[i] for i in idx]
        out = (df.groupby([geo_col, "category"]).size()
                 .rename("synthetic_count").reset_index())
        return out.rename(columns={geo_col: "geo_id"})

    return Control(name, family, geography, cats, realized, target)


def categorical_household_control(name, family, geography, column, categories, target):
    """Categorical control on ``frames.households`` (mirror of the person variant)."""
    geo_col = _geo_col(geography)

    def realized(frames, geo) -> pd.DataFrame:
        if column not in frames.households.columns:
            LOGGER.warning("control %s: column %r absent in households; skipped", name, column)
            return pd.DataFrame(columns=["geo_id", "category", "synthetic_count"])
        df = frames.households.merge(geo[["household_id", geo_col]], on="household_id", how="left")
        df = df.dropna(subset=[geo_col]).copy()
        df["category"] = df[column].astype(str)
        out = (df.groupby([geo_col, "category"]).size()
                 .rename("synthetic_count").reset_index())
        return out.rename(columns={geo_col: "geo_id"})

    return Control(name, family, geography, tuple(categories), realized, target)


def categorical_vehicle_control(name, family, geography, column, categories, target,
                                derive=None):
    """Categorical control on ``frames.vehicles``.

    The vehicles frame carries ``owner_id`` (= a person ``person_id``) but no
    ``household_id``, so it is joined vehicles -> persons (owner_id == person_id)
    -> geo to obtain the geography. If ``frames.vehicles is None`` an INFO line is
    logged and an empty long frame is returned (vehicles are an optional source).
    ``derive`` is an optional ``Series -> Series`` callable that maps the raw
    ``column`` onto the reported categories (e.g. powertrain label -> bev/not_bev);
    when ``None`` the raw column value (as string) is the category.
    """
    geo_col = _geo_col(geography)

    def realized(frames, geo) -> pd.DataFrame:
        empty = pd.DataFrame(columns=["geo_id", "category", "synthetic_count"])
        if frames.vehicles is None:
            LOGGER.info("control %s: vehicles absent; %s skipped", name, name)
            return empty
        if column not in frames.vehicles.columns:
            LOGGER.warning("control %s: column %r absent in vehicles; skipped", name, column)
            return empty
        veh = frames.vehicles.copy()
        if "owner_id" not in veh.columns:
            LOGGER.warning("control %s: vehicles have no 'owner_id'; cannot join geography; skipped", name)
            return empty
        persons = frames.persons[["person_id", "household_id"]].copy()
        veh = veh.merge(persons, left_on="owner_id", right_on="person_id", how="left")
        veh = veh.merge(geo[["household_id", geo_col]], on="household_id", how="left")
        veh = veh.dropna(subset=[geo_col]).copy()
        if derive is not None:
            veh["category"] = derive(veh[column]).astype(str)
        else:
            veh["category"] = veh[column].astype(str)
        out = (veh.groupby([geo_col, "category"]).size()
                 .rename("synthetic_count").reset_index())
        return out.rename(columns={geo_col: "geo_id"})

    return Control(name, family, geography, tuple(categories), realized, target)


def _kreis_categorical_target(by_kreis: dict[str, np.ndarray], cats: tuple[str, ...]) -> pd.DataFrame:
    rows = []
    for ars5, vec in by_kreis.items():
        for cat, share in zip(cats, vec):
            rows.append({"geo_id": ars5, "category": cat, "target_share": float(share)})
    return pd.DataFrame(rows)


def license_target(data_path: str) -> pd.DataFrame:
    by_kreis, _ = RT.load_license_breakdown(data_path)
    return _kreis_categorical_target(by_kreis, RT.LICENSE_CATEGORIES)


def pt_ticket_target(data_path: str) -> pd.DataFrame:
    by_kreis, _ = RT.load_pt_subscription_breakdown(data_path)
    return _kreis_categorical_target(by_kreis, RT.PT_TICKET_CATEGORIES)


def cars_target(data_path: str) -> pd.DataFrame:
    by_kreis, _, values = RT.load_kreis_share_table(data_path, "mid2023_H7_cars_by_kreis.csv")
    cats = tuple(str(v) for v in values)
    return _kreis_categorical_target(by_kreis, cats)


def bikes_target(data_path: str) -> pd.DataFrame:
    by_kreis, _, values = RT.load_kreis_share_table(data_path, "mid2023_H12_3_bikes_by_kreis.csv")
    cats = tuple(str(v) for v in values)
    return _kreis_categorical_target(by_kreis, cats)


def _shares_within_geo(df: pd.DataFrame, geo_col: str, cat_col: str,
                       weight_col: str) -> pd.DataFrame:
    """Reshape a (geo, category, weight) frame to long target shares.

    Returns ``[geo_id, category, target_share]`` with the shares per ``geo_id``
    summing to 1 (cells with a zero geo total are dropped, which cannot happen
    here because the census sources always carry a positive geo weight)."""
    g = (df.groupby([geo_col, cat_col], observed=True, as_index=False)[weight_col]
           .sum())
    totals = g.groupby(geo_col, observed=True)[weight_col].transform("sum")
    g = g[totals > 0].copy()
    g["target_share"] = g[weight_col] / totals[totals > 0].to_numpy()
    return g.rename(columns={geo_col: "geo_id", cat_col: "category"})[
        ["geo_id", "category", "target_share"]
    ]


def household_size_target(data_path: str) -> pd.DataFrame:
    """Per-Gemeinde household-size target from Zensus 2022 1000A-2081.

    Reuses the census stage's own parser
    (:func:`braunschweig.data.census.households_type.load_household_size_by_commune`,
    the same per-commune size source the IPF size margin consumes). Categories are
    the source's six size bins ("1".."5", "6+"); ``geo_id`` is the 8-digit AGS
    commune_id (matching the ``commune_id`` produced by
    :func:`braunschweig.analysis.spatial.assign_geographies` and used by the
    ``household_size`` bucket control).

    The Zensus 1000A-2081 source carries a 12-digit ARS; this function converts
    it to the 8-digit AGS via ``ARS[:5] + ARS[9:12]`` (the standard rule used
    throughout the project). Shares sum to 1 per (converted) commune."""
    from braunschweig.data.census import households_type as HT

    df = HT.load_household_size_by_commune(data_path, SCOPE_PREFIXES)
    df["hh_size"] = df["hh_size"].astype(str)
    # Convert 12-digit ARS -> 8-digit AGS8 so the target geo_id matches the
    # commune_id produced by assign_geographies (e.g. "031010000000" -> "03101000").
    mask12 = df["commune_id"].str.len() == 12
    df.loc[mask12, "commune_id"] = (
        df.loc[mask12, "commune_id"].str[:5]
        + df.loc[mask12, "commune_id"].str[9:12]
    )
    result = _shares_within_geo(df, "commune_id", "hh_size", "weight")
    # Sanity check: shares should sum to 1 per commune (allow fp tolerance).
    sums = result.groupby("geo_id")["target_share"].sum()
    bad = sums[abs(sums - 1.0) > 1e-6]
    if not bad.empty:
        LOGGER.warning(
            "household_size_target: %d commune(s) have target_share sum != 1.0 "
            "(max deviation %.2e); check census source data.",
            len(bad), float(abs(bad - 1.0).max()),
        )
    return result


def _age_sex_kreis_frame(data_path: str) -> pd.DataFrame:
    """Kreis-level (sex, age_class) counts from DESTATIS 12411-0018.

    Thin wrapper over
    :func:`braunschweig.data.census.population.load_age_sex_by_kreis` so both the
    age-group and sex targets share the one standalone load."""
    from braunschweig.data.census import population as POP

    return POP.load_age_sex_by_kreis(data_path, SCOPE_PREFIXES)


def age_group_target(data_path: str) -> pd.DataFrame:
    """Kreis-level age-group target from DESTATIS 12411-0018.

    The official 17 age classes are aggregated into the :data:`AGE_GROUP_BOUNDS`
    bands (every band edge is a native DESTATIS class lower bound, so this is an
    exact aggregation, not an interpolation). ``geo_id`` is the 5-digit Kreis;
    categories are the same labels the registered ``age_group``
    :func:`banded_person_control` produces, so realized and target align."""
    df = _age_sex_kreis_frame(data_path)
    cats = _band_labels(AGE_GROUP_BOUNDS)
    edges = sorted(AGE_GROUP_BOUNDS)
    idx = np.searchsorted(np.asarray(edges, dtype=float),
                          df["age_class"].to_numpy(dtype=float), side="right")
    df = df.assign(category=[cats[i] for i in idx])
    return _shares_within_geo(df, "kreis", "category", "weight")


def sex_target(data_path: str) -> pd.DataFrame:
    """Kreis-level sex target (male/female) from DESTATIS 12411-0018."""
    df = _age_sex_kreis_frame(data_path)
    return _shares_within_geo(df, "kreis", "sex", "weight")


def bev_share_target(data_path: str) -> pd.DataFrame:
    """Per-Kreis BEV vs non-BEV target from KBA FZ 27.15.

    Uses :func:`braunschweig.data.kba.fleet_tables.load_kreis_powertrain`
    (the same FZ 27.15 source the fleet powertrain raking calibrates against).
    ``geo_id`` is the 5-digit Kreis; categories are ``bev`` / ``not_bev`` with
    ``not_bev = 1 - bev_share``."""
    from braunschweig.data.kba import fleet_tables as FT

    df = FT.load_kreis_powertrain(data_path)
    rows = []
    for _, r in df.iterrows():
        kreis = str(r["kreis_ags5"])
        bev = float(r["bev_share"])
        rows.append({"geo_id": kreis, "category": "bev", "target_share": bev})
        rows.append({"geo_id": kreis, "category": "not_bev", "target_share": 1.0 - bev})
    return pd.DataFrame(rows)


def _bev_not_bev(powertrain: pd.Series) -> pd.Series:
    """Map a powertrain/technology label onto the bev/not_bev category.

    The fleet writer stores the canonical powertrain label (e.g. "bev") in the
    vehicles ``technology`` column; everything that is not exactly "bev" (case-
    insensitive) is reported as ``not_bev``."""
    return np.where(powertrain.astype(str).str.lower() == "bev", "bev", "not_bev")


def build_registry(data_path: str) -> list[Control]:
    """Build the full control set: the four MiD person/household controls plus
    the census + economic-status + fleet controls.

    Target wiring (CLAUDE.md: no invented data) -- a control carries a real
    ``target`` loader ONLY where a genuine geographic reference distribution
    exists; otherwise it is registered DESCRIPTIVE-ONLY (``target=None``) and the
    decision is logged so a downstream consumer can confirm:

    * household_size -> Zensus 2022 1000A-2081 (Gemeinde) -- REAL target.
    * age_group / sex -> DESTATIS 12411-0018 (Kreis) -- REAL target.
    * cars/bikes/license/pt -> MiD reference CSVs -- REAL target (existing).
    * bev_share -> KBA FZ 27.15 (Kreis) -- REAL target (lazy; only loaded when a
      comparison runs, so a missing non-redistributable fleet file does not break
      registry construction).
    * economic_status -> DESCRIPTIVE-ONLY: modelled from MiD hhtype x region Bayes;
      there is no hard Kreis/Gemeinde target by design.
    * housing_tenure -> DESCRIPTIVE-ONLY: the MiD tenure source is conditional on
      income bracket x raumtyp, not a Kreis/Gemeinde marginal, so no geographic
      target is defensible.
    * income_class -> DESCRIPTIVE-ONLY: MiD H4 is size-conditional (income x HH
      size), not a direct geographic target.
    """
    reg: list[Control] = []

    reg.append(categorical_person_control(
        "driving_license_type", "mid_person", "kreis", "license_type",
        RT.LICENSE_CATEGORIES, license_target))
    reg.append(categorical_person_control(
        "pt_ticket_type", "mid_person", "kreis", "pt_subscription_type",
        RT.PT_TICKET_CATEGORIES, pt_ticket_target))

    _, _, car_vals = RT.load_kreis_share_table(data_path, "mid2023_H7_cars_by_kreis.csv")
    reg.append(bucket_household_control(
        "cars_per_hh", "mid_household", "kreis", "number_of_cars",
        top=int(max(car_vals)), target=cars_target))
    _, _, bike_vals = RT.load_kreis_share_table(data_path, "mid2023_H12_3_bikes_by_kreis.csv")
    reg.append(bucket_household_control(
        "bicycles_per_hh", "mid_household", "kreis", "number_of_bicycles",
        top=int(max(bike_vals)), target=bikes_target))

    # --- Census distribution controls (REAL targets) -------------------------
    reg.append(banded_person_control(
        "age_group", "census", "kreis", "age",
        bounds=AGE_GROUP_BOUNDS, target=age_group_target))
    reg.append(categorical_person_control(
        "sex", "census", "kreis", "sex",
        ("male", "female"), sex_target))
    # household_size uses the bucket builder so values >= 6 are collapsed to the
    # "6+" label matching the Zensus 1000A-2081 target categories. The geography
    # is "gemeinde" (8-digit commune_id) because the Zensus source is per-Gemeinde.
    reg.append(bucket_household_control(
        "household_size", "census", "gemeinde", "household_size",
        top=6, top_label="6+", target=household_size_target))

    # --- Economic status (DESCRIPTIVE-ONLY: Bayes-modelled, no geo target) ----
    LOGGER.info(
        "control economic_status registered DESCRIPTIVE-ONLY (target=None): "
        "modelled from MiD hhtype x region Bayes, no hard Kreis target exists.")
    reg.append(categorical_person_control(
        "economic_status", "census", "kreis", "economic_status",
        ECONOMIC_STATUS_CATEGORIES, target=None))

    # --- Housing tenure (DESCRIPTIVE-ONLY: source is income x raumtyp, not geo) -
    LOGGER.info(
        "control housing_tenure registered DESCRIPTIVE-ONLY (target=None): "
        "MiD tenure source is conditional on income bracket x raumtyp, not a "
        "Kreis/Gemeinde marginal -- no geographic target is defensible.")
    reg.append(categorical_household_control(
        "housing_tenure", "census", "kreis", "housing_tenure",
        HOUSING_TENURE_CATEGORIES, target=None))

    # --- Income class (DESCRIPTIVE-ONLY: MiD H4 is size-conditional) ----------
    LOGGER.info(
        "control income_class registered DESCRIPTIVE-ONLY (target=None): "
        "MiD H4 income is conditional on HH size, not a direct geographic target.")
    income_classes = tuple(cls for cls, _ in _income_class_categories())
    reg.append(categorical_household_control(
        "income_class", "census", "kreis", "income",
        income_classes, target=None))

    # --- Fleet BEV share (REAL target, KBA FZ 27.15) -------------------------
    # The target loader is lazy: a missing non-redistributable fleet file does
    # not break registry construction (it only fails if a comparison is run).
    reg.append(categorical_vehicle_control(
        "bev_share", "distribution", "kreis", "technology",
        ("bev", "not_bev"), bev_share_target, derive=_bev_not_bev))

    return reg


def _income_class_categories() -> list[tuple[str, int]]:
    """The BMDV income EUR-class vocabulary used by the synthesis (for the
    descriptive-only income_class control). Imported lazily to keep the census
    stage import graph out of module load."""
    from braunschweig.data.census.household_income import INCOME_CLASS_MAP

    return list(INCOME_CLASS_MAP)
