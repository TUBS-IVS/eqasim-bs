"""RegioStaR-2 stratum-key helpers for donor stratification (Phase 4A).

Provides a shared mapping from the native urbanity indicator on EACH data source
to the common binary stratum label ``{"urban", "rural"}`` used in Phase 4B donor
stratification:

- **Cells / MiD seed**: RS7 code (71-77) from the prepared-cell parquet
  (``RegioStaR7`` column) -> ``cell_urban_class_from_rs7``.
- **ENTD donor**: ``urban_type`` string (UU2010 categories) from the ENTD
  household frame -> ``entd_urban_class``.

Both functions delegate to the authoritative helpers that already exist in the
codebase (``braunschweig.data.bbsr.regiostar.regiostar2_label`` and
``synthesis.population.matched.URBAN_TYPE_TO_URBAN_CLASS``) to avoid duplicating
the mapping logic.

These helpers are PLUMBING only: Phase 4A carries the stratum key as an extra
column on cells, the MiD seed households, and the ENTD donor households.
Phase 4B will use them to stratify donor selection.  No synthesis behaviour
is changed until Phase 4B.

RegioStaR-2 split (BMVI/BBSR):
    Stadtregion (urban)  -- RS7 71, 72, 73, 74
    Laendliche Region (rural) -- RS7 75, 76, 77
"""

from __future__ import annotations

from braunschweig.data.bbsr.regiostar import regiostar2_label
from synthesis.population.matched import URBAN_TYPE_TO_URBAN_CLASS

# The complete set of valid stratum labels.  Phase 4B code that builds per-stratum
# donor pools should iterate over this tuple, not hard-code string literals.
URBAN_CLASSES = ("urban", "rural")


def cell_urban_class_from_rs7(rs7) -> str:
    """Return the RegioStaR-2 urban/rural class for a cell's RegioStaR-7 code.

    Delegates to ``braunschweig.data.bbsr.regiostar.regiostar2_label`` which
    raises ``ValueError`` for codes outside 71-77 (no silent fallback, per
    CLAUDE.md mandatory rule).

    Args:
        rs7: RegioStaR-7 code, integer or integer-castable value.

    Returns:
        ``"urban"`` for codes 71-74 (Stadtregion),
        ``"rural"`` for codes 75-77 (Laendliche Region).

    Raises:
        ValueError: If ``rs7`` is outside the valid range 71-77.
    """
    return regiostar2_label(rs7)


def entd_urban_class(urban_type: str) -> str:
    """Return the RegioStaR-2-comparable urban/rural class for an ENTD urban_type.

    Delegates to ``synthesis.population.matched.URBAN_TYPE_TO_URBAN_CLASS`` which
    maps UU2010 categories (central_city, suburb, isolated_city -> urban;
    none -> rural).  This is a documented approximation: UU2010 and RegioStaR-2
    use different national definitions, but both collapse to the same binary
    urban/rural label (see ``synthesis.population.matched`` module docs).

    Args:
        urban_type: One of the ENTD UU2010 categories:
            ``"central_city"``, ``"suburb"``, ``"isolated_city"`` -> ``"urban"``
            ``"none"`` -> ``"rural"``

    Returns:
        ``"urban"`` or ``"rural"``.

    Raises:
        KeyError: If ``urban_type`` is not in ``URBAN_TYPE_TO_URBAN_CLASS``.
    """
    return URBAN_TYPE_TO_URBAN_CLASS[urban_type]
