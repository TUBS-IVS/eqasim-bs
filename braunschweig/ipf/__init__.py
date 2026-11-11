"""Iterative Proportional Fitting (IPF) for the Braunschweig synthetic population.

The modules ``model``, ``prepare`` and ``attributed`` were moved here from
``bavaria/ipf/``. Each carries a docstring header in the form::

    Origin: eqasim-bavaria @ b20fbe6, file ``bavaria/ipf/<name>.py``.
    Adapted for Braunschweig:
      - <bullet list of substantive deviations>

The deviations are listed in each module's own header (for example the
post-IPF margin-deviation check, BUG-009, in ``braunschweig.ipf.model``).
"""

from __future__ import annotations

__all__: list[str] = []
