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


def categorical_person_control(name, family, geography, column, categories, target,
                               age_min=None, age_max=None, derive=None):
    """Categorical control on ``frames.persons``.

    Optional parameters keep existing callers byte-identical (they pass none):

    * ``age_min`` / ``age_max``: when both/either are set AND an ``"age"`` column
      exists, restrict the realized distribution to persons whose age is within
      the inclusive ``[age_min, age_max]`` band (e.g. the MiD P9 employment base
      age 14+ via ``age_min=14, age_max=None``). Persons outside the band are
      excluded from the control entirely.
    * ``derive``: an optional ``Series -> array-like`` callable mapping the raw
      ``column`` onto the reported categories (e.g. a boolean ``employed`` ->
      ``"employed"`` / ``"not_employed"``). When ``None`` the raw column value
      (as string) is the category.
    """
    geo_col = _geo_col(geography)

    def realized(frames, geo) -> pd.DataFrame:
        if column not in frames.persons.columns:
            LOGGER.warning("control %s: column %r absent in persons; skipped", name, column)
            return pd.DataFrame(columns=["geo_id", "category", "synthetic_count"])
        df = frames.persons.merge(geo[["household_id", geo_col]], on="household_id", how="left")
        df = df.dropna(subset=[geo_col]).copy()
        if (age_min is not None or age_max is not None) and "age" in df.columns:
            ages = pd.to_numeric(df["age"], errors="coerce")
            lower = -np.inf if age_min is None else float(age_min)
            upper = np.inf if age_max is None else float(age_max)
            df = df[(ages >= lower) & (ages <= upper)]
        if derive is not None:
            df["category"] = np.asarray(derive(df[column])).astype(str)
        else:
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

    The geography is obtained by joining the vehicles to ``geo`` on
    ``household_id``: the German household fleet vehicles already carry a
    ``household_id`` column (used directly), while the legacy eqasim vehicles
    carry only ``owner_id`` (= a person ``person_id``), which is first resolved to
    ``household_id`` via ``frames.persons``. If ``frames.vehicles is None`` an INFO
    line is logged and an empty long frame is returned (vehicles are an optional
    source). ``derive`` is an optional ``Series -> Series`` callable that maps the
    raw ``column`` onto the reported categories (e.g. powertrain label ->
    bev/not_bev); when ``None`` the raw column value (as string) is the category.
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
        # Resolve household_id: use it directly when present (German household
        # fleet), else map owner_id -> person_id -> household_id (legacy fleet).
        # Doing the owner_id join when household_id already exists would create
        # household_id_x/_y and break the geo merge.
        if "household_id" not in veh.columns:
            if "owner_id" not in veh.columns:
                LOGGER.warning(
                    "control %s: vehicles have neither 'household_id' nor "
                    "'owner_id'; cannot join geography; skipped", name)
                return empty
            persons = frames.persons[["person_id", "household_id"]].drop_duplicates("person_id")
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


# Eqasim run-output booleans are written as strings; treat these (case-folded,
# stripped) as truthy. Reused by the licence and employment derivations so the
# two boolean fallbacks share one convention.
_TRUTHY_TOKENS: frozenset[str] = frozenset({"true", "1", "yes"})


def _is_truthy(series: pd.Series) -> np.ndarray:
    """Vectorised eqasim truthy test: ``str(v).strip().lower() in {true,1,yes}``."""
    return series.astype(str).str.strip().str.lower().isin(_TRUTHY_TOKENS).to_numpy()


def _employed_label(series: pd.Series) -> np.ndarray:
    """Map a boolean ``employed`` column onto the employment control categories."""
    return np.where(_is_truthy(series), "employed", "not_employed")


