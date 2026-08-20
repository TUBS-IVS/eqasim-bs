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
import re
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
from braunschweig.synthesis.vehicles.wohnmobile_age import (
    WOHNMOBILE_SEGMENT, WohnmobileHolderAgeTilt)
from braunschweig.synthesis.vehicles.segment import SegmentModel

logger = logging.getLogger(__name__)


#: Canonical powertrain order used for every powertrain probability vector.
POWERTRAINS: tuple[str, ...] = ft.POWERTRAIN_LABELS

#: Electric (plug-in) powertrains tilted by the Gemeinde private BEV/PHEV share.
ELECTRIC_POWERTRAINS: tuple[str, ...] = ("bev", "phev")

#: Key under which a Gemeinde/Kreis electric-share map carries the COMBINED
#: electric share (BEV + PHEV + fuel cell) instead of a per-powertrain split.
#: Some KBA per-Gemeinde editions publish only that combined share; the tilt then
#: scales the whole electric mass by one factor, which preserves the within-Kreis
#: BEV:PHEV ratio rather than inventing one (ADR-0086).
COMBINED_ELECTRIC_KEY: str = "electric_combined"


# --------------------------------------------------------------------------- #
# Gemeinde-name normalisation for the FZ 27.17 tilt join (issue #161)
# --------------------------------------------------------------------------- #
#: Municipal-status suffix tokens that appear appended to a Gemeinde name after
#: a comma in BOTH source vocabularies of the Gemeinde-tilt join -- the KBA
#: FZ 27.17 sheet (:func:`braunschweig.data.kba.fleet_tables.load_gemeinde_private_bev`,
#: already ASCII-transliterated and frequently ABBREVIATED, e.g.
#: ``"GIFHORN,ST."``, ``"BROME,FLECKEN"``) and the BBSR RegioStaR ``name_20``
#: reference the household home Gemeinde is derived from (proper unicode
#: umlauts, spelled out in full, e.g. ``"Gifhorn, Stadt"``,
#: ``"Brome, Flecken"``). Stripping the suffix on BOTH sides lets the two
#: vocabularies join on the base Gemeinde name.
#:
#: The suffix vocabulary is NOT limited to a fixed token list: RegioStaR also
#: carries ``", BERG- UND UNIVERSITAETSSTADT"`` (Clausthal-Zellerfeld) and
#: ``", GEMFR. GEBIET"``, so everything from the FIRST comma on is dropped
#: (measured below). The token list is kept for documentation of the common
#: cases only.
_GEMEINDE_SUFFIX_TOKENS = ("STADT", "ST", "FLECKEN")
_GEMEINDE_SUFFIX_PATTERN = re.compile(r",.*$")

#: Marker of a *gemeindefreies Gebiet* -- an unpopulated forest / military
#: training area that RegioStaR lists as its own ``name_20`` entry
#: (``"HARZ (LANDKREIS GOSLAR), GEMFR. GEBIET"``,
#: ``"SCHOENINGEN, GEMFR. GEBIET"``). No KBA Gemeinde table has a row for one
#: (no vehicles are registered there), so such a label must NOT be folded onto
#: the neighbouring town's key -- see :func:`normalize_gemeinde_name`.
_GEMEINDEFREI_MARKER = "GEMFR"


