"""Per-vehicle generative chain for the German (KBA/MiD) fleet + HBEFA mapping.

For each household car -- a row carrying the household ``economic_status``, the
home Kreis (AGS-5), the home Gemeinde and the home RegioStaR raumtyp -- this
module draws a full, mutually consistent vehicle specification and maps it to a
canonical HBEFA :class:`braunschweig.synthesis.vehicles.hbefa.VehicleType` for
emissions. Everything is driven by a single seeded RNG so a run is reproducible.

The draw order (spec component 4) is:

1. **segment** from the income-coupled segment IPF
   (:mod:`braunschweig.synthesis.vehicles.segment`), given the household's
   economic status and home raumtyp. This is where the income->segment coupling
   enters.
2. **powertrain** from ``P(powertrain | segment)`` (KBA FZ 27.10), but **raked
   per Kreis** so the Kreis powertrain totals match KBA FZ 27.15 AND tilted to
   the home Gemeinde's private BEV/PHEV share (KBA FZ 27.17). The
   income->segment->powertrain path carries the income correlation of the
   electric share; the rake + tilt enforce the LOCAL electric share. See
   :class:`PowertrainModel`.
3. **euro_class** from ``P(euro | powertrain)`` (KBA FZ 27.4, NDS).
4. **age band** from ``P(age | powertrain)`` (KBA FZ 27.7), kept consistent with
   the Euro class (newer age <=> higher Euro; see :func:`_age_consistent_with_euro`).
5. **brand + model** from ``P(model | segment)`` (KBA FZ 12.1; the model implies
   the brand), reconciled with ``P(brand | powertrain)`` (KBA FZ 27.11) where
   feasible. Brand/model are ADDITIVE (not HBEFA-critical) attributes; they are
   sampled in an isolated, fully guarded step with its own fallback so a parsing
   or edge issue there can never break the emissions-relevant chain.
6. The ``(powertrain, euro_class, segment)`` triple is mapped to a canonical
   HBEFA :class:`VehicleType` (:mod:`braunschweig.synthesis.vehicles.hbefa`).

The public entry point is :func:`sample_fleet`, which returns the input frame
augmented with the spec columns plus the distinct :class:`VehicleType` records to
register with the MATSim writer.

All numeric reference values come from the committed derived CSVs loaded via
:mod:`braunschweig.data.kba.fleet_tables`; hard-coding them in Python is
prohibited (see CLAUDE.md).
"""

from __future__ import annotations

import collections.abc
import logging
import re as _re
import time
from dataclasses import dataclass, field
from typing import Mapping, Optional, Sequence

import numpy as np
import pandas as pd

from braunschweig.data.kba import fleet_tables as ft
from braunschweig.data.kba.feasible_fuels import FeasibleFuels
from braunschweig.data.kba.hsn_tsn import canonical_brand, model_family
from braunschweig.ipf.joint_age_size import rake_2d
from braunschweig.synthesis.vehicles import hbefa
from braunschweig.synthesis.vehicles.age_income import AgeIncomeModel
from braunschweig.synthesis.vehicles.segment import SegmentModel

logger = logging.getLogger(__name__)


def normalize_gemeinde(name: str) -> str:
    """Normalise a Gemeinde name for matching population labels to FZ 27.17 keys.

    Uppercase, transliterate German umlauts/eszett (UE/OE/AE/SS), drop any
    administrative suffix after a comma (", STADT" / ",ST." / ",FLECKEN" / ...)
    and any parenthetical qualifier ("(LANDKREIS ...)"), collapse whitespace.
    Applied identically on BOTH sides so e.g. population "WOLFENBÜTTEL, STADT"
    matches the FZ 27.17 key "WOLFENBUETTEL,ST.". Coverage measured on the 100%
    run: 33.6% -> 100% of gemeinde-bearing fleet cars.
    """
    s = str(name).upper().strip()
    for a, b in (("Ü", "UE"), ("Ö", "OE"), ("Ä", "AE"), ("ß", "SS")):
        s = s.replace(a, b)
    s = _re.sub(r",.*$", "", s)      # drop administrative suffix after comma
    s = _re.sub(r"\(.*?\)", "", s)   # drop parenthetical qualifier
    return " ".join(s.split())


#: Canonical powertrain order used for every powertrain probability vector.
POWERTRAINS: tuple[str, ...] = ft.POWERTRAIN_LABELS

#: Electric (plug-in) powertrains tilted by the Gemeinde private BEV/PHEV share.
ELECTRIC_POWERTRAINS: tuple[str, ...] = ("bev", "phev")

#: Vehicle-age band -> representative age in years (band midpoint; ``30_plus``
#: uses a conservative 32). Used to attach a numeric ``age`` column alongside the
#: categorical ``age_band`` so downstream consumers (and the legacy writer's
#: ``age`` attribute) have an integer year.
AGE_BAND_MIDPOINT_YEARS: dict[str, int] = {
    "under_5": 2,
    "5_to_9": 7,
    "10_to_14": 12,
    "15_to_19": 17,
    "20_to_24": 22,
    "25_to_29": 27,
    "30_plus": 32,
}

#: Euro class -> the registration-period start year (EU type-approval dates for
#: passenger cars). Used only for the age<->Euro consistency rule: a vehicle's
#: registration year is ``current_year - age``, and a combustion Euro class
#: cannot be NEWER than the registration year allows. ``other`` (pre-Euro-1 /
#: unclassifiable) has no lower bound.
EURO_INTRODUCTION_YEAR: dict[str, int] = {
    "euro1": 1993,
    "euro2": 1997,
    "euro3": 2001,
    "euro4": 2006,
    "euro5": 2011,
    "euro6": 2015,
    "other": 0,
}

#: Reference "current year" of the KBA stock (FZ tables are the 2024-01-01
#: register). Registration year = REGISTER_YEAR - vehicle_age_years.
REGISTER_YEAR = 2024


