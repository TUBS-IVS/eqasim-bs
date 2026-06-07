"""Schema-validated loaders for the committed KBA / MiD fleet reference CSVs.

The derived CSVs live under ``<data_path>/braunschweig/kba/derived/`` and are
produced by ``scripts/extract_kba_fleet.py`` (the only supported way to update
them; hand-editing is part of the provenance trail but re-running the extractor
is preferred). This module is the single read path for the downstream fleet
synthesis (segment IPF, per-vehicle generative chain, HBEFA mapping), mirroring
the role of ``braunschweig.data.mid.reference_tables`` for the per-person MiD
constraints.

Every loader:
  * reads its CSV (tolerating ``# ...`` comment header lines);
  * validates that the required columns are present (clear ``RuntimeError`` on a
    missing/renamed column -- schema drift must never load silently);
  * validates that the categorical labels (segment / powertrain / status / euro
    class / age band) are a subset of the canonical sets defined here, which are
    kept identical to the labels written by the extraction script;
  * for the per-Kreis tables, validates that all 8 ZGB Kreise are present as
    ``"03" + Kreis3`` AGS-5 string codes (consistent with
    ``braunschweig.data.bbsr.regiostar.ars_to_ags8``).

The canonical label constants are duplicated from the extraction script on
purpose: the readers must fail loudly if a future extractor run drifts away from
the agreed vocabulary, so the two definitions act as a mutual contract.
"""

from __future__ import annotations

import logging
import os
from typing import Iterable

import pandas as pd

logger = logging.getLogger(__name__)

# Subdirectory inside the synpp ``data_path`` where the derived CSVs live.
KBA_SUBDIR = os.path.join("braunschweig", "kba", "derived")

# --------------------------------------------------------------------------- #
# Canonical label sets (kept identical to scripts/extract_kba_fleet.py)
# --------------------------------------------------------------------------- #
#: Canonical powertrain labels used everywhere downstream.
POWERTRAIN_LABELS: tuple[str, ...] = (
    "petrol", "diesel", "gas", "bev", "phev", "hybrid", "hydrogen", "other",
)

#: Canonical 5-class MiD economic-status labels, ordered low -> high.
STATUS_LABELS: tuple[str, ...] = (
    "very_low", "low", "medium", "high", "very_high",
)

#: Canonical snake_case segment labels (stable across KBA + MiD). KBA has the
#: extra ``wohnmobile`` segment that MiD does not report; both are listed so the
#: same set validates every table.
SEGMENT_LABELS: tuple[str, ...] = (
    "minis", "kleinwagen", "kompaktklasse", "mittelklasse", "obere_mittelklasse",
    "oberklasse", "suv", "gelaendewagen", "sportwagen", "mini_vans",
    "grossraum_vans", "utilities", "wohnmobile", "sonstige",
)

#: Canonical Euro-class labels (FZ 27.4; ``other`` = Sonstige residual).
EURO_CLASS_LABELS: tuple[str, ...] = (
    "euro1", "euro2", "euro3", "euro4", "euro5", "euro6", "other",
)

#: Canonical vehicle-age band labels (FZ 27.7).
AGE_BAND_LABELS: tuple[str, ...] = (
    "under_5", "5_to_9", "10_to_14", "15_to_19", "20_to_24", "25_to_29",
    "30_plus",
)

#: The 8 ZGB Kreise as AGS-5 ("03" + Kreis3 == KBA Kennziffer).
ZGB_KREISE_AGS5: tuple[str, ...] = (
    "03101", "03102", "03103", "03151", "03153", "03154", "03157", "03158",
)

#: Niedersachsen is the base region for the income<->segment coupling.
BUNDESLAND_NIEDERSACHSEN = "Niedersachsen"

#: RegioStaR-7 code (71..77) -> MiD raumtyp region name (as written in the
#: ``mid2023_segment_by_status_raumtyp.csv`` region column). The MiD
#: "zusammengefasster Raumtyp (7 Kategorien)" is the BMV/BBSR RegioStaR-7
#: typology used by ``braunschweig.data.bbsr.regiostar``, so a per-home RS7 code
#: maps 1:1 onto the raumtyp columns.
RS7_TO_RAUMTYP_REGION: dict[int, str] = {
    71: "Stadtregion - Metropole",
    72: "Stadtregion - Regiopole und Grossstadt",
    73: "Stadtregion - Mittelstadt, staedtischer Raum",
    74: "Stadtregion - kleinstaedtischer, doerflicher Raum",
    75: "laendliche Region - zentrale Stadt",
    76: "laendliche Region - Mittelstadt, staedtischer Raum",
    77: "laendliche Region - kleinstaedtischer, doerflicher Raum",
}


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #
def _path(data_path: str, filename: str) -> str:
    return os.path.join(data_path, KBA_SUBDIR, filename)


