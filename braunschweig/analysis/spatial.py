"""Shared spatial helpers for the Braunschweig analysis modules.

Exposes the ZGB-8 Kreise map, VG250 / RegioStaR path constants, and loaders
that attach Kreis, commune_id and RegioStaR-7 to home points.  Extracted from
``braunschweig.analysis.run_mid_validation`` so multiple analysis modules can
share the same definitions without duplication.

This module is also the single owner of VG250 archive access (issue #293):
before this, ``braunschweig.analysis.spatial`` and
``braunschweig.analysis.dashboard.spatial_metrics`` each located the archive,
extracted it and read ``DE_VG250.gpkg`` independently, with different
caching strategies (re-read-per-call vs. extract-once) and -- the part that
mattered scientifically -- different opinions on what a missing archive
means (one raised, the other returned ``None`` with no log line). Both now
go through :func:`load_vg250_layer` / :func:`_resolve_vg250_gpkg` below:

- The **strict** analysis/validation path (this module's ``load_kreise`` and
  ``load_gemeinden``) raises ``FileNotFoundError`` naming the expected path.
  A missing per-Kreis geography there would silently invalidate the whole
  validation run, so failing loudly is the only defensible behaviour.
- The **tolerant** dashboard path (``dashboard.spatial_metrics._ensure_vg250``)
  returns ``None`` so the rest of the dashboard still renders without the
  per-Kreis panel -- but it now logs an explicit ``warning`` naming the
  missing archive and the metrics that will be omitted, so the gap is never
  silent (see CLAUDE.md "Fallback transparency"). A one-shot warning is
  correct here, not a fallback rate: this is a single input either present
  or absent, not a per-item primary/fallback split.

Both callers also now share one caching decision: the archive is extracted
once into :data:`VG250_CACHE` and re-extracted only if the cache is older
than the source zip (so a data refresh in ``eqasim-data`` can not silently
keep serving stale cached content, which was a risk of the dashboard's
former extract-once-forever cache).
"""

from __future__ import annotations

import logging
import os
import shutil
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

# Shared extract-once cache for the VG250 GeoPackage (gitignored via the
# repo-wide "**/.cache/" rule). Both the strict and tolerant callers read
# through this single location; see the module docstring for why.
VG250_CACHE = REPO_ROOT / ".cache" / "vg250" / "DE_VG250.gpkg"

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


def _resolve_vg250_gpkg(*, strict: bool) -> Path | None:
    """Return a local path to the extracted ``DE_VG250.gpkg``, the single
    place that decides what a missing VG250 archive means.

    Extracts the archive into :data:`VG250_CACHE` once and re-uses that copy
    on subsequent calls, re-extracting only if the cache predates the source
    zip's modification time (so a data refresh is picked up automatically
    instead of requiring a manual cache clear).

    Parameters
    ----------
    strict:
        ``True`` (the analysis/validation callers): raise
        ``FileNotFoundError`` naming the expected archive path when it is
        missing. A missing per-Kreis geography would silently invalidate the
        whole run, so failing loudly is the only defensible behaviour there.

        ``False`` (the dashboard caller): log an explicit ``warning`` naming
        the missing archive and the metrics that will be omitted, then
        return ``None`` so the rest of the dashboard still renders. This is
        a single input either present or absent -- a one-shot warning is the
        right instrument, not a fallback rate (see CLAUDE.md "Fallback
        transparency").
    """
    if not VG250_ZIP.exists():
        if strict:
            raise FileNotFoundError(
                f"VG250 archive missing: {VG250_ZIP}.  Re-run the synpp data download."
            )
        LOGGER.warning(
            "VG250 archive not found at %s; per-Kreis dashboard metrics "
            "(Kreis polygons, per-Kreis mode share/km, OD matrix) will be "
            "omitted for this run.  Re-run the synpp data download to restore it.",
            VG250_ZIP,
        )
        return None
    if not VG250_CACHE.exists() or VG250_CACHE.stat().st_mtime < VG250_ZIP.stat().st_mtime:
        VG250_CACHE.parent.mkdir(parents=True, exist_ok=True)
        # Extract to a unique temporary file in the SAME directory and then
        # os.replace() it into place: that call is atomic on one filesystem, so
        # a concurrent reader either sees the previous complete copy or the new
        # complete one, never a half-written gpkg. Writing VG250_CACHE directly
        # would also leave a truncated file with a NEWER mtime than the zip if
        # the process died mid-write -- the freshness check below would then
        # accept the corrupt cache forever instead of re-extracting it.
        temporary_path = VG250_CACHE.with_name(f"{VG250_CACHE.name}.{os.getpid()}.part")
        try:
            with zipfile.ZipFile(VG250_ZIP) as z, z.open(VG250_INNER) as src:
                with open(temporary_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)
            os.replace(temporary_path, VG250_CACHE)
        finally:
            temporary_path.unlink(missing_ok=True)
    return VG250_CACHE


def load_vg250_layer(layer: str, *, strict: bool = True) -> gpd.GeoDataFrame | None:
    """Read one VG250 layer (e.g. ``"vg250_gem"``, ``"vg250_krs"``) through
    the shared extract-once cache.

    See :func:`_resolve_vg250_gpkg` for the ``strict``/tolerant failure-mode
    contract. Returns ``None`` only when ``strict=False`` and the archive is
    absent; with ``strict=True`` (the default) a missing archive always
    raises, so callers that pass the default never see ``None``.
    """
    path = _resolve_vg250_gpkg(strict=strict)
    if path is None:
        return None
    return gpd.read_file(path, layer=layer)


def load_kreise(homes_crs: Any) -> gpd.GeoDataFrame:
    """Load ZGB-8 Kreis polygons keyed by 5-digit ARS (``ars5``).

    VG250 ``ARS`` is 12 digits; ``ars5`` is the first 5 digits (Land(2) +
    Kreis(3)), which is the standard 5-digit Kreiskennziffer used throughout
    the pipeline.  Geometries are dissolved to one polygon per Kreis,
    reprojected to ``homes_crs``, and a ``kreis_name`` column is added from
    the :data:`ZGB8` display-name map.
    """
    vg = load_vg250_layer("vg250_gem", strict=True)
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
    vg = load_vg250_layer("vg250_gem", strict=True)
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
