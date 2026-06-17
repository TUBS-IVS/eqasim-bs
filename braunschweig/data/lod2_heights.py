"""Pure helpers for the LoD2 height preprocessing: parse 3D-Shape DBF attributes
into OI->height, and select tiles intersecting a region."""
from __future__ import annotations
import re
import pandas as pd
from shapely.geometry import shape

OI_RE = re.compile(r"DENIAL[0-9A-Za-z]+")


def _oi(extern_ref) -> str | None:
    m = OI_RE.search(str(extern_ref))
    return m.group(0) if m else None


def extract_heights(df: pd.DataFrame) -> pd.DataFrame:
    """From a LoD2 3D-Shape attribute frame -> one row per building [OI, height_m, roofType].

    The 3D-Shape stores one row per building surface (wall/roof), all sharing the
    building's gml_id + measHeight, so we dedup by gml_id. OI is parsed from externRef.
    """
    out = df.copy()
    out["OI"] = out["externRef"].map(_oi)
    out["height_m"] = pd.to_numeric(out["measHeight"], errors="coerce")
    out = out.dropna(subset=["OI"]).drop_duplicates("gml_id")
    return out[["OI", "height_m", "roofType"]].reset_index(drop=True)


def tiles_for_region(index: dict, region_4326) -> list[dict]:
    """Return index features (the `properties` dict) whose geometry intersects region (EPSG:4326)."""
    hits = []
    for f in index.get("features", []):
        if shape(f["geometry"]).intersects(region_4326):
            hits.append(f["properties"])
    return hits
