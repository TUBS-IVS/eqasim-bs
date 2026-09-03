# braunschweig/analysis/matsim_archive.py
"""Resolve the archived MATSim simulation output for analysis stages.

``matsim.output`` mirrors the run stage's ``simulation_output/`` into the
stable ``<output_path>/matsim_output/`` (config flag ``archive_matsim_output``,
default ON; ADR-0064 / issue #156) precisely so the outputs survive a synpp
cache wipe and are findable without knowing the stage hash.

Analysis stages resolve THAT directory from configuration instead of declaring
a stage dependency on ``matsim.simulation.run``: the stage edge was consumed
only to compute a directory path, but it forced an analysis-only invocation to
recompute the entire simulation chain (issue #354). ``simwrapper_include_matsim``
therefore stays a pure signal ("this run has MATSim outputs"), and absence of
the archive is handled by the callers' loud-skip contracts.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

LOGGER = logging.getLogger("braunschweig.analysis.matsim_archive")

ARCHIVE_DIR_NAME = "matsim_output"
# matsim.output asserts this file exists after every archive write; a directory
# without it is a half-written archive and must never be consumed.
ARCHIVE_SENTINEL = "output_events.xml.gz"
ARCHIVE_INFO_NAME = "ARCHIVE_INFO.json"


def resolve_matsim_archive(output_path: "str | Path") -> Path | None:
    """Return ``<output_path>/matsim_output`` when a complete archive exists.

    Returns ``None`` when the directory is absent or lacks the sentinel
    ``output_events.xml.gz``; callers must then SKIP their MATSim-dependent
    parts loudly (named reason, no silent fallback) -- see
    :func:`archive_missing_reason`.

    When found, the archive provenance from ``ARCHIVE_INFO.json`` (source stage
    hash dir + creation time) is logged so every analysis artifact stays
    traceable to the simulation run that produced its inputs.
    """
    archive = Path(output_path) / ARCHIVE_DIR_NAME
    if not (archive / ARCHIVE_SENTINEL).exists():
        return None

    provenance = "no ARCHIVE_INFO.json (archive predates provenance file)"
    info_path = archive / ARCHIVE_INFO_NAME
    if info_path.exists():
        try:
            info = json.loads(info_path.read_text())
            provenance = "source_hash_dir=%s, created=%s" % (
                info.get("source_hash_dir", "unknown"), info.get("created", "unknown"))
        except (json.JSONDecodeError, OSError) as exc:
            provenance = "ARCHIVE_INFO.json unreadable (%s)" % exc
    LOGGER.info("[matsim_archive] using MATSim output archive %s (%s)", archive, provenance)
    return archive


def archive_missing_reason(output_path: "str | Path") -> str:
    """Named skip reason for the loud-skip contracts of the analysis stages."""
    return ("no MATSim output archive at %s (matsim.output writes it when "
            "archive_matsim_output=true; run the simulation phase first)"
            % (Path(output_path) / ARCHIVE_DIR_NAME))
