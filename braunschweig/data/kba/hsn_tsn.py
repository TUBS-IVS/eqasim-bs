"""HSN/TSN engine-attribute lookup + matcher for the synthetic fleet.

This module attaches engine technical attributes -- representative engine power
(kW and PS), displacement (ccm), a dominant fuel and a representative HSN/TSN
code -- to every synthetic fleet vehicle by matching on the vehicle's brand and
model family. The data come from the local-only KBA HSN/TSN directory
``<data_path>/braunschweig/kba/hsn_tsn_lookup.csv`` (31423 variant rows, columns
``brand, hsn, tsn, model, power_ps, power_kw, displacement_ccm, fuel``; built by
``scripts/scrape_hsn_tsn.py``). The CSV is part of the gitignored ``eqasim-data``
tree; the scrape script regenerates it.

Provenance / scope of the match
-------------------------------
The match is *inherently approximate*. The KBA fleet generative chain
(:mod:`braunschweig.synthesis.vehicles.fleet_sampling_de`) draws a coarse
**model family** (a KBA Modellreihe such as ``VW GOLF`` or ``FIAT 500``), while
the HSN/TSN file lists the much finer **variant** strings (``VW Golf VII 2.0
TDI``, ``VW Golf I Cabrio L`` ...). One model family therefore maps to many
HSN/TSN rows spanning decades of generations and engine sizes. We collapse those
rows to a single representative record by taking the **median** power_kw /
power_ps / displacement_ccm over the matching variants, the **most common**
(hsn, tsn) pair, and the **dominant** fuel. The attached engine attributes are
thus a plausible central value for the model family, NOT the exact engine of a
specific car. They are additive completeness data and are not (yet) consumed by
the simulation.

Brand canonicalisation
----------------------
The HSN/TSN file uses 62 display brand names ("Alfa Romeo", "Mercedes-Benz",
"VW", ...). The fleet ``brand`` column is the first whitespace token of the KBA
Modellreihe string (so "VOLKSWAGEN"/"VW" -> ``VW``, "MERCEDES" -> the family
"Mercedes-Benz", "ALFA" -> "Alfa Romeo"). :data:`FLEET_BRAND_TO_HSN_TSN` maps the
fleet brand tokens onto the HSN/TSN display brands. Fleet marques absent from the
HSN/TSN file (e.g. ASTON, BENTLEY, DS, FERRARI) map to ``None`` and fall back to
the brand-level / global median; the unmapped brands encountered are logged.

Model-family normalisation
--------------------------
:func:`model_family` produces a single normalised token shared by both sides:

  1. lower-case the model string and strip surrounding whitespace;
  2. strip the canonical brand display name prefix if present (HSN/TSN side),
     else strip the leading fleet brand token (fleet side);
  3. drop a redundant leading marque token that some KBA strings repeat
     (e.g. "ALFA ROMEO ALFA 147" -> after stripping "alfa romeo" the tail is
     "alfa 147"; the leading "alfa" is dropped -> "147");
  4. return the first remaining whitespace token (e.g. "golf", "500",
     "c-klasse"). An empty result (model is only the brand) yields ``""``.

So KBA "GOLF" and HSN/TSN "VW Golf VII 2.0 TDI" both normalise to ``golf``.

No-silent-fallback rule
-----------------------
:func:`attach_hsn_tsn` records, per vehicle, which match TIER produced the
record -- ``exact`` ((brand, family, fuel group)), ``model`` ((brand, family)),
``brand`` (brand median) or ``global`` (global median) -- and logs the tier
rates. A low ``exact + model`` rate signals a brand/model normalisation bug
(not merely sparse data) and is logged at WARNING level.
"""

from __future__ import annotations

import logging
import os
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

# Relative path of the KBA segment-model CSV inside the synpp ``data_path``.
KBA_SEGMENT_MODEL_RELATIVE_PATH = os.path.join(
    "braunschweig", "kba", "derived", "kba_segment_model.csv"
)

logger = logging.getLogger(__name__)

#: Relative path of the HSN/TSN lookup CSV inside the synpp ``data_path``.
HSN_TSN_RELATIVE_PATH = os.path.join("braunschweig", "kba", "hsn_tsn_lookup.csv")

#: Required columns of the HSN/TSN lookup CSV (schema contract; a missing /
#: renamed column must fail loudly rather than load silently).
REQUIRED_COLUMNS = (
    "brand", "hsn", "tsn", "model", "power_ps", "power_kw",
    "displacement_ccm", "fuel",
)

