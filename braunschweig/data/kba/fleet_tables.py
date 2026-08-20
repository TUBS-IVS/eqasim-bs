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

#: Canonical Euro-6 substage labels (Task B4). Euro-6e (the newest sub-class)
#: is folded into ``euro6d`` at extraction time (see
#: ``scripts/extract_kba_fleet.py::extract_fuel_euro6_substage_nds``), so this
#: set has exactly three members.
EURO6_SUBSTAGE_LABELS: tuple[str, ...] = ("euro6ab", "euro6dtemp", "euro6d")

#: Value of the ``euro6_substage`` output column for every car the substage does
#: NOT apply to: a non-Euro-6 vehicle, an electrified/fuel-cell drivetrain (no
#: combustion Euro stage at all), or a combustion Euro-6 car for which no
#: substage reference data resolved. This is a REAL category, not a missing
#: marker: the emitted fleet carries no NA/None anywhere (ADR-0081 item A4), and
#: a reader can distinguish "substage unknown/not applicable" from a drawn
#: substage without null handling. See ADR-0084.
EURO6_SUBSTAGE_NOT_APPLICABLE: str = "not_applicable"

#: The three additive Euro-6 substage COUNT columns on ``kba_kreis_euro.csv``
#: (Task B4). OPTIONAL for backward compatibility -- see :func:`load_kreis_euro`.
EURO6_SUBSTAGE_COLUMNS: tuple[str, ...] = ("euro6d", "euro6dtemp", "euro6ab")

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

#: Canonical KBA holder-age class labels for the wohnmobile holder-age tilt
#: (issue #315, ADR-0093), young -> old, exactly the classes published on the
#: KBA infographic / PM 23/2025 (Stichtag 2025-04-01).
WOHNMOBILE_AGE_CLASS_LABELS: tuple[str, ...] = (
    "up_to_20", "21_29", "30_39", "40_49", "50_59", "60_69", "70_79", "80_plus",
)

#: Residual row label of the wohnmobile holder-age table: vehicles the KBA
#: source pages attribute to NO age class (share recorded in the committed CSV
#: itself). Kept for traceability; never asserted to be commercial holders
#: (ADR-0093).
WOHNMOBILE_AGE_NOT_ATTRIBUTED: str = "not_attributed"

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
    """Strict ZGB-only check: exactly the 8 ZGB Kreise, no more, no fewer.

    Used by loaders whose source table is genuinely ZGB-scoped (FZ 27.15, FZ
    27.17, the 2026 Gemeinde EV shares): a non-ZGB code there would indicate a
    join or filter bug in the extractor, not real additional coverage. Do NOT
    weaken this helper for Task B3 -- use :func:`_require_zgb_subset` instead
    for tables that now legitimately cover every German Kreis.
    """
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