def _read(data_path: str, filename: str) -> pd.DataFrame:
    """Read a derived CSV, tolerating ``# ...`` comment header lines.

    Kreis-code columns are read as strings to preserve the leading zero of the
    ``"03..."`` AGS-5 codes.
    """
    path = _path(data_path, filename)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"KBA fleet table not found: {path} (run scripts/extract_kba_fleet.py "
            f"to (re)generate the derived CSVs)."
        )
    dtype = {"kreis_ags5": str}
    return pd.read_csv(path, comment="#", dtype=dtype)


def _require_columns(df: pd.DataFrame, expected: Iterable[str], filename: str) -> None:
    missing = set(expected) - set(df.columns)
    if missing:
        raise RuntimeError(
            f"{filename}: missing columns {sorted(missing)} "
            f"(have {sorted(df.columns)}) -- schema drift; re-run "
            f"scripts/extract_kba_fleet.py."
        )


def _require_labels(values: Iterable[str], allowed: Iterable[str],
                    dimension: str, filename: str) -> None:
    unexpected = set(values) - set(allowed)
    if unexpected:
        raise RuntimeError(
            f"{filename}: unexpected {dimension} labels {sorted(unexpected)} "
            f"(allowed {sorted(allowed)})."
        )


def _require_zgb_kreise(values: Iterable[str], filename: str) -> None:
    codes = set(values)
    missing = set(ZGB_KREISE_AGS5) - codes
    if missing:
        raise RuntimeError(
            f"{filename}: missing ZGB Kreise {sorted(missing)} "
            f"(expected all of {sorted(ZGB_KREISE_AGS5)})."
        )
    extra = codes - set(ZGB_KREISE_AGS5)
    if extra:
        raise RuntimeError(
            f"{filename}: unexpected non-ZGB Kreis codes {sorted(extra)}."
        )


# --------------------------------------------------------------------------- #
# Loaders -- one per derived CSV
# --------------------------------------------------------------------------- #
def load_segment_powertrain(data_path: str) -> pd.DataFrame:
    """FZ 27.10: per-segment totals + BEV/PHEV/hybrid/gas/hydrogen + shares.

    Carries the national KBA **segment marginal** (``segment_share``) used as the
    exact target of the segment IPF, plus the per-segment alternative-drive
    counts/shares used for ``P(powertrain | segment)``.
    """
    filename = "kba_segment_powertrain.csv"
    df = _read(data_path, filename)
    _require_columns(
        df,
        ["segment", "total", "bev", "phev", "hybrid", "gas", "hydrogen",
         "segment_share"],
        filename,
    )
    _require_labels(df["segment"], SEGMENT_LABELS, "segment", filename)
    return df


def load_kreis_powertrain(data_path: str) -> pd.DataFrame:
    """FZ 27.15: per-Kreis total + alternative-drive + BEV/PHEV/hybrid/gas.

    The per-Kreis electric (BEV/PHEV) shares are the calibration target for the
    powertrain raking. Validated to carry all 8 ZGB Kreise as AGS-5 codes.
    """
    filename = "kba_kreis_powertrain.csv"
    df = _read(data_path, filename)
    _require_columns(
        df,
        ["kreis_ags5", "kreis_name", "total", "alt_total", "bev", "phev",
         "hybrid", "gas", "bev_share", "phev_share", "alt_share"],
        filename,
    )
    _require_zgb_kreise(df["kreis_ags5"], filename)
    return df


def load_gemeinde_private_bev(data_path: str) -> pd.DataFrame:
    """FZ 27.17: per-Gemeinde private car total + private BEV/PHEV (+ shares).

    Used as the within-Kreis Gemeinde tilt on the private electric share. Every
    Gemeinde row's ``kreis_ags5`` must be one of the 8 ZGB Kreise.
    """
    filename = "kba_gemeinde_private_bev.csv"
    df = _read(data_path, filename)
    _require_columns(
        df,
        ["kreis_ags5", "kreis_name", "gemeinde", "private_total", "private_bev",
         "private_phev", "private_bev_share", "private_phev_share"],
        filename,
    )
    _require_zgb_kreise(df["kreis_ags5"], filename)
    return df