# --------------------------------------------------------------------------- #
# Powertrain model: P(powertrain | segment) raked per Kreis + Gemeinde tilt
# --------------------------------------------------------------------------- #
@dataclass
class PowertrainModel:
    """``P(powertrain | segment, kreis)`` raked to the per-Kreis KBA marginal.

    The model holds, per Kreis, the raked ``segment x powertrain`` joint whose
    column marginal matches the KBA FZ 27.15 per-Kreis powertrain totals and
    whose row marginal matches the national ``P(segment)`` (so the seed's
    income->segment->powertrain association is preserved). The per-car draw is
    the segment row of that Kreis joint, optionally tilted to the home Gemeinde's
    private electric share (FZ 27.17).
    """

    segments: list[str]
    powertrains: list[str]
    # kreis_ags5 -> P(powertrain | segment) matrix (n_segment, n_powertrain).
    kreis_segment_powertrain: dict[str, np.ndarray]
    # National P(powertrain | segment) fallback (n_segment, n_powertrain).
    national_segment_powertrain: np.ndarray
    # Per-Kreis private electric (bev/phev) share, used as the tilt denominator.
    kreis_private_electric_share: dict[str, dict[str, float]]
    # (kreis_ags5, gemeinde_upper) -> private electric share, tilt numerator.
    gemeinde_private_electric_share: dict[tuple[str, str], dict[str, float]]
    # Mutable fallback counters.
    _kreis_primary: int = field(default=0)
    _kreis_fallback: int = field(default=0)
    _gemeinde_primary: int = field(default=0)
    _gemeinde_fallback: int = field(default=0)
    _grid_primary: int = field(default=0)
    _grid_fallback: int = field(default=0)

    @classmethod
    def from_data_path(cls, data_path: str, segments: Sequence[str],
                       max_iterations: int = 200,
                       tolerance: float = 1e-9) -> "PowertrainModel":
        """Build the per-Kreis powertrain model from the committed KBA CSVs.

        Per-Kreis powertrain marginal source selection (no-silent-fallback rule):

        * **Primary** — if ``kba_kreis_fuel.csv`` (Regionalstatistik 46251-02) is
          present, the real per-Kreis petrol/diesel/gas/bev/phev/hybrid/other
          counts are used directly via
          :meth:`_kreis_powertrain_marginal_from_fuel`. This gives the exact
          per-Kreis petrol:diesel ratio without the NDS approximation.
        * **Fallback** — if the file is absent (``FileNotFoundError``), the
          existing FZ 27.15 + NDS petrol:diesel split path is used unchanged.
          A log message records which source was actually used.
        """
        df_seg = ft.load_segment_powertrain(data_path)
        df_kreis = ft.load_kreis_powertrain(data_path)
        df_fuel_nds = ft.load_fuel_euro_nds(data_path)

        segments = list(segments)
        powertrains = list(POWERTRAINS)

        # NDS petrol:diesel split of the combustion residual (FZ 27.4 totals).
        # Needed for the national segment x powertrain seed matrix regardless of
        # which source is used for the per-Kreis marginal.
        fuel_totals = df_fuel_nds.groupby("fuel")["count"].sum()
        petrol_total = float(fuel_totals.get("petrol", 0.0))
        diesel_total = float(fuel_totals.get("diesel", 0.0))
        combustion_total = petrol_total + diesel_total
        if combustion_total <= 0:
            raise RuntimeError(
                "FZ 27.4 has no petrol/diesel counts; cannot split the "
                "combustion residual into petrol/diesel."
            )
        petrol_fraction = petrol_total / combustion_total

        national_counts = cls._national_segment_powertrain_counts(
            df_seg, segments, powertrains, petrol_fraction)
        # Row-normalised P(powertrain | segment), used as the rake seed shape and
        # the national fallback.
        national_psg = cls._row_normalise(national_counts)

        # Per-Kreis powertrain marginal target: prefer real 46251-02 fuel counts;
        # fall back to FZ 27.15 + NDS split when the file is absent.
        try:
            df_kreis_fuel = ft.load_kreis_fuel(data_path)
            kreis_marginal = cls._kreis_powertrain_marginal_from_fuel(
                df_kreis_fuel, powertrains)
            logger.info(
                "[fleet_de] per-Kreis powertrain marginal: primary source = "
                "Regionalstatistik 46251-02 fuel counts (kba_kreis_fuel.csv); "
                "real petrol:diesel ratio used per Kreis."
            )
        except FileNotFoundError:
            kreis_marginal = cls._kreis_powertrain_marginal(
                df_kreis, powertrains, petrol_fraction)
            logger.info(
                "[fleet_de] per-Kreis powertrain marginal: fallback to "
                "FZ 27.15 + NDS petrol:diesel split (kba_kreis_fuel.csv absent)."
            )

        # National segment marginal (FZ 27.10 segment_share) -- the rake row
        # target, preserving the income->segment->powertrain association.
        seg_share = (
            df_seg.set_index("segment")["segment_share"].reindex(segments)
            .fillna(0.0).to_numpy(dtype=float)
        )
        seg_share = seg_share / seg_share.sum()

        kreis_psp: dict[str, np.ndarray] = {}
        for kreis, col_target in kreis_marginal.items():
            # Seed = P(powertrain | segment) * P(segment): carries the
            # association and has the national segment marginal as its row sums.
            seed = national_psg * seg_share[:, None]
            joint = rake_2d(
                seed,
                row_targets=seg_share,
                col_targets=col_target / col_target.sum(),
                max_iterations=max_iterations,
                tolerance=tolerance,
            )
            kreis_psp[kreis] = cls._row_normalise(joint)

        # Task B3 traceability: the per-Kreis powertrain marginal now potentially
        # covers every German Kreis (Regionalstatistik 46251-02 is not ZGB-filtered
        # any more), which is what lets cross-cordon in-commuters draw their real
        # home-Kreis fuel mix. Log the realised coverage so a broken join upstream
        # (e.g. the primary source silently reverting to the 8-Kreis FZ 27.15
        # fallback) is visible rather than a silent loss of coverage.
        n_zgb_covered = sum(1 for k in kreis_psp if k in set(ft.ZGB_KREISE_AGS5))
        n_non_zgb_covered = len(kreis_psp) - n_zgb_covered
        logger.info(
            "[fleet_de] per-Kreis powertrain marginal coverage: %d Kreise "
            "(%d ZGB + %d non-ZGB).",
            len(kreis_psp), n_zgb_covered, n_non_zgb_covered,
        )

        # Per-Kreis private electric shares (FZ 27.15, 2025), the tilt denominator
        # for the FZ27.17-fallback path (both 2025 -- already same-vintage).
        kreis_priv_fz2715 = cls._kreis_private_electric_share(df_kreis)

        # Per-Gemeinde electric shares: prefer the 2026 kba_gemeinde_ev.csv source;
        # fall back to the FZ 27.17 kba_gemeinde_private_bev.csv when absent.
        #
        # Review Finding 3: the 2026 Gemeinde numerator must NOT be tilted against
        # the 2025 FZ27.15 Kreis share -- EV grew 2025->2026, so that ratio is
        # systematically >1 and produces a fleet-wide EV level shift that the
        # per-Kreis electric rake then preserves. When the 2026 source is active,
        # the denominator is instead derived from the SAME 2026 file (see
        # :meth:`_kreis_private_electric_share_2026`), so numerator and
        # denominator share both vintage and scope.
        try:
            df_gem_ev = ft.load_gemeinde_ev(data_path)
            gem_priv = _gemeinde_electric_share_2026(df_gem_ev)
            try:
                df_gem_fz27_weights = ft.load_gemeinde_private_bev(data_path)
            except FileNotFoundError:
                df_gem_fz27_weights = None
                logger.warning(
                    "[fleet_de] gemeinde EV tilt: FZ27.17 private_total weights "
                    "absent (kba_gemeinde_private_bev.csv not found); the 2026 "
                    "same-vintage Kreis-mean denominator will be UNWEIGHTED "
                    "(every Gemeinde weight = 1)."
                )
            kreis_priv = cls._kreis_private_electric_share_2026(
                df_gem_ev, df_gem_fz27_weights)
            logger.info(
                "[fleet_de] gemeinde EV tilt: 2026 source (kba_gemeinde_ev); "
                "%d Gemeinden loaded; same-vintage (2026/2026) weighted "
                "Kreis-mean denominator (pure relative tilt, no EV level shift).",
                len(gem_priv)
            )
        except FileNotFoundError:
            df_gem_fz27 = ft.load_gemeinde_private_bev(data_path)
            gem_priv = cls._gemeinde_private_electric_share(df_gem_fz27)
            kreis_priv = kreis_priv_fz2715
            logger.info(
                "[fleet_de] gemeinde EV tilt: FZ27.17 fallback "
                "(kba_gemeinde_ev.csv absent); %d Gemeinden loaded; "
                "same-vintage (2025/2025) FZ27.15 Kreis denominator.",
                len(gem_priv)
            )

        return cls(
            segments=segments,
            powertrains=powertrains,
            kreis_segment_powertrain=kreis_psp,
            national_segment_powertrain=national_psg,
            kreis_private_electric_share=kreis_priv,
            gemeinde_private_electric_share=gem_priv,
        )

    # -- construction helpers ------------------------------------------------
    @staticmethod
    def _national_segment_powertrain_counts(
        df_seg: pd.DataFrame, segments: Sequence[str],
        powertrains: Sequence[str], petrol_fraction: float,
    ) -> np.ndarray:
        """National ``segment x powertrain`` count matrix from FZ 27.10.

        FZ 27.10 reports per-segment ``total`` and the alternative-drive columns
        (bev/phev/hybrid/gas/hydrogen). The combustion residual
        ``total - sum(alt)`` is split into petrol/diesel by the NDS petrol:diesel
        ratio (FZ 27.4). ``other`` is left at zero at the national level (FZ
        27.10 has no residual fuel column beyond the listed alternatives).
        """
        idx = {p: i for i, p in enumerate(powertrains)}
        out = np.zeros((len(segments), len(powertrains)), dtype=float)
        seg_rows = df_seg.set_index("segment")
        for i, seg in enumerate(segments):
            row = seg_rows.loc[seg]
            total = float(row["total"])
            bev = float(row["bev"])
            phev = float(row["phev"])
            hybrid = float(row["hybrid"])
            gas = float(row["gas"])
            hydrogen = float(row["hydrogen"])
            alt = bev + phev + hybrid + gas + hydrogen
            combustion = max(total - alt, 0.0)
            out[i, idx["bev"]] = bev
            out[i, idx["phev"]] = phev
            out[i, idx["hybrid"]] = hybrid
            out[i, idx["gas"]] = gas
            out[i, idx["hydrogen"]] = hydrogen
            out[i, idx["petrol"]] = combustion * petrol_fraction
            out[i, idx["diesel"]] = combustion * (1.0 - petrol_fraction)
        return out

    @staticmethod
    def _kreis_powertrain_marginal(
        df_kreis: pd.DataFrame, powertrains: Sequence[str],
        petrol_fraction: float,
    ) -> dict[str, np.ndarray]:
        """Per-Kreis powertrain marginal target vector (FZ 27.15).

        bev/phev/hybrid/gas come straight from FZ 27.15; the combustion residual
        ``total - alt_total`` is split petrol/diesel by the NDS ratio.
        hydrogen/other are not tabulated per Kreis and are left at zero (their
        national mass is negligible -- see FZ 27.10).
        """
        idx = {p: i for i, p in enumerate(powertrains)}
        out: dict[str, np.ndarray] = {}
        for _, row in df_kreis.iterrows():
            vec = np.zeros(len(powertrains), dtype=float)
            total = float(row["total"])
            bev = float(row["bev"])
            phev = float(row["phev"])
            hybrid = float(row["hybrid"])
            gas = float(row["gas"])
            alt_total = float(row["alt_total"])
            combustion = max(total - alt_total, 0.0)
            vec[idx["bev"]] = bev
            vec[idx["phev"]] = phev
            vec[idx["hybrid"]] = hybrid
            vec[idx["gas"]] = gas
            vec[idx["petrol"]] = combustion * petrol_fraction
            vec[idx["diesel"]] = combustion * (1.0 - petrol_fraction)
            out[str(row["kreis_ags5"])] = vec
        return out

    @staticmethod
    def _kreis_powertrain_marginal_from_fuel(
        df_kreis_fuel: pd.DataFrame, powertrains: Sequence[str],
    ) -> dict[str, np.ndarray]:
        """Per-Kreis powertrain marginal from Regionalstatistik 46251-02 fuel counts.

        Uses the REAL per-Kreis petrol/diesel/gas/bev/phev/hybrid/other counts from
        ``kba_kreis_fuel.csv`` directly, so no NDS petrol:diesel approximation is
        needed. The ``hydrogen`` powertrain has no per-Kreis column in 46251-02 and
        is set to zero, matching the existing FZ 27.15 behaviour.

        Columns consumed from ``df_kreis_fuel``:
          ``kreis_ags5``, ``petrol``, ``diesel``, ``gas``,
          ``bev``, ``phev``, ``hybrid``, ``other``.

        Returns:
            dict mapping each ``kreis_ags5`` string to a count-like
            ``np.ndarray`` over ``powertrains`` (same shape contract as
            :meth:`_kreis_powertrain_marginal`).
        """
        idx = {p: i for i, p in enumerate(powertrains)}
        out: dict[str, np.ndarray] = {}
        for _, row in df_kreis_fuel.iterrows():
            vec = np.zeros(len(powertrains), dtype=float)
            vec[idx["petrol"]] = float(row["petrol"])
            vec[idx["diesel"]] = float(row["diesel"])
            vec[idx["gas"]] = float(row["gas"])
            vec[idx["bev"]] = float(row["bev"])
            vec[idx["phev"]] = float(row["phev"])
            vec[idx["hybrid"]] = float(row["hybrid"])
            vec[idx["other"]] = float(row["other"])
            # hydrogen: no per-Kreis column in 46251-02; left at zero.
            out[str(row["kreis_ags5"])] = vec
        return out

    @staticmethod
    def _kreis_private_electric_share(df_kreis: pd.DataFrame) -> dict[str, dict[str, float]]:
        """Per-Kreis electric share used as the Gemeinde-tilt denominator (2025 path).

        FZ 27.15 ``bev_share`` / ``phev_share`` are the 2025 all-ownership shares;
        the FZ27.17 Gemeinde table (also 2025) reports the PRIVATE shares. Using
        the Kreis all-ownership share as the denominator and the Gemeinde private
        share as the numerator yields a relative within-Kreis tilt that is robust
        to the ownership-scope difference (the tilt is a ratio of shares, so a
        constant private:all offset cancels to first order).

        This denominator is used ONLY when the FZ27.17 Gemeinde source is the
        active numerator (``kba_gemeinde_ev.csv`` absent) -- numerator and
        denominator are then both 2025, i.e. already same-vintage. When the 2026
        ``kba_gemeinde_ev.csv`` numerator is active, :meth:`_kreis_private_electric_share_2026`
        is used instead so the denominator shares the numerator's 2026 vintage.
        """
        out: dict[str, dict[str, float]] = {}
        for _, row in df_kreis.iterrows():
            out[str(row["kreis_ags5"])] = {
                "bev": float(row["bev_share"]),
                "phev": float(row["phev_share"]),
            }
        return out

    @staticmethod
    def _kreis_private_electric_share_2026(
        df_gem_ev: pd.DataFrame,
        df_gem_fz27: Optional[pd.DataFrame],
    ) -> dict[str, dict[str, float]]:
        """Same-vintage (2026) per-Kreis electric share, the Gemeinde-tilt
        denominator used ONLY when the 2026 ``kba_gemeinde_ev.csv`` numerator
        (:func:`_gemeinde_electric_share_2026`) is active (review Finding 3).

        Using the 2025 FZ27.15 Kreis share as the denominator for a 2026
        Gemeinde-level numerator is a vintage mismatch: EV ownership grew
        2025->2026, so the ratio is systematically greater than one, and the
        per-Kreis electric rake then PRESERVES that inflated mean -- a
        fleet-wide EV level shift disguised as a local tilt. This method fixes
        that by deriving the denominator from the SAME 2026 file: for each
        Kreis and each electric powertrain (``bev``, ``phev``), the weighted
        mean of that Kreis's Gemeinde shares, weighted by each Gemeinde's
        ``private_total`` from FZ 27.17
        (:func:`braunschweig.data.kba.fleet_tables.load_gemeinde_private_bev`),
        joined on ``(kreis_ags5, gemeinde_norm)``. Numerator and denominator then
        share BOTH vintage (2026) AND scope (private cars), so the
        private-total-weighted Kreis mean of the ratio is ~= 1: a pure
        within-Kreis relative tilt, not a level shift.

        A Gemeinde with no matching FZ27.17 ``private_total`` (name not present
        in the FZ27.17 table, or ``df_gem_fz27`` entirely absent) falls back to
        weight 1.0 (unweighted contribution to the Kreis mean); this fallback
        rate is logged (no-silent-fallback rule) so a systematically broken join
        would be visible as a high fallback rate rather than silently degrading
        every Kreis mean to an unweighted average.

        NOTE ON EV LEVEL: this method only reshapes the WITHIN-Kreis relative
        tilt. The per-Kreis EV LEVEL itself stays anchored to the per-Kreis
        powertrain marginal (Regionalstatistik 46251-02, Stichtag 2025-01-01;
        see :meth:`_kreis_powertrain_marginal_from_fuel` and its FZ27.15
        fallback :meth:`_kreis_powertrain_marginal`) -- vintage updates to the
        EV level happen only via marginal refreshes, not via this Gemeinde tilt
        (ADR-0050 provenance discipline).

        Args:
            df_gem_ev: The 2026 ``kba_gemeinde_ev.csv`` frame (the same frame
                passed to :func:`_gemeinde_electric_share_2026` for the
                numerator), with columns ``kreis_ags5``, ``gemeinde_norm``,
                ``bev_share``, ``phev_share``.
            df_gem_fz27: The FZ 27.17 ``kba_gemeinde_private_bev.csv`` frame,
                used only for its ``private_total`` weights, joined on
                ``(kreis_ags5, normalize_gemeinde(gemeinde))``; or ``None`` when
                that file is absent (every Gemeinde then falls back to weight
                1.0).

        Returns:
            dict mapping ``kreis_ags5`` to ``{"bev": weighted_mean, "phev":
            weighted_mean}``. A powertrain key is omitted for a Kreis where no
            Gemeinde row has a valid (non-NaN) share for it.
        """
        weight_by_key: dict[tuple[str, str], float] = {}
        if df_gem_fz27 is not None:
            for _, row in df_gem_fz27.iterrows():
                key = (str(row["kreis_ags5"]), normalize_gemeinde(row["gemeinde"]))
                weight_by_key[key] = float(row["private_total"])

        weighted_sums: dict[str, dict[str, float]] = {}
        weight_sums: dict[str, dict[str, float]] = {}
        weight_primary = 0
        weight_fallback = 0
        for _, row in df_gem_ev.iterrows():
            kreis_ags5 = str(row["kreis_ags5"])
            gemeinde_norm = str(row["gemeinde_norm"])
            weight = weight_by_key.get((kreis_ags5, gemeinde_norm))
            # ``np.isfinite`` also catches a NaN ``private_total`` (should not
            # occur for a count column, but a NaN weight must never silently
            # corrupt the weighted mean -- treat it the same as "no match").
            if weight is None or not np.isfinite(weight):
                weight = 1.0
                weight_fallback += 1
            else:
                weight_primary += 1
            kreis_weighted = weighted_sums.setdefault(kreis_ags5, {})
            kreis_weight = weight_sums.setdefault(kreis_ags5, {})
            for col, pt in (("bev_share", "bev"), ("phev_share", "phev")):
                val = row[col]
                if pd.notna(val):
                    kreis_weighted[pt] = kreis_weighted.get(pt, 0.0) + weight * float(val)
                    kreis_weight[pt] = kreis_weight.get(pt, 0.0) + weight

        weight_total = weight_primary + weight_fallback
        weight_fallback_rate = (weight_fallback / weight_total) if weight_total else 0.0
        (logger.warning if weight_fallback_rate > 0.5 else logger.info)(
            "[fleet_de] gemeinde EV tilt: 2026 Kreis-mean denominator weights: "
            "primary (FZ27.17 private_total) %d/%d (%.1f%%), fallback "
            "(no FZ27.17 match, weight=1) %d (%.1f%%).",
            weight_primary, weight_total,
            100.0 * weight_primary / weight_total if weight_total else 0.0,
            weight_fallback, 100.0 * weight_fallback_rate,
        )

        out: dict[str, dict[str, float]] = {}
        for kreis_ags5, kreis_weighted in weighted_sums.items():
            kreis_weight = weight_sums[kreis_ags5]
            shares: dict[str, float] = {}
            for pt, weighted_sum in kreis_weighted.items():
                weight_sum = kreis_weight[pt]
                if weight_sum > 0:
                    shares[pt] = weighted_sum / weight_sum
            out[kreis_ags5] = shares
        return out

    @staticmethod
    def _gemeinde_private_electric_share(
        df_gem: pd.DataFrame,
    ) -> dict[tuple[str, str], dict[str, float]]:
        """(kreis, gemeinde) -> private bev/phev share (FZ 27.17).

        The Gemeinde name is upper-cased for a case-insensitive join with the
        home Gemeinde label. Missing (coerced) shares are dropped so the tilt
        falls back to the Kreis level for that Gemeinde.
        """
        out: dict[tuple[str, str], dict[str, float]] = {}
        for _, row in df_gem.iterrows():
            key = (str(row["kreis_ags5"]), normalize_gemeinde(row["gemeinde"]))
            shares: dict[str, float] = {}
            for col, pt in (("private_bev_share", "bev"),
                            ("private_phev_share", "phev")):
                val = row[col]
                if pd.notna(val):
                    shares[pt] = float(val)
            if shares:
                out[key] = shares
        return out

    @staticmethod
    def _row_normalise(matrix: np.ndarray) -> np.ndarray:
        sums = matrix.sum(axis=1, keepdims=True)
        return np.divide(matrix, sums, out=np.zeros_like(matrix), where=sums > 0)

    # -- per-car query -------------------------------------------------------
    def powertrain_probabilities(self, segment: str, kreis_ags5: str,
                                 gemeinde: Optional[str],
                                 grid_ev_share: Optional[float] = None,
                                 gemeinde_grid_mean: Optional[float] = None,
                                 ) -> np.ndarray:
        """``P(powertrain | segment, kreis)`` with the Gemeinde electric tilt
        and an optional further within-Gemeinde tilt from a 5 km grid cell.

        Falls back to the national ``P(powertrain | segment)`` when the Kreis is
        unknown (counted/logged), and to the Kreis-level electric share when the
        Gemeinde is unknown or has no FZ 27.17 entry.

        The grid tilt is applied AFTER the Gemeinde tilt and BEFORE the final
        renormalisation.  When ``grid_ev_share`` or ``gemeinde_grid_mean`` is
        ``None`` the grid tilt is a no-op, so all existing callers (which do not
        pass these parameters) produce byte-identical results.

        Args:
            segment: Vehicle segment label (must be in ``self.segments``).
            kreis_ags5: Home Kreis AGS-5 code.
            gemeinde: Home Gemeinde name (``None`` -> Kreis-level tilt only).
            grid_ev_share: EV share of the household's 5 km grid cell
                (``None`` -> grid tilt disabled).
            gemeinde_grid_mean: Household-weighted mean EV share across all
                grid cells within the Gemeinde (``None`` -> grid tilt disabled).
                Must be > 0 for the tilt to fire.
        """
        if segment not in self.segments:
            raise ValueError(f"unknown segment '{segment}'")
        seg_index = self.segments.index(segment)

        kreis_matrix = self.kreis_segment_powertrain.get(kreis_ags5)
        if kreis_matrix is None:
            self._kreis_fallback += 1
            base = self.national_segment_powertrain[seg_index].copy()
        else:
            self._kreis_primary += 1
            base = kreis_matrix[seg_index].copy()

        base = self._apply_gemeinde_tilt(base, kreis_ags5, gemeinde)
        base = self._apply_grid_tilt(base, grid_ev_share, gemeinde_grid_mean)
        total = base.sum()
        if total <= 0:
            return np.ones(len(self.powertrains)) / len(self.powertrains)
        return base / total

    def _apply_gemeinde_tilt(self, pmf: np.ndarray, kreis_ags5: str,
                             gemeinde: Optional[str]) -> np.ndarray:
        """Tilt the bev/phev mass to the home Gemeinde's private share.

        Multiplies the electric powertrain probabilities by
        ``gemeinde_share / kreis_share`` (clipped to a sane band) and leaves the
        non-electric mass untouched; the whole vector is renormalised by the
        caller. The tilt is a relative re-weighting, so a Gemeinde with a higher
        private BEV share than its Kreis gets proportionally more BEVs.
        """
        if gemeinde is None:
            self._gemeinde_fallback += 1
            return pmf
        key = (kreis_ags5, normalize_gemeinde(gemeinde))
        gem_shares = self.gemeinde_private_electric_share.get(key)
        kreis_shares = self.kreis_private_electric_share.get(kreis_ags5)
        if gem_shares is None or kreis_shares is None:
            self._gemeinde_fallback += 1
            return pmf
        self._gemeinde_primary += 1
        idx = {p: i for i, p in enumerate(self.powertrains)}
        tilted = pmf.copy()
        for pt in ELECTRIC_POWERTRAINS:
            kreis_share = kreis_shares.get(pt, 0.0)
            gem_share = gem_shares.get(pt)
            if gem_share is None or kreis_share <= 0.0:
                continue
            # Clip the tilt to [0.2, 5] so a tiny denominator cannot explode a
            # single Gemeinde's electric share. F8 NOTE: the 0.2 lower floor is
            # DELIBERATE -- a Gemeinde with a genuine near-zero EV share still keeps
            # >=20% of the Kreis's relative EV propensity rather than being fully
            # suppressed. This is an anti-explosion guard; it means "no-EV pockets"
            # are not represented at the extreme (acceptable trade-off).
            factor = float(np.clip(gem_share / kreis_share, 0.2, 5.0))
            tilted[idx[pt]] *= factor
        return tilted

    def _apply_grid_tilt(self, pmf: np.ndarray,
                         grid_ev_share: Optional[float],
                         gemeinde_grid_mean: Optional[float]) -> np.ndarray:
        """Tilt the bev/phev mass to the household's 5 km grid cell EV share.

        Multiplies the electric powertrain probabilities by
        ``clip(grid_ev_share / gemeinde_grid_mean, 0.2, 5.0)`` and leaves the
        non-electric mass untouched; the whole vector is renormalised by the
        caller.

        The ratio ``grid_ev_share / gemeinde_grid_mean`` re-weights EV mass
        toward high-EV cells WITHIN the Gemeinde.  Because ``gemeinde_grid_mean``
        is the household-weighted mean of the Gemeinde's cell shares (wired by
        T9b), the ratio averages to ~1 within the Gemeinde so the Gemeinde-level
        EV aggregate is preserved; only intra-Gemeinde variation is added.

        The tilt is a no-op (fallback counted) when:
          * ``grid_ev_share`` is ``None``,
          * ``gemeinde_grid_mean`` is ``None``,
          * ``gemeinde_grid_mean`` <= 0 (avoids division by zero), or
          * ``grid_ev_share`` is NaN (suppressed / missing cell data).

        Args:
            pmf: Probability mass vector over powertrains (modified in-place on
                a copy, then returned; the original is not mutated).
            grid_ev_share: EV share of the household's grid cell, or ``None``.
            gemeinde_grid_mean: Household-weighted mean cell EV share for the
                Gemeinde, or ``None``.

        Returns:
            Tilted pmf (un-renormalised; the caller normalises).
        """
        # When both params are None the caller has not requested a grid tilt at
        # all (the default case for all existing callers).  Do not count a
        # fallback so the rate log stays meaningful: a non-zero fallback rate
        # always means a partially-configured call where the tilt could not fire.
        if grid_ev_share is None and gemeinde_grid_mean is None:
            return pmf
        if (grid_ev_share is None
                or gemeinde_grid_mean is None
                or not np.isfinite(gemeinde_grid_mean)
                or gemeinde_grid_mean <= 0.0
                or not np.isfinite(grid_ev_share)):
            # ``np.isfinite`` catches NaN/inf for BOTH Python float and numpy
            # scalars (e.g. ``np.float64`` NaN from a pandas spatial join), so a
            # missing/suppressed cell share can never divide into the pmf and
            # produce a NaN probability (no-NA guarantee).  grid_ev_share is
            # guaranteed non-None here (the ``is None`` term short-circuits first).
            self._grid_fallback += 1
            return pmf
        self._grid_primary += 1
        idx = {p: i for i, p in enumerate(self.powertrains)}
        tilted = pmf.copy()
        # F8 NOTE: the 0.2 lower floor is DELIBERATE (same as the Gemeinde tilt) --
        # a true near-zero-EV cell keeps >=20% of the Gemeinde's relative EV
        # propensity rather than being fully suppressed (anti-explosion guard).
        factor = float(np.clip(grid_ev_share / gemeinde_grid_mean, 0.2, 5.0))
        for pt in ELECTRIC_POWERTRAINS:
            tilted[idx[pt]] *= factor
        return tilted

    def log_fallback_rate(self) -> None:
        """Log the primary-vs-fallback rates (no-silent-fallback rule)."""
        ktot = self._kreis_primary + self._kreis_fallback
        gtot = self._gemeinde_primary + self._gemeinde_fallback
        grtot = self._grid_primary + self._grid_fallback
        krate = (self._kreis_fallback / ktot) if ktot else 0.0
        grate = (self._gemeinde_fallback / gtot) if gtot else 0.0
        grrate = (self._grid_fallback / grtot) if grtot else 0.0
        (logger.warning if krate > 0.05 else logger.info)(
            "[fleet_de] powertrain Kreis lookup: primary %d/%d (%.1f%%), "
            "fallback %d (%.1f%%)", self._kreis_primary, ktot,
            100.0 * self._kreis_primary / ktot if ktot else 0.0,
            self._kreis_fallback, 100.0 * krate,
        )
        (logger.warning if grate > 0.50 else logger.info)(
            "[fleet_de] powertrain Gemeinde tilt: primary %d/%d (%.1f%%), "
            "fallback %d (%.1f%%)", self._gemeinde_primary, gtot,
            100.0 * self._gemeinde_primary / gtot if gtot else 0.0,
            self._gemeinde_fallback, 100.0 * grate,
        )
        (logger.warning if grrate > 0.50 else logger.info)(
            "[fleet_de] powertrain grid tilt: primary %d/%d (%.1f%%), "
            "fallback %d (%.1f%%)", self._grid_primary, grtot,
            100.0 * self._grid_primary / grtot if grtot else 0.0,
            self._grid_fallback, 100.0 * grrate,
        )