#: Fleet brand token (first token of the KBA Modellreihe, upper-case) -> the
#: HSN/TSN display brand name. Built by inspecting BOTH label sets: the 62
#: HSN/TSN display brands and the 62 fleet brand tokens. Fleet marques with no
#: HSN/TSN counterpart are intentionally absent (they resolve to ``None`` ->
#: brand/global fallback, logged). Two-word display brands ("Alfa Romeo",
#: "Mercedes-Benz", "Land Rover") are reached from the single fleet token
#: ("ALFA", "MERCEDES", "LAND").
FLEET_BRAND_TO_HSN_TSN: dict[str, str] = {
    "ABARTH": "Abarth",
    "ALFA": "Alfa Romeo",
    "ALPINE": "Alpine",
    "AUDI": "Audi",
    "AUSTIN": "Austin",         # "Austin" now present in 62-brand lookup; was "Rover".
    "BMW": "BMW",
    "BYD": "BYD",
    "CADILLAC": "Cadillac",
    "CHEVROLET": "Chevrolet",   # Fleet Chevrolet (Spark, Matiz, Aveo, Captiva, Cruze, Orlando, Corvette, Trax, Kalos, Camaro, Nubira, Lacetti, Epica, Rezzo) in KBA lookup.
    "CHRYSLER": "Chrysler",
    "CITROEN": "Citroen",
    "CUPRA": "Cupra",
    "DACIA": "Dacia",
    "DAEWOO": "Daewoo",
    "DAIHATSU": "Daihatsu",
    "DODGE": "Dodge",
    "FIAT": "Fiat",
    "FORD": "Ford",
    "GENESIS": "Genesis",
    "HONDA": "Honda",
    "HUMMER": "Hummer",
    "HYUNDAI": "Hyundai",
    "INEOS": "Ineos",
    "INFINITI": "Infiniti",
    "ISUZU": "Isuzu",
    "IVECO": "Iveco",
    "JAGUAR": "Jaguar",
    "JEEP": "Jeep",
    "KIA": "Kia",
    "LADA": "Lada",
    "LAMBORGHINI": "Lamborghini",
    "LANCIA": "Lancia",
    "LAND": "Land Rover",
    "LEXUS": "Lexus",
    "LOTUS": "Lotus",
    "LYNK": "Lynk-Co",
    "MAN": "MAN",
    "MASERATI": "Maserati",
    "MAZDA": "Mazda",
    "MERCEDES": "Mercedes-Benz",
    "MINI": "Mini",
    "MITSUBISHI": "Mitsubishi",
    "NIO": "Nio",
    "NISSAN": "Nissan",
    "OPEL": "Opel",
    "PEUGEOT": "Peugeot",
    "POLESTAR": "Polestar",
    "PORSCHE": "Porsche",
    "RENAULT": "Renault",
    "ROVER": "Rover",
    "SAAB": "Saab",
    "SEAT": "Seat",
    "SKODA": "Skoda",
    "SMART": "Smart",
    "SSANGYONG": "Ssangyong",
    "SUBARU": "Subaru",
    "SUZUKI": "Suzuki",
    "TALBOT": "Talbot",
    "TESLA": "Tesla",
    "TOYOTA": "Toyota",
    "VOLKSWAGEN": "VW",
    "VOLVO": "Volvo",
    "VW": "VW",
}

#: Our canonical powertrain (:data:`braunschweig.data.kba.fleet_tables.POWERTRAIN_LABELS`)
#: -> the dominant HSN/TSN fuel string used for the optional exact-tier
#: refinement. Hybrids and gas span several HSN/TSN fuel spellings; we map them
#: to their primary-energy spelling and accept a substring match in the lookup
#: (see :meth:`HsnTsnLookup.lookup`). Powertrains with no clean HSN/TSN fuel
#: counterpart map to ``None`` -> no fuel refinement (model-tier match).
POWERTRAIN_TO_FUEL_GROUP: dict[str, Optional[str]] = {
    "petrol": "Benzin",
    "diesel": "Diesel",
    "gas": "Benzin/Autogas",
    "bev": "Elektro",
    "phev": "Benzin/Elektro Plug-in",
    "hybrid": "Benzin/Elektro",
    "hydrogen": "Wasserstoff/Elektro",
    "other": None,
}


def canonical_brand(fleet_brand: str) -> Optional[str]:
    """Map a fleet brand token to its HSN/TSN display brand, or ``None``.

    The fleet brand is the first whitespace token of the KBA Modellreihe string
    (upper-case). Returns the HSN/TSN display brand name when known, else
    ``None`` (the caller falls back to the brand/global median and logs it).
    """
    if fleet_brand is None:
        return None
    return FLEET_BRAND_TO_HSN_TSN.get(str(fleet_brand).strip().upper())


def powertrain_to_fuel_group(powertrain: str) -> Optional[str]:
    """Map a canonical powertrain to its dominant HSN/TSN fuel string (or ``None``)."""
    if powertrain is None:
        return None
    return POWERTRAIN_TO_FUEL_GROUP.get(str(powertrain).strip().lower())


