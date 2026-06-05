"""Fix the cross-cordon in-commuter subpopulation's travel mode in the MATSim config.

The eqasim-generated ``replanning`` module runs DiscreteModeChoice for the default
subpopulation, which would overwrite the injected in-commuters' fixed (Mikrozensus)
modes. This adds a per-subpopulation strategy block for subpopulation
``incommuter`` that allows **route innovation (ReRoute) but no mode innovation** --
so an in-commuter keeps its assigned mode while still finding sensible routes under
congestion (the user-chosen "fixed mode, ReRoute allowed" policy).

Pure string transform on the config XML; the prepare stage applies it to the
generated config when the cordon feature is enabled. Idempotent.

See ``docs/superpowers/specs/2026-06-05-cross-cordon-external-demand-design.md``.
"""
from __future__ import annotations

import re

# ChangeExpBeta (selector) + ReRoute (route innovation) only. No SubtourModeChoice
# / ChangeMode / DiscreteModeChoice for this subpopulation -> mode stays fixed.
_BLOCKS = """    <parameterset type="strategysettings" >
      <param name="strategyName" value="ChangeExpBeta" />
      <param name="weight" value="0.9" />
      <param name="subpopulation" value="{sub}" />
    </parameterset>
    <parameterset type="strategysettings" >
      <param name="strategyName" value="ReRoute" />
      <param name="weight" value="0.1" />
      <param name="subpopulation" value="{sub}" />
    </parameterset>
"""


def add_incommuter_fixed_mode_strategy(config_xml: str, subpopulation: str = "incommuter") -> str:
    """Insert fixed-mode (+ReRoute) strategy settings for ``subpopulation`` into the
    ``replanning`` module of an eqasim MATSim config XML string.

    Idempotent: if a strategy for that subpopulation already exists the input is
    returned unchanged. Raises ValueError if there is no ``replanning`` module.
    """
    if 'value="%s"' % subpopulation in config_xml:
        return config_xml
    match = re.search(r'(<module name="replanning"[^>]*>)(.*?)(</module>)', config_xml, re.S)
    if not match:
        raise ValueError('no <module name="replanning"> found in config')
    blocks = _BLOCKS.format(sub=subpopulation)
    return config_xml[:match.start(3)] + blocks + config_xml[match.start(3):]