# --------------------------------------------------------------------------- #
# Gemeinde EV tilt builder (2026 source)
# --------------------------------------------------------------------------- #
def _gemeinde_electric_share_2026(
    df: pd.DataFrame,
) -> dict[tuple[str, str], dict[str, float]]:
    """(kreis_ags5, gemeinde_norm) -> electric shares from the 2026 kba_gemeinde_ev.

    Builds the Gemeinde tilt map from the 2026 per-Gemeinde EV CSV
    (``kba_gemeinde_ev.csv``, Stichtag 2026-04-01). The CSV already carries a
    ``gemeinde_norm`` column that was normalised during extraction, so no
    additional name normalisation is applied here (the key is used as-is,
    matching the population's ``normalize_gemeinde``-normalised Gemeinde label).

    The returned dict entries carry:

    * ``bev``      -- ``bev_share`` (fraction of all private Pkw)
    * ``phev``     -- ``phev_share`` (fraction of all private Pkw)
    * ``hydrogen`` -- ``fuelcell_share`` (stored for completeness / future use)

    Hydrogen tilt is intentionally NOT applied by
    :meth:`PowertrainModel._apply_gemeinde_tilt` because there is no defensible
    per-Kreis hydrogen denominator: FZ 27.15 has no per-Kreis fuel-cell count,
    and at ~0.01 % of the fleet any invented denominator would fabricate a
    scientifically indefensible signal. The ``hydrogen`` key is retained in the
    dict so a future task can add it when a suitable denominator becomes available.

    NaN-share rows are dropped per powertrain, consistent with the FZ 27.17
    builder (:meth:`PowertrainModel._gemeinde_private_electric_share`): a
    Gemeinde whose share is suppressed / missing for a given powertrain falls
    back to the Kreis share for that powertrain only. A row where ALL shares are
    NaN is dropped entirely (no entry in the output map).
    """
    out: dict[tuple[str, str], dict[str, float]] = {}
    for _, row in df.iterrows():
        key = (str(row["kreis_ags5"]), str(row["gemeinde_norm"]))
        shares: dict[str, float] = {}
        for col, pt in (("bev_share", "bev"),
                        ("phev_share", "phev"),
                        ("fuelcell_share", "hydrogen")):
            val = row[col]
            if pd.notna(val):
                shares[pt] = float(val)
        if shares:
            out[key] = shares
    return out


# --------------------------------------------------------------------------- #
# Euro / age conditional distributions (FZ 27.4 / FZ 27.7)
# --------------------------------------------------------------------------- #
def _euro_given_powertrain(data_path: str) -> dict[str, np.ndarray]:
    """``P(euro | powertrain)`` (FZ 27.4, NDS) as powertrain -> pmf over euro.

    FZ 27.4 only tabulates the combustion + bev/phev/hybrid/other fuels. The
    powertrains absent from FZ 27.4 (``gas`` aside, ``hydrogen``) reuse the
    ``other`` fuel row as a conservative fallback. The ``share`` column is
    already ``P(euro | fuel)``.
    """
    df = ft.load_fuel_euro_nds(data_path)
    euros = list(ft.EURO_CLASS_LABELS)
    out: dict[str, np.ndarray] = {}
    for fuel, grp in df.groupby("fuel"):
        vec = (grp.set_index("euro_class")["share"].reindex(euros)
               .fillna(0.0).to_numpy(dtype=float))
        s = vec.sum()
        out[fuel] = (vec / s) if s > 0 else _euro_all_other(euros)
    # Fallbacks for powertrains absent from FZ 27.4.
    fallback = out.get("other", _euro_all_other(euros))
    for pt in POWERTRAINS:
        out.setdefault(pt, fallback)
    return out


def _euro_all_other(euros: Sequence[str]) -> np.ndarray:
    vec = np.zeros(len(euros), dtype=float)
    vec[list(euros).index("other")] = 1.0
    return vec


def _euro_given_kreis_powertrain(
    data_path: str,
) -> "Optional[dict[tuple[str, str], np.ndarray]]":
    """``P(euro | kreis, powertrain)`` from Regionalstatistik 46251-03.

    Returns a dict keyed by ``(kreis_ags5, powertrain)`` -> normalised euro pmf,
    or ``None`` if ``kba_kreis_euro.csv`` is absent (caller falls back to the
    national FZ 27.4 pmf).

    **Assumptions (46251-03 coverage):**

    * *diesel*: uses the Kreis ``teil=="diesel"`` euro counts directly.
    * *petrol*, *gas*, *other*: uses ``max(all_count - diesel_count, 0)`` per euro
      class as a non-diesel combustion proxy. 46251-03 does not break down euro by
      individual fuel type, so petrol / gas / other all share this non-diesel shape.
      This is an intentional modelling assumption; document it explicitly.
      F12 NOTE: 46251-03 ``all`` is ALL Pkw (not combustion-only), so ``all-diesel``
      still contains the euro grouping of electrified Pkw (bev/phev/hybrid). The
      petrol/gas/other euro SHAPE is therefore slightly contaminated by electrified
      vehicles' euro classes. The effect is small (electrified are a minority and
      their euro is overridden to "electric" for bev/hydrogen at draw time) and
      acknowledged; a per-fuel-per-Kreis euro table would be needed to remove it.
    * *bev*, *phev*, *hybrid*, *hydrogen*: 46251-03 covers only combustion; fall
      back to the national pmf from :func:`_euro_given_powertrain` for every Kreis.

    If a (kreis, powertrain) marginal is all-zero after clipping (degenerate data),
    the national pmf is substituted and a warning is logged.
    """
    euros = list(ft.EURO_CLASS_LABELS)
    try:
        df = ft.load_kreis_euro(data_path)
    except FileNotFoundError:
        return None

    national = _euro_given_powertrain(data_path)
    # Powertrains that are NOT covered by 46251-03 (no combustion euro split);
    # for these every Kreis reuses the national pmf.
    _NON_COMBUSTION = frozenset({"bev", "phev", "hybrid", "hydrogen"})
    # Combustion powertrains in 46251-03; we derive per-Kreis for these.
    _DIESEL_PT = "diesel"
    _NON_DIESEL_COMBUSTIONs = {"petrol", "gas", "other"}

    out: dict[tuple[str, str], np.ndarray] = {}

    for kreis_ags5, grp in df.groupby("kreis_ags5"):
        kreis = str(kreis_ags5)
        row_all = grp[grp["teil"] == "all"]
        row_dsl = grp[grp["teil"] == "diesel"]
        if row_all.empty:
            logger.warning(
                "[fleet_de] T6b: Kreis %s has no 'all' row in kba_kreis_euro; "
                "using national pmf for all powertrains.",
                kreis,
            )
            for pt in POWERTRAINS:
                out[(kreis, pt)] = national[pt]
            continue

        all_counts = np.array(
            [row_all.iloc[0][e] for e in euros], dtype=float)

        # Diesel pmf: from the 'diesel' teil row.
        if not row_dsl.empty:
            dsl_counts = np.array(
                [row_dsl.iloc[0][e] for e in euros], dtype=float)
        else:
            # No diesel row -> treat as all-zero diesel (all_counts stay as is).
            dsl_counts = np.zeros(len(euros), dtype=float)

        s_dsl = dsl_counts.sum()
        if s_dsl > 0:
            pmf_diesel: np.ndarray = dsl_counts / s_dsl
        else:
            logger.warning(
                "[fleet_de] T6b: Kreis %s diesel euro marginal is all-zero; "
                "substituting national diesel pmf.",
                kreis,
            )
            pmf_diesel = national[_DIESEL_PT]
        out[(kreis, _DIESEL_PT)] = pmf_diesel

        # Non-diesel combustion pmf: max(all - diesel, 0) per euro class.
        non_dsl_counts = np.maximum(all_counts - dsl_counts, 0.0)
        s_non_dsl = non_dsl_counts.sum()
        if s_non_dsl > 0:
            pmf_non_dsl: np.ndarray = non_dsl_counts / s_non_dsl
        else:
            logger.warning(
                "[fleet_de] T6b: Kreis %s non-diesel combustion euro marginal "
                "is all-zero (all_counts=diesel_counts); substituting national "
                "petrol pmf.",
                kreis,
            )
            pmf_non_dsl = national.get("petrol", national.get("other", _euro_all_other(euros)))
        # petrol / gas / other all share this non-diesel combustion shape
        # (documented assumption: 46251-03 has no per-fuel-type euro split).
        for pt in _NON_DIESEL_COMBUSTIONs:
            out[(kreis, pt)] = pmf_non_dsl

        # Non-combustion powertrains: always use the national pmf.
        for pt in _NON_COMBUSTION:
            out[(kreis, pt)] = national[pt]

    return out