def model_family(brand: str, model: str) -> str:
    """Normalise a model string to a single brand-stripped family token.

    See the module docstring for the exact steps. ``brand`` is the HSN/TSN
    display brand (e.g. "VW", "Alfa Romeo") used to strip the leading brand
    prefix; ``model`` is either an HSN/TSN variant string or a KBA Modellreihe
    string. Returns the lower-cased first remaining token, or ``""`` when the
    model is only the brand name.
    """
    if model is None:
        return ""
    text = str(model).strip().lower()
    if not text:
        return ""

    # 1. Strip the canonical display-brand prefix (HSN/TSN side, e.g.
    #    "vw golf ..." -> "golf ...", "mercedes-benz c-klasse" -> "c-klasse").
    if brand:
        brand_lower = str(brand).strip().lower()
        if text.startswith(brand_lower + " "):
            text = text[len(brand_lower) + 1:].strip()
        else:
            # 2. Fleet side: the KBA Modellreihe is "BRAND MODEL"; the first
            #    token is the (single-word) fleet brand token. Drop it.
            first, _, rest = text.partition(" ")
            if rest and not _looks_like_model_token(first):
                text = rest.strip()

    if not text:
        return ""

    # 3. Drop a redundant leading marque token some KBA strings repeat (e.g.
    #    "alfa romeo alfa 147" -> "alfa 147"); the leading "alfa" is dropped.
    first, _, rest = text.partition(" ")
    if rest and brand and first == str(brand).strip().lower().split(" ")[0]:
        text = rest.strip()

    # 4. First remaining whitespace token.
    token = text.split(" ", 1)[0] if text else ""
    return _strip_generation_suffix(token)


def _strip_generation_suffix(token: str) -> str:
    """Collapse a model-family token to its generation-independent stem.

    Bridges the two taxonomies' suffix conventions so both sides agree:

      * Mercedes ``"c-klasse"`` (KBA Modellreihe) -> ``"c"`` (HSN/TSN uses the
        single-letter class), via an explicit ``-klasse`` strip;
      * Opel/VW generation suffixes ``"corsa-d"`` / ``"astra-k"`` /
        ``"ascona-c"`` (HSN/TSN) -> ``"corsa"`` / ``"astra"`` / ``"ascona"``, so
        they match the suffix-less KBA family ``"corsa"`` / ``"astra"``;
      * Trailing comma from KBA comma-separated multi-model strings
        (``"glk,"`` -> ``"glk"``, ``"mirage,"`` -> ``"mirage"``): the KBA
        Modellreihe sometimes lists two successor models as ``"MERCEDES GLK,
        GLC"``; the first token carries a trailing comma that must be stripped
        so it matches the single-model lookup key ``"glk"``;
      * Trailing exclamation mark from HSN/TSN brand quirks (``"up!"`` ->
        ``"up"``): VW's city car is stored as ``"VW Up! 1.0"`` in the HSN/TSN
        file but the KBA Modellreihe uses ``"VW UP"`` (no ``!``); stripping the
        ``!`` makes both sides agree on ``"up"``.

    The generation strip fires ONLY when the suffix is a short trailing
    ``-<1-2 chars>`` AND the stem before it is at least three characters, so
    genuine hyphenated names with a short STEM are preserved (``"c-max"``,
    ``"b-max"``, ``"gt-r"``, ``"e-tron"`` keep their hyphen).
    """
    if not token:
        return ""
    # Strip trailing comma (comma-separated multi-model KBA strings).
    if token.endswith(","):
        token = token[:-1]
    # Strip trailing exclamation mark (e.g. HSN/TSN "up!" -> "up").
    if token.endswith("!"):
        token = token[:-1]
    if not token:
        return ""
    if token.endswith("-klasse"):
        return token[: -len("-klasse")]
    head, sep, tail = token.rpartition("-")
    if sep and len(head) >= 3 and 1 <= len(tail) <= 2:
        return head
    return token


def _looks_like_model_token(token: str) -> bool:
    """Heuristic: a token that is clearly a model designator, not a brand word.

    Used only on the fleet-side branch of :func:`model_family` to avoid stripping
    a leading token that is actually the model (defensive; KBA Modellreihe
    strings always start with the brand, so this rarely triggers).
    """
    # Numeric / alphanumeric model codes ("500", "147", "a-klasse", "id.3").
    return bool(token) and (token[0].isdigit() or "-" in token or "." in token)


@dataclass(frozen=True)
class EngineRecord:
    """Representative engine attributes for a brand/model family (median aggregate)."""

    power_kw: float
    power_ps: float
    displacement_ccm: float
    fuel_detail: str
    hsn: str
    tsn: str

    def as_columns(self) -> dict[str, object]:
        """Return the record as the five public attach columns."""
        return {
            "engine_power_kw": self.power_kw,
            "engine_power_ps": self.power_ps,
            "displacement_ccm": self.displacement_ccm,
            "fuel_detail": self.fuel_detail,
            "hsn": self.hsn,
            "tsn": self.tsn,
        }


@dataclass(frozen=True)
class VariantPool:
    """A pool of HSN/TSN variant rows for a fallback tier (brand or global).

    Holds parallel numpy arrays for a uniform-random draw; the fuel_detail is
    the authoritative HSN/TSN fuel-group string for this pool (already
    fuel-conditioned by how the pool is constructed). The arrays may contain
    many rows; drawing a random index each time ensures unmatched cars are NOT
    all assigned one identical engine fingerprint.
    """

    power_kw: np.ndarray
    power_ps: np.ndarray
    displacement: np.ndarray
    fuel_detail: str       # authoritative fuel string for this pool (already fuel-filtered)
    hsn: np.ndarray
    tsn: np.ndarray

    @classmethod
    def from_group(cls, group: pd.DataFrame, fuel_detail: str) -> "VariantPool":
        """Build a pool from a subset of the HSN/TSN frame."""
        return cls(
            power_kw=group["power_kw"].to_numpy(dtype=float),
            power_ps=group["power_ps"].to_numpy(dtype=float),
            displacement=group["displacement_ccm"].to_numpy(dtype=float),
            fuel_detail=fuel_detail,
            hsn=group["hsn"].to_numpy(dtype=str),
            tsn=group["tsn"].to_numpy(dtype=str),
        )