def license_control(name, family, geography, target, age_min=None, age_max=None):
    """Driving-licence control that is robust to the run-output schema.

    The categorical ``license_type`` column (values from ``RT.LICENSE_CATEGORIES``)
    is preferred, but the eqasim run-output person-attribute writer often omits it
    while still writing the boolean ``has_driving_license``. This builder therefore:

    * uses ``license_type`` verbatim when present;
    * otherwise derives the category from the boolean ``has_driving_license``
      (truthy -> ``"ja"``, else ``"nein"``) -- ``"keine_angabe"`` cannot be
      represented this way, which is logged once (its P17.1 share is ~1-2%);
    * otherwise (neither column present) logs a WARNING and returns an empty long
      frame (no silent fallback).

    Optional parameters:

    * ``age_min`` / ``age_max``: when set AND an ``"age"`` column exists, restrict
      the realized distribution to persons whose age is within the inclusive
      ``[age_min, age_max]`` band -- mirrors the filter in
      :func:`categorical_person_control`.  Register with ``age_min=14`` to match
      the MiD P17.1 14+ survey base.  Note that the synthesis floor is 18 (the
      BF17 / begleitetes Fahren option is intentionally ignored), leaving a
      structural ~1pp shortfall vs the 14+ target for ages 14-17; this is
      documented in ``quality_assessment.CAUSE_HINTS["driving_license_type"]``.
    """
    geo_col = _geo_col(geography)

    def realized(frames, geo) -> pd.DataFrame:
        empty = pd.DataFrame(columns=["geo_id", "category", "synthetic_count"])
        persons = frames.persons
        if "license_type" in persons.columns:
            df = persons.merge(geo[["household_id", geo_col]], on="household_id", how="left")
            df = df.dropna(subset=[geo_col]).copy()
            df["category"] = df["license_type"].astype(str)
        elif "has_driving_license" in persons.columns:
            LOGGER.info(
                "control %s: 'license_type' absent; deriving category from the "
                "boolean 'has_driving_license' (truthy -> 'ja', else 'nein'). "
                "'keine_angabe' cannot be represented from a boolean.",
                name,
            )
            df = persons.merge(geo[["household_id", geo_col]], on="household_id", how="left")
            df = df.dropna(subset=[geo_col]).copy()
            df["category"] = np.where(_is_truthy(df["has_driving_license"]), "ja", "nein")
        else:
            LOGGER.warning(
                "control %s: neither 'license_type' nor 'has_driving_license' "
                "present in persons; skipped", name,
            )
            return empty
        # Restrict to the MiD survey age base when age_min / age_max are set.
        if (age_min is not None or age_max is not None) and "age" in df.columns:
            ages = pd.to_numeric(df["age"], errors="coerce")
            lower = -np.inf if age_min is None else float(age_min)
            upper = np.inf if age_max is None else float(age_max)
            df = df[(ages >= lower) & (ages <= upper)]
        out = (df.groupby([geo_col, "category"]).size()
                 .rename("synthetic_count").reset_index())
        return out.rename(columns={geo_col: "geo_id"})

    return Control(name, family, geography, RT.LICENSE_CATEGORIES, realized, target)


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