def _single_kreis_powertrain_age_euro_joint(
    kreis_ags5: str, fuel: str,
    age_given_powertrain: Mapping[str, np.ndarray],
    euro_given_national: Mapping[str, np.ndarray],
    euro_given_kreis: "Mapping[tuple[str, str], np.ndarray]",
) -> np.ndarray:
    """Single-cell IPF joint ``P(age_band, euro_class | kreis, powertrain)``.

    Extracted from :func:`_age_euro_joint_kreis` so both the eager dict builder
    and :class:`LazyKreisEuroJoint` (Task B3: the per-Kreis euro joint now
    potentially spans every German Kreis, ~400 x 8 powertrains, which is too
    slow to build eagerly -- see the class docstring) share exactly the same
    per-cell computation.

    Uses the same :func:`_age_euro_joint_matrices` IPF machinery as the
    national joint, but substitutes the per-Kreis euro column target where
    available.
    """
    ages = list(ft.AGE_BAND_LABELS)
    euros = list(ft.EURO_CLASS_LABELS)
    age_pmf = age_given_powertrain[fuel]
    euro_pmf = euro_given_kreis.get(
        (kreis_ags5, fuel), euro_given_national.get(fuel, np.ones(len(euros)) / len(euros)))
    r = np.asarray(age_pmf, dtype=float)
    r = r / r.sum() if r.sum() > 0 else np.ones(len(ages)) / len(ages)
    c = np.asarray(euro_pmf, dtype=float)
    c = c / c.sum() if c.sum() > 0 else np.ones(len(euros)) / len(euros)
    allowed = np.array(
        [[1.0 if _age_consistent_with_euro(a, e, fuel) else 0.0 for e in euros]
         for a in ages],
        dtype=float,
    )
    return _ipf_joint(allowed, r, c)


def _age_euro_joint_kreis(
    age_given_powertrain: Mapping[str, np.ndarray],
    euro_given_national: Mapping[str, np.ndarray],
    euro_given_kreis: "dict[tuple[str, str], np.ndarray]",
) -> "dict[tuple[str, str], np.ndarray]":
    """Per-(Kreis, powertrain) joint ``P(age_band, euro_class | kreis, powertrain)``,
    built EAGERLY for every (kreis, powertrain) pair.

    Kept for direct unit testing and as the reference implementation that
    :class:`LazyKreisEuroJoint` must match exactly; :meth:`FleetSampler.from_data_path`
    uses the lazy class instead (Task B3: eager build measured ~15 s wall-time
    at the ~400-Kreis German-wide scale, see the class docstring).

    Parameters
    ----------
    age_given_powertrain:
        National ``P(age_band | powertrain)`` (rows = age bands).
    euro_given_national:
        National ``P(euro_class | powertrain)`` fallback.
    euro_given_kreis:
        Per-(kreis, powertrain) euro pmf from :func:`_euro_given_kreis_powertrain`.

    Returns
    -------
    dict[(kreis_ags5, powertrain) -> IPF joint matrix (n_age x n_euro)]
    """
    kreise = {k for k, _pt in euro_given_kreis.keys()}
    out: dict[tuple[str, str], np.ndarray] = {}
    for kreis in kreise:
        for fuel in age_given_powertrain:
            out[(kreis, fuel)] = _single_kreis_powertrain_age_euro_joint(
                kreis, fuel, age_given_powertrain, euro_given_national, euro_given_kreis)
    return out


class LazyKreisEuroJoint(collections.abc.Mapping):
    """Lazily-computed, cached ``{(kreis_ags5, powertrain): IPF joint}`` mapping.

    Task B3 extended the Regionalstatistik 46251-03 per-Kreis Euro table
    (``kba_kreis_euro.csv``) to every German Kreis, so the per-(Kreis,
    powertrain) age-euro joint can now span ~400 Kreise x 8 powertrains
    (~3200 small 7x7 IPFs). Building all of them EAGERLY at
    ``FleetSampler.from_data_path`` time was measured at ~15 s wall-time for a
    synthetic ~400-Kreis fixture -- most of those combinations are never drawn
    by a given run's actual household population (which typically touches the
    8 ZGB home Kreise plus whichever in-commuter home Kreise are present).

    This class defers each cell's IPF to first access via ``__getitem__`` (and
    therefore ``.get()``, since :class:`collections.abc.Mapping` implements
    ``get`` on top of ``__getitem__``) and caches the result, so the realised
    compute cost matches the number of DISTINCT ``(kreis, powertrain)`` pairs
    actually queried during sampling, not the full national universe. The
    logical key set (and therefore ``len()``, ``in``, ``.keys()``) is still the
    full cross product of Kreise-with-euro-data x configured powertrains,
    identical to what the eager :func:`_age_euro_joint_kreis` builder would
    produce -- :func:`_single_kreis_powertrain_age_euro_joint` is the exact
    same per-cell computation used by both, so every returned value is
    numerically identical to the eager result (verified by
    ``tests/test_fleet_b1_euro_kreis.py::test_age_euro_joint_kreis_is_lazily_computed_and_matches_eager``).

    Determinism is preserved: the IPF computation has no randomness, so lazy +
    cached is bit-identical to eager, just deferred (and never repeated for the
    same key thanks to the cache).
    """

    def __init__(
        self,
        age_given_powertrain: Mapping[str, np.ndarray],
        euro_given_national: Mapping[str, np.ndarray],
        euro_given_kreis: "Mapping[tuple[str, str], np.ndarray]",
    ) -> None:
        self._age_given_powertrain = age_given_powertrain
        self._euro_given_national = euro_given_national
        self._euro_given_kreis = euro_given_kreis
        kreise = {k for k, _pt in euro_given_kreis.keys()}
        self._keys: frozenset = frozenset(
            (kreis, fuel) for kreis in kreise for fuel in age_given_powertrain
        )
        self._cache: dict[tuple[str, str], np.ndarray] = {}

    def __getitem__(self, key: tuple[str, str]) -> np.ndarray:
        if key not in self._keys:
            raise KeyError(key)
        if key not in self._cache:
            kreis_ags5, fuel = key
            self._cache[key] = _single_kreis_powertrain_age_euro_joint(
                kreis_ags5, fuel, self._age_given_powertrain,
                self._euro_given_national, self._euro_given_kreis,
            )
        return self._cache[key]

    def __iter__(self):
        return iter(self._keys)

    def __len__(self) -> int:
        return len(self._keys)


def _age_given_powertrain(data_path: str) -> dict[str, np.ndarray]:
    """``P(age_band | powertrain)`` (FZ 27.7) as powertrain -> pmf over age band.

    FZ 27.7 ``share`` is ``P(age_band | fuel)`` only within the listed fuels; we
    re-normalise per fuel so each powertrain pmf sums to 1. Powertrains absent
    from FZ 27.7 reuse the petrol age profile.
    """
    df = ft.load_age_fuel(data_path)
    bands = list(ft.AGE_BAND_LABELS)
    out: dict[str, np.ndarray] = {}
    for fuel, grp in df.groupby("fuel"):
        vec = (grp.set_index("age_band")["pkw_count"].reindex(bands)
               .fillna(0.0).to_numpy(dtype=float))
        s = vec.sum()
        out[fuel] = (vec / s) if s > 0 else np.ones(len(bands)) / len(bands)
    fallback = out.get("petrol", np.ones(len(bands)) / len(bands))
    for pt in POWERTRAINS:
        out.setdefault(pt, fallback)
    return out


def _age_consistent_with_euro(age_band: str, euro_class: str,
                              powertrain: str) -> bool:
    """Return ``True`` iff the vehicle age can coexist with the Euro class.

    Consistency rule: a combustion vehicle of a given Euro stage cannot be older
    than the Euro stage's EU introduction allows. The registration year is
    ``REGISTER_YEAR - max_age_in_band``; if that earliest possible registration
    year is before the Euro stage existed, the pair is inconsistent (e.g. a
    25-29-year-old car cannot be Euro-6). Newer => higher Euro. Electrified /
    fuel-cell powertrains carry no combustion Euro stage, so any age is allowed.
    """
    if powertrain not in hbefa.COMBUSTION_POWERTRAINS:
        return True
    intro = EURO_INTRODUCTION_YEAR.get(euro_class, 0)
    if intro <= 0:
        return True
    max_age = _age_band_max_years(age_band)
    earliest_registration = REGISTER_YEAR - max_age
    # Allow a 1-year grace for end-of-stage registrations.
    return earliest_registration >= intro - 1


def _age_band_max_years(age_band: str) -> int:
    """Upper edge of an age band in years (``30_plus`` -> a large bound)."""
    mapping = {
        "under_5": 4, "5_to_9": 9, "10_to_14": 14, "15_to_19": 19,
        "20_to_24": 24, "25_to_29": 29, "30_plus": 60,
    }
    return mapping[age_band]


def _ipf_joint(allowed: np.ndarray, row_target: np.ndarray, col_target: np.ndarray,
               iterations: int = 1000, tol: float = 1e-10) -> np.ndarray:
    """Iterative proportional fit of a joint on ``allowed`` cells to two marginals.

    ``allowed`` is a 0/1 support matrix (rows x cols). Returns a matrix whose row
    sums match ``row_target`` and column sums match ``col_target`` as closely as
    the support allows. When the two marginals are marginally infeasible on the
    support, IPF returns the maximum-entropy compromise (a small residual on the
    over-constrained marginal). Falls back to the independent outer product when
    no cell is allowed (degenerate data).
    """
    M = allowed.astype(float).copy()
    if M.sum() <= 0:
        return np.outer(row_target, col_target)
    for _ in range(iterations):
        rs = M.sum(axis=1)
        rs[rs == 0] = 1.0
        M = M * (row_target / rs)[:, None]
        cs = M.sum(axis=0)
        cs[cs == 0] = 1.0
        M = M * (col_target / cs)[None, :]
        if (np.abs(M.sum(axis=1) - row_target).max() < tol
                and np.abs(M.sum(axis=0) - col_target).max() < tol):
            break
    return M


def _age_euro_joint_matrices(
    age_given_powertrain: Mapping[str, np.ndarray],
    euro_given_powertrain: Mapping[str, np.ndarray],
) -> dict[str, np.ndarray]:
    """Per-fuel joint ``P(age_band, euro_class | fuel)`` (rows=age, cols=euro).

    Drawing euro and age from independent KBA marginals and then masking age to
    the drawn Euro class destroys the KBA ``age|fuel`` marginal: Euro-6 (intro
    2015) forces cars <= 9 yr, so a Euro-6-heavy ``euro|fuel`` drags the fleet
    ~4-5 yr too young (measured: petrol 12.05 -> 7.33 yr). Instead fit a joint
    that honours BOTH committed KBA marginals (``age|fuel`` as row targets,
    ``euro|fuel`` as column targets) on the Euro-age consistency support (see
    :func:`_age_consistent_with_euro`) via :func:`_ipf_joint`, then draw
    ``(age, euro)`` jointly (:func:`_draw_age_euro_joint`). This preserves the
    KBA age marginal (fleet mean ~10.6 yr) AND the euro marginal (emissions).
    """
    ages = list(ft.AGE_BAND_LABELS)
    euros = list(ft.EURO_CLASS_LABELS)
    out: dict[str, np.ndarray] = {}
    for fuel, age_pmf in age_given_powertrain.items():
        r = np.asarray(age_pmf, dtype=float)
        r = r / r.sum() if r.sum() > 0 else np.ones(len(ages)) / len(ages)
        c = np.asarray(euro_given_powertrain.get(fuel, np.ones(len(euros))), dtype=float)
        c = c / c.sum() if c.sum() > 0 else np.ones(len(euros)) / len(euros)
        allowed = np.array(
            [[1.0 if _age_consistent_with_euro(a, e, fuel) else 0.0 for e in euros]
             for a in ages],
            dtype=float,
        )
        out[fuel] = _ipf_joint(allowed, r, c)
    return out


def _draw_age_euro_joint(rng: np.random.Generator, joint_matrix: np.ndarray,
                         tilt: Optional[np.ndarray] = None) -> tuple[str, str]:
    """Draw ``(age_band, euro_class)`` jointly from a per-fuel joint matrix.

    ``joint_matrix`` has rows=age bands, cols=Euro classes (see
    :func:`_age_euro_joint_matrices`). An optional ``tilt`` (length = #age bands)
    multiplicatively reweights the age rows before the joint draw and is
    renormalised, so the income->age signal shifts the age mix within the
    population without overriding the marginal (mean-preserving in expectation).
    """
    ages = list(ft.AGE_BAND_LABELS)
    euros = list(ft.EURO_CLASS_LABELS)
    M = np.asarray(joint_matrix, dtype=float).copy()
    if tilt is not None:
        M = M * np.asarray(tilt, dtype=float)[:, None]
    total = M.sum()
    if total <= 0:
        return ages[0], euros[0]
    flat = (M / total).ravel()
    k = int(rng.choice(len(flat), p=flat))
    ai, ei = divmod(k, len(euros))
    return ages[ai], euros[ei]


# --------------------------------------------------------------------------- #
# Brand / model conditional distributions (additive, isolated)
# --------------------------------------------------------------------------- #
def _model_given_segment(data_path: str) -> dict[str, pd.DataFrame]:
    """segment -> DataFrame[model, share] (FZ 12.1, model implies brand).

    Returned per-segment frames are normalised to a proper pmf over models. The
    brand is derived from the model string (the first token) at draw time.
    """
    df = ft.load_segment_model(data_path)
    out: dict[str, pd.DataFrame] = {}
    for seg, grp in df.groupby("segment"):
        sub = grp[["model", "share"]].copy()
        s = sub["share"].sum()
        if s > 0:
            sub["share"] = sub["share"] / s
            out[seg] = sub.reset_index(drop=True)
    return out


def _model_fuel_weight_vector(row) -> np.ndarray:
    """Weight vector over POWERTRAINS for one model (kba_model_fuel row).

    Tracked powertrains carry the model's registered-stock share. The
    Modellreihen table does NOT track gas/hydrogen/other; weighting them 1.0
    would boost them by ~1/tracked_share after renormalisation (scale bug), so
    they carry the MEAN tracked share instead -- scale-neutral: an untracked
    feasible powertrain keeps its Kreis proportion relative to the model's
    average tracked propensity.

    Args:
        row: mapping (DataFrame row or dict) with ``petrol_share``,
            ``diesel_share``, ``bev_share``, ``phev_share``, ``hybrid_share``.

    Returns:
        Length-8 float array of weights over ``POWERTRAINS``.
    """
    petrol = float(row["petrol_share"]); diesel = float(row["diesel_share"])
    bev = float(row["bev_share"]); phev = float(row["phev_share"])
    hybrid = float(row["hybrid_share"])
    neutral = float(np.mean([petrol, diesel, bev, phev, hybrid]))
    # Order = POWERTRAINS: petrol, diesel, gas, bev, phev, hybrid, hydrogen, other
    return np.array([petrol, diesel, neutral, bev, phev, hybrid, neutral, neutral],
                    dtype=float)