def normalize_gemeinde_name(name: Optional[str]) -> str:
    """Canonicalise a Gemeinde name for the cross-source Gemeinde-tilt join.

    Fixes issue #161: :meth:`PowertrainModel.powertrain_probabilities` looks up
    ``(kreis_ags5, gemeinde)`` in a dict built from the KBA FZ 27.17 Gemeinde
    tilt table, while the household home Gemeinde comes from the BBSR RegioStaR
    ``name_20`` reference. Without normalisation the two vocabularies almost
    never agree: FZ 27.17 is ASCII-transliterated and often abbreviated
    (``"RUEHEN"``, ``"GIFHORN,ST."``), while RegioStaR carries proper unicode
    umlauts and the full suffix (``"Ruehen"`` written as "Rühen",
    ``"Gifhorn, Stadt"``) -- a 100 %-uppercase-only join therefore missed ~69 %
    of vehicles (live 100 % run: primary 209285/684762, fallback 69.4 %).
    Applying this SAME function to BOTH key producers
    (:meth:`PowertrainModel._gemeinde_private_electric_share`, which builds the
    FZ 27.17 side, and :meth:`PowertrainModel._apply_gemeinde_tilt`, which
    builds the household-side lookup key) folds both vocabularies onto one
    canonical form so the join actually succeeds.

    Steps (fixed order):

    1. Upper-case (Python's ``str.upper()`` already maps German sz -> ``SS``).
    2. Fold the remaining upper-case umlauts (``Ae -> AE``, ``Oe -> OE``,
       ``Ue -> UE``) to the KBA sheet's own two-letter transliteration.
    3. Return ``""`` for a *gemeindefreies Gebiet* (see below).
    4. Drop a parenthetical qualifier (``"MUEDEN (ALLER)"`` vs the FZ 27.17
       spelling ``"MUEDEN(ALLER)"``, ``"VELTHEIM (OHE)"``).
    5. Drop everything from the first comma on -- this covers the frequent
       municipal-status suffixes (``", STADT"``, ``",ST."``, ``", FLECKEN"``)
       as well as the long-form ones RegioStaR uses
       (``"CLAUSTHAL-ZELLERFELD, BERG- UND UNIVERSITAETSSTADT"``).
    6. Drop periods (``"ST." -> "ST"``) and collapse internal whitespace.

    Returns ``""`` for ``None`` / ``NaN`` so a missing Gemeinde is never
    silently coerced into a spurious match; the caller treats an empty key as
    "no Gemeinde tilt applicable" (counted as the existing Gemeinde fallback).

    A *gemeindefreies Gebiet* (unpopulated forest / military training area, e.g.
    ``"SCHOENINGEN, GEMFR. GEBIET"``) also returns ``""``: no KBA Gemeinde table
    contains such an area, so folding it onto the neighbouring town's key would
    be a FALSE match that silently hands it that town's EV tilt. Excluding it
    keeps the label in the (counted) Gemeinde fallback instead.

    Measured coverage (merge of ``feature/fleet-quality-and-data``, ZGB-8, KBA
    FZ 27.17 ``kba_gemeinde_private_bev.csv`` keys vs the 126 RegioStaR
    ``name_20`` labels): **113/116 populated Gemeinden (97.4 %)** with zero
    false matches. The three misses (Hahausen, Wallmoden, Lutter am Barenberge)
    are a REFERENCE-DATA gap -- the FZ 27.17 sheet lists only 7 of the Kreis
    Goslar Gemeinden -- and fall back to the Kreis share. The predecessor rules
    scored 110/116 (fixed suffix-token list) and 113/116-with-3-false-matches
    (comma-drop without the gemeindefrei exclusion).
    """
    if name is None or pd.isna(name):
        return ""
    text = str(name).strip().upper()
    text = text.replace("Ä", "AE").replace("Ö", "OE").replace("Ü", "UE")
    if _GEMEINDEFREI_MARKER in text:
        return ""
    text = re.sub(r"\(.*?\)", " ", text)
    text = _GEMEINDE_SUFFIX_PATTERN.sub("", text)
    text = text.replace(".", "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


# --------------------------------------------------------------------------- #
# Gebietsstand crosswalk: 2020 population labels -> current KBA Gemeinden
# --------------------------------------------------------------------------- #
#: Municipal mergers that took effect AFTER the population's Gebietsstand-2020
#: Gemeinde vocabulary (BBSR RegioStaR ``name_20``) but BEFORE the KBA reference
#: vintages (FZ 27.17 2025-01-01, kba_gemeinde_ev 2026-04-01). Without the
#: crosswalk the household label has no counterpart in the reference table and
#: the Gemeinde tilt silently degrades to the Kreis share, even though the area
#: IS covered by the reference -- under the successor Gemeinde's name.
#:
#: Keys are ``(kreis_ags5, normalised predecessor name)``, values the normalised
#: successor name. Both sides are already passed through
#: :func:`normalize_gemeinde_name`.
#:
#: Entries:
#:
#: * Kreis Goslar (03153): the Samtgemeinde Lutter am Barenberge member
#:   communities Flecken Lutter am Barenberge (AGS 03153009), Hahausen
#:   (03153006) and Wallmoden (03153014) were incorporated into Stadt
#:   Langelsheim on 1 November 2021; the merged town carries the NEW AGS
#:   03153019 (old Langelsheim was 03153007). Source: Niedersächsische
#:   Staatskanzlei, press release "Mitgliedsgemeinden der Samtgemeinde Lutter am
#:   Barenberge fusionieren mit der Stadt Langelsheim"; corroborated in the data
#:   itself -- KBA 2023.01..2026.04 lists exactly 7 Goslar Gemeinden including
#:   03153019 Langelsheim and none of the three predecessors.
#:
#: This is an administrative FACT crosswalk, not a tunable parameter: adding an
#: entry that is not backed by a merger statute would fabricate a reference
#: assignment (see CLAUDE.md, "No invented reference values").
GEMEINDE_GEBIETSSTAND_CROSSWALK: dict[tuple[str, str], str] = {
    ("03153", "HAHAUSEN"): "LANGELSHEIM",
    ("03153", "LUTTER AM BARENBERGE"): "LANGELSHEIM",
    ("03153", "WALLMODEN"): "LANGELSHEIM",
}


def apply_gebietsstand_crosswalk(kreis_ags5: str, gemeinde_norm: str) -> str:
    """Map a normalised predecessor Gemeinde name onto its successor.

    Returns ``gemeinde_norm`` unchanged when no merger applies (the common
    case), so the function is a no-op for every Gemeinde whose Gebietsstand-2020
    name still exists in the KBA reference vintage.
    """
    return GEMEINDE_GEBIETSSTAND_CROSSWALK.get(
        (str(kreis_ags5), gemeinde_norm), gemeinde_norm)


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
    # Cars whose Gebietsstand-2020 Gemeinde label was mapped onto a successor
    # Gemeinde (municipal merger, see GEMEINDE_GEBIETSSTAND_CROSSWALK). Counted
    # separately from primary/fallback so the crosswalk stays observable.
    _gemeinde_crosswalked: int = field(default=0)
    # Electric powertrains tilted via the COMBINED electric share because the
    # source published no BEV/PHEV split for that Gemeinde (ADR-0086).
    _gemeinde_combined: int = field(default=0)
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

        kreis_psp, n_marginal_primary, n_marginal_degenerate = (
            cls._rake_per_kreis_powertrain(
                kreis_marginal, national_psg, seg_share,
                max_iterations, tolerance,
            )
        )

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
        # No-silent-fallback: report how many Kreise used real per-Kreis fuel
        # mass (primary) vs the national P(powertrain|segment) degenerate
        # fallback. A non-trivial degenerate count almost always means a broken
        # 46251-02 join upstream (empty/suppressed rows), not genuinely empty
        # Kreise, and should be investigated.
        if n_marginal_degenerate:
            logger.warning(
                "[fleet_de] per-Kreis powertrain marginal: %d/%d Kreis(e) had no "
                "usable fuel mass (all components zero/suppressed/NaN) -> national "
                "P(powertrain|segment) used for them; %d Kreis(e) used real "
                "per-Kreis data. A high degenerate count signals a broken join.",
                n_marginal_degenerate, len(kreis_marginal), n_marginal_primary,
            )
        else:
            logger.info(
                "[fleet_de] per-Kreis powertrain marginal: all %d Kreis(e) used "
                "real per-Kreis fuel mass (0 degenerate).", n_marginal_primary,
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
    @classmethod
    def _rake_per_kreis_powertrain(
        cls, kreis_marginal: dict[str, np.ndarray], national_psg: np.ndarray,
        seg_share: np.ndarray, max_iterations: int, tolerance: float,
    ) -> tuple[dict[str, np.ndarray], int, int]:
        """Rake each per-Kreis powertrain marginal onto the national seed.

        For every Kreis, the seed ``P(powertrain | segment) * P(segment)`` (which
        carries the income->segment->powertrain association and has the national
        segment marginal as its row sums) is biproportionally raked so its row
        sums match ``seg_share`` and its column sums match the per-Kreis
        powertrain marginal.

        Degenerate-Kreis guard (no-silent-fallback rule): a Kreis can in
        principle carry ``insg>0`` yet have every fuel component suppressed (read
        as ``NaN`` by :func:`load_kreis_fuel`) or zero. NaN/inf components are
        treated as 0 (Regionalstatistik 46251-02 only suppresses SMALL counts, so
        petrol/diesel -- the dominant categories -- are never affected). If no
        finite positive mass remains, the per-Kreis column target is undefined;
        that Kreis falls back to the national ``P(powertrain | segment)`` rather
        than divide by zero and emit a NaN pmf that would crash ``rng.choice`` at
        draw time. The Task B3 all-Kreise marginal makes this reachable for
        cross-cordon in-commuters carrying an arbitrary home Kreis, so the
        degenerate count is returned for logging.

        Args:
            kreis_marginal: Mapping ``kreis_ags5 -> count-like powertrain vector``
                (order matches ``national_psg`` columns).
            national_psg: Row-normalised national ``P(powertrain | segment)``
                matrix, shape ``(n_segments, n_powertrains)``; also the degenerate
                fallback per Kreis.
            seg_share: National segment marginal (sums to 1), shape
                ``(n_segments,)``.
            max_iterations: IPF iteration cap passed to :func:`rake_2d`.
            tolerance: IPF convergence tolerance passed to :func:`rake_2d`.

        Returns:
            ``(kreis_psp, n_primary, n_degenerate)`` where ``kreis_psp`` maps each
            Kreis to a row-normalised ``P(powertrain | segment)`` matrix,
            ``n_primary`` counts Kreise raked from real per-Kreis mass, and
            ``n_degenerate`` counts Kreise that used the national fallback.
        """
        kreis_psp: dict[str, np.ndarray] = {}
        n_primary = 0
        n_degenerate = 0
        for kreis, col_target in kreis_marginal.items():
            col = np.nan_to_num(np.asarray(col_target, dtype=float),
                                nan=0.0, posinf=0.0, neginf=0.0)
            total = col.sum()
            if not np.isfinite(total) or total <= 0.0:
                kreis_psp[kreis] = national_psg.copy()
                n_degenerate += 1
                continue
            seed = national_psg * seg_share[:, None]
            joint = rake_2d(
                seed,
                row_targets=seg_share,
                col_targets=col / total,
                max_iterations=max_iterations,
                tolerance=tolerance,
            )
            kreis_psp[kreis] = cls._row_normalise(joint)
            n_primary += 1
        return kreis_psp, n_primary, n_degenerate

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
                ``(kreis_ags5, normalize_gemeinde_name(gemeinde))``; or ``None`` when
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
                key = (str(row["kreis_ags5"]), normalize_gemeinde_name(row["gemeinde"]))
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
            # ADR-0086: the combined electric share is accumulated alongside the
            # per-powertrain ones, so the denominator exists for whichever of the
            # two the numerator side can supply. A zero is NOT a measured share
            # here either (see _gemeinde_electric_share_2026).
            for col, pt in (("bev_share", "bev"), ("phev_share", "phev"),
                            ("ev_share", COMBINED_ELECTRIC_KEY)):
                val = row[col] if col in row else None
                if val is not None and pd.notna(val) and float(val) > 0.0:
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

        The Gemeinde name is normalised via :func:`normalize_gemeinde_name`
        (issue #161) so the join is robust to the KBA sheet's
        ASCII-transliterated, abbreviated spelling versus the BBSR RegioStaR
        proper-unicode home Gemeinde label. Missing (coerced) shares are
        dropped so the tilt falls back to the Kreis level for that Gemeinde.
        """
        out: dict[tuple[str, str], dict[str, float]] = {}
        collisions: list[tuple[str, str]] = []
        for _, row in df_gem.iterrows():
            key = (str(row["kreis_ags5"]), normalize_gemeinde_name(row["gemeinde"]))
            shares: dict[str, float] = {}
            for col, pt in (("private_bev_share", "bev"),
                            ("private_phev_share", "phev")):
                val = row[col]
                if pd.notna(val):
                    shares[pt] = float(val)
            if shares:
                # Two distinct source rows collapsing onto one normalised key would
                # silently keep only the last row's shares; the committed FZ 27.17
                # table has no such pair today, so any collision signals either a
                # data update or an over-aggressive normalize_gemeinde_name rule.
                if key in out:
                    collisions.append(key)
                out[key] = shares
        if collisions:
            logger.warning(
                "[fleet_de] Gemeinde tilt table: %d normalised (kreis, gemeinde) key "
                "collision(s) -- later rows overwrote earlier ones (first examples: %s). "
                "Check normalize_gemeinde_name against the FZ 27.17 source names.",
                len(collisions), collisions[:5],
            )
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

        The lookup key is normalised via :func:`normalize_gemeinde_name`
        (issue #161) -- the SAME function used to build
        ``gemeinde_private_electric_share`` -- so the household's BBSR
        RegioStaR Gemeinde label joins the KBA FZ 27.17 sheet's
        ASCII-transliterated, abbreviated spelling. The normalised name is then
        passed through :func:`apply_gebietsstand_crosswalk` so a household whose
        Gebietsstand-2020 Gemeinde was merged into a successor Gemeinde before
        the KBA vintage still finds its (successor's) reference row instead of
        degrading to the Kreis share.
        """
        if gemeinde is None:
            self._gemeinde_fallback += 1
            return pmf
        gemeinde_norm = normalize_gemeinde_name(gemeinde)
        crosswalked = apply_gebietsstand_crosswalk(kreis_ags5, gemeinde_norm)
        if crosswalked != gemeinde_norm:
            self._gemeinde_crosswalked += 1
        key = (kreis_ags5, crosswalked)
        gem_shares = self.gemeinde_private_electric_share.get(key)
        kreis_shares = self.kreis_private_electric_share.get(kreis_ags5)
        if gem_shares is None or kreis_shares is None:
            self._gemeinde_fallback += 1
            return pmf
        idx = {p: i for i, p in enumerate(self.powertrains)}
        tilted = pmf.copy()
        # ADR-0086: prefer the per-powertrain share; where the source publishes only
        # the combined electric share, tilt both electric powertrains by that single
        # factor (preserving the Kreis BEV:PHEV ratio instead of inventing one).
        # Which path was taken is counted so an inert tilt can never hide again.
        combined_factor = None
        gem_combined = gem_shares.get(COMBINED_ELECTRIC_KEY)
        kreis_combined = kreis_shares.get(COMBINED_ELECTRIC_KEY, 0.0)
        if gem_combined is not None and kreis_combined > 0.0:
            combined_factor = float(np.clip(gem_combined / kreis_combined, 0.2, 5.0))
        applied = False
        for pt in ELECTRIC_POWERTRAINS:
            kreis_share = kreis_shares.get(pt, 0.0)
            gem_share = gem_shares.get(pt)
            if gem_share is None or kreis_share <= 0.0:
                if combined_factor is not None:
                    tilted[idx[pt]] *= combined_factor
                    applied = True
                    self._gemeinde_combined += 1
                continue
            # Clip the tilt to [0.2, 5] so a tiny denominator cannot explode a
            # single Gemeinde's electric share. F8 NOTE: the 0.2 lower floor is
            # DELIBERATE -- a Gemeinde with a genuine near-zero EV share still keeps
            # >=20% of the Kreis's relative EV propensity rather than being fully
            # suppressed. This is an anti-explosion guard; it means "no-EV pockets"
            # are not represented at the extreme (acceptable trade-off).
            factor = float(np.clip(gem_share / kreis_share, 0.2, 5.0))
            tilted[idx[pt]] *= factor
            applied = True
        # A key match that tilts NOTHING is a fallback, not a primary hit: counting
        # it as primary is what let the 2026-source defect read as "tilt working".
        if applied:
            self._gemeinde_primary += 1
        else:
            self._gemeinde_fallback += 1
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

    def log_fallback_rate(self, population_label: str = "") -> None:
        """Log the primary-vs-fallback rates (no-silent-fallback rule).

        ``population_label`` distinguishes multiple sampler invocations in one
        run log (e.g. "residents" vs "in-commuters" -- the latter legitimately
        hits 100% Kreis fallback because origin Kreise lie outside the ZGB KBA
        tables; without the label the two blocks are indistinguishable and the
        in-commuter one reads like a broken primary path).
        """
        tag = "[fleet_de][%s]" % population_label if population_label else "[fleet_de]"
        ktot = self._kreis_primary + self._kreis_fallback
        gtot = self._gemeinde_primary + self._gemeinde_fallback
        grtot = self._grid_primary + self._grid_fallback
        krate = (self._kreis_fallback / ktot) if ktot else 0.0
        grate = (self._gemeinde_fallback / gtot) if gtot else 0.0
        grrate = (self._grid_fallback / grtot) if grtot else 0.0
        (logger.warning if krate > 0.05 else logger.info)(
            "%s powertrain Kreis lookup: primary %d/%d (%.1f%%), "
            "fallback %d (%.1f%%)", tag, self._kreis_primary, ktot,
            100.0 * self._kreis_primary / ktot if ktot else 0.0,
            self._kreis_fallback, 100.0 * krate,
        )
        (logger.warning if grate > 0.50 else logger.info)(
            "%s powertrain Gemeinde tilt: primary %d/%d (%.1f%%), "
            "fallback %d (%.1f%%)", tag, self._gemeinde_primary, gtot,
            100.0 * self._gemeinde_primary / gtot if gtot else 0.0,
            self._gemeinde_fallback, 100.0 * grate,
        )
        if self._gemeinde_combined:
            logger.info(
                "%s powertrain Gemeinde tilt: %d electric-powertrain tilt(s) used "
                "the COMBINED electric share (source publishes no BEV/PHEV split "
                "for that Gemeinde, ADR-0086)", tag, self._gemeinde_combined,
            )
        if self._gemeinde_crosswalked:
            logger.info(
                "%s powertrain Gemeinde tilt: %d/%d cars (%.2f%%) reached their "
                "reference row via the Gebietsstand crosswalk (merged Gemeinde)",
                tag, self._gemeinde_crosswalked, gtot,
                100.0 * self._gemeinde_crosswalked / gtot if gtot else 0.0,
            )
        (logger.warning if grrate > 0.50 else logger.info)(
            "%s powertrain grid tilt: primary %d/%d (%.1f%%), "
            "fallback %d (%.1f%%)", tag, self._grid_primary, grtot,
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
    matching the population's ``normalize_gemeinde_name``-normalised Gemeinde
    label).

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
    # A share column that is zero (or NaN) for EVERY row carries no information --
    # the KBA arcgis export ships editions where exactly that is true of the
    # BEV/plug-in-hybrid columns. Treating those zeros as measured shares is what
    # made the whole Gemeinde tilt inert (issue #277); a zero inside an otherwise
    # informative column, by contrast, IS a measurement (a genuine no-EV pocket)
    # and is kept. Only the two columns that DRIVE the tilt are screened this way;
    # ``fuelcell_share`` is stored as-is because hydrogen is never tilted.
    tilt_columns = {"bev_share": "bev", "phev_share": "phev"}
    informative = {
        col: bool((pd.to_numeric(df[col], errors="coerce").fillna(0.0) > 0.0).any())
        for col in tilt_columns if col in df.columns
    }
    degenerate = sorted(col for col, ok in informative.items() if not ok)
    if degenerate and len(df) > 0:
        logger.warning(
            "[fleet_de] Gemeinde EV tilt source (2026): column(s) %s carry no "
            "positive value in any of the %d row(s) -- treated as ABSENT, not as "
            "measured zeros. The tilt uses the combined electric share instead "
            "(ADR-0086).", degenerate, len(df),
        )
    n_split = 0
    for _, row in df.iterrows():
        key = (str(row["kreis_ags5"]), str(row["gemeinde_norm"]))
        shares: dict[str, float] = {}
        for col, pt in tilt_columns.items():
            if not informative.get(col, False):
                continue
            val = row[col]
            if pd.notna(val):
                shares[pt] = float(val)
        if shares:
            n_split += 1
        if "fuelcell_share" in row and pd.notna(row["fuelcell_share"]):
            shares["hydrogen"] = float(row["fuelcell_share"])
        # The KBA arcgis export ships editions in which ONLY the combined
        # "Pkw Elektro Anteil" carries information and every BEV / plug-in-hybrid /
        # fuel-cell column is a literal 0 (verified for the 2026.04 edition: 113/113
        # ZGB rows). Storing those zeros as if they were measured shares makes the
        # whole Gemeinde tilt inert -- the defect issue #277 found. The combined
        # share is kept under COMBINED_ELECTRIC_KEY so the tilt can operate on the
        # total electric mass instead (ADR-0086).
        ev_total = row.get("ev_share")
        if pd.notna(ev_total) and float(ev_total) > 0.0:
            shares[COMBINED_ELECTRIC_KEY] = float(ev_total)
        if shares:
            out[key] = shares
    if out:
        logger.info(
            "[fleet_de] Gemeinde EV tilt source (2026): %d/%d row(s) carry a "
            "per-powertrain BEV/PHEV split; the rest tilt on the combined "
            "electric share.", n_split, len(out),
        )
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
# Task B5: Euro-6 substage (6ab / 6d-temp / 6d) conditional draw
# --------------------------------------------------------------------------- #
def _euro6_substage_given_kreis(data_path: str) -> "dict[tuple[str, str], np.ndarray]":
    """``P(substage | euro6, kreis, powertrain)`` from the Euro-6 substage
    columns on ``kba_kreis_euro.csv`` (Task B4: ``euro6d``, ``euro6dtemp``,
    ``euro6ab``), composed diesel-vs-non-diesel EXACTLY like the headline euro
    joint (see :func:`_euro_given_kreis_powertrain`, "T6b"):

    * ``diesel``: the ``teil=="diesel"`` row's substage counts, normalised.
    * ``petrol``, ``gas``, ``other``: ``max(all - diesel, 0)`` per substage
      column, normalised. 46251-03 does not break the substage down by
      individual fuel, so these three non-diesel combustion fuels share the
      same composed shape (same documented assumption as T6b).

    Returns an EMPTY dict (never ``None``) when ``kba_kreis_euro.csv`` is
    absent, so the caller (:class:`Euro6SubstageModel`) falls straight through
    to the national fallback. A (Kreis, diesel/non-diesel) cell whose composed
    substage counts sum to zero or NaN (pre-Task-B4 zero-filled data -- see
    :func:`braunschweig.data.kba.fleet_tables.load_kreis_euro` -- or a
    genuinely Euro-6-substage-free Kreis-fuel) is OMITTED from the returned
    dict rather than stored as a degenerate 0/0 pmf (no-silent-fallback rule):
    a missing key is the caller's signal to fall back to the national pmf.
    """
    substages = list(ft.EURO6_SUBSTAGE_LABELS)
    try:
        df = ft.load_kreis_euro(data_path)
    except FileNotFoundError:
        logger.info(
            "[fleet_de] Euro-6 substage: kba_kreis_euro.csv absent -> "
            "per-Kreis substage composition disabled (national fallback / "
            "plain-euro6 fallback only)."
        )
        return {}

    _DIESEL_PT = "diesel"
    _NON_DIESEL_COMBUSTIONs = {"petrol", "gas", "other"}
    out: dict[tuple[str, str], np.ndarray] = {}
    n_diesel_ok = 0
    n_diesel_zero = 0
    n_nondiesel_ok = 0
    n_nondiesel_zero = 0

    for kreis_ags5, grp in df.groupby("kreis_ags5"):
        kreis = str(kreis_ags5)
        row_all = grp[grp["teil"] == "all"]
        row_dsl = grp[grp["teil"] == "diesel"]
        if row_all.empty:
            continue
        all_counts = np.nan_to_num(
            np.array([row_all.iloc[0][s] for s in substages], dtype=float))
        if not row_dsl.empty:
            dsl_counts = np.nan_to_num(
                np.array([row_dsl.iloc[0][s] for s in substages], dtype=float))
        else:
            dsl_counts = np.zeros(len(substages), dtype=float)

        s_dsl = dsl_counts.sum()
        if s_dsl > 0:
            out[(kreis, _DIESEL_PT)] = dsl_counts / s_dsl
            n_diesel_ok += 1
        else:
            n_diesel_zero += 1

        non_dsl_counts = np.maximum(all_counts - dsl_counts, 0.0)
        s_non_dsl = non_dsl_counts.sum()
        if s_non_dsl > 0:
            pmf_non_dsl = non_dsl_counts / s_non_dsl
            for pt in _NON_DIESEL_COMBUSTIONs:
                out[(kreis, pt)] = pmf_non_dsl
            n_nondiesel_ok += 1
        else:
            n_nondiesel_zero += 1

    n_kreis = n_diesel_ok + n_diesel_zero
    logger.info(
        "[fleet_de] Euro-6 substage per-Kreis composition (kba_kreis_euro.csv, "
        "%d Kreise): diesel cells %d/%d usable (%d all-zero/NaN -> national "
        "fallback); non-diesel-combustion cells %d/%d usable (%d all-zero/NaN "
        "-> national fallback).",
        n_kreis, n_diesel_ok, n_kreis, n_diesel_zero,
        n_nondiesel_ok, n_kreis, n_nondiesel_zero,
    )
    return out


@dataclass
class Euro6SubstageModel:
    """``P(substage | euro6, kreis, powertrain)`` with a national FZ 27.4 fallback.

    Applied strictly AFTER the joint ``(age, euro)`` draw and the pure-electric
    ``euro_class`` override (see :func:`sample_fleet`), and only when the drawn
    ``euro_class == "euro6"`` and ``powertrain`` is a genuine combustion
    powertrain (:data:`hbefa.COMBUSTION_POWERTRAINS`; this excludes ``phev`` /
    ``hybrid``, which keep their drawn combustion-shaped euro_class untouched,
    and ``bev`` / ``hydrogen``, whose euro_class is already overridden to
    ``"electric"`` before this stage runs).

    Both sources degrade gracefully to an empty dict when their CSV is absent
    (:func:`_euro6_substage_given_kreis` / :meth:`_national_pmf`), so this class
    is ALWAYS constructed (never ``None``): :meth:`substage_pmf` simply returns
    ``None`` when neither source has a usable pmf for a given (Kreis,
    powertrain), and the caller then keeps the plain ``"euro6"`` label without
    consuming any RNG -- the no-silent-fallback / determinism contract for this
    feature (see the Task B5 brief).
    """

    #: Canonical substage label order (also the pmf vector order everywhere).
    substages: list[str]
    #: (kreis_ags5, powertrain) -> pmf over ``substages``. Only combustion
    #: powertrains with a usable (non-all-zero) composed source are present.
    kreis_pmf: dict[tuple[str, str], np.ndarray]
    #: powertrain -> national FZ 27.4 (Niedersachsen) pmf over ``substages``.
    #: Only fuels with a positive Euro-6 substage total are present.
    national_pmf: dict[str, np.ndarray]
    # Mutable fallback counters (no-silent-fallback rule).
    _kreis_primary: int = field(default=0)
    _national_fallback: int = field(default=0)
    _absent_fallback: int = field(default=0)

    @classmethod
    def from_data_path(cls, data_path: str) -> "Euro6SubstageModel":
        return cls(
            substages=list(ft.EURO6_SUBSTAGE_LABELS),
            kreis_pmf=_euro6_substage_given_kreis(data_path),
            national_pmf=cls._national_pmf(data_path),
        )

    @staticmethod
    def _national_pmf(data_path: str) -> dict[str, np.ndarray]:
        """National FZ 27.4 (Niedersachsen) ``P(substage | euro6, fuel)`` fallback.

        Returns an empty dict (never ``None``) when
        ``kba_fuel_euro6_substage_nds.csv`` is absent. A fuel whose substage
        counts are all-zero (no Euro-6 registrations for that fuel -- see
        ``scripts/extract_kba_fleet.py::extract_fuel_euro6_substage_nds``) is
        OMITTED rather than stored as a degenerate 0/0 pmf.
        """
        substages = list(ft.EURO6_SUBSTAGE_LABELS)
        try:
            df = ft.load_fuel_euro6_substage_nds(data_path)
        except FileNotFoundError:
            logger.info(
                "[fleet_de] Euro-6 substage: kba_fuel_euro6_substage_nds.csv "
                "absent -> national fallback disabled (per-Kreis-only or "
                "plain-euro6 fallback)."
            )
            return {}
        out: dict[str, np.ndarray] = {}
        n_zero = 0
        for fuel, grp in df.groupby("fuel"):
            vec = (grp.set_index("substage")["count"].reindex(substages)
                   .fillna(0.0).to_numpy(dtype=float))
            s = vec.sum()
            if s > 0:
                out[fuel] = vec / s
            else:
                n_zero += 1
        if n_zero:
            logger.warning(
                "[fleet_de] Euro-6 substage national fallback: %d fuel(s) have "
                "an all-zero substage total (no Euro-6 registrations for that "
                "fuel) -> no national pmf for those fuels (falls through to "
                "plain euro6).",
                n_zero,
            )
        return out

    def pmf_for(self, kreis_ags5: str, powertrain: str) -> Optional[np.ndarray]:
        """Pure (non-counting) lookup, used by the ``_effective_expected`` mirror.

        Must NOT increment the fallback counters (those are incremented once,
        by :meth:`substage_pmf`, at actual draw time); this method exists so
        the validator can recompute the SAME pmf a car's draw used without
        double-counting the fallback rate.
        """
        pmf = self.kreis_pmf.get((kreis_ags5, powertrain))
        if pmf is not None:
            return pmf
        return self.national_pmf.get(powertrain)

    def substage_pmf(self, kreis_ags5: str, powertrain: str) -> Optional[np.ndarray]:
        """Return the pmf to draw the substage from, counting the fallback level.

        Fallback chain (no-silent-fallback rule): per-(Kreis, powertrain)
        composed pmf -> national FZ 27.4 pmf -> ``None`` (the caller keeps the
        plain ``"euro6"`` label without drawing, so NO RNG is consumed on this
        path -- determinism when substage data is absent).
        """
        pmf = self.kreis_pmf.get((kreis_ags5, powertrain))
        if pmf is not None:
            self._kreis_primary += 1
            return pmf
        pmf = self.national_pmf.get(powertrain)
        if pmf is not None:
            self._national_fallback += 1
            return pmf
        self._absent_fallback += 1
        return None

    def log_fallback_rate(self) -> None:
        """Log the per-Kreis vs national vs plain-euro6 fallback rates."""
        total = self._kreis_primary + self._national_fallback + self._absent_fallback
        if total == 0:
            logger.info(
                "[fleet_de] Euro-6 substage draw: no combustion Euro-6 vehicle "
                "drawn this run (0 substage lookups)."
            )
            return
        absent_rate = self._absent_fallback / total
        (logger.warning if absent_rate > 0.5 else logger.info)(
            "[fleet_de] Euro-6 substage draw: per-Kreis %d/%d (%.1f%%), "
            "national fallback %d (%.1f%%), plain-euro6 fallback (no substage "
            "data) %d (%.1f%%).",
            self._kreis_primary, total, 100.0 * self._kreis_primary / total,
            self._national_fallback, 100.0 * self._national_fallback / total,
            self._absent_fallback, 100.0 * absent_rate,
        )


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
    #: Task B5: Euro-6 substage (6ab/6d-temp/6d) conditional draw model.
    #: ALWAYS constructed by :meth:`from_data_path` (never ``None`` in
    #: production; both its internal sources degrade gracefully to an empty
    #: dict when their CSV is absent -- see :class:`Euro6SubstageModel`). Kept
    #: ``Optional`` for defensive checks / direct test construction, mirroring
    #: the other optional models on this dataclass.
    euro6_substage: Optional[Euro6SubstageModel] = None
    #: Issue #315: wohnmobile holder-age tilt built from the COMMITTED
    #: kba_wohnmobile_holder_age.csv. ``None`` ONLY when that CSV is absent --
    #: unlike the server-generated optional tables, :func:`sample_fleet` RAISES
    #: in that state when its flag is ON (absence of a committed file is a
    #: checkout/wiring defect, never a normal state; ADR-0093).
    wohnmobile_age_tilt: Optional[WohnmobileHolderAgeTilt] = None

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
        # Task B5: Euro-6 substage model. Always constructed (never raises);
        # both its sources degrade gracefully to an empty dict when their CSV
        # is absent, so building it here has no effect on RNG/determinism --
        # only actually DRAWING from it (gated by the ``euro6_substage`` flag
        # in :func:`sample_fleet`) can.
        euro6_substage = Euro6SubstageModel.from_data_path(data_path)
        logger.info(
            "[fleet_de] Euro-6 substage model built: %d per-Kreis cell(s), "
            "%d national fuel(s).",
            len(euro6_substage.kreis_pmf), len(euro6_substage.national_pmf),
        )
        # Issue #315: wohnmobile holder-age tilt reference. Built leniently here
        # (the sampler does not know the flag); sample_fleet raises when the
        # flag is ON and this stayed None.
        wohnmobile_age_tilt_model: Optional[WohnmobileHolderAgeTilt] = None
        try:
            wohnmobile_age_tilt_model = WohnmobileHolderAgeTilt.from_data_path(data_path)
            logger.info(
                "[fleet_de] wohnmobile holder-age tilt: reference loaded "
                "(kba_wohnmobile_holder_age.csv)."
            )
        except FileNotFoundError:
            logger.warning(
                "[fleet_de] wohnmobile holder-age tilt: "
                "kba_wohnmobile_holder_age.csv ABSENT. Unlike the "
                "server-generated MiD tables this file is COMMITTED; "
                "sample_fleet will raise if fleet_wohnmobile_age_tilt is ON."
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
            euro6_substage=euro6_substage,
            wohnmobile_age_tilt=wohnmobile_age_tilt_model,
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
def _powertrain_rake_factors(
    pmfs: np.ndarray, kreis_target_share: dict[str, float],
    target_idx: dict[str, int], max_iterations: int = 50,
    tolerance: float = 1e-9, kreis: str = "?",
) -> tuple[np.ndarray, dict[str, float]]:
    """Per-powertrain multiplicative scale factors for ONE Kreis.

    Task 6 masks every car's powertrain pmf to its model-feasible set and weights
    it by the per-model fuel mix, which distorts the per-Kreis powertrain
    distribution away from the raked target: electric mass is removed from
    combustion-only models, and the model-fuel weights bias the surviving
    combustion mass. This computes, for each powertrain ``e`` passed in
    ``target_idx``, a scale factor ``alpha_e`` such that scaling every car's
    ``pmf_i[e]`` by ``alpha_e`` and renormalising makes the EXPECTED per-Kreis
    share of ``e`` equal its target (ADR-0085).

    Feasibility is preserved by construction: scaling only re-weights cars that
    already carry nonzero feasible mass on ``e`` (a combustion-only car has
    ``pmf_i[e] == 0`` and stays 0 under any finite scale). The powertrains are
    coupled (raising one steals renormalised mass from the others), so the factors
    are found by a small fixed-point iteration -- a one-dimensional multiplicative
    raking over the masked support.

    Parameters
    ----------
    pmfs : (n_cars, n_powertrains) array of the per-car *masked* powertrain pmfs
        for the cars of this Kreis (each row sums to 1).
    kreis_target_share : {powertrain -> FZ 27.15 share} for this Kreis (the
        electric entries are the rake targets).
    target_idx : {powertrain -> column index in ``pmfs``} for every powertrain to rake.
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
        return factors, {e: 0.0 for e in target_idx}

    target_cols = list(target_idx.values())
    targets = np.array(
        [kreis_target_share.get(e, 0.0) for e in target_idx], dtype=float)
    cols = np.array(target_cols, dtype=int)

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

    for j, e in enumerate(target_idx):
        factors[cols[j]] = alpha[j]
        resid = float(achieved[j] - targets[j])
        residuals[e] = resid
        # No silent fallback (F5): an unreachable target -- from either side --
        # means the model-feasibility mask leaves too little (or too much) mass on
        # this powertrain to hit the per-Kreis target share.
        if resid < -0.01:
            logger.warning(
                "[fleet_de] Task 7 per-Kreis powertrain rake: Kreis %s "
                "%s UNREACHABLE (under target) -- target %.4f, max achievable "
                "%.4f (residual %.4f); too few cars whose feasible set allows it.",
                kreis, e, targets[j], targets[j] + resid, resid,
            )
        elif resid > 0.01:
            logger.warning(
                "[fleet_de] Task 7 per-Kreis powertrain rake: Kreis %s "
                "%s UNREACHABLE (over target) -- target %.4f, min achievable "
                "%.4f (residual %.4f); the mask forces this powertrain on too "
                "many cars to scale it down further.",
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
                 wohnmobile_age_tilt: bool = True,
                 euro6_substage: bool = True,
                 population_label: str = "",
                 ) -> "tuple[pd.DataFrame, pd.DataFrame] | tuple[pd.DataFrame, pd.DataFrame, dict]":
    """Draw a full vehicle specification for every household car.

    Parameters
    ----------
    df_cars : DataFrame with one row per household car carrying the columns
        ``economic_status`` (one of
        :data:`braunschweig.data.kba.fleet_tables.STATUS_LABELS`),
        ``kreis_ags5`` (home Kreis AGS-5 string), ``gemeinde`` (home Gemeinde
        name; may be missing/``NaN``) and ``raumtyp`` (RegioStaR-7 code 71..77;
        may be missing/``NaN``); optionally ``owner_age`` (assigned owner's age
        in years; consumed by the wohnmobile holder-age tilt; an absent column
        is a loud 100%-fallback batch).
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
    wohnmobile_age_tilt : when ``True`` (default) AND ``consistency_v2=True``,
        PASS 1 tilts the segment pmf's ``wohnmobile`` mass by the owner's age
        class against the KBA 2025-04-01 holder-age reference, with a global
        calibration scalar keeping the expected national wohnmobile share exact
        (issue #315, ADR-0093). The tilt consumes no RNG, so ``False`` (or
        ``consistency_v2=False``) is byte-identical to the untilted draw. The
        reference CSV is COMMITTED: with the flag ON its absence raises instead
        of silently disabling the feature.
    euro6_substage : when ``True`` (default) AND ``consistency_v2=True``, PASS 2
        refines a combustion vehicle's drawn ``euro_class`` from the headline
        ``"euro6"`` into one of the three real Euro-6 substages (``euro6ab``,
        ``euro6dtemp``, ``euro6d``) STRICTLY AFTER the joint ``(age, euro)``
        draw and the pure-electric override above, via
        :meth:`FleetSampler.euro6_substage.substage_pmf` (per-Kreis
        diesel/non-diesel composition from Regionalstatistik 46251-03, falling
        back to the national FZ 27.4 substage split). Only fires for
        ``euro_class == "euro6"`` and ``powertrain in
        hbefa.COMBUSTION_POWERTRAINS`` (``phev``/``hybrid`` keep their drawn
        combustion-shaped euro_class untouched; ``bev``/``hydrogen`` already
        carry ``"electric"`` by this point). ``False``, ``consistency_v2=False``,
        or substage data absent for a given (Kreis, powertrain) with no
        national fallback either leave the plain ``"euro6"`` label and consume
        NO additional RNG (byte-identical / deterministic on the absent-data
        path).

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
    # Task B5 / ADR-0084: the Euro-6 substage lives in its OWN column so
    # euro_class keeps the canonical FZ 27.4 vocabulary. Pre-filled with the
    # not-applicable category, so a row that never reaches the substage draw
    # (legacy path, non-Euro-6, electrified) still carries a real value.
    out_euro6_substage = [ft.EURO6_SUBSTAGE_NOT_APPLICABLE] * n
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

    # Issue #315: wohnmobile holder-age tilt (consistency_v2 path only). MUST
    # sit AFTER the model_brands downgrade above so it keys on the EFFECTIVE
    # consistency_v2 (same effective-v2 contract as the age_income tilt just
    # above), not the caller's raw flag -- otherwise a model_brands=False run
    # would fit/raise for a tilt the (downgraded-to-legacy) draw never
    # consults. Fitted per frame so P_pop and the calibration scalar describe
    # THIS batch. NOTE: the fit queries segment_probabilities once per
    # (status, raumtyp) cell, which adds a handful of hits to the segment
    # model's own tilt counters.
    _wm_tilt = None
    _wm_index = -1
    if consistency_v2 and wohnmobile_age_tilt:
        if sampler.wohnmobile_age_tilt is None:
            raise RuntimeError(
                "fleet_wohnmobile_age_tilt=True but kba_wohnmobile_holder_age.csv "
                "was not found under <data_path>/braunschweig/kba/derived/. The "
                "table is COMMITTED: its absence is a checkout or data-path "
                "wiring defect, never a normal state (project absent-input "
                "rule). Restore the file (git checkout, or re-run "
                "scripts/extract_kba_fleet.py) or set "
                "fleet_wohnmobile_age_tilt=false explicitly."
            )
        if "owner_age" not in df_cars.columns:
            sampler.wohnmobile_age_tilt.mark_batch_fallback(n)
            logger.warning(
                "[fleet_de]%s wohnmobile holder-age tilt: df_cars carries no "
                "'owner_age' column -> 100%% fallback for this batch (the tilt "
                "input never arrived; every car keeps the untilted segment pmf).",
                f" [{population_label}]" if population_label else "",
            )
        else:
            sampler.wohnmobile_age_tilt.fit_population(df_cars, sampler.segment_model)
            _wm_tilt = sampler.wohnmobile_age_tilt
            _wm_index = segments.index(WOHNMOBILE_SEGMENT)

    # Task 6 (consistency_v2): model-feasible powertrain mask (Bug 2).
    # When the HSN/TSN-derived feasible-fuels model is available we draw the
    # model BEFORE the powertrain and mask the powertrain pmf to the model's
    # feasible powertrain set. powertrain_feasibility_list carries per-row
    # provenance ("model_constrained" | "segment_fallback") for the Task 8 hook;
    # _feasibility_fallback counts cars whose mask zeroed the whole pmf (no
    # overlap) so the unmasked pmf was kept. _feasibility_tier_count splits the
    # "model_constrained" cars into Tier 1 (exact brand+family hit) vs Tier 2
    # (brand-wide fallback) so a Tier1 -> Tier2 drift is observable in the
    # aggregate log (issue #163; the plain "model_constrained" count alone
    # cannot show that the HSN/TSN lookup is losing family-level resolution).
    _feasible_fuels = sampler.feasible_fuels if consistency_v2 else None
    powertrain_feasibility_list: list[str] = ["segment_fallback"] * n
    _feasibility_fallback = 0
    _feasibility_tier_count: dict[str, int] = {"family": 0, "brand": 0}
    _powertrain_idx = {p: i for i, p in enumerate(POWERTRAINS)}

    records = df_cars.to_dict(orient="records")

    def _finalize_spec(i: int, segment: str, powertrain: str, euro_class: str,
                       age_band: str, brand: str, model: str,
                       euro6_substage_label: str = ft.EURO6_SUBSTAGE_NOT_APPLICABLE
                       ) -> None:
        """Map the (powertrain, euro, segment) triple to HBEFA and store row i.

        ``euro6_substage_label`` is the Euro-6 substage drawn for a combustion
        Euro-6 car (``euro6ab`` / ``euro6dtemp`` / ``euro6d``) or
        :data:`ft.EURO6_SUBSTAGE_NOT_APPLICABLE`. It refines the HBEFA emission
        concept ONLY -- ``euro_class`` keeps its canonical headline label so the
        realised Euro marginal stays comparable to the KBA reference (ADR-0084).
        """
        # The HBEFA concept is the one place the substage must be visible: HBEFA
        # tabulates Euro-6ab / 6d-temp / 6d as distinct emission concepts.
        euro_for_hbefa = (euro6_substage_label
                          if euro6_substage_label in ft.EURO6_SUBSTAGE_LABELS
                          else euro_class)
        vt = hbefa.vehicle_type_for(
            powertrain, euro_for_hbefa, segment,
            size_map=sampler.size_map,
            fallback_counter=size_fallback_counter,
        )
        vehicle_types.setdefault(vt.type_id, vt)
        out_segment[i] = segment
        out_powertrain[i] = powertrain
        out_euro[i] = euro_class
        out_euro6_substage[i] = euro6_substage_label
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
            # Issue #315: holder-age tilt on the wohnmobile mass -- BEFORE the
            # draw and BEFORE the eff_seg accumulation below, so the realised-
            # margin validator targets the distribution actually drawn from.
            if _wm_tilt is not None:
                seg_pmf = _wm_tilt.tilt(seg_pmf, car.get("owner_age"), _wm_index)
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
            feasibility_tier = None
            if _feasible_fuels is not None and model:
                feasible, feasibility_tier = _feasible_fuels.model_feasible_powertrains_with_tier(
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
                    if feasibility_tier in _feasibility_tier_count:
                        _feasibility_tier_count[feasibility_tier] += 1
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
                    # Same feasibility tier bookkeeping as the weighted-mask
                    # branch above: the car IS model-constrained, only the soft
                    # per-model weights could not be applied.
                    if feasibility_tier in _feasibility_tier_count:
                        _feasibility_tier_count[feasibility_tier] += 1
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
        # ADR-0085: rake EVERY powertrain, not only the electric ones. Masking plus
        # the per-model fuel weights distort the whole distribution, and correcting
        # the electric mass alone left the combustion split biased by +10.2pp petrol
        # against the committed 46251-02 ZGB reference (measured with
        # scripts/measure_combustion_split.py).
        target_idx = dict(_powertrain_idx)
        kreis_factors: dict[str, np.ndarray] = {}
        rows_by_kreis: dict[str, list[int]] = {}
        for i in range(n):
            rows_by_kreis.setdefault(car_kreis[i], []).append(i)
        for kreis, rows in rows_by_kreis.items():
            pmfs = np.array([car_pmf[i] for i in rows], dtype=float)
            unmasked = np.array([car_unmasked_pmf[i] for i in rows], dtype=float)
            # Target = mean UNMASKED (Gemeinde/grid/income-tilted) pmf. That vector
            # is what PowertrainModel already raked onto the per-Kreis KBA marginal,
            # so raking back onto it restores the reference distribution while
            # keeping the tilts and using the model-fuel weights only as a
            # WITHIN-support preference.
            target = {
                e: float(unmasked[:, idx].mean())
                for e, idx in target_idx.items()
            }
            # Unreachable-target WARNINGs (both under- and over-shoot, F5) are
            # logged inside _powertrain_rake_factors itself, keyed by ``kreis``.
            factors, residuals = _powertrain_rake_factors(
                pmfs, target, target_idx, kreis=kreis)
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

            # Task B5: Euro-6 substage (6ab/6d-temp/6d) conditional draw.
            # Fires STRICTLY AFTER the joint (age, euro) draw and the
            # pure-electric override above, only for a genuine combustion
            # Euro-6 vehicle (bev/hydrogen never reach here: their euro_class
            # was already overridden to "electric"; phev/hybrid are excluded
            # because they are not in hbefa.COMBUSTION_POWERTRAINS, so they
            # always keep their drawn combustion-shaped euro_class untouched).
            # ADR-0084: the drawn substage refines the HBEFA emission concept and
            # is emitted as its own column; euro_class is NOT overwritten, so the
            # realised Euro marginal stays comparable to FZ 27.4 / 46251-03.
            _euro6_substage_label = ft.EURO6_SUBSTAGE_NOT_APPLICABLE
            if (euro6_substage and sampler.euro6_substage is not None
                    and euro_class == "euro6"
                    and powertrain in hbefa.COMBUSTION_POWERTRAINS):
                _substage_pmf = sampler.euro6_substage.substage_pmf(
                    car_kreis[i], powertrain)
                if _substage_pmf is not None:
                    # A real pmf resolved (per-Kreis or national fallback) ->
                    # draw. The absent-data path (both fallbacks miss) returns
                    # None above and consumes NO rng here, keeping the
                    # not-applicable label (determinism / byte-identity).
                    _euro6_substage_label = _draw_categorical(
                        rng, sampler.euro6_substage.substages, _substage_pmf)

            _finalize_spec(
                i, car_segment[i], powertrain, euro_class, age_band,
                car_brand[i], car_model[i],
                euro6_substage_label=_euro6_substage_label)
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
        # Task B5 / ADR-0084: the Euro-6 substage refinement. Emitted in the v2
        # path only, like the other two provenance columns, so the legacy frame
        # keeps its exact pre-existing schema.
        df_spec["euro6_substage"] = out_euro6_substage

    df_vehicle_types = pd.DataFrame.from_records(
        [vt.as_record() for vt in vehicle_types.values()]
    )

    # Fallback observability (project no-silent-fallback rule).
    sampler.powertrain_model.log_fallback_rate(population_label)
    if age_model is not None:
        age_model.log_fallback_rate()
    if consistency_v2 and ev_income_tilt and sampler.ev_income_tilt is not None:
        sampler.ev_income_tilt.log_fallback_rate()
    if consistency_v2 and euro6_substage and sampler.euro6_substage is not None:
        sampler.euro6_substage.log_fallback_rate()
    if consistency_v2 and wohnmobile_age_tilt and sampler.wohnmobile_age_tilt is not None:
        sampler.wohnmobile_age_tilt.log_fallback_rate(population_label)
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
    # Issue #163: the Tier-1 (exact brand+family) vs Tier-2 (brand-wide
    # fallback) split is logged alongside the aggregate "model_constrained"
    # count -- see FeasibleFuels.model_feasible_powertrains_with_tier -- so a
    # rising Tier1 -> Tier2 drift (the HSN/TSN lookup losing family-level
    # resolution for more of the fleet) is observable, not hidden inside a
    # single combined count.
    if consistency_v2 and _feasible_fuels is not None:
        n_constrained = sum(
            1 for s in powertrain_feasibility_list if s == "model_constrained")
        c_rate = n_constrained / n if n else 0.0
        n_tier_family = _feasibility_tier_count["family"]
        n_tier_brand = _feasibility_tier_count["brand"]
        logger.info(
            "[fleet_de] model-feasible powertrain mask (consistency_v2, Bug 2): "
            "%d/%d vehicles (%.1f%%) model-constrained "
            "(tier1 exact-family=%d, tier2 brand-fallback=%d); %d (%.1f%%) "
            "no-overlap fallbacks (unmasked pmf kept).",
            n_constrained, n, 100.0 * c_rate,
            n_tier_family, n_tier_brand,
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
            euro6_substage_model=(
                sampler.euro6_substage
                if (euro6_substage and sampler.euro6_substage is not None)
                else None
            ),
            car_kreis=car_kreis,
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
        # Issue #315: wohnmobile holder-age acceptance check (tilt-active only).
        if _wm_tilt is not None:
            _wm_summary = _fv.validate_wohnmobile_holder_age(df_spec, _wm_tilt)
            _validation_summary["wohnmobile_holder_age"] = _wm_summary
            _validation_summary["any_flagged"] = (
                _validation_summary["any_flagged"] or _wm_summary["flagged"])
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
    euro6_substage_model: "Optional[Euro6SubstageModel]" = None,
    car_kreis: Optional[Sequence[str]] = None,
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

    Task B5: when ``euro6_substage_model`` and ``car_kreis`` are both supplied,
    this function ALSO mirrors the Euro-6 substage draw: for a combustion car
    (``pt in hbefa.COMBUSTION_POWERTRAINS``), the joint's ``"euro6"`` column
    mass is further split across the three substage labels using the SAME pmf
    the actual draw would use (:meth:`Euro6SubstageModel.pmf_for`, a pure
    lookup that does not touch the fallback counters). A car whose (Kreis,
    powertrain) has no usable substage pmf (absent-data fallback) keeps its
    ``"euro6"`` mass unsplit, exactly mirroring the draw's plain-"euro6"
    fallback. When either argument is ``None`` (flag off / no sampler
    available), the euro dimension is unchanged from the pre-Task-B5 behaviour.

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
    euro6_substage_model : optional active :class:`Euro6SubstageModel` (Task
        B5); ``None`` disables the substage mirror (pre-Task-B5 behaviour).
    car_kreis : optional per-car home Kreis AGS-5 (same length as
        ``car_pmfs``), required together with ``euro6_substage_model`` to look
        up the per-(Kreis, powertrain) substage pmf.

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
    # Task B5 / ADR-0084: the three Euro-6 substage labels form their OWN
    # dimension (mirroring the ``euro6_substage`` output column), present only
    # when a substage model is actually active -- the draw only emits substages
    # then. They are NOT folded into the euro dimension: euro_class keeps the
    # headline KBA vocabulary on both the realised and the expected side, which is
    # what makes the euro comparison meaningful against FZ 27.4 / 46251-03.
    substage_labels: list[str] = (
        list(euro6_substage_model.substages) + [ft.EURO6_SUBSTAGE_NOT_APPLICABLE]
        if euro6_substage_model is not None else []
    )
    # Euro dimension: real combustion labels + the "electric" category.
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
    # Separate accumulator for the substage dimension; its last slot is the
    # not-applicable category. Empty (and unused) when no substage model is active.
    acc_substage = np.zeros(len(substage_labels), dtype=float)
    # Precomputed indices for the substage split (avoids repeated list.index()
    # lookups inside the per-car loop).
    substage_ext_idx = list(range(len(substage_labels) - 1)) if substage_labels else []
    substage_na_idx = len(substage_labels) - 1 if substage_labels else 0
    euro6_col = euro_labels_list.index("euro6") if "euro6" in euro_labels_list else None

    if drawn_powertrains is not None:
        it_pt: Sequence = drawn_powertrains
    else:
        it_pt = [""] * n  # type: ignore[assignment]
    if car_kreis is not None:
        it_kreis: Sequence = car_kreis
    else:
        it_kreis = [None] * n  # type: ignore[assignment]

    for idx in range(n):
        pmf = car_pmfs[idx]
        factors = kreis_factors[idx]
        joint = age_euro_joints[idx]
        tilt = tilts[idx]
        pt = it_pt[idx]
        kreis = it_kreis[idx]

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
            # A pure-electric car carries NO Euro-6 substage, so its whole mass
            # belongs in the not-applicable bucket. Skipping it here (the original
            # ADR-0084 mirror did, via this `continue`) left acc_substage summing to
            # less than n, which understated `not_applicable` by exactly the
            # pure-electric share and made the validator report a ~4pp DRIFT on a
            # dimension that was in fact correct.
            if substage_labels:
                acc_substage[substage_na_idx] += 1.0
            continue

        euro_marginal = M.sum(axis=0).copy()  # real euro marginal
        # Task B5 / ADR-0084: the Euro-6 substage is its OWN dimension, so the
        # euro_class marginal stays on the headline KBA vocabulary here -- exactly
        # like the draw, which no longer overwrites euro_class. What IS mirrored is
        # the substage distribution itself: this car's "euro6" mass spread over the
        # substage labels with the SAME pmf the draw would resolve
        # (kreis -> national -> absent), everything else counting as
        # not-applicable. A car whose (Kreis, powertrain) has no usable pmf
        # contributes its whole mass to not-applicable, mirroring the draw's
        # fallback (no false alarm).
        if (euro6_substage_model is not None and euro6_col is not None
                and drawn_powertrains is not None and kreis is not None
                and pt in hbefa.COMBUSTION_POWERTRAINS):
            euro6_mass = float(euro_marginal[euro6_col])
            substage_pmf = (euro6_substage_model.pmf_for(kreis, pt)
                            if euro6_mass > 0.0 else None)
            if substage_pmf is not None:
                for k, ext_idx in enumerate(substage_ext_idx):
                    acc_substage[ext_idx] += euro6_mass * float(substage_pmf[k])
                acc_substage[substage_na_idx] += 1.0 - euro6_mass
            else:
                acc_substage[substage_na_idx] += 1.0
        elif euro6_substage_model is not None:
            acc_substage[substage_na_idx] += 1.0
        acc_euro[:len(euro_labels_list)] += euro_marginal

    # Normalise by n to get mean expected marginals (which sum to 1.0).
    # Include the "electric" / Euro-6-substage labels in the euro dict only
    # when they have mass (i.e. the corresponding feature was actually active
    # and drew at least once) -- the headline euro_labels_list entries (incl.
    # plain "euro6") are always included, even at 0.0.
    conditional_labels = {electric_euro}
    euro_dict: dict[str, float] = {}
    for j, e in enumerate(euro_labels_ext):
        v = float(acc_euro[j] / n)
        if v > 0.0 or e not in conditional_labels:
            euro_dict[e] = v
    out: dict[str, dict[str, float]] = {
        "powertrain": {p: float(acc_pt[j] / n)
                       for j, p in enumerate(powertrains)},
        "age_band": {a: float(acc_age[j] / n)
                     for j, a in enumerate(age_labels)},
        "euro_class": euro_dict,
    }
    if substage_labels:
        out["euro6_substage"] = {lbl: float(acc_substage[j] / n)
                                 for j, lbl in enumerate(substage_labels)}
    return out
