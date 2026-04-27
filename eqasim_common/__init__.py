"""Region-neutral helpers shared across eqasim regional forks.

This package collects code that is **inherited verbatim** (or with only
trivial adaptation) from upstream `eqasim` / `eqasim-bavaria` and is therefore
not Braunschweig-specific.  The intent is that future regional forks
(Hannover, Hamburg, ...) can reuse this package without re-extracting the
same utilities from another fork.

Conventions enforced inside this package
----------------------------------------

* Do **not** put region-specific logic here.  If a function depends on
  ZGB-8 ARS codes, Niedersachsen data formats, or Braunschweig calibration
  constants, it belongs under :mod:`braunschweig`.
* Every module that originates from upstream must declare its provenance in
  the module docstring, e.g.::

      \"\"\"OSM convert wrapper.

      Origin: eqasim-bavaria @ b20fbe6, file ``bavaria/data/osm/osmconvert.py``.
      Inherited unchanged.
      \"\"\"

* Public API stays small; prefer free functions over classes unless state
  is genuinely needed.

This package was introduced in Phase 1 of the ``refactor/braunschweig-clean-fork``
branch (see :doc:`/plan/refactor-eqasim-bs.md`).  It currently exposes no
symbols at the top level; sub-packages are populated incrementally during
Phase 2.
"""

from __future__ import annotations

__all__: list[str] = []