def _build_model_fuel_weights(mf_df: "pd.DataFrame") -> "dict[str, np.ndarray]":
    """Build the per-model fuel-type weight vector dict from kba_model_fuel.csv.

    The weight vector is aligned with ``POWERTRAINS`` and has length 8:
      [petrol_share, diesel_share, neutral(gas), bev_share, phev_share,
       hybrid_share, neutral(hydrogen), neutral(other)]
    where ``neutral`` is the mean of the five tracked shares (see
    :func:`_model_fuel_weight_vector`).

    Gas, hydrogen, and other are not tracked per model in the KBA source, so
    they receive the scale-neutral mean weight rather than a hardcoded 1.0 --
    a hardcoded 1.0 would boost a feasible-but-untracked powertrain by
    ~1/tracked_share after renormalisation (review Finding 1).

    Args:
        mf_df: DataFrame from :func:`braunschweig.data.kba.fleet_tables.load_model_fuel`.

    Returns:
        Dict mapping model string (``"MARKE MODELLREIHE"`` convention) to a
        length-8 float array of weights over ``POWERTRAINS``.
    """
    out: dict[str, np.ndarray] = {}
    for _, row in mf_df.iterrows():
        model = str(row["model"])
        out[model] = _model_fuel_weight_vector(row)
    return out


# --------------------------------------------------------------------------- #
# Task B2: EV-income tilt -- P(bev/phev | economic_status) vs the pooled MiD mix
# --------------------------------------------------------------------------- #
#: Minimum MiD base_weighted for a (status, powertrain) cell to be used
#: directly; below this the cell is treated as too sparse to carry a signal
#: and the tilt factor falls back to 1.0 (no-op) for that cell -- same
#: threshold and rationale as :data:`age_income.MIN_CELL_WEIGHT`.
EV_INCOME_MIN_CELL_WEIGHT: float = 30.0


@dataclass
class EvIncomeTiltModel:
    """Within-Kreis EV-income tilt: ``f_pt(status) = clip(P(pt|status) / P(pt|all), 0.2, 5.0)``.

    Built from :func:`~braunschweig.data.kba.fleet_tables.load_mid_antrieb_by_status`
    (MiD 2023 vehicle powertrain x economic status). For each economic status and
    each electric powertrain (``bev``, ``phev``) the tilt is the ratio of the
    status-conditional MiD powertrain share to the pooled ("all") MiD share,
    clipped to ``[0.2, 5.0]`` (the same anti-explosion band used by the Gemeinde
    and grid electric tilts, see :meth:`PowertrainModel._apply_gemeinde_tilt`).
    Every other powertrain carries a factor of 1.0 (untouched).

    CRITICAL PLACEMENT (see :func:`sample_fleet`, PASS 1): the tilt must be
    applied to the car's WORKING powertrain pmf strictly AFTER the
    ``unmasked_pmf`` snapshot is taken and BEFORE the model-feasibility mask.
    Because the Task 7 per-Kreis electric rake targets the mean of
    ``unmasked_pmf`` (which never sees this tilt), the tilt only REDISTRIBUTES
    electric mass within a Kreis toward higher-status households; it cannot
    drift the per-Kreis electric AGGREGATE away from the spatial (KBA) target
    (the same design as the income-age tilt vs the KBA age marginal).

    Thin-cell rule: a (status, powertrain) cell whose ``base_weighted`` is below
    :data:`EV_INCOME_MIN_CELL_WEIGHT` is treated as too sparse to carry a
    signal -- the factor is forced to 1.0 and the cell is counted/logged (a
    thin MiD cell must never inject noise into the fleet).
    """

    #: status -> length-len(POWERTRAINS) multiplicative factor vector (only the
    #: bev/phev entries can differ from 1.0).
    _factors: dict[str, np.ndarray]
    #: Statuses whose electric cells were ALL either missing or thin -- their
    #: factor vector is all-ones and a lookup counts as a fallback, not a
    #: primary hit (there is no real MiD signal behind it).
    _fallback_statuses: set = field(default_factory=set)
    # Mutable fallback counters (no-silent-fallback rule).
    _ev_income_primary: int = field(default=0)
    _ev_income_fallback: int = field(default=0)

    @classmethod
    def _from_dataframe(cls, df: pd.DataFrame) -> "EvIncomeTiltModel":
        """Build from a DataFrame with the ``mid2023_antrieb_by_status.csv`` schema.

        Exposed for unit tests that inject a synthetic table without touching
        the filesystem.
        """
        idx = {p: i for i, p in enumerate(POWERTRAINS)}
        all_rows = df[df["status"] == "all"].set_index("powertrain")
        factors: dict[str, np.ndarray] = {}
        fallback_statuses: set = set()
        n_thin_cells = 0
        n_checked_cells = 0
        for status in ft.STATUS_LABELS:
            vec = np.ones(len(POWERTRAINS), dtype=float)
            status_rows = df[df["status"] == status].set_index("powertrain")
            any_real_signal = False
            for pt in ELECTRIC_POWERTRAINS:
                n_checked_cells += 1
                if pt not in status_rows.index or pt not in all_rows.index:
                    logger.warning(
                        "[fleet_de] EV-income tilt: (status=%s, powertrain=%s) "
                        "missing from mid2023_antrieb_by_status.csv -> factor=1.0",
                        status, pt,
                    )
                    continue
                base_weighted = float(status_rows.loc[pt, "base_weighted"])
                if base_weighted < EV_INCOME_MIN_CELL_WEIGHT:
                    n_thin_cells += 1
                    logger.warning(
                        "[fleet_de] EV-income tilt: thin MiD cell (status=%s, "
                        "powertrain=%s, base_weighted=%.1f < %.0f) -> factor=1.0 "
                        "(a sparse cell must not inject noise).",
                        status, pt, base_weighted, EV_INCOME_MIN_CELL_WEIGHT,
                    )
                    continue
                p_all = float(all_rows.loc[pt, "share"])
                if p_all <= 0.0:
                    logger.warning(
                        "[fleet_de] EV-income tilt: pooled 'all' share for "
                        "powertrain '%s' is zero -> factor=1.0 for status '%s'.",
                        pt, status,
                    )
                    continue
                p_status = float(status_rows.loc[pt, "share"])
                factor = float(np.clip(p_status / p_all, 0.2, 5.0))
                vec[idx[pt]] = factor
                any_real_signal = True
            factors[status] = vec
            if not any_real_signal:
                fallback_statuses.add(status)
        if n_thin_cells:
            logger.warning(
                "[fleet_de] EV-income tilt: %d/%d (status, electric powertrain) "
                "cells below the thin-cell threshold (base_weighted < %.0f); "
                "factor forced to 1.0 for those cells.",
                n_thin_cells, n_checked_cells, EV_INCOME_MIN_CELL_WEIGHT,
            )
        return cls(_factors=factors, _fallback_statuses=fallback_statuses)

    @classmethod
    def from_data_path(cls, data_path: str) -> "EvIncomeTiltModel":
        """Construct from the committed ``mid2023_antrieb_by_status.csv``.

        Raises:
            FileNotFoundError: propagated from the loader when the derived CSV
                is absent (the caller, :meth:`FleetSampler.from_data_path`,
                catches this and leaves the tilt inactive).
        """
        df = ft.load_mid_antrieb_by_status(data_path)
        return cls._from_dataframe(df)

    def tilt(self, status: str) -> np.ndarray:
        """Return the length-len(POWERTRAINS) multiplicative factor vector for *status*.

        An all-ones vector means no income signal is applied (either the
        status is unknown, or every electric cell for it was missing/thin).
        Counts a primary hit only when at least one electric powertrain of
        *status* carries a real (non-thin, present) MiD-derived factor.
        """
        vec = self._factors.get(status)
        if vec is None:
            self._ev_income_fallback += 1
            logger.warning(
                "[fleet_de] EV-income tilt: unknown economic status '%s' -> "
                "factor=1.0 (no tilt applied).", status,
            )
            return np.ones(len(POWERTRAINS), dtype=float)
        if status in self._fallback_statuses:
            self._ev_income_fallback += 1
        else:
            self._ev_income_primary += 1
        return vec

    def log_fallback_rate(self) -> tuple[int, int]:
        """Log the primary-vs-fallback rate (no-silent-fallback rule)."""
        primary = self._ev_income_primary
        fallback = self._ev_income_fallback
        total = primary + fallback
        rate = (fallback / total) if total else 0.0
        (logger.warning if rate > 0.5 else logger.info)(
            "[fleet_de] EV-income tilt: primary %d/%d (%.1f%%), fallback %d (%.1f%%)",
            primary, total, 100.0 * primary / total if total else 0.0,
            fallback, 100.0 * rate,
        )
        return primary, fallback


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
@dataclass
class FleetSampler:
    """Bundled models for the per-vehicle generative chain.

    Holds the segment IPF, the per-Kreis powertrain model, and the Euro / age /
    brand-model conditional tables. Build via :meth:`from_data_path`.
    """

    segment_model: SegmentModel
    powertrain_model: PowertrainModel
    euro_given_powertrain: dict[str, np.ndarray]
    age_given_powertrain: dict[str, np.ndarray]
    #: Per-fuel joint P(age_band, euro_class | fuel), fit by IPF to both KBA
    #: marginals on the Euro-age consistency support (see
    #: :func:`_age_euro_joint_matrices`). Consumed by the consistency_v2 joint
    #: age/euro draw so the fleet age marginal matches KBA (~10.6 yr).
    age_euro_joint: dict[str, np.ndarray]
    model_given_segment: dict[str, pd.DataFrame]
    size_map: dict[str, str]
    # Task 6: model-feasible powertrain sets (Bug 2). Built from the HSN/TSN
    # lookup, which is local-only / gitignored; ``None`` when the CSV is absent
    # (then the feasibility mask is simply not applied — OFF-safe).
    feasible_fuels: Optional[FeasibleFuels] = None
    #: Task 6b: per-(Kreis, powertrain) joint P(age_band, euro_class | kreis,
    #: powertrain) built from Regionalstatistik 46251-03 per-Kreis Euro counts.
    #: ``None`` when kba_kreis_euro.csv is absent (then the national
    #: ``age_euro_joint`` is used unchanged -- byte-identical fallback). Task
    #: B3: a :class:`LazyKreisEuroJoint` (dict-like, computed and cached on
    #: first access) rather than a plain eager dict -- see that class's
    #: docstring for the ~400-Kreis performance rationale.
    age_euro_joint_kreis: Optional[Mapping[tuple, np.ndarray]] = None
    #: Task 10: per-model fuel-type weight vector over POWERTRAINS.
    #: Maps model string (same "MARKE MODELLREIHE" convention as kba_segment_model)
    #: to a length-8 numpy array aligned with POWERTRAINS.  Built from
    #: ``kba_model_fuel.csv``; ``None`` when the file is absent, which restores the
    #: binary (0/1) feasibility mask -- byte-identical fallback.
    model_fuel: Optional[dict[str, np.ndarray]] = None
    #: Task B2: EV-income tilt (bev/phev vs economic status) built from
    #: ``mid2023_antrieb_by_status.csv``. ``None`` when the file is absent, which
    #: disables the tilt entirely (PASS 1 in :func:`sample_fleet` checks
    #: ``sampler.ev_income_tilt is not None`` before ever calling it) --
    #: byte-identical fallback.
    ev_income_tilt: Optional[EvIncomeTiltModel] = None

    @classmethod
    def from_data_path(cls, data_path: str,
                       size_map: Optional[Mapping[str, str]] = None) -> "FleetSampler":
        segment_model = SegmentModel.from_data_path(data_path)
        powertrain_model = PowertrainModel.from_data_path(
            data_path, segment_model.segments)
        # Task 6: build the feasible-fuels model from the HSN/TSN lookup when the
        # (local-only) CSV is present; absence only disables the feasibility mask.
        feasible_fuels: Optional[FeasibleFuels] = None
        try:
            feasible_fuels = FeasibleFuels.from_data_path(data_path)
        except FileNotFoundError:
            logger.info(
                "[fleet_de] HSN/TSN lookup absent; model-feasible powertrain "
                "mask (Bug 2) disabled (consistency_v2 keeps the unmasked pmf)."
            )
        euro_given = _euro_given_powertrain(data_path)
        age_given = _age_given_powertrain(data_path)
        # Task 6b: try to build the per-Kreis euro joint from 46251-03.
        # Returns None when kba_kreis_euro.csv is absent -- fallback to national.
        euro_given_kreis = _euro_given_kreis_powertrain(data_path)
        if euro_given_kreis is not None:
            logger.info(
                "[fleet_de] per-Kreis euro joint (46251-03): building per-"
                "(Kreis, powertrain) age-euro IPF joints."
            )
            # Task B3: the per-Kreis euro joint now potentially covers every
            # German Kreis (Regionalstatistik 46251-03 is not ZGB-filtered any
            # more, see load_kreis_euro / _require_zgb_subset). Building all
            # ~400 Kreise x 8 powertrains eagerly was MEASURED at ~15 s
            # wall-time for a synthetic ~400-Kreis fixture (exceeds the
            # brief's ~10 s bound) -- most (kreis, powertrain) pairs are never
            # drawn by a given run's actual household population. Use the lazy,
            # cached mapping instead (see LazyKreisEuroJoint docstring); its
            # values are numerically identical to the eager builder.
            _build_start = time.perf_counter()
            age_euro_joint_kreis: Optional[Mapping[tuple, np.ndarray]] = LazyKreisEuroJoint(
                age_given, euro_given, euro_given_kreis)
            _build_seconds = time.perf_counter() - _build_start
            _n_kreise_euro = len({k for k, _pt in age_euro_joint_kreis.keys()})
            (logger.warning if _build_seconds > 10.0 else logger.info)(
                "[fleet_de] per-Kreis euro joint (46251-03): lazy index covers "
                "%d Kreise x %d powertrains (index built in %.3f s). Individual "
                "(kreis, powertrain) IPF joints are computed and cached on "
                "first draw, not eagerly (Task B3 performance note: eager "
                "build measured ~15 s at ~400-Kreis scale).",
                _n_kreise_euro, len(POWERTRAINS), _build_seconds,
            )
        else:
            logger.info(
                "[fleet_de] national euro joint (FZ27.4 fallback): "
                "kba_kreis_euro.csv absent; per-Kreis euro marginal disabled."
            )
            age_euro_joint_kreis = None
        # Task 10: per-model fuel-type weight vectors.  Build from kba_model_fuel.csv
        # when present; fall back to None (binary feasibility mask) when absent.
        model_fuel: Optional[dict[str, np.ndarray]] = None
        try:
            mf_df = ft.load_model_fuel(data_path)
            model_fuel = _build_model_fuel_weights(mf_df)
            logger.info(
                "[fleet_de] model-fuel weight: active (%d models).",
                len(model_fuel),
            )
        except FileNotFoundError:
            logger.info(
                "[fleet_de] model-fuel weight: absent -> binary feasibility mask."
            )
        # Task B2: EV-income tilt. Absent CSV -> tilt inactive, byte-identical
        # to the pre-Task-B2 behaviour (see EvIncomeTiltModel docstring for the
        # placement rationale -- the caller in sample_fleet only ever consults
        # ``sampler.ev_income_tilt`` when it is not None).
        ev_income_tilt: Optional[EvIncomeTiltModel] = None
        try:
            ev_income_tilt = EvIncomeTiltModel.from_data_path(data_path)
            logger.info(
                "[fleet_de] EV-income tilt: active "
                "(mid2023_antrieb_by_status.csv found)."
            )
        except FileNotFoundError:
            logger.info(
                "[fleet_de] EV-income tilt: mid2023_antrieb_by_status.csv "
                "absent -> tilt inactive (byte-identical to the no-tilt "
                "behaviour)."
            )
        return cls(
            segment_model=segment_model,
            powertrain_model=powertrain_model,
            euro_given_powertrain=euro_given,
            age_given_powertrain=age_given,
            age_euro_joint=_age_euro_joint_matrices(age_given, euro_given),
            model_given_segment=_model_given_segment(data_path),
            size_map=dict(size_map) if size_map is not None else {},
            feasible_fuels=feasible_fuels,
            age_euro_joint_kreis=age_euro_joint_kreis,
            model_fuel=model_fuel,
            ev_income_tilt=ev_income_tilt,
        )


