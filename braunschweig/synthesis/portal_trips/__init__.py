"""Portal trips: long-distance and boundary-crossing trips through the cordon gates (eqasim-bs#442).

The reporting-day trip table is rewritten behind the alias ``synthesis.population.trips.final``:
every leg whose distance exceeds ``braunschweig.portal.max_routable_distance_m`` becomes one
outside stay at a cordon gate, with the donor's mode fixed on the way out and back and the
stay's end time taken from the donor's diary. Stage modules: ``stage`` (the computation, one
dict), ``trips_final`` (the trips frame) and ``anchors`` (the gate anchors the chain solver
treats as fixed activities). Pure helpers: ``classification``, ``gates``, ``timing``, ``modes``,
``rewrite``. Rationale and rejected alternatives: docs/decisions/ADR-0141-portal-trips-through-the-cordon-gates.md;
maintenance rules: docs/codebase/notes/portal-trips.md.
"""