def _draw_record(pool: VariantPool, rng: np.random.Generator) -> EngineRecord:
    """Draw one variant uniformly from a pool (deterministic via rng)."""
    i = int(rng.integers(len(pool.power_kw)))
    return EngineRecord(pool.power_kw[i], pool.power_ps[i], pool.displacement[i],
                        pool.fuel_detail, pool.hsn[i], pool.tsn[i])


def _aggregate(group: pd.DataFrame) -> EngineRecord:
    """Collapse the HSN/TSN rows of one match key to a representative record.

    Power and displacement are the MEDIAN over the variants (robust to the wide
    spread of generations within a model family); the (hsn, tsn) pair is the
    most common one; the fuel is the most common (dominant) fuel string.
    """
    power_kw = float(np.median(group["power_kw"].to_numpy(dtype=float)))
    power_ps = float(np.median(group["power_ps"].to_numpy(dtype=float)))
    displacement = float(np.median(group["displacement_ccm"].to_numpy(dtype=float)))
    fuel_detail = str(group["fuel"].mode().iloc[0]) if len(group) else ""
    hsn_tsn_pairs = list(zip(group["hsn"].astype(str), group["tsn"].astype(str)))
    hsn, tsn = Counter(hsn_tsn_pairs).most_common(1)[0][0] if hsn_tsn_pairs else ("", "")
    return EngineRecord(power_kw, power_ps, displacement, fuel_detail, hsn, tsn)