def _draw_categorical(rng: np.random.Generator, labels: Sequence[str],
                      pmf: np.ndarray) -> str:
    pmf = np.asarray(pmf, dtype=float)
    total = pmf.sum()
    if total <= 0:
        return labels[0]
    return str(rng.choice(labels, p=pmf / total))


def _draw_age_consistent_with_euro(rng: np.random.Generator,
                                   age_pmf: np.ndarray, euro_class: str,
                                   powertrain: str) -> str:
    """Draw an age band that is consistent with the Euro class.

    The age pmf is masked to the bands consistent with ``euro_class`` (per
    :func:`_age_consistent_with_euro`) and renormalised. If no band is
    consistent (degenerate data) the unmasked pmf is used so a vehicle is always
    produced.
    """
    bands = list(ft.AGE_BAND_LABELS)
    mask = np.array(
        [_age_consistent_with_euro(b, euro_class, powertrain) for b in bands],
        dtype=float,
    )
    masked = age_pmf * mask
    if masked.sum() <= 0:
        masked = age_pmf
    return _draw_categorical(rng, bands, masked)


# --------------------------------------------------------------------------- #
# Task 7: per-Kreis electric-mass recalibration on the feasible support (Bug 2)
# --------------------------------------------------------------------------- #
def _electric_rake_factors(
    pmfs: np.ndarray, kreis_target_share: dict[str, float],
    electric_idx: dict[str, int], max_iterations: int = 50,
    tolerance: float = 1e-9, kreis: str = "?",
) -> tuple[np.ndarray, dict[str, float]]:
    """Per-electric-powertrain multiplicative scale factors for ONE Kreis.

    Task 6 masks every car's powertrain pmf to its model-feasible set, which
    systematically removes bev/phev mass from combustion-only models and so
    drives the per-Kreis electric share BELOW the FZ 27.15 target. This computes,
    for each electric powertrain ``e`` (bev, phev), a scale factor ``alpha_e``
    such that scaling every car's ``pmf_i[e]`` by ``alpha_e`` and renormalising
    makes the EXPECTED per-Kreis count of ``e`` equal the FZ 27.15 target
    (``N_kreis * share_e``).

    Feasibility is preserved by construction: scaling only re-weights cars that
    already carry nonzero feasible mass on ``e`` (a combustion-only car has
    ``pmf_i[e] == 0`` and stays 0 under any finite scale). bev and phev are
    coupled (raising one steals renormalised mass from the other), so the two
    factors are found by a small fixed-point iteration.

    Parameters
    ----------
    pmfs : (n_cars, n_powertrains) array of the per-car *masked* powertrain pmfs
        for the cars of this Kreis (each row sums to 1).
    kreis_target_share : {powertrain -> FZ 27.15 share} for this Kreis (the
        electric entries are the rake targets).
    electric_idx : {electric powertrain -> column index in ``pmfs``}.
    kreis : Kreis code, used only to make the WARNING log messages below
        traceable; has no effect on the computed factors/residuals.

    Returns
    -------
    (factors, residuals) :
        ``factors`` is a length-``n_powertrains`` vector of multiplicative scale
        factors (1.0 for every non-electric powertrain); ``residuals`` maps each
        electric powertrain to ``achieved_share - target_share`` AFTER raking
        (≈0 when reachable; the signed unreachable residual otherwise).

        Two UNREACHABLE cases are possible and both are logged here as a
        no-silent-fallback WARNING (F5):

        * Too LITTLE feasible mass (the maximum achievable share -- all
          feasible mass forced onto ``e`` -- is below the target): the factor
          is driven high and the residual is reported NEGATIVE
          (``resid < -0.01``).
        * Too MUCH forced feasible mass (e.g. every car in the Kreis is
          assigned to an electric-only model, so ``e`` already saturates the
          masked pmf at 1.0 for every car): the achieved share cannot be
          scaled DOWN below what the mask already forces, and the residual is
          reported POSITIVE (``resid > +0.01``). This over-shoot mirrors the
          under-shoot case and typically signals a high-EV Kreis where the
          model-feasibility mask leaves too few non-electric alternatives.
    """
    n_cars, n_pt = pmfs.shape
    factors = np.ones(n_pt, dtype=float)
    residuals: dict[str, float] = {}
    if n_cars == 0:
        return factors, {e: 0.0 for e in electric_idx}

    electric_cols = list(electric_idx.values())
    targets = np.array(
        [kreis_target_share.get(e, 0.0) for e in electric_idx], dtype=float)
    cols = np.array(electric_cols, dtype=int)

    # Fixed-point iteration on the electric scale factors. After each update we
    # renormalise every car's pmf with the current factors and measure the mean
    # electric shares; we then nudge each factor by target/achieved. Because the
    # electric columns couple only through the (shared) renormalisation
    # denominator, this converges in a handful of iterations.
    alpha = np.ones(len(cols), dtype=float)
    for _ in range(max_iterations):
        scaled = pmfs.copy()
        scaled[:, cols] *= alpha
        denom = scaled.sum(axis=1, keepdims=True)
        # A car whose whole pmf is zero cannot happen (masked pmfs sum to 1), but
        # guard anyway so a degenerate row contributes nothing.
        np.divide(scaled, denom, out=scaled, where=denom > 0)
        achieved = scaled[:, cols].mean(axis=0)
        # Update factors towards the target; clip the denominator so a Kreis with
        # zero feasible mass for an electric powertrain does not divide by zero.
        ratio = np.divide(
            targets, achieved,
            out=np.full_like(targets, 1.0), where=achieved > 0,
        )
        if np.all(np.abs(achieved - targets) <= tolerance):
            alpha = alpha * ratio  # final nudge (no-op at tolerance)
            break
        alpha = alpha * ratio
        # Cars with zero feasible mass for e keep achieved==0 forever -> ratio
        # stays 1 there and the target is simply unreachable (handled below).
        alpha = np.clip(alpha, 0.0, 1e12)

    # Final measured shares with the converged factors.
    scaled = pmfs.copy()
    scaled[:, cols] *= alpha
    denom = scaled.sum(axis=1, keepdims=True)
    np.divide(scaled, denom, out=scaled, where=denom > 0)
    achieved = scaled[:, cols].mean(axis=0)

    for j, e in enumerate(electric_idx):
        factors[cols[j]] = alpha[j]
        resid = float(achieved[j] - targets[j])
        residuals[e] = resid
        # No silent fallback (F5): an unreachable target -- from either side --
        # means the model-feasibility mask leaves too little (or too much)
        # electric-capable mass to hit the FZ 27.15 share for this Kreis.
        if resid < -0.01:
            logger.warning(
                "[fleet_de] Task 7 per-Kreis electric rake: Kreis %s "
                "%s UNREACHABLE (under target) -- target %.4f, max achievable "
                "%.4f (residual %.4f); too few electric-capable cars.",
                kreis, e, targets[j], targets[j] + resid, resid,
            )
        elif resid > 0.01:
            logger.warning(
                "[fleet_de] Task 7 per-Kreis electric rake: Kreis %s "
                "%s UNREACHABLE (over target) -- target %.4f, min achievable "
                "%.4f (residual %.4f); forced electric-only models cannot be "
                "scaled down further.",
                kreis, e, targets[j], targets[j] + resid, resid,
            )
    return factors, residuals


