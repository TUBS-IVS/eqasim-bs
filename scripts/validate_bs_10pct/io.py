"""Loaders for the Braunschweig 10 % synthesis output.

All functions are cached on first call to keep the validator fast on
repeated runs (loading 113 k persons + 354 k trips + GPKGs costs ~10 s).
"""
from __future__ import annotations

import gzip
import logging
from functools import lru_cache
from pathlib import Path
from xml.etree import ElementTree as ET

import geopandas as gpd
import numpy as np
import pandas as pd

from .config import EPSG, OUTPUT_DIR, PREFIX, SPATIAL_DIR, ZGB8

LOG = logging.getLogger(__name__)


def _csv(name: str) -> Path:
    return OUTPUT_DIR / f"{PREFIX}{name}.csv"


def _gpkg(name: str) -> Path:
    return OUTPUT_DIR / f"{PREFIX}{name}.gpkg"


@lru_cache(maxsize=1)
def load_persons() -> pd.DataFrame:
    df = pd.read_csv(_csv("persons"), sep=";")
    return df


@lru_cache(maxsize=1)
def load_households() -> pd.DataFrame:
    return pd.read_csv(_csv("households"), sep=";")


@lru_cache(maxsize=1)
def load_trips() -> pd.DataFrame:
    df = pd.read_csv(_csv("trips"), sep=";")
    # Travel time in minutes; clamp negative (shouldn't happen) to 0.
    df["travel_time_min"] = np.maximum((df["arrival_time"] - df["departure_time"]) / 60.0, 0.0)
    df["departure_hour"] = (df["departure_time"] // 3600).astype(int).clip(0, 47) % 24
    return df


@lru_cache(maxsize=1)
def load_homes_gdf() -> gpd.GeoDataFrame:
    gdf = gpd.read_file(_gpkg("homes"))
    if gdf.crs is None:
        gdf = gdf.set_crs(epsg=EPSG)
    elif gdf.crs.to_epsg() != EPSG:
        gdf = gdf.to_crs(epsg=EPSG)
    return gdf


@lru_cache(maxsize=1)
def load_commutes_gdf() -> gpd.GeoDataFrame:
    gdf = gpd.read_file(_gpkg("commutes"))
    if gdf.crs is None:
        gdf = gdf.set_crs(epsg=EPSG)
    elif gdf.crs.to_epsg() != EPSG:
        gdf = gdf.to_crs(epsg=EPSG)
    # Crow-fly distance in km.
    gdf["distance_km"] = gdf.geometry.length / 1000.0
    return gdf


@lru_cache(maxsize=1)
def load_trips_gdf() -> gpd.GeoDataFrame:
    gdf = gpd.read_file(_gpkg("trips"))
    if gdf.crs is None:
        gdf = gdf.set_crs(epsg=EPSG)
    elif gdf.crs.to_epsg() != EPSG:
        gdf = gdf.to_crs(epsg=EPSG)
    gdf["distance_km"] = gdf.geometry.length / 1000.0
    return gdf


@lru_cache(maxsize=1)
def load_kreise_gdf() -> gpd.GeoDataFrame:
    """Return ZGB-8 Kreis polygons in EPSG:25832, dissolved from VG250-Gemeinden."""
    import zipfile
    from .config import REPO
    zip_path = REPO / "eqasim-data" / "data" / "germany" / "vg250-ew_12-31.utm32s.gpkg.ebenen.zip"
    inner = "vg250-ew_12-31.utm32s.gpkg.ebenen/vg250-ew_ebenen_1231/DE_VG250.gpkg"
    with zipfile.ZipFile(zip_path) as z, z.open(inner) as f:
        gdf = gpd.read_file(f, layer="vg250_gem")
    gdf["ars5"] = gdf["ARS"].astype(str).str[:5]
    gdf = gdf[gdf["ars5"].isin(ZGB8)].copy()
    if gdf.crs is None or gdf.crs.to_epsg() != EPSG:
        gdf = gdf.to_crs(epsg=EPSG)
    gdf = gdf.dissolve(by="ars5", as_index=False)
    gdf["kreis_name"] = gdf["ars5"].map(ZGB8)
    return gdf[["ars5", "kreis_name", "geometry"]]


@lru_cache(maxsize=1)
def homes_with_kreis() -> pd.DataFrame:
    """Map every household_id to its home Kreis (AGS-5)."""
    homes = load_homes_gdf()
    kreise = load_kreise_gdf()
    joined = gpd.sjoin(homes, kreise, how="left", predicate="within")
    joined["ars5"] = joined["ars5"].fillna("UNKNOWN")
    return joined[["household_id", "ars5"]].drop_duplicates("household_id")


@lru_cache(maxsize=1)
def persons_with_kreis() -> pd.DataFrame:
    persons = load_persons()
    homes = homes_with_kreis()
    return persons.merge(homes, on="household_id", how="left")


# ---------------------------------------------------------------------------
# MATSim mode extractor — modes live only in the selected plan inside
# population.xml.gz, not in trips.csv. We parse once and cache as CSV.
# ---------------------------------------------------------------------------
_MODE_CACHE = OUTPUT_DIR / f"{PREFIX}trip_modes.csv"


def _extract_modes(xml_gz: Path) -> pd.DataFrame:
    """Stream-parse a MATSim plans XML and return main-mode per logical trip.

    A logical trip = sequence of legs between two non-interaction activities.
    Main mode priority: pt > car > car_passenger > bicycle > walk.
    """
    PRIO = {"pt": 5, "car": 4, "car_passenger": 3, "ride": 3, "bicycle": 2, "walk": 1}
    rows: list[tuple[str, int, str]] = []
    with gzip.open(xml_gz, "rb") as fh:
        person_id: str | None = None
        in_selected_plan = False
        trip_index = 0
        # Modes accumulated for current logical trip.
        leg_modes: list[str] = []
        seen_first_activity = False

        def flush(pid: str, idx: int, modes: list[str]) -> None:
            if not modes:
                return
            best = max(modes, key=lambda m: PRIO.get(m, 0))
            rows.append((pid, idx, best))

        for event, elem in ET.iterparse(fh, events=("start", "end")):
            tag = elem.tag
            if event == "start":
                if tag == "person":
                    person_id = elem.get("id")
                    in_selected_plan = False
                    trip_index = 0
                    leg_modes = []
                    seen_first_activity = False
                elif tag == "plan":
                    in_selected_plan = elem.get("selected", "no") == "yes"
                    trip_index = 0
                    leg_modes = []
                    seen_first_activity = False
                elif tag == "activity" and in_selected_plan:
                    atype = elem.get("type", "")
                    is_interaction = atype.endswith("interaction") or " interaction" in atype
                    if not is_interaction:
                        if seen_first_activity and leg_modes:
                            flush(person_id, trip_index, leg_modes)
                            trip_index += 1
                            leg_modes = []
                        seen_first_activity = True
                elif tag == "leg" and in_selected_plan:
                    mode = elem.get("mode") or "unknown"
                    leg_modes.append(mode)
            elif event == "end" and tag == "person":
                # Flush trailing trip if any (shouldn't happen — plan ends with activity).
                if leg_modes:
                    flush(person_id, trip_index, leg_modes)
                elem.clear()

    df = pd.DataFrame(rows, columns=["person_id", "trip_index", "mode"])
    df["person_id"] = pd.to_numeric(df["person_id"], errors="coerce").astype("Int64")
    return df


@lru_cache(maxsize=1)
def load_trip_modes() -> pd.DataFrame:
    """Return DataFrame[person_id, trip_index, mode]; cached as CSV."""
    if _MODE_CACHE.exists():
        return pd.read_csv(_MODE_CACHE)
    xml_gz = OUTPUT_DIR / f"{PREFIX}population.xml.gz"
    LOG.info("Extracting modes from %s …", xml_gz.name)
    df = _extract_modes(xml_gz)
    df.to_csv(_MODE_CACHE, index=False)
    LOG.info("Cached %d leg modes to %s", len(df), _MODE_CACHE.name)
    return df


@lru_cache(maxsize=1)
def trips_full() -> pd.DataFrame:
    """Trips with mode (from MATSim XML) + home Kreis attached."""
    trips = load_trips()
    modes = load_trip_modes()
    full = trips.merge(modes, on=["person_id", "trip_index"], how="left")
    full["mode"] = full["mode"].fillna("unknown")
    persons = persons_with_kreis()[["person_id", "ars5"]]
    full = full.merge(persons, on="person_id", how="left")
    full["ars5"] = full["ars5"].fillna("UNKNOWN")
    # Join geometry-distance from gpkg.
    geom = load_trips_gdf()[["person_id", "trip_index", "distance_km"]]
    full = full.merge(geom, on=["person_id", "trip_index"], how="left")
    return full


# Map ENTD/MATSim modes onto MiD 4-bucket schema.
MODE_TO_MID = {
    "car": "miv",
    "car_passenger": "miv",
    "pt": "oeev",  # placeholder so we map to 'oev' below
    "bike": "rad",
    "bicycle": "rad",
    "walk": "fuss",
    "ride": "miv",  # car as passenger, occasionally emitted by eqasim
}
# Final canonical buckets for MiD comparison.
MID_BUCKETS = ("miv", "oev", "rad", "fuss")


def map_mode(m: str) -> str:
    if pd.isna(m):
        return "other"
    m = str(m).lower()
    if m == "pt":
        return "oev"
    return MODE_TO_MID.get(m, "other")
