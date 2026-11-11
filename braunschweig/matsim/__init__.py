"""MATSim integration for Braunschweig (scenario preparation, simulation hooks).

Phase 2.12 of the refactor moves ``bavaria/matsim/simulation/prepare.py`` here.
The Java classes it drives live in ``org.eqasim.braunschweig.*`` (eqasim-java-bs) and
``org.eqasim.core.*``; only the Python-side configuration of those classes lives in this
package.
"""

from __future__ import annotations

__all__: list[str] = []