@dataclass
class HsnTsnLookup:
    """In-memory HSN/TSN lookup keyed by (canonical brand, model family[, fuel]).

    Tiers (queried in order by :meth:`lookup`):

      * ``brand_model_fuel_records`` -- (brand, family, fuel group) -> record
        (the EXACT tier, when the powertrain fuel group is known and present);
      * ``brand_model_records``      -- (brand, family) -> record (MODEL tier);
      * ``brand_records``            -- brand -> median record (BRAND tier);
      * ``segment_fuel_pools``       -- (segment, fuel group) -> pool (SEGMENT tier,
        Task 3: between brand and global; requires segment kwarg to lookup/attach);
      * ``global_record``            -- global median over all variants.
    """

    brand_model_fuel_records: dict[tuple[str, str, str], EngineRecord]
    brand_model_records: dict[tuple[str, str], EngineRecord]
    brand_records: dict[str, EngineRecord]
    global_record: EngineRecord
    brand_fuel_records: dict[tuple[str, str], EngineRecord] = field(default_factory=dict)
    global_fuel_records: dict[str, EngineRecord] = field(default_factory=dict)
    # Variant pools for the pooled fallback tiers (brand/global) — used by
    # attach_hsn_tsn to draw a per-vehicle engine rather than returning one
    # identical median for every unmatched car (Bug 1 fix). Keyed by
    # (brand, fuel_group) and fuel_group respectively (mirroring the median dicts).
    brand_fuel_pools: dict[tuple[str, str], VariantPool] = field(default_factory=dict)
    global_fuel_pools: dict[str, VariantPool] = field(default_factory=dict)
    brand_pools: dict[str, VariantPool] = field(default_factory=dict)
    global_pool: Optional[VariantPool] = field(default=None)
    # Task 3: segment-conditioned engine pool, keyed by (segment, fuel_group).
    # Populated when from_data_path is called (kba_segment_model.csv available).
    # Empty dict when only from_frame is used (e.g. in unit tests without data_path).
    segment_fuel_pools: dict[tuple[str, str], VariantPool] = field(default_factory=dict)
    # Diagnostic counters (no-silent-fallback rule).
    _tier_counts: Counter = field(default_factory=Counter)
    _unmapped_brands: Counter = field(default_factory=Counter)

    @classmethod
    def from_data_path(cls, data_path: str) -> "HsnTsnLookup":
        """Build the lookup from ``<data_path>/braunschweig/kba/hsn_tsn_lookup.csv``.

        Also loads ``kba_segment_model.csv`` (when present) to build
        segment-conditioned engine pools (Task 3: segment tier between brand
        and global). The segment tagging is opportunistic; a missing
        kba_segment_model.csv only suppresses the segment tier.
        """
        path = os.path.join(data_path, HSN_TSN_RELATIVE_PATH)
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"HSN/TSN lookup not found: {path} (run scripts/scrape_hsn_tsn.py "
                f"to (re)generate it; the CSV is local-only / gitignored)."
            )
        df = pd.read_csv(path)
        missing = set(REQUIRED_COLUMNS) - set(df.columns)
        if missing:
            raise RuntimeError(
                f"{path}: missing columns {sorted(missing)} (have "
                f"{sorted(df.columns)}) -- schema drift; re-run scrape_hsn_tsn.py."
            )

        # Task 3: build (canonical_brand, family) -> segment map from
        # kba_segment_model.csv so we can tag every HSN/TSN variant row with a
        # KBA segment and build segment-conditioned variant pools.
        family_to_segment: dict[tuple[str, str], str] = {}
        seg_model_path = os.path.join(data_path, KBA_SEGMENT_MODEL_RELATIVE_PATH)
        if os.path.exists(seg_model_path):
            try:
                df_seg = pd.read_csv(seg_model_path, comment="#")
                for _, row in df_seg.iterrows():
                    seg_model_str = str(row["model"])
                    segment = str(row["segment"])
                    brand_token = seg_model_str.split(" ", 1)[0].upper()
                    cb = canonical_brand(brand_token)
                    if cb is None:
                        continue
                    fam = model_family(cb, seg_model_str)
                    if fam:
                        family_to_segment[(cb, fam)] = segment
                logger.debug(
                    "[hsn_tsn] built family->segment map: %d entries from %s",
                    len(family_to_segment), seg_model_path,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[hsn_tsn] could not load kba_segment_model.csv for segment "
                    "tagging (segment tier will be skipped): %s", exc,
                )
        else:
            logger.debug(
                "[hsn_tsn] kba_segment_model.csv not found at %s; "
                "segment tier disabled.", seg_model_path,
            )

        return cls.from_frame(df, family_to_segment=family_to_segment)

    @classmethod
    def from_frame(
        cls,
        df: pd.DataFrame,
        family_to_segment: Optional[dict[tuple[str, str], str]] = None,
    ) -> "HsnTsnLookup":
        """Build the lookup from an in-memory HSN/TSN frame (used by tests).

        Parameters
        ----------
        df : HSN/TSN variant frame (columns: brand, hsn, tsn, model, power_ps,
            power_kw, displacement_ccm, fuel).
        family_to_segment : optional ``(canonical_brand, family) -> segment`` map
            built from ``kba_segment_model.csv`` in :meth:`from_data_path`. When
            ``None`` (tests / callers without data_path) the segment tier is
            silently disabled (``segment_fuel_pools`` stays empty).
        """
        df = df.copy()
        df["hsn"] = df["hsn"].astype(str)
        df["tsn"] = df["tsn"].astype(str)
        df["fuel"] = df["fuel"].astype(str)
        # The display brand is the match brand; the family is brand-stripped.
        df["_family"] = [model_family(b, m) for b, m in zip(df["brand"], df["model"])]

        brand_model_fuel: dict[tuple[str, str, str], EngineRecord] = {}
        brand_model: dict[tuple[str, str], EngineRecord] = {}
        brand_recs: dict[str, EngineRecord] = {}

        for brand, brand_group in df.groupby("brand"):
            brand_recs[str(brand)] = _aggregate(brand_group)
            for family, fam_group in brand_group.groupby("_family"):
                if not family:
                    continue
                brand_model[(str(brand), str(family))] = _aggregate(fam_group)
                for fuel, fuel_group in fam_group.groupby("fuel"):
                    brand_model_fuel[(str(brand), str(family), str(fuel))] = \
                        _aggregate(fuel_group)

        global_record = _aggregate(df)

        brand_fuel: dict[tuple[str, str], EngineRecord] = {}
        brand_fuel_pools: dict[tuple[str, str], VariantPool] = {}
        brand_pools: dict[str, VariantPool] = {}
        for brand, brand_group in df.groupby("brand"):
            brand_pools[str(brand)] = VariantPool.from_group(
                brand_group, str(brand_group["fuel"].mode().iloc[0]) if len(brand_group) else "")
            for fuel, fuel_group in brand_group.groupby("fuel"):
                brand_fuel[(str(brand), str(fuel))] = _aggregate(fuel_group)
                brand_fuel_pools[(str(brand), str(fuel))] = VariantPool.from_group(
                    fuel_group, str(fuel))
        global_fuel: dict[str, EngineRecord] = {}
        global_fuel_pools: dict[str, VariantPool] = {}
        for fuel, fuel_group in df.groupby("fuel"):
            global_fuel[str(fuel)] = _aggregate(fuel_group)
            global_fuel_pools[str(fuel)] = VariantPool.from_group(fuel_group, str(fuel))
        global_pool = VariantPool.from_group(
            df, str(df["fuel"].mode().iloc[0]) if len(df) else "")

        # Task 3: build segment-conditioned variant pools when the
        # family_to_segment map was provided (from_data_path path).
        # Tag each HSN/TSN row with its KBA segment via the (brand, family) key.
        segment_fuel_pools: dict[tuple[str, str], VariantPool] = {}
        if family_to_segment:
            df["_segment"] = [
                family_to_segment.get((str(b), str(f)), "")
                for b, f in zip(df["brand"], df["_family"])
            ]
            tagged = df[df["_segment"] != ""]
            n_tagged = len(tagged)
            n_pools = 0
            for (segment, fuel), seg_fuel_group in tagged.groupby(["_segment", "fuel"]):
                if len(seg_fuel_group) == 0:
                    continue
                segment_fuel_pools[(str(segment), str(fuel))] = VariantPool.from_group(
                    seg_fuel_group, str(fuel)
                )
                n_pools += 1
            logger.debug(
                "[hsn_tsn] segment pools: %d HSN/TSN rows tagged, %d "
                "(segment, fuel) pools built",
                n_tagged, n_pools,
            )

        return cls(
            brand_model_fuel_records=brand_model_fuel,
            brand_model_records=brand_model,
            brand_records=brand_recs,
            global_record=global_record,
            brand_fuel_records=brand_fuel,
            global_fuel_records=global_fuel,
            brand_fuel_pools=brand_fuel_pools,
            global_fuel_pools=global_fuel_pools,
            brand_pools=brand_pools,
            global_pool=global_pool,
            segment_fuel_pools=segment_fuel_pools,
        )

    def lookup(self, fleet_brand: str, family: str,
               powertrain: Optional[str] = None,
               segment: Optional[str] = None) -> tuple[EngineRecord, str]:
        """Resolve (fleet brand, model family, powertrain[, segment]) -> (record, tier).

        Tier order: exact (brand+family+fuel group) -> model (brand+family) ->
        brand (brand median) -> segment (segment+fuel pool, Task 3) ->
        global (global median). The fuel group is the dominant HSN/TSN fuel
        string for the powertrain; an exact-tier hit additionally requires that
        fuel group to be present for the model family, else the match drops to
        the model tier (so a model with no electric variant in the HSN/TSN file
        still gets its combustion median).

        The ``segment`` parameter enables the SEGMENT tier (between brand and
        global): when given and ``segment_fuel_pools`` contains an entry for
        ``(segment, fuel_group)``, that pool is used. Pass ``None`` (default)
        to skip the segment tier entirely (OFF-safe for callers without segment
        data and for from_frame-built lookups without segment pools).
        """
        brand = canonical_brand(fleet_brand)
        fuel_group = powertrain_to_fuel_group(powertrain)
        if brand is None:
            if fleet_brand:
                self._unmapped_brands[str(fleet_brand)] += 1
            # No brand match: try segment tier first, then global.
            if segment is not None and fuel_group is not None and self.segment_fuel_pools:
                pool = self.segment_fuel_pools.get((str(segment), fuel_group))
                if pool is not None:
                    self._tier_counts["segment"] += 1
                    # Return global_record as a placeholder; attach_hsn_tsn
                    # detects tier=="segment" and draws from the pool directly.
                    return self.global_record, "segment"
            self._tier_counts["global"] += 1
            if fuel_group is not None:
                rec = self.global_fuel_records.get(fuel_group)
                if rec is not None:
                    return rec, "global"
            return self.global_record, "global"

        if family:
            if fuel_group is not None:
                rec = self.brand_model_fuel_records.get((brand, family, fuel_group))
                if rec is not None:
                    self._tier_counts["exact"] += 1
                    return rec, "exact"
            rec = self.brand_model_records.get((brand, family))
            if rec is not None:
                self._tier_counts["model"] += 1
                return rec, "model"

        # BRAND tier, fuel-conditioned first.
        if fuel_group is not None:
            rec = self.brand_fuel_records.get((brand, fuel_group))
            if rec is not None:
                self._tier_counts["brand"] += 1
                return rec, "brand"
        rec = self.brand_records.get(brand)
        if rec is not None:
            self._tier_counts["brand"] += 1
            return rec, "brand"

        # SEGMENT tier (Task 3): between brand and global.
        # Only active when the caller passes a segment AND segment_fuel_pools
        # are populated (i.e. from_data_path with kba_segment_model.csv).
        if segment is not None and fuel_group is not None and self.segment_fuel_pools:
            pool = self.segment_fuel_pools.get((str(segment), fuel_group))
            if pool is not None:
                self._tier_counts["segment"] += 1
                # Return global_record as a placeholder; attach_hsn_tsn detects
                # tier=="segment" and draws from the pool directly.
                return self.global_record, "segment"

        # GLOBAL tier, fuel-conditioned first.
        if fuel_group is not None:
            rec = self.global_fuel_records.get(fuel_group)
            if rec is not None:
                self._tier_counts["global"] += 1
                return rec, "global"
        self._tier_counts["global"] += 1
        return self.global_record, "global"

    def get_pool(self, fleet_brand: str,
                 powertrain: Optional[str] = None) -> Optional[VariantPool]:
        """Return the best-matching variant pool for a brand/global fallback.

        Used by :func:`attach_hsn_tsn` for the pooled tiers (brand/global) so
        that the drawn engine varies per vehicle rather than being a single
        constant median. Returns ``None`` when the pool dicts are empty (e.g.
        a lookup built before Task 2; callers fall back to the median record).
        """
        brand = canonical_brand(fleet_brand)
        fuel_group = powertrain_to_fuel_group(powertrain)
        if brand is not None and fuel_group is not None:
            pool = self.brand_fuel_pools.get((brand, fuel_group))
            if pool is not None:
                return pool
            pool = self.brand_pools.get(brand)
            if pool is not None:
                return pool
        elif brand is not None:
            pool = self.brand_pools.get(brand)
            if pool is not None:
                return pool
        # Global fallback.
        if fuel_group is not None:
            pool = self.global_fuel_pools.get(fuel_group)
            if pool is not None:
                return pool
        return self.global_pool

    def get_segment_pool(self, segment: str,
                         powertrain: Optional[str] = None) -> Optional[VariantPool]:
        """Return the segment-conditioned variant pool for a segment/fuel combo.

        Used by :func:`attach_hsn_tsn` when tier is ``"segment"`` so that
        each vehicle draws a varied engine from the matching segment+fuel pool.
        Returns ``None`` when the segment tier is not populated or the
        (segment, fuel_group) pair is absent.
        """
        if not self.segment_fuel_pools:
            return None
        fuel_group = powertrain_to_fuel_group(powertrain)
        if fuel_group is None:
            return None
        return self.segment_fuel_pools.get((str(segment), fuel_group))

    def log_tier_rates(self) -> None:
        """Log the per-tier match rates (no-silent-fallback rule).

        A low ``exact + model`` rate is escalated to WARNING because it signals a
        brand/model normalisation bug rather than merely sparse reference data.
        """
        total = sum(self._tier_counts.values())
        if total == 0:
            logger.info("[hsn_tsn] no vehicles matched (empty fleet).")
            return
        exact = self._tier_counts.get("exact", 0)
        model = self._tier_counts.get("model", 0)
        brand = self._tier_counts.get("brand", 0)
        glob = self._tier_counts.get("global", 0)
        exact_model_rate = (exact + model) / total
        emit = logger.warning if exact_model_rate < 0.5 else logger.info
        emit(
            "[hsn_tsn] match tier rates over %d vehicles: exact %d (%.1f%%), "
            "model %d (%.1f%%), brand %d (%.1f%%), global %d (%.1f%%); "
            "exact+model %.1f%%",
            total, exact, 100.0 * exact / total, model, 100.0 * model / total,
            brand, 100.0 * brand / total, glob, 100.0 * glob / total,
            100.0 * exact_model_rate,
        )
        if self._unmapped_brands:
            top = ", ".join(
                f"{b}={c}" for b, c in self._unmapped_brands.most_common(10))
            logger.warning(
                "[hsn_tsn] %d unmapped fleet brand(s) fell back to global "
                "median (top: %s); add them to FLEET_BRAND_TO_HSN_TSN if a "
                "HSN/TSN counterpart exists.",
                len(self._unmapped_brands), top,
            )


