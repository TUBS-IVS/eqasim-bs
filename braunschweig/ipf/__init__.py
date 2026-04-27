"""Iterative Proportional Fitting (IPF) for the Braunschweig synthetic population.

Phase 2.6 of the refactor moves ``bavaria/ipf/{model,prepare,attributed}.py``
here.  Until then the BS pipeline references ``braunschweig.ipf.*`` via aliases in
:file:`config_local_braunschweig.yml`.

After the move every module in this package will carry a docstring header in
the form::

    Origin: eqasim-bavaria @ b20fbe6, file ``bavaria/ipf/<name>.py``.
    Adapted for Braunschweig:
      - <bullet list of substantive deviations>

Per Decision D-5 the move is behaviour-preserving; bug fixes (BUG-008,
BUG-009, BUG-010, BUG-011) are deferred to a follow-up branch.
"""

from __future__ import annotations

__all__: list[str] = []
