"""HBEFA vehicle-type mapping for the German fleet generative chain.

This module turns a per-vehicle specification produced by
:mod:`braunschweig.synthesis.vehicles.fleet_sampling_de` -- a ``(powertrain,
euro_class, segment)`` triple -- into a canonical MATSim/HBEFA
:class:`VehicleType`. The four HBEFA engine attributes consumed by the eqasim
MATSim vehicle writer (``braunschweig.matsim.scenario.vehicles`` /
``matsim.scenario.vehicles``) are

  * ``HbefaVehicleCategory``  -- always ``PASSENGER_CAR`` here (Pkw);
  * ``HbefaTechnology``       -- derived from the powertrain;
  * ``HbefaSizeClass``        -- derived from the segment via a configurable map;
  * ``HbefaEmissionsConcept`` -- derived from the powertrain + Euro class.

Naming convention
-----------------
The strings follow the HBEFA 4.x German passenger-car naming convention as used
by the VSP MATSim scenarios (matsim-berlin / matsim-vsp) and the
eqasim-germany configuration, so the produced types resolve against a standard
HBEFA emissions file:

  * combustion technologies are ``petrol (4S)``, ``diesel`` and the gaseous
    ``bifuel CNG/petrol`` (CNG/LPG mapped to the bifuel concept);
  * electrified technologies are ``electricity`` (BEV),
    ``plug-in hybrid petrol/electricity`` (PHEV), ``petrol (hybrid)`` (full /
    mild hybrid) and ``Fuel Cell`` (hydrogen);
  * the emission concept for a combustion technology is
    ``PC <fuel> Euro-<n>`` (e.g. ``PC petrol Euro-6``); BEV, hybrid, PHEV and
    fuel-cell vehicles carry a technology-specific concept that does not encode
    a combustion Euro stage (``PC BEV``, ``PC P-Hybrid``, ``PC PHEV petrol``,
    ``PC Fuel Cell``), because HBEFA does not tabulate combustion Euro stages
    for a pure-electric drivetrain;
  * a Euro-6 combustion vehicle may carry one of the three Euro-6 substages
    (Task B5, conditionally drawn -- see
    :class:`braunschweig.synthesis.vehicles.fleet_sampling_de.Euro6SubstageModel`),
    which map to their own concept strings ``PC <fuel> Euro-6ab`` /
    ``PC <fuel> Euro-6d-temp`` / ``PC <fuel> Euro-6d``.

The size class is one of ``small`` / ``medium`` / ``large`` (the HBEFA size
classes that exist for every technology, including the electrified ones). The
segment -> size mapping is configurable (see :data:`DEFAULT_SEGMENT_SIZE_MAP`)
because the body-style-to-size assignment is a modelling choice rather than a
KBA observation.

All randomness lives upstream in the generative chain; this module is a pure,
deterministic lookup.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Mapping, Optional

logger = logging.getLogger(__name__)

#: HBEFA vehicle category for every fleet vehicle synthesised here (Pkw).
HBEFA_VEHICLE_CATEGORY = "PASSENGER_CAR"

#: Allowed HBEFA size classes (technology-independent, valid for BEV too).
HBEFA_SIZE_CLASSES: tuple[str, ...] = ("small", "medium", "large")

#: Powertrain -> HBEFA technology string (German HBEFA 4.x naming).
POWERTRAIN_TO_TECHNOLOGY: dict[str, str] = {
    "petrol": "petrol (4S)",
    "diesel": "diesel",
    "gas": "bifuel CNG/petrol",
    "bev": "electricity",
    "phev": "plug-in hybrid petrol/electricity",
    "hybrid": "petrol (hybrid)",
    "hydrogen": "Fuel Cell",
    # ``other`` (unclassifiable residual) is mapped to the petrol combustion
    # path so it still receives a valid Euro-stage emission concept; this is the
    # most conservative choice for an unknown drivetrain.
    "other": "petrol (4S)",
}

#: Powertrains that carry a combustion Euro stage in their emission concept.
#: Electrified / fuel-cell drivetrains do NOT (HBEFA has no combustion Euro
#: stage for them), so their euro_class is collapsed to a fixed concept below.
COMBUSTION_POWERTRAINS: frozenset[str] = frozenset({"petrol", "diesel", "gas", "other"})

#: Pure-electric drivetrains: BEV and hydrogen/fuel-cell.  These have NO
#: combustion engine, so no combustion Euro stage applies.  PHEV and full hybrid
#: DO have a combustion engine and therefore keep their drawn combustion Euro.
ELECTRIC_EURO_POWERTRAINS: frozenset[str] = frozenset({"bev", "hydrogen"})

#: Real euro_class category stored on pure-electric (BEV / hydrogen) vehicles.
#: This is a genuine category label -- not a missing-data sentinel -- that
#: signals "pure-electric drivetrain; no combustion Euro stage" while still
#: being a fully imputed, non-null value.  HBEFA's ``emission_concept_for``
#: already ignores euro for these powertrains, so the stored ``type_id`` /
#: emission concept is unaffected -- this is provenance / semantic clarity only.
ELECTRIC_EURO: str = "electric"

#: Fixed (Euro-independent) emission concepts for the electrified / fuel-cell
#: powertrains. These are the canonical VSP HBEFA passenger-car concepts.
NON_COMBUSTION_EMISSION_CONCEPT: dict[str, str] = {
    "bev": "PC BEV",
    "phev": "PC PHEV petrol",
    "hybrid": "PC P-Hybrid",
    "hydrogen": "PC Fuel Cell",
}

#: Fuel token used inside the ``PC <fuel> Euro-<n>`` combustion concept.
_COMBUSTION_CONCEPT_FUEL: dict[str, str] = {
    "petrol": "petrol",
    "diesel": "diesel",
    "gas": "CNG",
    "other": "petrol",
}

#: Allowed Euro-class labels (must match
#: :data:`braunschweig.data.kba.fleet_tables.EURO_CLASS_LABELS`, plus the three
#: Task B5 Euro-6 substages). ``other`` is the KBA Sonstige residual; for a
#: combustion vehicle it is treated as the oldest pre-Euro-1 stage in the HBEFA
#: concept (``Euro-0``) so it still produces a valid, conservative concept
#: string.
#:
#: Task B5: ``euro6ab`` / ``euro6dtemp`` / ``euro6d`` are the conditional
#: Euro-6 substage refinement drawn by
#: :class:`braunschweig.synthesis.vehicles.fleet_sampling_de.Euro6SubstageModel`
#: (per-Kreis diesel/non-diesel composition from Regionalstatistik 46251-03,
#: falling back to the national FZ 27.4 substage split, falling back to plain
#: ``euro6`` when no substage data is available). The headline ``euro6``
#: concept stays mapped for that final fallback and for any legacy caller that
#: still passes the plain label.
EURO_TO_HBEFA_STAGE: dict[str, str] = {
    "euro1": "Euro-1",
    "euro2": "Euro-2",
    "euro3": "Euro-3",
    "euro4": "Euro-4",
    "euro5": "Euro-5",
    "euro6": "Euro-6",
    "euro6ab": "Euro-6ab",
    "euro6dtemp": "Euro-6d-temp",
    "euro6d": "Euro-6d",
    "other": "Euro-0",
}

#: Default segment -> HBEFA size class map. The KBA segments (snake_case, see
#: :data:`braunschweig.data.kba.fleet_tables.SEGMENT_LABELS`) are grouped by the
#: HBEFA passenger-car size convention: the smallest body styles are ``small``,
#: the volume mid segments ``medium``, the premium / large / off-road styles
#: ``large``. ``utilities`` and the residual ``sonstige`` default to ``medium``
#: (the most common Pkw size) -- callers may override via the config map.
DEFAULT_SEGMENT_SIZE_MAP: dict[str, str] = {
    "minis": "small",
    "kleinwagen": "small",
    "kompaktklasse": "medium",
    "mittelklasse": "medium",
    "mini_vans": "medium",
    "grossraum_vans": "large",
    "obere_mittelklasse": "large",
    "oberklasse": "large",
    "suv": "large",
    "gelaendewagen": "large",
    "sportwagen": "large",
    "utilities": "medium",
    "wohnmobile": "large",
    "sonstige": "medium",
}

#: Size class used when a segment is absent from the size map (logged fallback).
DEFAULT_SIZE_FALLBACK = "medium"


@dataclass(frozen=True)
class VehicleType:
    """A canonical HBEFA passenger-car vehicle type.

    ``type_id`` is a stable, collision-free key derived from the four HBEFA
    attributes, so two vehicles with the same ``(technology, size, emission)``
    share exactly one registered :class:`VehicleType`. The combination of the
    four HBEFA attributes is what the emission model consumes; the ``type_id``
    is only an identifier.
    """

    type_id: str
    hbefa_category: str
    hbefa_technology: str
    hbefa_size: str
    hbefa_emission: str

    def as_record(self) -> dict[str, object]:
        """Return the type as a record matching the eqasim vehicle-type schema.

        The numeric geometry fields (``length`` / ``width``) reuse the legacy
        eqasim passenger-car defaults so the MATSim writer (which reads
        ``length`` / ``width`` / the four ``hbefa_*`` columns) needs no change.
        """
        return {
            "type_id": self.type_id,
            "length": 7.5,
            "width": 1.0,
            "mode": "car",
            "hbefa_cat": self.hbefa_category,
            "hbefa_tech": self.hbefa_technology,
            "hbefa_size": self.hbefa_size,
            "hbefa_emission": self.hbefa_emission,
        }


def _slug(value: str) -> str:
    """Make a string safe for use inside a ``type_id`` (ASCII, no spaces)."""
    out = []
    for ch in value:
        if ch.isalnum():
            out.append(ch)
        elif ch in " -/(),":
            out.append("_")
        # drop anything else
    slug = "".join(out)
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug.strip("_").lower()


def technology_for_powertrain(powertrain: str) -> str:
    """Return the HBEFA technology string for a canonical powertrain label."""
    if powertrain not in POWERTRAIN_TO_TECHNOLOGY:
        raise ValueError(
            f"unknown powertrain '{powertrain}' "
            f"(expected one of {sorted(POWERTRAIN_TO_TECHNOLOGY)})"
        )
    return POWERTRAIN_TO_TECHNOLOGY[powertrain]


def emission_concept_for(powertrain: str, euro_class: str) -> str:
    """Return the HBEFA emission concept for a ``(powertrain, euro_class)``.

    For combustion powertrains the concept encodes the Euro stage
    (``PC <fuel> Euro-<n>``); electrified / fuel-cell powertrains carry a fixed,
    Euro-independent concept (HBEFA has no combustion Euro stage for them), so
    the ``euro_class`` argument is ignored for those.
    """
    if powertrain in COMBUSTION_POWERTRAINS:
        if euro_class not in EURO_TO_HBEFA_STAGE:
            raise ValueError(
                f"unknown euro_class '{euro_class}' for combustion powertrain "
                f"'{powertrain}' (expected one of {sorted(EURO_TO_HBEFA_STAGE)})"
            )
        fuel = _COMBUSTION_CONCEPT_FUEL[powertrain]
        stage = EURO_TO_HBEFA_STAGE[euro_class]
        return f"PC {fuel} {stage}"
    if powertrain in NON_COMBUSTION_EMISSION_CONCEPT:
        return NON_COMBUSTION_EMISSION_CONCEPT[powertrain]
    raise ValueError(
        f"unknown powertrain '{powertrain}' "
        f"(expected one of {sorted(POWERTRAIN_TO_TECHNOLOGY)})"
    )


def size_class_for_segment(
    segment: str,
    size_map: Optional[Mapping[str, str]] = None,
    fallback_counter: Optional[dict[str, int]] = None,
) -> str:
    """Return the HBEFA size class for a KBA segment via ``size_map``.

    A segment missing from the map falls back to :data:`DEFAULT_SIZE_FALLBACK`;
    the fallback is logged (and counted in ``fallback_counter`` if given) per the
    project no-silent-fallback rule.
    """
    effective = dict(DEFAULT_SEGMENT_SIZE_MAP)
    if size_map is not None:
        effective.update(size_map)
    size = effective.get(segment)
    if size is None:
        if fallback_counter is not None:
            fallback_counter[segment] = fallback_counter.get(segment, 0) + 1
        logger.warning(
            "[hbefa] segment '%s' missing from size map -> fallback size '%s'",
            segment, DEFAULT_SIZE_FALLBACK,
        )
        size = DEFAULT_SIZE_FALLBACK
    if size not in HBEFA_SIZE_CLASSES:
        raise ValueError(
            f"size map yields invalid HBEFA size '{size}' for segment "
            f"'{segment}' (allowed {HBEFA_SIZE_CLASSES})"
        )
    return size


def vehicle_type_for(
    powertrain: str,
    euro_class: str,
    segment: str,
    size_map: Optional[Mapping[str, str]] = None,
    fallback_counter: Optional[dict[str, int]] = None,
) -> VehicleType:
    """Map a ``(powertrain, euro_class, segment)`` spec to a :class:`VehicleType`.

    The returned type's ``type_id`` is canonical: it depends only on the three
    HBEFA attributes, so equal specs collapse to one registered type.
    """
    technology = technology_for_powertrain(powertrain)
    emission = emission_concept_for(powertrain, euro_class)
    size = size_class_for_segment(segment, size_map, fallback_counter)
    type_id = "car_%s_%s_%s" % (_slug(technology), size, _slug(emission))
    return VehicleType(
        type_id=type_id,
        hbefa_category=HBEFA_VEHICLE_CATEGORY,
        hbefa_technology=technology,
        hbefa_size=size,
        hbefa_emission=emission,
    )


def is_valid_vehicle_type(vehicle_type: VehicleType) -> bool:
    """Return ``True`` iff every HBEFA attribute of ``vehicle_type`` is allowed.

    Used by tests and validation: the category is ``PASSENGER_CAR``, the
    technology is one of the mapped technologies, the size is one of
    :data:`HBEFA_SIZE_CLASSES`, and the emission concept is either a valid
    combustion ``PC <fuel> Euro-<n>`` string or one of the fixed electrified
    concepts.
    """
    if vehicle_type.hbefa_category != HBEFA_VEHICLE_CATEGORY:
        return False
    if vehicle_type.hbefa_technology not in set(POWERTRAIN_TO_TECHNOLOGY.values()):
        return False
    if vehicle_type.hbefa_size not in HBEFA_SIZE_CLASSES:
        return False
    valid_emissions = set(NON_COMBUSTION_EMISSION_CONCEPT.values())
    for fuel in set(_COMBUSTION_CONCEPT_FUEL.values()):
        for stage in EURO_TO_HBEFA_STAGE.values():
            valid_emissions.add(f"PC {fuel} {stage}")
    return vehicle_type.hbefa_emission in valid_emissions