def sample_fleet(df_cars: pd.DataFrame, data_path: str, random_seed: int,
                 size_map: Optional[Mapping[str, str]] = None,
                 sampler: Optional[FleetSampler] = None,
                 model_brands: bool = True,
                 consistency_v2: bool = True,
                 age_income_coupling: bool = True,
                 age_euro_joint: bool = True,
                 ev_income_tilt: bool = True,
                 ) -> "tuple[pd.DataFrame, pd.DataFrame] | tuple[pd.DataFrame, pd.DataFrame, dict]":
    """Draw a full vehicle specification for every household car.

    Parameters
    ----------
    df_cars : DataFrame with one row per household car carrying the columns
        ``economic_status`` (one of
        :data:`braunschweig.data.kba.fleet_tables.STATUS_LABELS`),
        ``kreis_ags5`` (home Kreis AGS-5 string), ``gemeinde`` (home Gemeinde
        name; may be missing/``NaN``) and ``raumtyp`` (RegioStaR-7 code 71..77;
        may be missing/``NaN``).
    data_path : the synpp ``data_path`` (the parent of ``braunschweig/kba/...``).
    random_seed : seed for the single :class:`numpy.random.Generator` used for
        every draw (reproducible).
    size_map : optional segment -> HBEFA size-class overrides for the HBEFA
        mapping (``hbefa_segment_size_map`` config key).
    sampler : optional pre-built :class:`FleetSampler` (avoids re-reading the
        CSVs when sampling several frames); built from ``data_path`` if ``None``.
    model_brands : when ``False`` the additive (non-HBEFA) brand/model attributes
        are not drawn (the ``brand``/``model`` columns are left empty); the
        emissions-relevant segment->powertrain->euro->HBEFA chain is unchanged.
        Driven by the ``fleet_model_brands`` config key (default ``True``).
    consistency_v2 : when ``True`` (default), vehicles whose segment draw lands on
        ``"sonstige"`` are redistributed: a replacement segment is redrawn from
        the KBA segment marginal restricted to the modelled (model-bearing)
        segments, proportional to ``segment_share``. This ensures no emitted
        vehicle carries the ``"sonstige"`` segment (F11: an empty brand is NOT
        fully ruled out -- a modelled segment that happens to lack Modellreihen
        rows, e.g. ``wohnmobile``, can still yield an empty brand/model, which
        then takes the intended unmasked-pmf fallback). When ``False``, the legacy
        behaviour is preserved (``sonstige`` can appear with an empty brand).
    age_income_coupling : when ``True`` (default) AND ``consistency_v2=True``,
        the per-car age PMF is multiplied by the MiD income-age tilt from
        :class:`~braunschweig.synthesis.vehicles.age_income.AgeIncomeModel`
        before the Euro-consistency draw. The tilt is built once per call and
        indexed by ``(segment, economic_status)``. When ``False`` OR when
        ``consistency_v2=False``, the unmodified ``P(age | powertrain)`` is used
        (byte-identical to the pre-Feature-B behaviour).
    ev_income_tilt : when ``True`` (default) AND ``consistency_v2=True`` AND the
        sampler carries an active :class:`EvIncomeTiltModel` (built from
        ``mid2023_antrieb_by_status.csv`` by :meth:`FleetSampler.from_data_path`;
        ``None`` when that CSV is absent), PASS 1 multiplies the car's working
        powertrain pmf by ``EvIncomeTiltModel.tilt(economic_status)`` strictly
        AFTER the Task-7 rake-target (``unmasked_pmf``) snapshot and BEFORE the
        model-feasibility mask. This redistributes bev/phev mass toward
        higher-status households WITHIN each Kreis while leaving the Task-7
        per-Kreis electric AGGREGATE (rake target = mean ``unmasked_pmf``)
        unchanged. ``False``, ``consistency_v2=False``, or an absent CSV leave
        the powertrain pmf untouched (byte-identical to the pre-Task-B2
        behaviour).

    Returns
    -------
    When ``consistency_v2=True`` (default):
        ``(df_spec, df_vehicle_types, validation_summary)`` — a 3-tuple;
        ``validation_summary`` is the dict returned by
        :func:`~braunschweig.synthesis.vehicles.fleet_validation.validate_realised_margins`.
    When ``consistency_v2=False`` (legacy path):
        ``(df_spec, df_vehicle_types)`` — 2-tuple, byte-identical to the
        pre-Task-3 legacy behaviour.
    ``df_spec`` is ``df_cars`` with the added spec columns
    ``segment, powertrain, euro_class, age_band, age, brand, model,
    type_id, hbefa_cat, hbefa_tech, hbefa_size, hbefa_emission``;
    ``df_vehicle_types`` holds the distinct HBEFA :class:`VehicleType`
    records to register (one per ``type_id``).
    """
    required = {"economic_status", "kreis_ags5", "gemeinde", "raumtyp"}
    missing = required - set(df_cars.columns)
    if missing:
        raise ValueError(
            f"sample_fleet: df_cars is missing required columns {sorted(missing)}"
        )

    if sampler is None:
        sampler = FleetSampler.from_data_path(data_path, size_map=size_map)
    elif size_map is not None:
        sampler.size_map = dict(size_map)

    rng = np.random.default_rng(random_seed)
    segments = sampler.segment_model.segments

    n = len(df_cars)
    out_segment = [""] * n
    out_powertrain = [""] * n
    out_euro = [""] * n
    out_age_band = [""] * n
    out_age = [0] * n
    out_brand = [""] * n
    out_model = [""] * n
    out_type_id = [""] * n
    out_cat = [""] * n
    out_tech = [""] * n
    out_size = [""] * n
    out_emission = [""] * n

    vehicle_types: dict[str, hbefa.VehicleType] = {}
    size_fallback_counter: dict[str, int] = {}
    brand_model_fallback = 0

    # Task 5: precompute the redistribution pmf (modelled segments only) once.
    # Modelled = those present in sampler.model_given_segment (have FZ 12.1 rows).
    # brand_source_list collects per-row provenance for Task 8 to surface.
    _modelled_segments: list[str] = []
    _modelled_seg_pmf: Optional[np.ndarray] = None
    # consistency_v2 relies on drawing brand/model; disable it when model_brands=False.
    if consistency_v2 and not model_brands:
        consistency_v2 = False
    if consistency_v2:
        df_seg_share = ft.load_segment_powertrain(data_path)
        seg_share_map = (
            df_seg_share.set_index("segment")["segment_share"]
            .to_dict()
        )
        _modelled_segments = [
            s for s in segments
            if s in sampler.model_given_segment and s != "sonstige"
        ]
        if _modelled_segments:
            raw = np.array(
                [seg_share_map.get(s, 0.0) for s in _modelled_segments],
                dtype=float,
            )
            total = raw.sum()
            _modelled_seg_pmf = raw / total if total > 0 else (
                np.ones(len(_modelled_segments)) / len(_modelled_segments)
            )
        else:
            logger.warning(
                "[fleet_de] consistency_v2: no modelled segments found; "
                "sonstige redistribution disabled."
            )
            _modelled_seg_pmf = None

    sonstige_redistributed_count = 0
    # brand_source_list: per-row marker ("kba_model" | "sonstige_redistributed").
    # Task 8 hook: promoted to a df_spec column in the v2 assembly block.
    brand_source_list: list[str] = ["kba_model"] * n

    # Feature B (Task 3): income-age tilt model — built once per call, used only
    # in the v2 age draw when age_income_coupling=True.
    age_model: Optional[AgeIncomeModel] = None
    if consistency_v2 and age_income_coupling:
        age_model = AgeIncomeModel.from_data_path(data_path)

    # Task 6 (consistency_v2): model-feasible powertrain mask (Bug 2).
    # When the HSN/TSN-derived feasible-fuels model is available we draw the
    # model BEFORE the powertrain and mask the powertrain pmf to the model's
    # feasible powertrain set. powertrain_feasibility_list carries per-row
    # provenance ("model_constrained" | "segment_fallback") for the Task 8 hook;
    # _feasibility_fallback counts cars whose mask zeroed the whole pmf (no
    # overlap) so the unmasked pmf was kept.
    _feasible_fuels = sampler.feasible_fuels if consistency_v2 else None
    powertrain_feasibility_list: list[str] = ["segment_fallback"] * n
    _feasibility_fallback = 0
    _powertrain_idx = {p: i for i, p in enumerate(POWERTRAINS)}

    records = df_cars.to_dict(orient="records")

    def _finalize_spec(i: int, segment: str, powertrain: str, euro_class: str,
                       age_band: str, brand: str, model: str) -> None:
        """Map the (powertrain, euro, segment) triple to HBEFA and store row i."""
        vt = hbefa.vehicle_type_for(
            powertrain, euro_class, segment,
            size_map=sampler.size_map,
            fallback_counter=size_fallback_counter,
        )
        vehicle_types.setdefault(vt.type_id, vt)
        out_segment[i] = segment
        out_powertrain[i] = powertrain
        out_euro[i] = euro_class
        out_age_band[i] = age_band
        out_age[i] = AGE_BAND_MIDPOINT_YEARS[age_band]
        out_brand[i] = brand
        out_model[i] = model
        out_type_id[i] = vt.type_id
        out_cat[i] = vt.hbefa_category
        out_tech[i] = vt.hbefa_technology
        out_size[i] = vt.hbefa_size
        out_emission[i] = vt.hbefa_emission

    if not consistency_v2:
        # ===== LEGACY PATH (consistency_v2=False): byte-identical to before. =====
        for i, car in enumerate(records):
            status = car["economic_status"]
            kreis = str(car["kreis_ags5"])
            gemeinde = car.get("gemeinde")
            if pd.isna(gemeinde):
                gemeinde = None
            raumtyp = car.get("raumtyp")
            raumtyp = int(raumtyp) if pd.notna(raumtyp) else None

            # 1. segment <- income-coupled segment IPF.
            seg_pmf = sampler.segment_model.segment_probabilities(status, raumtyp)
            segment = _draw_categorical(rng, segments, seg_pmf)

            # 2. powertrain <- P(powertrain | segment) raked per Kreis + Gemeinde tilt.
            # The sub-communal grid EV tilt (T9b), like the A4 euro override, is a
            # consistency_v2-only feature: this legacy loop stays a VERBATIM copy of
            # the original draw and does NOT pass the grid columns, so the
            # consistency_v2=False path is byte-identical regardless of whether the
            # run attached grid_ev_share/gemeinde_grid_mean to df_cars.
            pt_pmf = sampler.powertrain_model.powertrain_probabilities(
                segment, kreis, gemeinde)
            powertrain = _draw_categorical(rng, list(POWERTRAINS), pt_pmf)

            # 3. euro_class <- P(euro | powertrain).
            # NOTE: the pure-electric euro="electric" override (A4) is applied ONLY on
            # the consistency_v2 path below. This legacy (consistency_v2=False) loop is
            # kept a VERBATIM copy of the original draw logic -- no per-loop overrides are
            # added here -- so electric rows keep their drawn combustion euro. Its OUTPUT
            # may still differ from the committed golden because SHARED model components
            # improved (the segment-status seed A3, the age/euro joint #93); the golden
            # (test_off_path_byte_identical) is regenerated after the branch stabilises,
            # guarded by the invariant that this loop's CODE stays diff-free vs the base.
            euro_pmf = sampler.euro_given_powertrain[powertrain]
            euro_class = _draw_categorical(rng, list(ft.EURO_CLASS_LABELS), euro_pmf)

            # 4. age band <- P(age | powertrain), consistent with the Euro class.
            age_pmf = sampler.age_given_powertrain[powertrain]
            age_band = _draw_age_consistent_with_euro(
                rng, age_pmf, euro_class, powertrain)

            # 5. brand + model <- P(model | segment) (isolated, guarded, additive).
            if model_brands:
                brand, model = _draw_brand_model(rng, sampler, segment)
                if not model:
                    brand_model_fallback += 1
            else:
                brand, model = "", ""

            _finalize_spec(i, segment, powertrain, euro_class, age_band, brand, model)
    else:
        # ===== CONSISTENCY_V2 PATH (Bug 2 + Task 7 calibration). =================
        # PASS 1: per car draw segment (+ sonstige redistribution) and brand/model,
        # then build the model-feasibility-MASKED powertrain pmf (Task 6). The
        # powertrain itself is NOT drawn yet: Task 7 first rakes the per-Kreis
        # electric mass on these masked pmfs so the masking no longer drifts the
        # FZ 27.15 bev/phev share. euro/age/HBEFA are derived in PASS 2 from the
        # final (raked) powertrain, so they stay internally consistent.
        car_kreis: list[str] = [""] * n
        car_segment: list[str] = [""] * n
        car_status: list[str] = [""] * n
        car_brand: list[str] = [""] * n
        car_model: list[str] = [""] * n
        car_pmf: list[np.ndarray] = [None] * n  # type: ignore[list-item]
        # Unmasked (but Gemeinde-tilted) pmf -- the rake target so the rake undoes
        # ONLY the feasibility-masking electric deficit and PRESERVES the FZ 27.17
        # Gemeinde tilt (the tilt is already in this pmf). Its per-Kreis mean
        # electric mass equals the FZ 27.15 share for the no-tilt case.
        car_unmasked_pmf: list[np.ndarray] = [None] * n  # type: ignore[list-item]
        # Task A2: accumulate the EFFECTIVE per-car segment pmf for the
        # realised-margin validator -- seg_pmf with its "sonstige" mass
        # redistributed over the modelled segments exactly as the redraw
        # below, so the validator target reflects the status/raumtyp-
        # conditioned + sonstige-redistributed distribution PASS 1 actually
        # draws from, not the raw national KBA FZ 27.10 marginal.
        _exp_segment = np.zeros(len(segments))
        for i, car in enumerate(records):
            status = car["economic_status"]
            kreis = str(car["kreis_ags5"])
            gemeinde = car.get("gemeinde")
            if pd.isna(gemeinde):
                gemeinde = None
            raumtyp = car.get("raumtyp")
            raumtyp = int(raumtyp) if pd.notna(raumtyp) else None

            # 1. segment <- income-coupled segment IPF.
            seg_pmf = sampler.segment_model.segment_probabilities(status, raumtyp)
            segment = _draw_categorical(rng, segments, seg_pmf)

            # Task A2: effective segment pmf for the validator -- seg_pmf with
            # its "sonstige" mass redistributed over the modelled segments
            # exactly as the redraw below (computed from seg_pmf BEFORE the
            # draw, so it reflects the target distribution, not this car's
            # realised draw).
            eff_seg = np.asarray(seg_pmf, dtype=float).copy()
            if _modelled_seg_pmf is not None:
                s_idx = segments.index("sonstige")
                mass = eff_seg[s_idx]
                eff_seg[s_idx] = 0.0
                for j, seg_name in enumerate(_modelled_segments):
                    eff_seg[segments.index(seg_name)] += mass * float(_modelled_seg_pmf[j])
            _exp_segment += eff_seg / eff_seg.sum()

            # Task 5: if sonstige drawn, redraw from modelled segments.
            if segment == "sonstige" and _modelled_seg_pmf is not None:
                segment = _draw_categorical(
                    rng, _modelled_segments, _modelled_seg_pmf)
                brand_source_list[i] = "sonstige_redistributed"
                sonstige_redistributed_count += 1

            # 2. brand + model <- P(model | segment) (isolated, guarded, additive).
            if model_brands:
                brand, model = _draw_brand_model(rng, sampler, segment)
                if not model:
                    brand_model_fallback += 1
            else:
                brand, model = "", ""

            # 3. feasible-fuels mask (Task 6): derive the powertrain set this model
            # can carry. None -> unknown model -> no mask.
            # T9b: pass optional grid columns (None when absent -> no-op).
            _grid_ev = car.get("grid_ev_share")
            _gem_mean = car.get("gemeinde_grid_mean")
            pt_pmf = sampler.powertrain_model.powertrain_probabilities(
                segment, kreis, gemeinde,
                grid_ev_share=_grid_ev,
                gemeinde_grid_mean=_gem_mean)
            unmasked_pmf = pt_pmf.copy()  # Task 7 rake target (tilt-preserving).
            # Task B2: EV-income tilt -- applied to the WORKING pt_pmf only, NEVER
            # to unmasked_pmf captured just above. The Task 7 per-Kreis electric
            # rake (below) targets the mean of unmasked_pmf (spatial-only), so this
            # tilt cannot drift the per-Kreis electric AGGREGATE away from the KBA
            # target; it only redistributes electric mass within the Kreis toward
            # higher-status households. Must stay strictly BEFORE the feasibility
            # mask below so it composes with masking exactly like the Gemeinde/grid
            # tilts (CRITICAL PLACEMENT -- see EvIncomeTiltModel docstring).
            if ev_income_tilt and sampler.ev_income_tilt is not None:
                pt_pmf = pt_pmf * sampler.ev_income_tilt.tilt(status)
            feasible = None
            if _feasible_fuels is not None and model:
                feasible = _feasible_fuels.model_feasible_powertrains(
                    brand, model_family(canonical_brand(brand) or "", model))
            if feasible is not None:
                # Task 10: use per-model fuel-type weights inside the feasible set
                # so the powertrain draw is biased toward the powertrains that model
                # is actually registered with (e.g. Golf -> petrol/diesel; Model Y ->
                # BEV).  model_fuel absent OR model not in dict -> wv = all-ones ->
                # EXACTLY the prior binary (0/1) mask (byte-identical fallback).
                w = (sampler.model_fuel.get(model)
                     if (sampler.model_fuel is not None and model) else None)
                wv = w if w is not None else np.ones(len(POWERTRAINS))
                mask = np.array(
                    [wv[k] if p in feasible else 0.0 for k, p in enumerate(POWERTRAINS)],
                    dtype=float,
                )
                pt_pmf_masked = pt_pmf * mask
                if pt_pmf_masked.sum() > 0:
                    pt_pmf = pt_pmf_masked
                    powertrain_feasibility_list[i] = "model_constrained"
                elif w is not None and (
                    np.array([1.0 if p in feasible else 0.0
                              for p in POWERTRAINS]) * pt_pmf
                ).sum() > 0:
                    # All-zero weighted mask but the binary mask would not be zero:
                    # the model's tracked shares are all 0 on the feasible powertrains
                    # (e.g. a model recorded as gas-only but feasible set is petrol/diesel).
                    # Fall back to the binary mask and count it so the fallback rate is
                    # observable.
                    binary_mask = np.array(
                        [1.0 if p in feasible else 0.0 for p in POWERTRAINS],
                        dtype=float,
                    )
                    pt_pmf_masked = pt_pmf * binary_mask
                    pt_pmf = pt_pmf_masked
                    powertrain_feasibility_list[i] = "model_constrained"
                    _feasibility_fallback += 1  # count soft-weight zero-sum as fallback
                else:
                    # No overlap (even with binary mask): keep the UNMASKED pmf.
                    _feasibility_fallback += 1
                    powertrain_feasibility_list[i] = "segment_fallback"
            # Normalise so the stored pmf is a proper distribution for the rake.
            s = pt_pmf.sum()
            pt_pmf = (pt_pmf / s) if s > 0 else (
                np.ones(len(POWERTRAINS)) / len(POWERTRAINS))

            car_kreis[i] = kreis
            car_segment[i] = segment
            car_status[i] = status
            car_brand[i] = brand
            car_model[i] = model
            car_pmf[i] = pt_pmf
            car_unmasked_pmf[i] = unmasked_pmf

        # Task 7: per-Kreis electric-mass rake on the feasible support. For each
        # Kreis, scale every car's masked bev/phev mass so the EXPECTED per-Kreis
        # electric share equals the per-Kreis mean of the UNMASKED (Gemeinde-
        # tilted) pmf -- i.e. undo ONLY the feasibility-masking deficit and
        # preserve the FZ 27.17 tilt. For the no-tilt case the unmasked per-Kreis
        # mean electric mass equals the FZ 27.15 share exactly (the PowertrainModel
        # rake guarantees it), so the per-Kreis bev/phev share returns to FZ 27.15.
        # Feasibility is preserved (a combustion-only car has 0 electric mass and
        # stays 0 under any finite scale).
        electric_idx = {e: _powertrain_idx[e] for e in ELECTRIC_POWERTRAINS}
        kreis_factors: dict[str, np.ndarray] = {}
        rows_by_kreis: dict[str, list[int]] = {}
        for i in range(n):
            rows_by_kreis.setdefault(car_kreis[i], []).append(i)
        for kreis, rows in rows_by_kreis.items():
            pmfs = np.array([car_pmf[i] for i in rows], dtype=float)
            unmasked = np.array([car_unmasked_pmf[i] for i in rows], dtype=float)
            # Target electric share = mean unmasked (tilted) electric mass.
            target = {
                e: float(unmasked[:, idx].mean())
                for e, idx in electric_idx.items()
            }
            # Unreachable-target WARNINGs (both under- and over-shoot, F5) are
            # logged inside _electric_rake_factors itself, keyed by ``kreis``.
            factors, residuals = _electric_rake_factors(
                pmfs, target, electric_idx, kreis=kreis)
            kreis_factors[kreis] = factors

        # PASS 2: apply the per-Kreis rake factor, draw the powertrain, then
        # re-derive euro/age from the FINAL powertrain (internal consistency) and
        # map to HBEFA.
        # Task 3: collect per-car effective inputs for the realised-margin
        # validator (_effective_expected).  We need the joint BEFORE the draw
        # (i.e. for the DRAWN powertrain) to compute the expected age/euro
        # marginals that correspond to the actual draws.
        _v3_joints: list[np.ndarray] = [None] * n  # type: ignore[list-item]
        _v3_tilts: list[Optional[np.ndarray]] = [None] * n
        _v3_pmfs: list[np.ndarray] = [None] * n  # type: ignore[list-item]
        _v3_kreis_factors: list[np.ndarray] = [None] * n  # type: ignore[list-item]
        # A4-revised (Task 6): also record the drawn powertrain so _effective_expected
        # can mirror the ELECTRIC_EURO override when computing the expected euro
        # marginal (pure-electric rows contribute only to the "electric" bucket).
        _v3_powertrains: list[str] = [""] * n
        for i in range(n):
            pmf = car_pmf[i] * kreis_factors[car_kreis[i]]
            powertrain = _draw_categorical(rng, list(POWERTRAINS), pmf)
            # Draw (age, euro) JOINTLY from the per-fuel IPF joint so BOTH KBA
            # marginals (age|fuel, euro|fuel) are honoured on the Euro-age
            # consistency support -- this fixes the euro-first age collapse that
            # made the fleet ~4 yr too young (petrol 12.05 -> 7.33). The income
            # tilt (Feature B) reweights the age rows (mean-preserving). Setting
            # age_euro_joint=False restores the legacy euro-first draw + age mask.
            # F6 NOTE: this age_euro_joint=False branch is NOT wired to any config
            # key (production always uses True); it is reachable only from direct/
            # test calls. On that branch the validator's expected age/euro (derived
            # from the joint in _effective_expected) would diverge from the actual
            # euro-first+mask draw and false-alarm -- acceptable for a test-only path.
            if age_euro_joint:
                tilt = (age_model.age_tilt(car_segment[i], car_status[i])
                        if age_model is not None else None)
                # Task 6b: select the per-Kreis joint when available; fall back
                # to the national per-powertrain joint otherwise. Use the SAME
                # selected joint for both the draw and the _v3_joints recording
                # (internal consistency).
                _selected_joint: np.ndarray
                if sampler.age_euro_joint_kreis is not None:
                    _selected_joint = sampler.age_euro_joint_kreis.get(
                        (car_kreis[i], powertrain),
                        sampler.age_euro_joint[powertrain],
                    )
                else:
                    _selected_joint = sampler.age_euro_joint[powertrain]
                age_band, euro_class = _draw_age_euro_joint(
                    rng, _selected_joint, tilt)
                # Pure-electric drivetrains (BEV / hydrogen) carry no combustion
                # Euro stage; override to the real "electric" category.  PHEV
                # and hybrid DO have a combustion engine, so they keep their
                # drawn euro (HBEFA emission_concept_for ignores euro for
                # non-combustion technologies regardless).
                if powertrain in hbefa.ELECTRIC_EURO_POWERTRAINS:
                    euro_class = hbefa.ELECTRIC_EURO
            else:
                euro_pmf = sampler.euro_given_powertrain[powertrain]
                euro_class = _draw_categorical(
                    rng, list(ft.EURO_CLASS_LABELS), euro_pmf)
                # Pure-electric drivetrains (BEV / hydrogen) have no combustion
                # Euro stage; override to the real "electric" category.
                if powertrain in hbefa.ELECTRIC_EURO_POWERTRAINS:
                    euro_class = hbefa.ELECTRIC_EURO
                age_pmf = sampler.age_given_powertrain[powertrain]
                if age_model is not None:
                    tilt = age_model.age_tilt(car_segment[i], car_status[i])
                    age_pmf = age_pmf * tilt   # multiplicative; _draw_age renormalises
                else:
                    tilt = None
                age_band = _draw_age_consistent_with_euro(
                    rng, age_pmf, euro_class, powertrain)
                _selected_joint = sampler.age_euro_joint[powertrain]
            _finalize_spec(
                i, car_segment[i], powertrain, euro_class, age_band,
                car_brand[i], car_model[i])
            # Task 3: record the effective inputs used for this car so that the
            # validator can compare the aggregate expected marginals to the
            # realised marginals.
            _v3_pmfs[i] = car_pmf[i]
            _v3_kreis_factors[i] = kreis_factors[car_kreis[i]]
            _v3_joints[i] = _selected_joint  # Task 6b: per-Kreis when available
            _v3_tilts[i] = tilt
            _v3_powertrains[i] = powertrain  # A4-revised: used to mirror the "electric" override

    df_spec = df_cars.copy()
    df_spec["segment"] = out_segment
    df_spec["powertrain"] = out_powertrain
    df_spec["euro_class"] = out_euro
    df_spec["age_band"] = out_age_band
    df_spec["age"] = out_age
    df_spec["brand"] = out_brand
    df_spec["model"] = out_model
    df_spec["type_id"] = out_type_id
    df_spec["hbefa_cat"] = out_cat
    df_spec["hbefa_tech"] = out_tech
    df_spec["hbefa_size"] = out_size
    df_spec["hbefa_emission"] = out_emission
    # Task 8: surface provenance columns only in the v2 path (OFF path stays
    # byte-identical for all existing columns; do NOT add them there).
    if consistency_v2:
        df_spec["brand_source"] = brand_source_list
        df_spec["powertrain_feasibility"] = powertrain_feasibility_list

    df_vehicle_types = pd.DataFrame.from_records(
        [vt.as_record() for vt in vehicle_types.values()]
    )

    # Fallback observability (project no-silent-fallback rule).
    sampler.powertrain_model.log_fallback_rate()
    if age_model is not None:
        age_model.log_fallback_rate()
    if consistency_v2 and ev_income_tilt and sampler.ev_income_tilt is not None:
        sampler.ev_income_tilt.log_fallback_rate()
    _log_simple_fallback("HBEFA size map", sum(size_fallback_counter.values()), n)
    if model_brands:
        _log_simple_fallback("brand/model draw", brand_model_fallback, n)
    if consistency_v2 and sonstige_redistributed_count:
        rate = sonstige_redistributed_count / n if n else 0.0
        logger.info(
            "[fleet_de] sonstige redistribution (consistency_v2): "
            "%d/%d vehicles (%.1f%%) redistributed to modelled segments.",
            sonstige_redistributed_count, n, 100.0 * rate,
        )

    # Task 6: model-feasible powertrain mask observability (consistency_v2).
    if consistency_v2 and _feasible_fuels is not None:
        n_constrained = sum(
            1 for s in powertrain_feasibility_list if s == "model_constrained")
        c_rate = n_constrained / n if n else 0.0
        logger.info(
            "[fleet_de] model-feasible powertrain mask (consistency_v2, Bug 2): "
            "%d/%d vehicles (%.1f%%) model-constrained; %d (%.1f%%) "
            "no-overlap fallbacks (unmasked pmf kept).",
            n_constrained, n, 100.0 * c_rate,
            _feasibility_fallback,
            100.0 * (_feasibility_fallback / n if n else 0.0),
        )

    # Task 3: validate realised fleet marginals vs effective target PMFs
    # (consistency_v2 path only; legacy path stays byte-identical).
    if consistency_v2:
        from braunschweig.synthesis.vehicles import fleet_validation as _fv

        _expected = _effective_expected(
            car_pmfs=_v3_pmfs,
            kreis_factors=_v3_kreis_factors,
            age_euro_joints=_v3_joints,
            tilts=_v3_tilts,
            powertrains=list(POWERTRAINS),
            age_labels=list(ft.AGE_BAND_LABELS),
            euro_labels=list(ft.EURO_CLASS_LABELS),
            drawn_powertrains=_v3_powertrains,
        )
        # Task A2 (review Finding 2): the segment draw target is the EFFECTIVE
        # per-car segment pmf accumulated in PASS 1 (status/raumtyp-
        # conditioned segment_probabilities, with "sonstige" mass
        # redistributed over the modelled segments) -- NOT the raw national
        # KBA FZ 27.10 marginal, which the realised distribution does not
        # target and which flagged DRIFT on essentially every run at scale.
        _expected["segment"] = {
            s: float(v) / n for s, v in zip(segments, _exp_segment)
        }
        _validation_summary = _fv.validate_realised_margins(
            df_spec, _expected, sample_rate=1.0)
        logger.info(
            "[fleet_de] realised-margin validation (consistency_v2): "
            "any_flagged=%s", _validation_summary["any_flagged"],
        )
        return df_spec, df_vehicle_types, _validation_summary

    return df_spec, df_vehicle_types


