"""Shared spatial helpers for the Braunschweig analysis modules.

Exposes the ZGB-8 Kreise map, VG250 / RegioStaR path constants, and loaders
that attach Kreis, commune_id and RegioStaR-7 to home points.  Extracted from
``braunschweig.analysis.run_mid_validation`` so multiple analysis modules can
share the same definitions without duplication.
"""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd

LOGGER = logging.getLogger("braunschweig.analysis.spatial")

REPO_ROOT = Path(__file__).resolve().parents[2]

# ZGB-8 Kreise: AGS-5 -> display name.  Kept consistent with
# `braunschweig.analysis.dashboard.build_dashboard.KREIS_NAMES`.
ZGB8: dict[str, str] = {
    "03101": "SK Braunschweig",
    "03102": "SK Salzgitter",
    "03103": "SK Wolfsburg",
    "03151": "LK Gifhorn",
    "03153": "LK Goslar",
    "03154": "LK Helmstedt",
    "03157": "LK Peine",
    "03158": "LK Wolfenbüttel",
}

# VG250 zip shipped alongside the rest of the spatial inputs.
VG250_ZIP = (
    REPO_ROOT
    / "eqasim-data"
    / "data"
    / "germany"
    / "vg250-ew_12-31.utm32s.gpkg.ebenen.zip"
)
VG250_INNER = (
    "vg250-ew_12-31.utm32s.gpkg.ebenen/"
    "vg250-ew_ebenen_1231/DE_VG250.gpkg"
)

# RegioStaR-7 reference for per-Raumtyp diagnostics. Filename pinned via
# scripts/download_regiostar.py (TASK-004); not regenerated here.
REGIOSTAR_XLSX = (
    REPO_ROOT / "eqasim-data" / "data" / "regiostar"
    / "regiostar_referenzdatei.xlsx"
)
REGIOSTAR_SHEET = "ReferenzGebietsstand2020"
REGIOSTAR7_LABELS: dict[int, str] = {
    71: "Metropole",
    72: "Regiopole/Großstadt",
    73: "Mittelstadt/städt. Raum",
    74: "Kleinstadt/dörfl. Raum",
    75: "Zentrale Stadt (ländlich)",
    76: "Mittelstadt (ländlich)",
    77: "Kleinstadt/dörfl. (ländlich)",
}


def load_kreise(homes_crs: Any) -> gpd.GeoDataFrame:
    """Load ZGB-8 Kreis polygons keyed by 5-digit ARS (``ars5``).

    VG250 ``ARS`` is 12 digits; ``ars5`` is the first 5 digits (Land(2) +
    Kreis(3)), which is the standard 5-digit Kreiskennziffer used throughout
    the pipeline.  Geometries are dissolved to one polygon per Kreis,
    reprojected to ``homes_crs``, and a ``kreis_name`` column is added from
    the :data:`ZGB8` display-name map.
    """
    if not VG250_ZIP.exists():
        raise FileNotFoundError(
            f"VG250 archive missing: {VG250_ZIP}.  Re-run the synpp data download."
        )
    with zipfile.ZipFile(VG250_ZIP) as z, z.open(VG250_INNER) as fh:
        vg = gpd.read_file(fh, layer="vg250_gem")
    vg["ars5"] = vg["ARS"].astype(str).str[:5]
    kreise = (
        vg[vg["ars5"].isin(ZGB8)][["ars5", "geometry"]]
        .dissolve(by="ars5", as_index=False)
        .to_crs(homes_crs)
    )
    kreise["kreis_name"] = kreise["ars5"].map(ZGB8)
    return kreise


def load_gemeinden(homes_crs: Any) -> gpd.GeoDataFrame:
    """Load ZGB-8 Gemeinde polygons keyed by 8-digit AGS (commune_id).

    VG250 ``ARS`` is 12 digits (Land(2) + RB(1) + Kreis(2) + VG(4) + Gem(3)).
    The 8-digit AGS used by the RegioStaR reference and downstream stages
    is ``ARS[0:5] + ARS[9:12]`` (Kreis prefix + 3-digit Gemeinde number).
    """
    if not VG250_ZIP.exists():
        raise FileNotFoundError(
            f"VG250 archive missing: {VG250_ZIP}.  Re-run the synpp data download."
        )
    with zipfile.ZipFile(VG250_ZIP) as z, z.open(VG250_INNER) as fh:
        vg = gpd.read_file(fh, layer="vg250_gem")
    ars = vg["ARS"].astype(str).str.zfill(12)
    vg["commune_id"] = ars.str[:5] + ars.str[9:12]
    vg["ars5"] = vg["commune_id"].str[:5]
    gem = (
        vg[vg["ars5"].isin(ZGB8)][["commune_id", "geometry"]]
        .dissolve(by="commune_id", as_index=False)
        .to_crs(homes_crs)
    )
    return gem