def load_fuel_euro_nds(data_path: str) -> pd.DataFrame:
    """FZ 27.4 (Niedersachsen): fuel x Euro-class counts + within-fuel shares.

    Provides ``P(Euro | powertrain)`` for the NDS scope.
    """
    filename = "kba_fuel_euro_nds.csv"
    df = _read(data_path, filename)
    _require_columns(df, ["fuel", "euro_class", "count", "share"], filename)
    _require_labels(df["fuel"], POWERTRAIN_LABELS, "fuel", filename)
    _require_labels(df["euro_class"], EURO_CLASS_LABELS, "euro_class", filename)
    return df


def load_age_fuel(data_path: str) -> pd.DataFrame:
    """FZ 27.7: vehicle-age band x fuel (Pkw column) + within-fuel shares.

    Provides ``P(age | powertrain)``.
    """
    filename = "kba_age_fuel.csv"
    df = _read(data_path, filename)
    _require_columns(df, ["age_band", "fuel", "pkw_count", "share"], filename)
    _require_labels(df["fuel"], POWERTRAIN_LABELS, "fuel", filename)
    _require_labels(df["age_band"], AGE_BAND_LABELS, "age_band", filename)
    return df


def load_brand_powertrain(data_path: str) -> pd.DataFrame:
    """FZ 27.11: per-brand totals + BEV/PHEV/hybrid/gas + brand share.

    Provides ``P(brand | powertrain)`` for the additive brand/model attributes.
    """
    filename = "kba_brand_powertrain.csv"
    df = _read(data_path, filename)
    _require_columns(
        df, ["brand", "total", "bev", "phev", "hybrid", "gas", "brand_share"],
        filename,
    )
    return df


def load_segment_model(data_path: str) -> pd.DataFrame:
    """FZ 12.1: per-segment model (Modellreihe) counts + within-segment share.

    Provides ``P(model | segment)`` (model implies brand) for the additive
    brand/model attributes.
    """
    filename = "kba_segment_model.csv"
    df = _read(data_path, filename)
    _require_columns(df, ["segment", "model", "count", "share"], filename)
    _require_labels(df["segment"], SEGMENT_LABELS, "segment", filename)
    return df


def load_mid_segment_by_status_bundesland(data_path: str) -> pd.DataFrame:
    """MiD 2023 segment x economic status, by Bundesland (column-%).

    ``share_pct`` is the column percentage P(status | segment, Bundesland);
    ``base_weighted`` is the per-(segment, region) weighted base. Niedersachsen
    is the base region for the income<->segment coupling.
    """
    filename = "mid2023_segment_by_status_bundesland.csv"
    df = _read(data_path, filename)
    _require_columns(
        df, ["region", "segment", "status", "share_pct", "base_weighted"],
        filename,
    )
    _require_labels(df["segment"], SEGMENT_LABELS, "segment", filename)
    _require_labels(df["status"], STATUS_LABELS, "status", filename)
    if BUNDESLAND_NIEDERSACHSEN not in set(df["region"]):
        raise RuntimeError(
            f"{filename}: base region '{BUNDESLAND_NIEDERSACHSEN}' missing "
            f"(have {sorted(df['region'].unique())})."
        )
    return df


def load_mid_segment_by_status_raumtyp(data_path: str) -> pd.DataFrame:
    """MiD 2023 segment x economic status, by RegioStaR-7 Raumtyp (column-%).

    Used as a within-NDS urban/rural tilt on the income<->segment coupling.
    Validated to carry all 7 RegioStaR-7 raumtyp regions.
    """
    filename = "mid2023_segment_by_status_raumtyp.csv"
    df = _read(data_path, filename)
    _require_columns(
        df, ["region", "segment", "status", "share_pct", "base_weighted"],
        filename,
    )
    _require_labels(df["segment"], SEGMENT_LABELS, "segment", filename)
    _require_labels(df["status"], STATUS_LABELS, "status", filename)
    expected_regions = set(RS7_TO_RAUMTYP_REGION.values())
    missing = expected_regions - set(df["region"])
    if missing:
        raise RuntimeError(
            f"{filename}: missing raumtyp regions {sorted(missing)}."
        )
    return df