def employment_target(data_path: str) -> pd.DataFrame:
    """Per-Kreis employed share from MiD 2023 Tabelle P9.

    Reads ``<data_path>/braunschweig/mid/mid2023_P9.csv`` (percentages summing to
    ~100 per Kreis). The employed share is the sum of the five employment columns
    (``vollzeit`` + ``teilzeit`` + ``geringfuegig`` + ``sonstiges`` +
    ``erwerbstaetig_unspec``) / 100, clipped to [0, 1]; ``not_employed`` is the
    complement. Two long rows per Kreis (``employed`` / ``not_employed``).

    ``geo_id`` is the 5-digit Kreis ``ars5``; the ZGB aggregate row (``03ZGB``)
    is excluded. The MiD P9 percentages are over the **"Personen ab 14 Jahre"**
    basis -- the standard MiD person basis (the same one used for P17.1 and
    P24.1). The registered employment control therefore matches the synthetic
    side to age **>= 14 with no upper bound**, so realized and target share the
    same denominator. (An upper cap of 74 would drop the 75+ group -- almost all
    non-employed -- from the synthetic denominator while MiD keeps it, biasing
    the realized employed share upward.)
    """
    path = f"{data_path}/braunschweig/mid/mid2023_P9.csv"
    df = pd.read_csv(path, comment="#", dtype={"ars5": str})
    df = df[df["ars5"] != "03ZGB"].copy()
    employ_cols = ["vollzeit", "teilzeit", "geringfuegig", "sonstiges", "erwerbstaetig_unspec"]
    employed = df[employ_cols].fillna(0.0).sum(axis=1) / 100.0
    employed = employed.clip(lower=0.0, upper=1.0)
    rows = []
    for ars5, share in zip(df["ars5"], employed):
        rows.append({"geo_id": str(ars5), "category": "employed", "target_share": float(share)})
        rows.append({"geo_id": str(ars5), "category": "not_employed", "target_share": float(1.0 - share)})
    return pd.DataFrame(rows)


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
    """Build the full control set: the MiD person/household controls plus the
    census + fleet controls.

    Every registered control carries a REAL ``target`` loader -- a genuine
    geographic reference distribution exists for each (CLAUDE.md: no invented
    data):

    * household_size -> Zensus 2022 1000A-2081 (Gemeinde).
    * age_group / sex -> DESTATIS 12411-0018 (Kreis).
    * cars/bikes/license/pt -> MiD reference CSVs.
    * employment -> MiD 2023 P9 (Kreis), age 14+ (no upper bound) base.
    * bev_share -> KBA FZ 27.15 (Kreis) -- lazy target loader; a missing
      non-redistributable fleet file does not break registry construction (it
      only fails if a comparison is run).

    The attributes ``economic_status``, ``housing_tenure`` and ``income_class``
    are intentionally NOT registered here: no hard Kreis/Gemeinde target exists
    for them (economic_status is Bayes-modelled from hhtype x region; the MiD
    tenure source is conditional on income x raumtyp; MiD H4 income is HH-size
    conditional), so a validation deviation would be meaningless. Their spatial
    distributions are exported by
    :func:`braunschweig.analysis.population_validation.geo_export.write_geo_package`
    instead, not validated against an invented target.
    """
    reg: list[Control] = []

    reg.append(license_control(
        "driving_license_type", "mid_person", "kreis", license_target, age_min=14))
    # MiD P24.1 survey base is age 14+; restrict the realized distribution to
    # match (persons <14 are deterministically assigned fahre_nie in synthesis).
    reg.append(categorical_person_control(
        "pt_ticket_type", "mid_person", "kreis", "pt_subscription_type",
        RT.PT_TICKET_CATEGORIES, pt_ticket_target, age_min=14))

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

    # --- Employment (REAL target, MiD 2023 P9; age 14+ base, no upper cap) ----
    # The MiD P9 percentages are over the "Personen ab 14 Jahre" basis, so the
    # synthetic side is filtered to age >= 14 with NO upper bound (the same basis
    # as P17.1 / P24.1). Capping at 74 would drop the 75+ group -- almost all
    # non-employed -- from the synthetic denominator while MiD keeps it, biasing
    # the realized employed share upward. The boolean `employed` column is mapped
    # to the {employed, not_employed} categories.
    reg.append(categorical_person_control(
        "employment", "mid_person", "kreis", "employed",
        ("employed", "not_employed"), employment_target,
        age_min=14, age_max=None, derive=_employed_label))

    # --- Fleet BEV share (REAL target, KBA FZ 27.15) -------------------------
    # The target loader is lazy: a missing non-redistributable fleet file does
    # not break registry construction (it only fails if a comparison is run).
    reg.append(categorical_vehicle_control(
        "bev_share", "distribution", "kreis", "technology",
        ("bev", "not_bev"), bev_share_target, derive=_bev_not_bev))

    return reg
