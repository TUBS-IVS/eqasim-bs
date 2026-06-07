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

if TYPE_CHECKING:
    from braunschweig.analysis.population_validation.population_source import PopulationFrames

LOGGER = logging.getLogger("braunschweig.analysis.population_validation.controls")

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


def bucket_household_control(name, family, geography, column, top, target):
    geo_col = _geo_col(geography)
    cats = tuple(str(i) for i in range(top + 1))

    def realized(frames, geo) -> pd.DataFrame:
        if column not in frames.households.columns:
            LOGGER.warning("control %s: column %r absent in households; skipped", name, column)
            return pd.DataFrame(columns=["geo_id", "category", "synthetic_count"])
        df = frames.households.merge(geo[["household_id", geo_col]], on="household_id", how="left")
        df = df.dropna(subset=[geo_col]).copy()
        vals = pd.to_numeric(df[column], errors="coerce").clip(upper=top)
        n_na = int(vals.isna().sum())
        if n_na:
            LOGGER.warning(
                "control %s: %d household(s) have non-numeric/missing %r; excluded from the bucket distribution",
                name, n_na, column,
            )
        df = df.assign(_bucket=vals)
        df = df[df["_bucket"].notna()]
        df["category"] = df["_bucket"].astype("int64").astype(str)
        out = (df.groupby([geo_col, "category"]).size()
                 .rename("synthetic_count").reset_index())
        return out.rename(columns={geo_col: "geo_id"})

    return Control(name, family, geography, cats, realized, target)


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


def build_registry(data_path: str) -> list[Control]:
    """Build the initial control set. Census + economic-status + fleet controls
    that need additional source wiring are added in Task 3b; the MiD
    person/household controls below are complete now."""
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

    return reg
