"""Plausible modes for the tours the discrete mode choice never re-chooses (eqasim-bs#453, ADR-0145).

The production mode choice runs ``ModelType.Tour`` with the ``OutsideFilter``: every tour that touches an
``outside`` activity keeps its diary mode for the whole run. After the location choice this package judges
the diary mode of such tours against the regional survey (``srv2023_mode_by_distance_band.csv``) at the
realised straight-line distance and draws a substitute where the survey practically never uses the mode.
Modules: ``config_keys`` (the one home of the key names and defaults), ``srv_table`` (the cell lookup),
``tours`` (home-based tours, leg geometry, the outside classification), ``rule`` (plausibility and the
seeded draw), ``stage`` (the synpp stage behind the alias ``synthesis.population.trips.simulated``).
Maintenance rules: docs/codebase/notes/outside-tour-modes.md.
"""