def _require_zgb_subset(values: Iterable[str], filename: str) -> None:
    """All-Kreise check: the 8 ZGB Kreise MUST be present; extras are ALLOWED.

    Used by the Regionalstatistik 46251 loaders (:func:`load_kreis_fuel`,
    :func:`load_kreis_euro`), whose raw source files cover every German Kreis
    (Task B3): a non-ZGB Kreis code is expected and lets cross-cordon
    in-commuters (who carry their real origin ``kreis_ags5``, see
    ``incommuters._incommuter_kreis_ags5``) draw their true home-Kreis mix. A
    missing ZGB Kreis is still a hard error -- the ZGB region itself must never
    silently lose coverage.
    """
    codes = set(values)
    missing = set(ZGB_KREISE_AGS5) - codes
    if missing:
        raise RuntimeError(
            f"{filename}: missing ZGB Kreise {sorted(missing)} "
            f"(expected all of {sorted(ZGB_KREISE_AGS5)})."
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


def load_fuel_euro6_substage_nds(data_path: str) -> pd.DataFrame:
    """FZ 27.4 (Niedersachsen): Euro-6 substage counts + within-fuel shares.

    Loads ``kba_fuel_euro6_substage_nds.csv`` produced by
    ``scripts/extract_kba_fleet.py::extract_fuel_euro6_substage_nds`` (Task B4).
    Provides ``P(substage | euro6, fuel)`` for the three Euro-6 substages
    (``euro6ab``, ``euro6dtemp``, ``euro6d``; Euro-6e is folded into
    ``euro6d`` at extraction time -- see that function's docstring), intended
    for the HBEFA Euro-6 sub-mapping (Task B5; not wired here).

    Columns: ``fuel, substage, count, share, stichtag``.

    Args:
        data_path: Root data path; ``braunschweig/kba/derived/`` is appended
            automatically.

    Returns:
        DataFrame with the five columns listed above.

    Raises:
        FileNotFoundError: If ``kba_fuel_euro6_substage_nds.csv`` is absent
            (run ``scripts/extract_kba_fleet.py`` on the server to generate it).
        RuntimeError: If required columns are missing, or any ``fuel``/
            ``substage`` label is outside the canonical sets (schema drift).
    """
    filename = "kba_fuel_euro6_substage_nds.csv"
    df = _read(data_path, filename)
    _require_columns(df, ["fuel", "substage", "count", "share", "stichtag"], filename)
    _require_labels(df["fuel"], POWERTRAIN_LABELS, "fuel", filename)
    _require_labels(df["substage"], EURO6_SUBSTAGE_LABELS, "substage", filename)
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


def load_model_fuel(data_path: str) -> pd.DataFrame:
    """Modellreihen (2026): per-model fuel-type shares.

    Loads ``kba_model_fuel.csv`` produced by
    ``scripts/extract_kba_fleet.py::extract_model_fuel``.

    Validates that all required columns are present and that every ``segment``
    value is a member of ``SEGMENT_LABELS`` (schema drift is surfaced loudly).

    Columns: ``segment, model, stichtag, petrol_share, diesel_share,
    hybrid_share, phev_share, bev_share``.

    Args:
        data_path: Root data path; ``braunschweig/kba/derived/`` is appended
            automatically.

    Returns:
        DataFrame with the eight columns listed above.

    Raises:
        FileNotFoundError: If ``kba_model_fuel.csv`` is absent (run
            ``scripts/extract_kba_fleet.py`` on the server to generate it).
        RuntimeError: If required columns are missing or any ``segment`` label
            is outside ``SEGMENT_LABELS``.
    """
    filename = "kba_model_fuel.csv"
    df = _read(data_path, filename)
    _require_columns(
        df,
        ["segment", "model", "stichtag", "petrol_share", "diesel_share",
         "hybrid_share", "phev_share", "bev_share"],
        filename,
    )
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


def load_mid_antrieb_by_status(data_path: str) -> pd.DataFrame:
    """MiD 2023 vehicle powertrain (A_ANTRIEB) x economic status (oek_status).

    ``share`` is P(powertrain | status); within each status cell the shares
    sum to 1.0. The ``status == "all"`` row pools every usable row regardless
    of economic status and carries the overall MiD powertrain mix -- the
    denominator used by the EV-income tilt (see the corresponding tilt
    wiring) to compute ``P(powertrain | status) / P(powertrain)``.

    Produced by ``scripts/build_mid_antrieb_by_status.py`` from the raw
    MiD 2023 Autos micro-data (``MiD2023_Autos.csv``).
    """
    filename = "mid2023_antrieb_by_status.csv"
    df = _read(data_path, filename)
    _require_columns(
        df, ["status", "powertrain", "share", "base_weighted"], filename,
    )
    _require_labels(df["powertrain"], POWERTRAIN_LABELS, "powertrain", filename)
    _require_labels(df["status"], (*STATUS_LABELS, "all"), "status", filename)
    missing_status = set(STATUS_LABELS) - set(df["status"])
    if missing_status:
        raise RuntimeError(
            f"{filename}: missing economic status group(s) {sorted(missing_status)} "
            f"(expected all of {sorted(STATUS_LABELS)} plus the pooled 'all' row)."
        )
    if "all" not in set(df["status"]):
        raise RuntimeError(
            f"{filename}: missing the pooled 'all' row (overall MiD powertrain "
            f"mix, required as the EV-income tilt denominator)."
        )
    return df


def load_wohnmobile_holder_age(data_path: str) -> pd.DataFrame:
    """KBA wohnmobile stock by holder age class (Stichtag 2025-04-01, issue #315).

    ``share_of_attributed`` is ``P(age class | wohnmobile)`` renormalised over
    the eight published natural-person classes; the ``not_attributed`` residual
    row carries NaN there (ADR-0093 ASSUMPTION: the unattributed residual
    shares the attributed age composition (its size is recorded in the committed
    CSV; see ADR-0093)). Produced by
    ``scripts/extract_kba_fleet.py::extract_wohnmobile_holder_age`` from the
    COMMITTED raw transcription of the KBA infographic / PM 23/2025 -- unlike
    the server-generated MiD tables, absence of this file is a checkout/wiring
    defect, which the sample_fleet flag guard turns into a hard error.
    """
    filename = "kba_wohnmobile_holder_age.csv"
    df = _read(data_path, filename)
    _require_columns(
        df,
        ["age_class", "age_min_years", "age_max_years", "vehicles",
         "published_share_pct", "share_of_attributed", "total_stock", "stichtag"],
        filename,
    )
    _require_labels(
        df["age_class"],
        (*WOHNMOBILE_AGE_CLASS_LABELS, WOHNMOBILE_AGE_NOT_ATTRIBUTED),
        "age_class", filename,
    )
    missing = set(WOHNMOBILE_AGE_CLASS_LABELS) - set(df["age_class"])
    if missing:
        raise RuntimeError(
            f"{filename}: missing holder-age class(es) {sorted(missing)} -- the "
            f"tilt needs the complete published table."
        )
    if df["age_class"].duplicated().any():
        raise RuntimeError(f"{filename}: duplicated age_class rows.")
    attributed = df[df["age_class"] != WOHNMOBILE_AGE_NOT_ATTRIBUTED]
    share_sum = float(attributed["share_of_attributed"].sum())
    if abs(share_sum - 1.0) > 1e-9:
        raise RuntimeError(
            f"{filename}: share_of_attributed sums to {share_sum!r}, expected 1.0 "
            f"over the {len(WOHNMOBILE_AGE_CLASS_LABELS)} attributed classes -- "
            f"re-run scripts/extract_kba_fleet.py."
        )
    totals = df["total_stock"].unique()
    if len(totals) != 1:
        raise RuntimeError(
            f"{filename}: total_stock must be one repeated value, got {totals!r}."
        )
    vehicles_sum = int(df["vehicles"].sum())
    if vehicles_sum != int(totals[0]):
        raise RuntimeError(
            f"{filename}: vehicles sum to {vehicles_sum} but total_stock says "
            f"{int(totals[0])} -- transcription drift; fix the raw CSV and re-run "
            f"scripts/extract_kba_fleet.py."
        )
    return df


def load_kreis_fuel(data_path: str) -> pd.DataFrame:
    """Regionalstatistik 46251-02: per-Kreis fuel counts + within-Kreis shares.

    Real per-Kreis powertrain marginal (petrol/diesel/gas/bev/phev/hybrid/other),
    Stichtag 01.01.2025. Supersedes the FZ 27.15 NDS petrol:diesel split for the
    per-Kreis powertrain rake. The raw 46251-02 file covers every German Kreis
    (Task B3): validated to carry at least all 8 ZGB Kreise; extra (non-ZGB)
    Kreis rows are allowed and are how cross-cordon in-commuters get their real
    home-Kreis fuel mix instead of the national fallback.
    """
    filename = "kba_kreis_fuel.csv"
    df = _read(data_path, filename)
    _require_columns(
        df, ["kreis_ags5", "kreis_name", "stichtag", "petrol", "diesel", "gas",
             "bev", "phev", "hybrid", "other", "total"], filename)
    _require_zgb_subset(df["kreis_ags5"], filename)
    return df


def load_kreis_euro(data_path: str) -> pd.DataFrame:
    """Regionalstatistik 46251-03: per-Kreis Euro-group counts + shares.

    ``teil`` is ``all`` (all fuels) or ``diesel``; euro columns are euro1..euro6 +
    other. Provides the per-Kreis Euro marginal, Stichtag 01.01.2025. The raw
    46251-03 file covers every German Kreis (Task B3): validated to carry at
    least all 8 ZGB Kreise (in the ``all`` rows) and the canonical Euro labels;
    extra (non-ZGB) Kreis rows are allowed.

    Task B4 adds three additive Euro-6 substage COUNT columns that are
    subsets/derivations of ``euro6`` (NOT part of ``total``, which stays
    unchanged): ``euro6d``, ``euro6dtemp`` (the two Destatis "darunter" subsets)
    and the derived residual ``euro6ab = max(euro6 - euro6d - euro6dtemp, 0)``.
    These columns are OPTIONAL for backward compatibility with
    ``kba_kreis_euro.csv`` files generated before Task B4 (and with synthetic
    test fixtures that predate it): when all three are present they are
    validated (must be non-negative) and returned as-is (PRIMARY path); when
    any is missing, the loader degrades gracefully by filling all three with
    0.0 and LOGGING the fallback (no-silent-fallback rule) instead of raising,
    so the per-Kreis powertrain rake and age-euro joint keep working unchanged
    on pre-B4 data.
    """
    filename = "kba_kreis_euro.csv"
    df = _read(data_path, filename)
    _require_columns(
        df, ["kreis_ags5", "kreis_name", "stichtag", "teil",
             "euro1", "euro2", "euro3", "euro4", "euro5", "euro6", "other",
             "total"], filename)
    _require_zgb_subset(df.loc[df["teil"] == "all", "kreis_ags5"], filename)
    unexpected = set(df["teil"]) - {"all", "diesel"}
    if unexpected:
        raise RuntimeError(f"{filename}: unexpected teil values {sorted(unexpected)}.")

    missing_substage = [c for c in EURO6_SUBSTAGE_COLUMNS if c not in df.columns]
    if missing_substage:
        logger.info(
            "[load_kreis_euro] %s: Euro-6 substage column(s) %s absent (pre-Task-B4 "
            "schema, fallback path); filling with 0.0 for all %d rows so the "
            "per-Kreis powertrain/euro logic keeps working unchanged. Re-run "
            "scripts/extract_kba_fleet.py to populate the real substage counts.",
            filename, missing_substage, len(df),
        )
        for column in EURO6_SUBSTAGE_COLUMNS:
            if column not in df.columns:
                df[column] = 0.0
    else:
        negative = {c: int((df[c] < 0).sum()) for c in EURO6_SUBSTAGE_COLUMNS if (df[c] < 0).any()}
        if negative:
            raise RuntimeError(
                f"{filename}: negative Euro-6 substage count(s) {negative} -- "
                f"euro6d/euro6dtemp/euro6ab must be >= 0."
            )
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


def load_ev_grid(data_path: str) -> pd.DataFrame:
    """KBA 5 km EV-share grid (2026), clipped to the ZGB bounding box.

    Loads ``kba_ev_grid.csv`` produced by
    ``scripts/extract_kba_fleet.py::extract_ev_grid``.

    Columns: ``cell_id, stichtag, ev_share, minx, miny, maxx, maxy, suppressed``.

    ``ev_share`` is in fractions (0-1).  ``suppressed`` marks cells that KBA
    flagged as low-count (``"-"`` in the source gpkg ``ZS_Anteil_`` column);
    such cells may carry NaN ``ev_share``.  ``minx, miny, maxx, maxy`` are the
    cell geometry bounds in EPSG:3857.

    Args:
        data_path: Root data path; ``braunschweig/kba/derived/`` is appended
            automatically.

    Returns:
        DataFrame with the eight columns listed above.

    Raises:
        FileNotFoundError: If ``kba_ev_grid.csv`` is absent (run
            ``scripts/extract_kba_fleet.py`` on the server to generate it).
        RuntimeError: If any required column is missing (schema drift).
    """
    filename = "kba_ev_grid.csv"
    df = _read(data_path, filename)
    _require_columns(
        df,
        ["cell_id", "stichtag", "ev_share", "minx", "miny", "maxx", "maxy", "suppressed"],
        filename,
    )
    # ``suppressed`` round-trips through CSV as the strings "True"/"False"; coerce
    # back to a real bool so a downstream ``if suppressed`` check is correct (a
    # bare non-empty string like "False" is truthy and would otherwise silently
    # mark every cell suppressed, disabling the grid EV tilt).
    df["suppressed"] = df["suppressed"].map(
        {"True": True, "False": False, True: True, False: False}
    )
    return df


def load_ev_regiostar7(data_path: str) -> pd.DataFrame:
    """KBA per-RegioStaR7 EV share (national), LOGGING-ONLY validation cross-check.

    Loads ``kba_ev_regiostar7.csv`` produced by
    ``scripts/extract_kba_fleet.py::extract_ev_regiostar7`` (Task B6). This is a
    NATIONAL aggregate (not ZGB-specific); it feeds ONLY
    :func:`braunschweig.synthesis.vehicles.fleet_validation.crosscheck_ev_by_regiostar7`,
    an order-of-magnitude cross-check that NEVER flags the run -- per the
    no-invented-reference-value rule (CLAUDE.md), a national figure cannot be
    asserted as a regional (ZGB) target, so it is reported as a CROSS-CHECK only.

    Columns: ``rs7, ev_share, stichtag``.

    Args:
        data_path: Root data path; ``braunschweig/kba/derived/`` is appended
            automatically.

    Returns:
        DataFrame with the three columns listed above, one row per
        RegioStaR-7 code.

    Raises:
        FileNotFoundError: If ``kba_ev_regiostar7.csv`` is absent (the raw
            RegioStaR7 timeseries CSV is a new, optional input -- run
            ``scripts/extract_kba_fleet.py`` on the server once it has been
            supplied to generate this file).
        RuntimeError: If required columns are missing, or any ``rs7`` code is
            outside the 7 canonical RegioStaR-7 codes (schema drift).
    """
    filename = "kba_ev_regiostar7.csv"
    df = _read(data_path, filename)
    _require_columns(df, ["rs7", "ev_share", "stichtag"], filename)
    allowed_rs7 = set(RS7_TO_RAUMTYP_REGION)
    unexpected = set(df["rs7"]) - allowed_rs7
    if unexpected:
        raise RuntimeError(
            f"{filename}: unexpected RegioStaR7 code(s) {sorted(unexpected)} "
            f"(allowed {sorted(allowed_rs7)})."
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
