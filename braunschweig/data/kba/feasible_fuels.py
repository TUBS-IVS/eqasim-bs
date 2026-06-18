"""Model-feasible powertrain sets derived from the HSN/TSN lookup (Bug 2).

Bug 2 (the fleet-consistency project): the per-vehicle chain draws a *powertrain*
purely from ``P(powertrain | segment, kreis)`` and only *afterwards* a brand +
model. Nothing then stops an exotic, combustion-only marque (Lamborghini) from
being assigned a *diesel*, or a pure-electric marque (Tesla) from being assigned
a *petrol* engine. :class:`FeasibleFuels` closes that gap: for a given
``(canonical brand, model family)`` it returns the SET of powertrains that
marque+family can plausibly carry, so the sampler can MASK the powertrain pmf to
the feasible set *after* drawing the model (which is why the sampler reorders the
model draw to BEFORE the powertrain draw in the ``consistency_v2`` path).

Data source -- HSN/TSN ONLY
---------------------------
Feasibility is derived **strictly** from the fuels that actually appear for that
``(canonical brand, family)`` in the HSN/TSN lookup (``hsn_tsn_lookup.csv``, 62
brands; built by :mod:`braunschweig.data.kba.hsn_tsn`). A prior investigation
established that the brand-level ``kba_brand_powertrain.csv`` cannot separate
petrol from diesel (it only reports the total plus the bev/phev/hybrid/gas
alternatives), so it can never EXCLUDE a combustion fuel and therefore cannot fix
Bug 2; that path is intentionally dropped. The HSN/TSN lookup, by contrast,
honestly constrains exotics (Lamborghini -> only "Benzin"; Tesla / Polestar ->
only "Elektro"; Porsche -> Benzin / Diesel / Elektro), so masking on it is
data-honest.

Unknown -> ``None`` (no masking)
--------------------------------
When the ``(canonical brand, family)`` is unknown -- the brand has no HSN/TSN
counterpart, or the family has no rows for that brand -- there is no evidence to
constrain the model, so :meth:`model_feasible_powertrains` returns ``None`` and
the caller keeps the *unmasked* segment pmf (logged). It never invents a
constraint from absence of data.

Fuel-string -> powertrain mapping
---------------------------------
The HSN/TSN ``fuel`` column uses German fuel-group strings. Each maps to exactly
one canonical powertrain (:data:`FUEL_STRING_TO_POWERTRAIN`). The canonical
single-fuel strings reuse the inverse of
:data:`braunschweig.data.kba.hsn_tsn.POWERTRAIN_TO_FUEL_GROUP`; the remaining
real-world spellings observed in the file (``"Diesel/Elektro"`` diesel-hybrid,
``"Benzin/Erdgas"`` CNG bivalent, ``"Benzin/Alkohol"`` flex-fuel, ``"Zweitakt"``
/ ``"Wankel"`` petrol combustion, the Plug-in variants) are classified
explicitly so feasibility neither over- nor under-constrains.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

import pandas as pd

from braunschweig.data.kba.hsn_tsn import (
    HSN_TSN_RELATIVE_PATH,
    POWERTRAIN_TO_FUEL_GROUP,
    canonical_brand,
    model_family,
)

logger = logging.getLogger(__name__)


def _invert_powertrain_to_fuel_group() -> dict[str, str]:
    """Inverse of POWERTRAIN_TO_FUEL_GROUP (fuel string -> powertrain).

    One fuel group maps to one powertrain, so the inversion is well defined for
    the canonical strings. ``None`` fuel groups (``other``) are skipped.
    """
    inv: dict[str, str] = {}
    for powertrain, fuel in POWERTRAIN_TO_FUEL_GROUP.items():
        if fuel is None:
            continue
        inv[fuel] = powertrain
    return inv


#: HSN/TSN ``fuel`` string -> canonical powertrain label. The canonical strings
#: come from inverting POWERTRAIN_TO_FUEL_GROUP; the remaining real spellings
#: observed in hsn_tsn_lookup.csv are classified explicitly. A fuel string absent
#: from this map is treated as feasibility-neutral (ignored; see
#: :meth:`FeasibleFuels.model_feasible_powertrains`).
FUEL_STRING_TO_POWERTRAIN: dict[str, str] = {
    **_invert_powertrain_to_fuel_group(),
    # --- explicit real-world spellings observed in the 62-brand lookup ---
    "Diesel/Elektro": "hybrid",            # diesel full/mild hybrid
    "Diesel/Elektro Plug-in": "phev",      # diesel plug-in hybrid
    "Benzin/Erdgas": "gas",                # CNG bivalent
    "Benzin/Alkohol": "petrol",            # E85 flex-fuel (combustion petrol)
    "Zweitakt": "petrol",                  # two-stroke petrol combustion
    "Wankel": "petrol",                    # rotary petrol combustion
}


class FeasibleFuels:
    """Feasible powertrain sets per ``(canonical brand, model family)``.

    Built from the HSN/TSN lookup CSV. :meth:`model_feasible_powertrains`
    returns the set of canonical powertrains a model can plausibly carry, or
    ``None`` when the model is unknown (no HSN/TSN rows for that brand+family).
    """

    def __init__(self, family_powertrains: dict[tuple[str, str], frozenset[str]]):
        #: (canonical_brand, family) -> frozenset of feasible powertrain labels.
        self._family_powertrains = family_powertrains

    # -- construction --------------------------------------------------------
    @classmethod
    def from_data_path(cls, data_path: str) -> "FeasibleFuels":
        """Build from ``<data_path>/braunschweig/kba/hsn_tsn_lookup.csv``."""
        path = os.path.join(data_path, HSN_TSN_RELATIVE_PATH)
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"HSN/TSN lookup not found: {path} (run scripts/scrape_hsn_tsn.py "
                f"to (re)generate it; the CSV is local-only / gitignored)."
            )
        return cls.from_frame(pd.read_csv(path))

    @classmethod
    def from_frame(cls, df: pd.DataFrame) -> "FeasibleFuels":
        """Build from an in-memory HSN/TSN variant frame (used by tests).

        The frame must carry the HSN/TSN ``brand``, ``model`` and ``fuel``
        columns. The ``brand`` column is the HSN/TSN display brand (already the
        canonical brand); the family is derived with
        :func:`braunschweig.data.kba.hsn_tsn.model_family`.
        """
        required = {"brand", "model", "fuel"}
        missing = required - set(df.columns)
        if missing:
            raise RuntimeError(
                f"feasible_fuels: HSN/TSN frame missing columns {sorted(missing)}"
            )

        family_powertrains: dict[tuple[str, str], set[str]] = {}
        unmapped_fuels: set[str] = set()
        for brand, model, fuel in zip(df["brand"], df["model"], df["fuel"]):
            brand_s = str(brand)
            family = model_family(brand_s, str(model))
            if not family:
                continue
            powertrain = FUEL_STRING_TO_POWERTRAIN.get(str(fuel))
            if powertrain is None:
                unmapped_fuels.add(str(fuel))
                continue
            family_powertrains.setdefault((brand_s, family), set()).add(powertrain)

        if unmapped_fuels:
            logger.warning(
                "[feasible_fuels] %d HSN/TSN fuel string(s) not in "
                "FUEL_STRING_TO_POWERTRAIN (inverse of POWERTRAIN_TO_FUEL_GROUP) "
                "and ignored for feasibility — each unmapped string NARROWS the "
                "feasible powertrain set for affected models; extend the map if "
                "a re-scrape introduced new fuel spellings: %s",
                len(unmapped_fuels), ", ".join(sorted(unmapped_fuels)),
            )

        frozen = {k: frozenset(v) for k, v in family_powertrains.items()}
        logger.info(
            "[feasible_fuels] built feasibility for %d (brand, family) keys "
            "from HSN/TSN lookup.", len(frozen),
        )
        return cls(frozen)

    # -- query ---------------------------------------------------------------
    def model_feasible_powertrains(
        self, brand: str, family: str
    ) -> Optional[set[str]]:
        """Feasible powertrain set for a fleet ``(brand, family)``, or ``None``.

        ``brand`` is the fleet brand token (the first token of the KBA
        Modellreihe, e.g. ``"TESLA"``, ``"PORSCHE"``); it is canonicalised to the
        HSN/TSN display brand via
        :func:`braunschweig.data.kba.hsn_tsn.canonical_brand`. ``family`` is the
        normalised model family token (as produced by
        :func:`braunschweig.data.kba.hsn_tsn.model_family`).

        Returns the SET of canonical powertrains that ``(brand, family)`` can
        carry per the HSN/TSN lookup, or ``None`` when the brand has no HSN/TSN
        counterpart or the family has no rows for that brand (unknown -> caller
        keeps the unmasked pmf).
        """
        cb = canonical_brand(brand)
        if cb is None or not family:
            return None
        feasible = self._family_powertrains.get((cb, str(family)))
        if feasible is None:
            return None
        return set(feasible)
