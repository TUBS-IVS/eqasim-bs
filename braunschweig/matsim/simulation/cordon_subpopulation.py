"""Fix the travel mode of the cross-cordon in-commuters in the MATSim config (eqasim-bs#566, ADR-0182).

The eqasim-generated ``replanning`` module runs DiscreteModeChoice for the default subpopulation, which would
re-choose the modes the synthesis assigned to the injected in-commuters (Mikrozensus mode, pt timetable check).
The synthesis puts them into their own subpopulations, ``incommuter`` (``braunschweig.synthesis.incommuters``)
and ``student_incommuter`` (``braunschweig.synthesis.student_incommuters``); this module adds one strategy block
per subpopulation that allows **route innovation (ReRoute) but no mode innovation**, so an in-commuter keeps its
assigned mode while still finding sensible routes under congestion.

The selector is KeepLastSelected, not a score-based one: eqasim enforces mode choice in the loop, and DMC's
``DiscreteModeChoiceConfigurator.checkModeChoiceInTheLoop`` aborts the run when ANY enabled strategy, whatever its
subpopulation, is not KeepLastSelected, DiscreteModeChoice or ReRoute. The freight agents use the same pair
(``org.eqasim.braunschweig.scenario.RunInjectFreight``).

Pure string transform on the config XML; ``braunschweig.matsim.simulation.prepare`` applies it to the final
config when the cordon feature is on. Idempotent per subpopulation.
"""
from __future__ import annotations

import logging
import math
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_LOG_TAG = "[cordon subpopulation]"

#: Subpopulation labels the in-commuter synthesis stages set; tests/test_incommuter_subpopulation.py pins them to
#: the labels those stages write.
INCOMMUTER_SUBPOPULATION = "incommuter"
STUDENT_INCOMMUTER_SUBPOPULATION = "student_incommuter"
INCOMMUTER_SUBPOPULATIONS = (INCOMMUTER_SUBPOPULATION, STUDENT_INCOMMUTER_SUBPOPULATION)

#: Strategies DMC accepts while mode choice in the loop is enforced (checkModeChoiceInTheLoop, discrete_mode_choice
#: 2026.0); any other enabled strategy aborts the run at startup.
MODE_CHOICE_IN_THE_LOOP_STRATEGIES = frozenset({"KeepLastSelected", "DiscreteModeChoice", "ReRoute"})

#: Share of an in-commuter subpopulation re-routed per iteration (MATSim strategy weight, dimensionless, in (0, 1));
#: KeepLastSelected gets the rest. 0.1 keeps the ReRoute share of the original fixed-mode policy (owner decision
#: 2026-11-05, ADR-0182).
KEY_REROUTE_WEIGHT = "cordon_incommuter_reroute_weight"
DEFAULT_REROUTE_WEIGHT = 0.1

_BLOCKS = """    <parameterset type="strategysettings" >
      <param name="strategyName" value="KeepLastSelected" />
      <param name="weight" value="{keep}" />
      <param name="subpopulation" value="{sub}" />
    </parameterset>
    <parameterset type="strategysettings" >
      <param name="strategyName" value="ReRoute" />
      <param name="weight" value="{reroute}" />
      <param name="subpopulation" value="{sub}" />
    </parameterset>
"""


def require_reroute_weight(value) -> float:
    """Return ``value`` as the ReRoute weight, raising ValueError unless it is a finite number in (0, 1), no bool."""
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
            or not 0.0 < value < 1.0):
        raise ValueError(f"{KEY_REROUTE_WEIGHT} must be a number strictly between 0 and 1 (the share of the "
                         f"in-commuters re-routed per iteration; KeepLastSelected gets the rest), got {value!r}")
    return float(value)


def _has_strategy_for(config_xml: str, subpopulation: str) -> bool:
    return re.search(r'<param\s+name="subpopulation"\s+value="%s"\s*/>' % re.escape(subpopulation),
                     config_xml) is not None


def add_incommuter_fixed_mode_strategy(config_xml: str, subpopulation: str = INCOMMUTER_SUBPOPULATION,
                                       reroute_weight: float = DEFAULT_REROUTE_WEIGHT) -> str:
    """Insert the fixed-mode (KeepLastSelected + ReRoute) strategy settings for ``subpopulation`` into the
    ``replanning`` module of an eqasim MATSim config XML string.

    Idempotent: if a strategy for that subpopulation already exists the input is returned unchanged. Raises
    ValueError if there is no ``replanning`` module or the weight is invalid (:func:`require_reroute_weight`).
    """
    reroute_weight = require_reroute_weight(reroute_weight)
    if _has_strategy_for(config_xml, subpopulation):
        return config_xml
    match = re.search(r'(<module name="replanning"[^>]*>)(.*?)(</module>)', config_xml, re.S)
    if not match:
        raise ValueError('no <module name="replanning"> found in config')
    blocks = _BLOCKS.format(sub=subpopulation, keep=repr(round(1.0 - reroute_weight, 12)), reroute=repr(reroute_weight))
    return config_xml[:match.start(3)] + blocks + config_xml[match.start(3):]


def add_incommuter_fixed_mode_strategies(config_xml: str, reroute_weight: float = DEFAULT_REROUTE_WEIGHT) -> str:
    """Insert the fixed-mode strategy settings for every in-commuter subpopulation (:data:`INCOMMUTER_SUBPOPULATIONS`)."""
    for subpopulation in INCOMMUTER_SUBPOPULATIONS:
        config_xml = add_incommuter_fixed_mode_strategy(config_xml, subpopulation, reroute_weight)
    return config_xml


def apply_to_config_file(config_path, reroute_weight: float = DEFAULT_REROUTE_WEIGHT) -> None:
    """Rewrite the MATSim config at ``config_path`` with the in-commuter strategy blocks and log what it added."""
    path = Path(config_path)
    before = path.read_text(encoding="utf-8")
    after = add_incommuter_fixed_mode_strategies(before, reroute_weight)
    if after != before:
        path.write_text(after, encoding="utf-8")
    added = [sub for sub in INCOMMUTER_SUBPOPULATIONS if not _has_strategy_for(before, sub)]
    logger.info("%s fixed-mode strategies (KeepLastSelected %.3f + ReRoute %.3f) added for %s, already present for %s "
                "in %s", _LOG_TAG, 1.0 - reroute_weight, reroute_weight, added or "none",
                [sub for sub in INCOMMUTER_SUBPOPULATIONS if sub not in added] or "none", path.name)
