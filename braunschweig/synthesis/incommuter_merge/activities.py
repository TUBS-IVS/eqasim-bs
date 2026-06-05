"""Wrapper: resident activities + injected in-commuter activities (aliases
synthesis.population.activities)."""
from braunschweig.synthesis.incommuter_merge._base import make_wrapper

configure, execute = make_wrapper(
    "synthesis.population.activities", "activities", ["person_id", "activity_index"])