_POOLED_TIERS = frozenset({"brand", "global", "segment"})


def attach_hsn_tsn(df_vehicles: pd.DataFrame,
                   data_path: Optional[str] = None,
                   lookup: Optional[HsnTsnLookup] = None,
                   random_seed: int = 0,
                   keep_tier: bool = True) -> pd.DataFrame:
    """Attach the five HSN/TSN engine columns to every fleet vehicle.

    For each vehicle the matcher looks up (brand, model family, powertrain) and,
    on a miss, falls back to (brand, model family) -> brand median -> global
    median. The added columns are ``engine_power_kw``, ``engine_power_ps``,
    ``displacement_ccm``, ``fuel_detail``, ``hsn``, ``tsn``. Match-tier rates are
    logged (no-silent-fallback rule).

    ``fuel_detail`` is always derived from the vehicle's own ``powertrain`` via
    :data:`POWERTRAIN_TO_FUEL_GROUP`; it is never copied verbatim from the lookup
    record (which may reflect a different fuel for the same model family). This
    ensures a diesel car never carries "Benzin" in ``fuel_detail``.

    For the pooled fallback tiers (``brand`` and ``global``) the engine numbers
    (power_kw/ps, displacement, hsn/tsn) are drawn uniformly at random from the
    matched variant pool so that unmatched cars do NOT all share one identical
    engine fingerprint. The draw is deterministic given ``random_seed``. The
    exact/model tiers are deterministic medians and are cached per distinct
    vehicle spec for performance.

    Parameters
    ----------
    df_vehicles : DataFrame with at least ``brand``, ``model`` and ``powertrain``
        columns (as produced by
        :func:`braunschweig.synthesis.vehicles.fleet_sampling_de.sample_fleet`).
    data_path : synpp ``data_path``; used to build the lookup when ``lookup`` is
        ``None``.
    lookup : an optional pre-built :class:`HsnTsnLookup` (avoids re-reading the
        CSV when attaching to several frames).
    random_seed : integer seed for the numpy RNG used to draw from variant pools
        for the pooled fallback tiers. Default 0 (fully deterministic).
    keep_tier : when ``True`` (default) the diagnostic ``hsn_tsn_match_tier``
        column is kept on the returned frame (handy for analysis / tests); set
        ``False`` to drop it.
    """
    required = {"brand", "model", "powertrain"}
    missing = required - set(df_vehicles.columns)
    if missing:
        raise ValueError(
            f"attach_hsn_tsn: df_vehicles is missing required columns "
            f"{sorted(missing)}"
        )
    if lookup is None:
        if data_path is None:
            raise ValueError("attach_hsn_tsn: provide either data_path or lookup")
        lookup = HsnTsnLookup.from_data_path(data_path)

    # Task 3: read the "segment" column when present; None otherwise so the
    # segment tier is skipped (OFF-safe for callers without segment data).
    has_segment = "segment" in df_vehicles.columns
    segment_values: list[Optional[str]] = (
        df_vehicles["segment"].tolist() if has_segment else [None] * len(df_vehicles)
    )

    rng = np.random.default_rng(random_seed)

    n = len(df_vehicles)
    power_kw = [0.0] * n
    power_ps = [0.0] * n
    displacement = [0.0] * n
    fuel_detail = [""] * n
    hsn = [""] * n
    tsn = [""] * n
    tier = [""] * n

    # Cache is ONLY for exact/model tiers (deterministic medians). Pooled tiers
    # (brand/global/segment) are drawn per-vehicle from the variant pool and must
    # NOT be cached (each draw must use the rng so different vehicles get different
    # engines).
    cache: dict[tuple[str, str, str, str], tuple[EngineRecord, str]] = {}
    records = df_vehicles[["brand", "model", "powertrain"]].to_dict(orient="records")
    for i, row in enumerate(records):
        brand = row["brand"]
        model = row["model"]
        powertrain = row["powertrain"]
        seg = segment_values[i]
        seg_str = None if pd.isna(seg) else str(seg)
        family = model_family(canonical_brand(brand) or "", model)
        # Include segment in cache key so different segments don't share a cache entry.
        key = (str(brand), str(family), str(powertrain), str(seg_str))

        cached = cache.get(key)
        if cached is not None:
            rec, vehicle_tier = cached
            # Pooled tiers are never cached — they must always draw per-vehicle.
            # If somehow a pooled tier ends up cached (shouldn't happen), draw anyway.
            if vehicle_tier in _POOLED_TIERS:
                cached = None

        if cached is None:
            rec_raw, vehicle_tier = lookup.lookup(brand, family, powertrain,
                                                  segment=seg_str)
            if vehicle_tier in _POOLED_TIERS:
                # Pool draw: per-vehicle random engine from the matched variant
                # distribution. Never cached so every vehicle gets an independent draw.
                if vehicle_tier == "segment" and seg_str is not None:
                    pool = lookup.get_segment_pool(seg_str, powertrain)
                else:
                    pool = lookup.get_pool(brand, powertrain)
                if pool is not None and len(pool.power_kw) > 0:
                    rec = _draw_record(pool, rng)
                else:
                    rec = rec_raw
                # Do NOT add to cache — the next vehicle at this spec must draw again.
            else:
                # exact/model tiers are deterministic medians; cache them.
                rec = rec_raw
                cache[key] = (rec, vehicle_tier)
        else:
            rec, vehicle_tier = cached

        power_kw[i] = rec.power_kw
        power_ps[i] = rec.power_ps
        displacement[i] = rec.displacement_ccm
        # fuel_detail is ALWAYS derived from the vehicle's own powertrain (Bug 1
        # fix: a diesel car must never carry "Benzin" from a petrol-dominant median).
        fuel_detail[i] = (POWERTRAIN_TO_FUEL_GROUP.get(str(powertrain).lower())
                          or rec.fuel_detail)
        hsn[i] = rec.hsn
        tsn[i] = rec.tsn
        tier[i] = vehicle_tier

    out = df_vehicles.copy()
    out["engine_power_kw"] = power_kw
    out["engine_power_ps"] = power_ps
    out["displacement_ccm"] = displacement
    out["fuel_detail"] = fuel_detail
    out["hsn"] = hsn
    out["tsn"] = tsn
    out["hsn_tsn_match_tier"] = tier

    # Log the per-vehicle tier rates from the attached column (authoritative;
    # independent of the lookup's own internal counter, which the spec-cache
    # short-circuits on repeats).
    _log_tier_rates_from_column(tier, lookup._unmapped_brands)

    if not keep_tier:
        out = out.drop(columns="hsn_tsn_match_tier")
    return out


