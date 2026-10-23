"""Let every agent choose its modes once at the first replanning (eqasim-bs#465, mechanism 4).

eqasim configures the discrete mode choice "in the loop" (``DiscreteModeChoiceConfigurator.
configureAsModeChoiceInTheLoop``, weight 0.05) next to ``KeepLastSelected`` (0.95): in every iteration each agent
chooses again with probability 0.05. After the 86 replanning rounds of run ``i465-wpe-smoke-1pct-2026-10-23`` about
0.95**86 = 1.2 % of the residents had never chosen and still used the synthesis mode, for example a 5-minute survey walk
to an education location 22 km away (34 of the 49 in-region persons with a walk trip over 5 km in the final plans had
an unchanged plan). The mode-share termination criterion cannot see that: the shares are stable long before every
agent has chosen.

:func:`add_initial_mode_choice` adds, for every subpopulation that has the in-the-loop DiscreteModeChoice strategy
(``disableAfterIteration`` -1), a second DiscreteModeChoice strategy with a very high weight and
``disableAfterIteration`` 1: MATSim sets its weight to 0 from iteration 2 on, so it acts in the first replanning only.
There the share of agents that choose is ``(weight + w_loop) / (weight + sum of the weights)`` (99.9999 % with the
default weight 1e6 against 0.05 + 0.95); afterwards the loop runs unchanged. Subpopulations without the mode choice
(freight) are untouched. The config is edited as text, like ``braunschweig.matsim.config_modules``, so the XML
declaration, the DOCTYPE and the line endings survive; an identical strategy already present is left alone.
"""
from __future__ import annotations

import logging
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger(__name__)

_LOG_TAG = "[initial mode choice]"

KEY_ENABLED = "braunschweig.initial_mode_choice.enabled"
DEFAULT_ENABLED = True
#: Weight of the first-replanning strategy relative to the strategies of the loop (whose weights sum to 1 in the
#: eqasim config). Valid: a finite number > 0. Not a behavioural parameter: it only sets how close to 100 % of the
#: agents choose in the first replanning.
KEY_WEIGHT = "braunschweig.initial_mode_choice.weight"
DEFAULT_WEIGHT = 1.0e6

STRATEGY_NAME = "DiscreteModeChoice"
REPLANNING_MODULE = "replanning"
_MODULE_START = re.compile(r'<module\s+name="replanning"\s*>')


def check_weight(weight) -> float:
    """Return the weight as a float; raise ValueError unless it is a finite number > 0."""
    if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not math.isfinite(weight) or weight <= 0:
        raise ValueError(f"{_LOG_TAG} {KEY_WEIGHT} must be a finite number > 0, got {weight!r}")
    return float(weight)


def _strategy_settings(config_path) -> list[dict[str, str]]:
    root = ET.parse(str(config_path)).getroot()
    module = next((m for m in root.findall("module") if m.get("name") == REPLANNING_MODULE), None)
    if module is None:
        raise ValueError(f"{_LOG_TAG} {config_path} has no {REPLANNING_MODULE} module")
    return [{p.get("name"): p.get("value") for p in s.findall("param")}
            for s in module.findall("parameterset") if s.get("type") == "strategysettings"]


def add_initial_mode_choice(config_path, weight: float = DEFAULT_WEIGHT) -> dict[str, float]:
    """Add the first-replanning DiscreteModeChoice strategy to the config; return the share per subpopulation.

    Raises ValueError for an invalid weight, a config without a replanning module or without any in-the-loop
    DiscreteModeChoice strategy (nothing to complement: the run would not do mode choice at all). Rewrites the file
    unless every strategy to add is already present.
    """
    weight = check_weight(weight)
    path = Path(config_path)
    settings = _strategy_settings(path)
    loop = [s for s in settings if s.get("strategyName") == STRATEGY_NAME and s.get("disableAfterIteration") == "-1"]
    if not loop:
        raise ValueError(f"{_LOG_TAG} {path} has no {STRATEGY_NAME} strategy with disableAfterIteration -1: the run "
                         "does no mode choice in the loop, so there is nothing to start")

    weight_text = repr(weight)
    shares, blocks = {}, []
    for strategy in loop:
        subpopulation = strategy.get("subpopulation")
        total = sum(float(s["weight"]) for s in settings if s.get("subpopulation") == subpopulation)
        shares[subpopulation] = (weight + float(strategy["weight"])) / (weight + total)
        present = any(s.get("strategyName") == STRATEGY_NAME and s.get("disableAfterIteration") == "1"
                      and s.get("subpopulation") == subpopulation and s.get("weight") == weight_text for s in settings)
        if not present:
            blocks.append([
                '\t\t<parameterset type="strategysettings" >',
                '\t\t\t<param name="disableAfterIteration" value="1" />',
                '\t\t\t<param name="executionPath" value="null" />',
                f'\t\t\t<param name="strategyName" value="{STRATEGY_NAME}" />',
                f'\t\t\t<param name="subpopulation" value="{subpopulation}" />',
                f'\t\t\t<param name="weight" value="{weight_text}" />',
                '\t\t</parameterset>'])

    if blocks:
        text = path.read_bytes().decode("utf-8")
        start = _MODULE_START.search(text)
        if start is None:
            raise ValueError(f"{_LOG_TAG} {path}: cannot locate the text of the {REPLANNING_MODULE} module")
        end = text.index("</module>", start.end())
        line_start = text.rfind("\n", 0, end) + 1                  # insert before the line of </module>
        newline = "\r\n" if "\r\n" in text else "\n"
        inserted = "".join(newline.join(block) + newline for block in blocks)
        path.write_bytes((text[:line_start] + inserted + text[line_start:]).encode("utf-8"))

    for subpopulation, share in shares.items():
        logger.info("%s %s strategy with weight %s active in the first replanning only (disableAfterIteration 1), "
                    "subpopulation %s: %.4f%% of its agents choose their modes in iteration 1, afterwards the loop "
                    "(weight %s) as before", _LOG_TAG, STRATEGY_NAME, weight_text, subpopulation, 100.0 * share,
                    next(s["weight"] for s in loop if s.get("subpopulation") == subpopulation))
    return shares
