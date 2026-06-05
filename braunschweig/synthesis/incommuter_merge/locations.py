"""Wrapper: resident activity locations + injected in-commuter locations (aliases
synthesis.population.spatial.locations)."""
from braunschweig.synthesis.incommuter_merge._base import make_wrapper

configure, execute = make_wrapper(
    "synthesis.population.spatial.locations", "locations",
    ["person_id", "activity_index"])