def load_regiostar() -> pd.DataFrame:
    """Load the RegioStaR-7 reference (commune_id -> regiostar7).

    Returns an empty frame with the expected schema if the source file is
    missing -- RS7 diagnostics will then be silently skipped instead of
    failing the whole validation run.
    """
    if not REGIOSTAR_XLSX.exists():
        LOGGER.warning(
            "RegioStaR reference missing (%s); RS7 breakdowns will be skipped.",
            REGIOSTAR_XLSX,
        )
        return pd.DataFrame(columns=["commune_id", "regiostar7"])
    raw = pd.read_excel(REGIOSTAR_XLSX, sheet_name=REGIOSTAR_SHEET, header=0)
    df = pd.DataFrame({
        "commune_id": raw["gem_20"].astype("Int64").astype(str).str.zfill(8),
        "regiostar7": pd.to_numeric(raw["RegioStaR7"], errors="coerce")
                       .astype("Int64"),
    }).dropna(subset=["regiostar7"])
    df["regiostar7"] = df["regiostar7"].astype(int)
    df["rs7_label"] = df["regiostar7"].map(REGIOSTAR7_LABELS).fillna("unknown")
    return df


def assign_geographies(
    homes: gpd.GeoDataFrame,
    kreise: gpd.GeoDataFrame | None = None,
) -> gpd.GeoDataFrame:
    """Attach ars5 (Kreis), commune_id (8-digit AGS) and regiostar7 to each home.

    One row per household_id. Logs the share of homes that could not be matched
    to a Kreis / Gemeinde (no silent fallback: a high unmatched rate is a bug
    signal, see CLAUDE.md 'Fallback transparency').

    Parameters
    ----------
    homes:
        GeoDataFrame of home points; must contain ``household_id``.
    kreise:
        Optional pre-loaded Kreis polygons as returned by :func:`load_kreise`.
        Pass an already-loaded frame to avoid re-reading VG250 when the caller
        has loaded it for another purpose (e.g. map plotting).  When ``None``
        the frame is loaded from VG250 automatically.
    """
    if kreise is None:
        kreise = load_kreise(homes.crs)
    homes_kreis = gpd.sjoin(
        homes, kreise[["ars5", "kreis_name", "geometry"]],
        how="left", predicate="within",
    ).drop(columns="index_right")

    regiostar = load_regiostar()
    if not regiostar.empty:
        gemeinden = load_gemeinden(homes.crs)
        homes_gem = gpd.sjoin(
            homes, gemeinden[["commune_id", "geometry"]],
            how="left", predicate="within",
        ).drop(columns="index_right")
        rs7_per_household = (
            homes_gem[["household_id", "commune_id"]]
            .merge(regiostar[["commune_id", "regiostar7", "rs7_label"]],
                   on="commune_id", how="left")
            .drop_duplicates("household_id")
        )
        homes_kreis = homes_kreis.merge(
            rs7_per_household[["household_id", "commune_id", "regiostar7", "rs7_label"]],
            on="household_id", how="left",
        )
    else:
        homes_kreis["commune_id"] = pd.NA
        homes_kreis["regiostar7"] = pd.NA
        homes_kreis["rs7_label"] = pd.NA

    n = len(homes_kreis)
    n_no_kreis = int(homes_kreis["ars5"].isna().sum())
    n_no_gem = int(homes_kreis["commune_id"].isna().sum())
    LOGGER.info(
        "assign_geographies: %d homes; Kreis matched %d (%.2f%%), unmatched %d; "
        "Gemeinde unmatched %d",
        n, n - n_no_kreis, 100.0 * (n - n_no_kreis) / max(n, 1), n_no_kreis, n_no_gem,
    )
    if n and n_no_kreis / n > 0.02:
        LOGGER.warning(
            "assign_geographies: %.2f%% of homes have no Kreis match -- check the "
            "home CRS vs VG250 (expected EPSG:25832).", 100.0 * n_no_kreis / n,
        )
    return homes_kreis
