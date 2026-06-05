"""Loaders for the in-commuter mode-by-distance reference (MiD/Mikrozensus).

See docs/superpowers/specs/2026-06-03-incommuter-mode-reference-design.md and
eqasim-data/data/braunschweig/mikrozensus/README.md for provenance.
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

from braunschweig.data.cordon.mode_reference import mode_reference_from_table, rake_to_margins

MZ_SUBDIR = os.path.join("braunschweig", "mikrozensus")


def _path(data_path: str, filename: str) -> str:
    """Resolve a reference CSV. Normally under ``<data_path>/braunschweig/mikrozensus/``;
    falls back to ``<data_path>/<filename>`` so tests can pass a flat directory."""
    nested = os.path.join(data_path, MZ_SUBDIR, filename)
    flat = os.path.join(data_path, filename)
    return nested if os.path.exists(nested) else flat


def _read_csv(path: str) -> pd.DataFrame:
    return pd.read_csv(path, comment="#")


def load_commute_mode_by_distance(data_path: str):
    """Nested ``{distance_band: {mode: probability}}`` from the committed national
    rbW mode-by-distance CSV (renormalised per band by ``mode_reference_from_table``)."""
    df = _read_csv(_path(data_path, "mid2023_commute_mode_by_distance_de.csv"))
    return mode_reference_from_table(df)


# NDS coarse distance band -> the MiD fine bands it contains. The 10-25 / 25-50
# NDS bands overlap the MiD 20-50 band; documented approximation, see the design
# spec section 3.2.
_COARSE_TO_MID = {
    "<5": ["<0.5", "0.5-1", "1-2", "2-5"],
    "5-10": ["5-10"],
    "10-25": ["10-20", "20-50"],
    "25-50": ["20-50"],
    "50+": ["50-100", ">=100"],
}


def nds_mode_margin(bus, tram, bahn, pkw, fahrrad, zu_fuss):
    """NDS commute mode margin (percent inputs) mapped to eqasim modes and
    renormalised to sum 1 over {walk, bicycle, car, pt}. ``sonstige`` is dropped.
    Bus + U-/Strassenbahn + Eisenbahn/S-Bahn -> pt; PKW -> car; Fahrrad ->
    bicycle; zu Fuss -> walk."""
    raw = {"pt": bus + tram + bahn, "car": pkw, "bicycle": fahrrad, "walk": zu_fuss}
    total = sum(raw.values())
    return {m: v / total for m, v in raw.items()}


def nds_distance_margin_on_mid_bands(coarse_shares, national_distance):
    """Distribute NDS coarse distance shares onto the 9 MiD bands proportional to
    the national distance distribution within each coarse band. ``coarse_shares``
    maps NDS band -> share; ``national_distance`` is a DataFrame[distance_band,
    share] (national). Returns ``{mid_band: share}`` summing to 1."""
    nat = dict(zip(national_distance["distance_band"], national_distance["share"]))
    out = {}
    for coarse, share in coarse_shares.items():
        mids = _COARSE_TO_MID[coarse]
        wsum = sum(nat.get(b, 0.0) for b in mids) or 1.0
        for b in mids:
            out[b] = out.get(b, 0.0) + share * nat.get(b, 0.0) / wsum
    s = sum(out.values()) or 1.0
    return {b: v / s for b, v in out.items()}


def build_incommuter_mode_reference(national_reference, national_distance,
                                    nds_distance_on_mid, nds_mode):
    """Anchor the national mode-by-distance joint to the NDS margins via IPF.

    ``national_reference`` = {band: {mode: prob}} (national rbW conditional).
    ``national_distance`` = DataFrame[distance_band, share] (national band weights).
    ``nds_distance_on_mid`` = {band: share}; ``nds_mode`` = {mode: share}.
    Returns the NDS-anchored ``{band: {mode: prob}}`` (each band renormalised)."""
    bands = list(national_reference.keys())
    modes = sorted({m for d in national_reference.values() for m in d})
    nat_dist = dict(zip(national_distance["distance_band"], national_distance["share"]))
    X = np.array([[nds_distance_on_mid.get(b, nat_dist.get(b, 0.0)) * national_reference[b].get(m, 0.0)
                   for m in modes] for b in bands], dtype=float)
    row_margin = np.array([nds_distance_on_mid.get(b, nat_dist.get(b, 0.0)) for b in bands])
    row_margin = row_margin / row_margin.sum()
    col_margin = np.array([nds_mode.get(m, 0.0) for m in modes])
    col_margin = col_margin / col_margin.sum()
    fitted = rake_to_margins(X, row_margin, col_margin)
    out = {}
    for i, b in enumerate(bands):
        total = fitted[i].sum() or 1.0
        out[b] = {m: fitted[i][j] / total for j, m in enumerate(modes)}
    return out


def load_national_distance(data_path: str) -> pd.DataFrame:
    """National commute distance distribution as DataFrame[distance_band, share].

    Reads from the committed MiT distance CSV (``mid2023_commute_distance_de.csv``).
    Used downstream to split coarse Bundesland distance margins onto the fine MiD
    distance bands via ``nds_distance_margin_on_mid_bands``."""
    df = _read_csv(_path(data_path, "mid2023_commute_distance_de.csv"))
    return df[["distance_band", "share"]].copy()


def load_bundesland_mode_margins(data_path: str) -> dict:
    """``{bundesland: {mode: share}}`` from the per-Bundesland mode-margin CSV.

    Reads ``mid_mode_margin_by_bundesland.csv`` (columns: bundesland, mode, share).
    Shares are taken as-is from the CSV; renormalisation happens inside
    ``build_incommuter_mode_reference``."""
    df = _read_csv(_path(data_path, "mid_mode_margin_by_bundesland.csv"))
    out = {}
    for bl, g in df.groupby("bundesland"):
        out[bl] = dict(zip(g["mode"], g["share"]))
    return out


def load_bundesland_distance_margins(data_path: str) -> dict:
    """``{bundesland: {coarse_band: share}}`` from the per-Bundesland distance CSV.

    Reads ``mid_distance_margin_by_bundesland.csv`` (columns: bundesland,
    coarse_band, share). Coarse bands match the keys of ``_COARSE_TO_MID``."""
    df = _read_csv(_path(data_path, "mid_distance_margin_by_bundesland.csv"))
    out = {}
    for bl, g in df.groupby("bundesland"):
        out[bl] = dict(zip(g["coarse_band"], g["share"]))
    return out


def load_pt_entry_stops(data_path):
    """``{source_ars5: [(x, y), ...]}`` direct-ride PT entry stops (EPSG:25832)
    from the committed CSV, for placing PT in-commuter homes near a station with
    a one-seat ride to ZGB. ARS kept as 5-digit strings (leading zeros)."""
    df = pd.read_csv(_path(data_path, "incommuter_pt_entry_stops.csv"), comment="#",
                     dtype={"source_ars5": str})
    out = {}
    for ars5, g in df.groupby("source_ars5"):
        out[str(ars5).zfill(5)] = list(zip(g["x"].astype(float), g["y"].astype(float)))
    return out


def build_mode_reference_by_bundesland(data_path: str, bundeslaender=None) -> dict:
    """Build the per-Bundesland-anchored mode reference for each Bundesland.

    Returns ``{bundesland: {distance_band: {mode: probability}}}``. The national
    rbW joint (Mikrozensus / MiT) is anchored via IPF to each Bundesland's mode
    and distance margins (Mikrozensus 12251-0105 / 12251-0106).

    ``bundeslaender`` optionally restricts to a subset of Bundeslaender (e.g. only
    those present among the in-commuter source Kreise). Bundeslaender missing from
    either margin CSV are silently skipped."""
    national_ref = load_commute_mode_by_distance(data_path)
    national_dist = load_national_distance(data_path)
    mode_margins = load_bundesland_mode_margins(data_path)
    dist_margins = load_bundesland_distance_margins(data_path)
    names = bundeslaender if bundeslaender is not None else sorted(mode_margins)
    out = {}
    for bl in names:
        if bl not in mode_margins or bl not in dist_margins:
            continue
        dist_on_mid = nds_distance_margin_on_mid_bands(dist_margins[bl], national_dist)
        out[bl] = build_incommuter_mode_reference(
            national_ref, national_dist, dist_on_mid, mode_margins[bl])
    return out
