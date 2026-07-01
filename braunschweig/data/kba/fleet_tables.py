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

#: National Pkw age band labels (Statista KBA ID 3438, 6-band scheme).
#: These are DIFFERENT from the FZ 27.7 age bands: they use a coarser grouping
#: intended for validation of the realised fleet age distribution, not for IPF.
AGE_NATIONAL_BAND_LABELS: tuple[str, ...] = (
    "under_2", "2_to_4", "5_to_9", "10_to_14", "15_to_29", "30_plus",
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


def load_mid_age_by_segment_status(data_path: str) -> pd.DataFrame:
    """MiD 2023 vehicle-age band x segment x economic status.

    ``share`` is P(age_band | segment, status); within each (segment, status)
    cell the shares sum to 1.0.  ``base_weighted`` is the weighted vehicle base
    of the (segment, status) cell used to produce the age distribution.

    Produced by ``scripts/build_mid_age_by_segment_status.py`` from the raw
    MiD 2023 Autos micro-data (``MiD2023_Autos.csv``).
    """
    filename = "mid2023_age_by_segment_status.csv"
    df = _read(data_path, filename)
    _require_columns(
        df, ["segment", "status", "age_band", "share", "base_weighted"],
        filename,
    )
    _require_labels(df["segment"], SEGMENT_LABELS, "segment", filename)
    _require_labels(df["status"], STATUS_LABELS, "status", filename)
    _require_labels(df["age_band"], AGE_BAND_LABELS, "age_band", filename)
    return df


def load_kreis_fuel(data_path: str) -> pd.DataFrame:
    """Regionalstatistik 46251-02: per-Kreis fuel counts + within-Kreis shares.

    Real per-Kreis powertrain marginal (petrol/diesel/gas/bev/phev/hybrid/other),
    Stichtag 01.01.2025. Supersedes the FZ 27.15 NDS petrol:diesel split for the
    per-Kreis powertrain rake. Validated to carry all 8 ZGB Kreise.
    """
    filename = "kba_kreis_fuel.csv"
    df = _read(data_path, filename)
    _require_columns(
        df, ["kreis_ags5", "kreis_name", "stichtag", "petrol", "diesel", "gas",
             "bev", "phev", "hybrid", "other", "total"], filename)
    _require_zgb_kreise(df["kreis_ags5"], filename)
    return df


def load_kreis_euro(data_path: str) -> pd.DataFrame:
    """Regionalstatistik 46251-03: per-Kreis Euro-group counts + shares.

    ``teil`` is ``all`` (all fuels) or ``diesel``; euro columns are euro1..euro6 +
    other. Provides the per-Kreis Euro marginal, Stichtag 01.01.2025. Validated to
    carry all 8 ZGB Kreise and the canonical Euro labels.
    """
    filename = "kba_kreis_euro.csv"
    df = _read(data_path, filename)
    _require_columns(
        df, ["kreis_ags5", "kreis_name", "stichtag", "teil",
             "euro1", "euro2", "euro3", "euro4", "euro5", "euro6", "other",
             "total"], filename)
    _require_zgb_kreise(df.loc[df["teil"] == "all", "kreis_ags5"], filename)
    unexpected = set(df["teil"]) - {"all", "diesel"}
    if unexpected:
        raise RuntimeError(f"{filename}: unexpected teil values {sorted(unexpected)}.")
    return df


def load_age_national(data_path: str) -> pd.DataFrame:
    """KBA/Statista ID 3438: national Pkw age distribution (VALIDATION control).

    Reads ``kba_age_national.csv`` (produced by ``scripts/extract_kba_fleet.py``)
    which carries a ``# mean_age_years=...`` comment header line.  The CSV
    columns are ``year, stichtag, band, share_pct`` for the 6 national age bands.

    This is a committed validation anchor -- never used as an IPF dimension.
    The file comment records ``mean_age_years=10.9`` as stated by the source.

    Args:
        data_path: Root data path (the ``derived/`` subdirectory is resolved
            automatically by ``_read``).

    Returns:
        DataFrame with 6 rows and columns ``year, stichtag, band, share_pct``.

    Raises:
        FileNotFoundError: If the CSV is absent (run
            ``scripts/extract_kba_fleet.py`` on the server to generate it).
        RuntimeError: If required columns are missing or band labels are
            outside the expected set (schema drift).
    """
    filename = "kba_age_national.csv"
    df = _read(data_path, filename)
    _require_columns(df, ["year", "stichtag", "band", "share_pct"], filename)
    _require_labels(df["band"], AGE_NATIONAL_BAND_LABELS, "band", filename)
    return df


def load_gemeinde_ev(data_path: str) -> pd.DataFrame:
    """KBA per-Gemeinde EV shares (Stichtag 2026-04-01), ZGB Gemeinden only.

    Loads ``kba_gemeinde_ev.csv`` produced by
    ``scripts/extract_kba_fleet.py::extract_gemeinde_ev``.  Validates that all
    required columns are present and that every ``kreis_ags5`` belongs to the 8
    ZGB Kreise (no non-ZGB rows, no extra Kreis codes that are not ZGB).

    Columns: ``kreis_ags5, ags8, gemeinde, gemeinde_norm, stichtag,
    ev_share, bev_share, phev_share, fuelcell_share``.

    Note: the loader does **not** require all 8 ZGB Kreise to be present
    (some Kreise may have only one Gemeinde = the Kreisstadt which is already the
    Kreis itself; the per-Gemeinde CSV only covers Gemeinden with sufficient counts).
    It does reject any Kreis code that is **not** one of the 8 ZGB codes.

    Args:
        data_path: Root data path; ``braunschweig/kba/derived/`` is appended
            automatically.

    Returns:
        DataFrame with the nine columns listed above.  ``kreis_ags5`` and ``ags8``
        are string columns (leading zero preserved).

    Raises:
        FileNotFoundError: If ``kba_gemeinde_ev.csv`` is absent.
        RuntimeError: If required columns are missing or any ``kreis_ags5`` is not
            one of the 8 ZGB Kreise.
    """
    filename = "kba_gemeinde_ev.csv"
    df = _read(data_path, filename)
    _require_columns(
        df,
        [
            "kreis_ags5", "ags8", "gemeinde", "gemeinde_norm", "stichtag",
            "ev_share", "bev_share", "phev_share", "fuelcell_share",
        ],
        filename,
    )
    unexpected = set(df["kreis_ags5"]) - set(ZGB_KREISE_AGS5)
    if unexpected:
        raise RuntimeError(
            f"{filename}: unexpected non-ZGB kreis_ags5 codes {sorted(unexpected)} "
            f"(allowed {sorted(ZGB_KREISE_AGS5)})."
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
