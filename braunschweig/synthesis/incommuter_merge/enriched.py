"""Wrapper: resident enriched persons + injected in-commuters (aliases
synthesis.population.enriched). households derives from this stage, so injected
in-commuters flow into households automatically."""
from braunschweig.synthesis.incommuter_merge._base import make_wrapper

configure, execute = make_wrapper(
    "braunschweig.synthesis.population.enriched", "persons", "person_id")
