"""Single source of truth for eqasim-bs terminal colour + the stage→phase map.

Palette + helpers are PORTED FROM cleancensus/theme.py — keep the palette in sync so
the two projects look "from one mold". ``PHASE_OF`` is eqasim-specific: it maps the
*short* stage names (after logging_setup._short_stage strips the braunschweig./synpp./…
prefixes) to a pipeline phase. Scheme B: the stage tag is coloured by phase, chosen
distinct from the severity palette so a tag never reads as a warning.

Colour is opt-out via NO_COLOR, and auto-off for non-TTY streams (so the file log and
redirected output stay plain + greppable).
"""
from __future__ import annotations

import os
import sys

RESET = "\x1b[0m"
DIM = "\x1b[2m"
BOLD = "\x1b[1m"

# Severity — coloured on the LEVEL column.
LEVEL_COLOR = {
    "DEBUG": "\x1b[2m",       # dim
    "INFO": "\x1b[32m",       # green
    "WARNING": "\x1b[33m",    # yellow
    "ERROR": "\x1b[31m",      # red
    "CRITICAL": "\x1b[1;31m", # bold red
}

# Structural — banner / bars.
BORDER = "\x1b[2m"
TITLE = "\x1b[1;36m"
ACCENT = "\x1b[36m"


def _c256(n: int) -> str:
    return f"\x1b[38;5;{n}m"


# Phase palette — stage tag. 256-colour, severity-safe (no green/yellow/red).
PHASE_COLOR = {
    "acquire": _c256(39),      # azure
    "transform": _c256(43),    # teal
    "validate": _c256(141),    # violet
    "controls": _c256(208),    # orange
    "orchestrate": _c256(103), # slate
    "misc": _c256(245),        # neutral grey
}

# Short stage name (after _short_stage) → phase. Covers the popsim run loggers;
# any unmapped stage (the wider data/synthesis/matsim pipeline) → 'misc'. Extend
# as more stages get phase identities.
PHASE_OF = {
    # prep
    "popsim.seed": "acquire", "popsim.member_completion": "acquire",
    # solve + expand
    "popsim.stage": "transform", "popsim.mid": "transform",
    "popsim.batch": "transform", "popsim.merge": "transform",
    "popsim.expand": "transform", "popsim.missing": "transform",
    "popsim.attributes": "transform",
    # income layer
    "popsim.income": "controls", "popsim.income_kreis_control": "controls",
    "popsim.income_spatial_tilt": "controls",
    # orchestration
    "synpp": "orchestrate",
}


def phase_of(stage: str) -> str:
    """Pipeline phase for a (short) stage name; unmapped stages → 'misc'."""
    return PHASE_OF.get(stage, "misc")


def stage_color(stage: str) -> str:
    """ANSI colour for a stage tag, by its phase."""
    return PHASE_COLOR[phase_of(stage)]


def want_color(color="auto", stream=None) -> bool:
    """Whether to emit ANSI colour. ``stream`` defaults to sys.stderr (console log);
    progress passes sys.stdout. Evaluated at call time so pytest stream-swaps work."""
    if color == "auto" or color is None:
        if os.environ.get("NO_COLOR"):
            return False
        s = stream if stream is not None else sys.stderr
        return bool(getattr(s, "isatty", lambda: False)())
    return bool(color)
