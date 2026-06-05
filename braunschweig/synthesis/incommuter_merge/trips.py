"""Wrapper: resident trips + injected in-commuter trips (aliases
synthesis.population.trips)."""
from braunschweig.synthesis.incommuter_merge._base import make_wrapper

configure, execute = make_wrapper(
    "synthesis.population.trips", "trips", ["person_id", "trip_index"])
