"""Region-neutral analysis stages and helpers (formerly top-level ``analysis/``).

Moved into :mod:`eqasim_common` during the Phase-2 refactor so that the
package layout matches the rule that everything region-neutral lives under
``eqasim_common/`` while regional notebooks/data live under
``<region>/analysis/`` (e.g. ``braunschweig/analysis``, ``bavaria/analysis``).

Synpp stage names follow the dotted module path, so stages that used to be
referenced as ``"eqasim_common.analysis.synthesis.income"`` are now
``"eqasim_common.analysis.synthesis.income"``.
"""
