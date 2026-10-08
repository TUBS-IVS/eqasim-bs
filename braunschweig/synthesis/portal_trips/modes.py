"""The fixed mode of a portal stay, checked against the synthetic person (eqasim-bs#442).

The mode is the donor's OUTBOUND mode and applies to both portal legs (same mode out and back,
so no vehicle is left at a gate and the DMC's VehicleContinuity holds; a differing donor return
mode is counted). Availability mirrors org.eqasim.braunschweig.mode_choice.BraunschweigModeAvailability:
car needs carAvailability != none AND a driving licence, bicycle needs bicycleAvailability != none,
car_passenger needs carPassengerAvailability in {some, all} -- or, when the population carries no
such column, car availability (the Java fallback). Substitution order on failure: car ->
car_passenger -> pt; car_passenger -> pt; bicycle -> pt. pt and walk are always available; a mode
outside MODE_FALLBACKS is not checked and kept as it is.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

MODE_FALLBACKS = {"car": ("car_passenger", "pt"), "car_passenger": ("pt",), "bicycle": ("pt",),
                  "pt": (), "walk": ()}
MODE_COLUMNS = ["mode", "outbound_mode", "return_mode", "substituted_from", "substitution_reason",
                "return_mode_differs"]
AVAILABLE_PASSENGER_VALUES = ("some", "all")
FINAL_FALLBACK_MODE = "pt"
REASON_NO_CAR = "no_car_availability"
REASON_NO_LICENCE = "no_driving_licence"
REASON_NO_BICYCLE = "no_bicycle_availability"
REASON_NO_PASSENGER = "no_car_passenger_availability"


def mode_availability(persons: pd.DataFrame) -> pd.DataFrame:
    """Per person (index ``person_id``): bool can_car, can_bicycle, can_car_passenger, has_car.

    ``has_car`` is the raw car availability (without the licence) so that a failed car check can be
    attributed to ``no_car_availability`` or ``no_driving_licence``.
    """
    required = ["person_id", "car_availability", "has_license", "bicycle_availability"]
    missing = [column for column in required if column not in persons.columns]
    if missing:
        raise ValueError(f"[portal_trips] persons frame lacks the availability columns {missing}")
    has_car = persons["car_availability"].astype(str).ne("none")
    can_car = has_car & persons["has_license"].fillna(False).astype(bool)
    can_bicycle = persons["bicycle_availability"].astype(str).ne("none")
    if "car_passenger_availability" in persons.columns:
        can_passenger = persons["car_passenger_availability"].astype(str).isin(AVAILABLE_PASSENGER_VALUES)
    else:
        can_passenger = has_car
    return pd.DataFrame({"can_car": can_car.to_numpy(), "can_bicycle": can_bicycle.to_numpy(),
                         "can_car_passenger": can_passenger.to_numpy(), "has_car": has_car.to_numpy()},
                        index=pd.Index(persons["person_id"].to_numpy(), name="person_id"))


def portal_modes(stays: pd.DataFrame, trips: pd.DataFrame, availability: pd.DataFrame) -> pd.DataFrame:
    """One row per stay, in the order of ``stays``, with the fixed mode and the substitution bookkeeping.

    ``substituted_from`` / ``substitution_reason`` are None where the donor's outbound mode is kept;
    ``return_mode`` is None for stays without a return leg; ``return_mode_differs`` is True only when
    a return leg exists and its donor mode differs from the outbound mode. Every stay person must
    have an availability row and every outbound trip a mode (ValueError otherwise).
    """
    mode_lookup = trips.set_index(["person_id", "trip_index"])["mode"]
    persons = stays["person_id"].to_numpy()
    outbound = mode_lookup.reindex(
        pd.MultiIndex.from_arrays([persons, stays["outbound_trip_index"].to_numpy()])).to_numpy(dtype=object)
    if pd.isna(outbound).any():
        raise ValueError(f"[portal_trips] {int(pd.isna(outbound).sum())} stays name an outbound trip without a "
                         "mode in the trips table; the stay table and the trips table are inconsistent")
    unknown_persons = sorted(set(persons) - set(availability.index))
    if unknown_persons:
        raise ValueError(f"[portal_trips] {len(unknown_persons)} stay persons have no availability row "
                         f"(first: {unknown_persons[:5]}); mode availability cannot be checked for them")
    has_return = stays["return_trip_index"].notna().to_numpy()
    return_index = stays["return_trip_index"].fillna(-1).astype(int).to_numpy()
    returning = mode_lookup.reindex(
        pd.MultiIndex.from_arrays([persons, return_index])).to_numpy(dtype=object)
    returning = np.where(has_return, returning, None)
    avail = availability.reindex(persons)
    can_by_mode = {"car": avail["can_car"].to_numpy(dtype=bool),
                   "bicycle": avail["can_bicycle"].to_numpy(dtype=bool),
                   "car_passenger": avail["can_car_passenger"].to_numpy(dtype=bool)}
    car_reason = np.where(avail["has_car"].to_numpy(dtype=bool), REASON_NO_LICENCE, REASON_NO_CAR)
    reason_by_mode = {"car": car_reason, "bicycle": REASON_NO_BICYCLE, "car_passenger": REASON_NO_PASSENGER}
    # pt, walk and modes outside MODE_FALLBACKS are always allowed (see the module docstring).
    substituted = np.zeros(len(stays), dtype=bool)
    reason = np.full(len(stays), None, dtype=object)
    for mode, can in can_by_mode.items():
        denied = (outbound == mode) & ~can
        substituted |= denied
        reason = np.where(denied, reason_by_mode[mode], reason)
    # Fixed-order substitution: the first fallback of the outbound mode that the person may use.
    chosen = outbound.copy()
    resolved = np.zeros(len(stays), dtype=bool)
    for origin, candidates in MODE_FALLBACKS.items():
        for candidate in candidates:
            take = substituted & ~resolved & (outbound == origin) & can_by_mode.get(candidate, True)
            chosen = np.where(take, candidate, chosen)
            resolved |= take
    chosen = np.where(substituted & ~resolved, FINAL_FALLBACK_MODE, chosen)
    return pd.DataFrame({
        "mode": chosen, "outbound_mode": outbound, "return_mode": returning,
        "substituted_from": np.where(substituted, outbound, None),
        "substitution_reason": np.where(substituted, reason, None),
        "return_mode_differs": has_return & (returning != outbound),
    }, columns=MODE_COLUMNS)