def _log_tier_rates_from_column(tier: list[str], unmapped: Counter) -> None:
    """Log per-vehicle tier rates computed from the attached tier column."""
    total = len(tier)
    if total == 0:
        logger.info("[hsn_tsn] no vehicles matched (empty fleet).")
        return
    counts = Counter(tier)
    exact = counts.get("exact", 0)
    model = counts.get("model", 0)
    brand = counts.get("brand", 0)
    segment = counts.get("segment", 0)
    glob = counts.get("global", 0)
    exact_model_rate = (exact + model) / total
    emit = logger.warning if exact_model_rate < 0.5 else logger.info
    emit(
        "[hsn_tsn] match tier rates over %d vehicles: exact %d (%.1f%%), "
        "model %d (%.1f%%), brand %d (%.1f%%), segment %d (%.1f%%), "
        "global %d (%.1f%%); exact+model %.1f%%",
        total, exact, 100.0 * exact / total, model, 100.0 * model / total,
        brand, 100.0 * brand / total, segment, 100.0 * segment / total,
        glob, 100.0 * glob / total,
        100.0 * exact_model_rate,
    )
    if unmapped:
        top = ", ".join(f"{b}={c}" for b, c in unmapped.most_common(10))
        logger.warning(
            "[hsn_tsn] %d unmapped fleet brand(s) fell back to global median "
            "(top: %s); add them to FLEET_BRAND_TO_HSN_TSN if a HSN/TSN "
            "counterpart exists.", len(unmapped), top,
        )
