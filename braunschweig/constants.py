"""Project-wide scientific constants shared across pipeline and analysis code.

Single source of truth for values that several modules must agree on exactly;
re-defining them locally is prohibited (a drifting copy silently breaks the
comparability of synthesis, validation and calibration results).
"""

# Routed-distance <-> straight-line (euclidean) detour factor.
#
# Used in BOTH directions throughout the project: synthesis converts survey
# ROUTED km (MiD wegkm_imp, ENTD V2_MDISTTOT) to straight-line targets by
# dividing, and validation converts synthetic straight-line distances back to
# routed-equivalents by multiplying (W12 / P38.2 trip-length checks, education
# T43 calibration, in-commuter gate timing). 1.3 is the eqasim/ENTD convention
# inherited from the upstream pipeline and is kept as ONE constant so every
# consumer agrees; change it only as a deliberate, project-wide re-baseline.
ROUTED_DETOUR_FACTOR = 1.3