def _draw_brand_model(rng: np.random.Generator, sampler: FleetSampler,
                      segment: str) -> tuple[str, str]:
    """Draw ``(brand, model)`` for a segment; fully guarded (additive attribute).

    Any failure (segment absent from FZ 12.1, empty frame, parse error) returns
    ``("", "")`` instead of raising, so a brand/model issue can never break the
    emissions-relevant chain. The model string's first token is taken as the
    brand (FZ 12.1 Modellreihe strings are ``BRAND MODEL``).
    """
    try:
        frame = sampler.model_given_segment.get(segment)
        if frame is None or frame.empty:
            return "", ""
        idx = rng.choice(len(frame), p=frame["share"].to_numpy(dtype=float))
        model = str(frame["model"].iloc[idx])
        brand = model.split(" ", 1)[0] if model else ""
        return brand, model
    except Exception as exc:  # noqa: BLE001 -- additive attribute, never fatal
        logger.warning(
            "[fleet_de] brand/model draw failed for segment '%s' (%s) -> "
            "empty brand/model", segment, exc,
        )
        return "", ""


def _log_simple_fallback(label: str, fallback: int, total: int) -> None:
    rate = (fallback / total) if total else 0.0
    (logger.warning if rate > 0.05 else logger.info)(
        "[fleet_de] %s: primary %d/%d (%.1f%%), fallback %d (%.1f%%)",
        label, total - fallback, total,
        100.0 * (total - fallback) / total if total else 0.0,
        fallback, 100.0 * rate,
    )


def _effective_expected(
    car_pmfs: Sequence[np.ndarray],
    kreis_factors: Sequence[np.ndarray],
    age_euro_joints: Sequence[np.ndarray],
    tilts: Sequence[Optional[np.ndarray]],
    powertrains: Sequence[str],
    age_labels: Sequence[str],
    euro_labels: Sequence[str],
    drawn_powertrains: Optional[Sequence[str]] = None,
) -> dict[str, dict[str, float]]:
    """Accumulate the effective per-car target PMFs for the three dimensions.

    For each car, the EFFECTIVE powertrain pmf is ``car_pmf * kreis_factor``
    (renormalised).  The age/euro marginals are derived from the tilted
    ``age_euro_joint`` matrix (rows=age bands, cols=Euro classes): the age
    marginal is the row sum and the euro marginal is the column sum of the
    tilt-applied, renormalised joint.  Accumulating these over all ``n`` cars
    and dividing by ``n`` yields the expected marginal PMFs (mean effective
    target) for the validator.

    For pure-electric powertrains (BEV / hydrogen, A4-revised), the euro is
    always overridden to ``hbefa.ELECTRIC_EURO`` ("electric") at draw time.
    When ``drawn_powertrains`` is supplied, this function mirrors that override:
    a pure-electric car contributes all its euro mass to the ``"electric"``
    bucket instead of the joint's real euro distribution, so the expected and
    realised euro marginals stay consistent.  PHEV and hybrid are NOT in
    ``ELECTRIC_EURO_POWERTRAINS`` and therefore contribute their full joint euro
    marginal as usual.

    This is a pure helper (no RNG, no data loading) so it is unit-testable
    without running the full sampler.

    Parameters
    ----------
    car_pmfs : per-car powertrain pmf (pre-rake; shape ``n_powertrain``).
    kreis_factors : per-car per-Kreis rake factor array (shape ``n_powertrain``).
    age_euro_joints : per-car ``age x euro`` joint matrix (shape
        ``n_age x n_euro``), from ``sampler.age_euro_joint[powertrain]``.
    tilts : per-car income-age tilt (shape ``n_age``) or ``None`` (no tilt).
    powertrains : powertrain label sequence (ordered like pmf vectors).
    age_labels : age-band label sequence (ordered like matrix rows).
    euro_labels : Euro-class label sequence (ordered like matrix columns).
    drawn_powertrains : optional per-car drawn powertrain label (same length as
        ``car_pmfs``).  When supplied, pure-electric rows (BEV / hydrogen)
        contribute their euro mass to ``hbefa.ELECTRIC_EURO`` rather than the
        joint columns (mirrors the A4-revised euro override in the draw).

    Returns
    -------
    dict with keys ``"powertrain"``, ``"age_band"``, ``"euro_class"``;
    each value is a ``label -> probability`` dict summing to 1.0.
    """
    n = len(car_pmfs)
    # "electric" label is not in euro_labels (those are the KBA combustion
    # labels); track it separately and merge at the end.
    electric_euro = hbefa.ELECTRIC_EURO
    euro_labels_list = list(euro_labels)
    # Extended euro dimension: real combustion labels + "electric" category.
    euro_labels_ext = euro_labels_list + [electric_euro]
    if n == 0:
        return {
            "powertrain": {p: 1.0 / len(powertrains) for p in powertrains},
            "age_band": {a: 1.0 / len(age_labels) for a in age_labels},
            "euro_class": {e: 1.0 / len(euro_labels_list) for e in euro_labels_list},
        }

    acc_pt = np.zeros(len(powertrains), dtype=float)
    acc_age = np.zeros(len(age_labels), dtype=float)
    acc_euro = np.zeros(len(euro_labels_ext), dtype=float)
    electric_idx = len(euro_labels_list)  # index of the "electric" category in acc_euro

    it = zip(car_pmfs, kreis_factors, age_euro_joints, tilts)
    if drawn_powertrains is not None:
        it_pt: Sequence = drawn_powertrains
    else:
        it_pt = [""] * n  # type: ignore[assignment]

    for (pmf, factors, joint, tilt), pt in zip(it, it_pt):
        # Effective powertrain pmf after the per-Kreis rake.
        eff_pt = np.asarray(pmf, dtype=float) * np.asarray(factors, dtype=float)
        s = eff_pt.sum()
        if s > 0:
            eff_pt = eff_pt / s
        acc_pt += eff_pt

        # Effective age/euro joint after the income-age tilt (if present).
        M = np.asarray(joint, dtype=float).copy()
        if tilt is not None:
            M = M * np.asarray(tilt, dtype=float)[:, None]
        m = M.sum()
        if m > 0:
            M = M / m
        acc_age += M.sum(axis=1)   # age marginal (unaffected by the euro override)

        # Euro marginal: mirror the A4-revised ELECTRIC_EURO override.
        # Only pure-electric drivetrains (BEV / hydrogen) have all their euro
        # mass collapsed to the "electric" category; PHEV and hybrid keep their
        # drawn joint euro marginal (they have a real combustion Euro stage).
        if drawn_powertrains is not None and pt in hbefa.ELECTRIC_EURO_POWERTRAINS:
            # All euro mass collapses to the "electric" category.
            acc_euro[electric_idx] += 1.0
        else:
            acc_euro[:len(euro_labels_list)] += M.sum(axis=0)  # real euro marginal

    # Normalise by n to get mean expected marginals (which sum to 1.0).
    # Include "electric" in the euro dict only when it has mass (i.e.
    # drawn_powertrains was supplied and there are pure-electric draws).
    euro_dict: dict[str, float] = {}
    for j, e in enumerate(euro_labels_ext):
        v = float(acc_euro[j] / n)
        if v > 0.0 or e != electric_euro:
            euro_dict[e] = v
    return {
        "powertrain": {p: float(acc_pt[j] / n)
                       for j, p in enumerate(powertrains)},
        "age_band": {a: float(acc_age[j] / n)
                     for j, a in enumerate(age_labels)},
        "euro_class": euro_dict,
    }
