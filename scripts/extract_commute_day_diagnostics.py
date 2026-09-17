"""Extract the commute-day-state diagnostics of a finished run into one strict-JSON artefact.

Issue #378; ADR-0104 check 4. The Phase B proof runs of 2026-09-05/06 built
``state_diagnostics.json`` with an ad-hoc snippet typed on the run server, and had to quote the
reporting-day trips counters (``n_donors_immobile``, ``n_donors_without_trips``,
``n_persons_replaced``, ``n_trips_removed`` / ``n_trips_added``) out of a committed LOG EXCERPT
because no structured artefact carried them. This script is that snippet, committed, tested and
made reproducible; it decides nothing and computes nothing -- every number it writes was produced
by a stage and is copied verbatim.

WHAT IT READS -- a synpp WORKING DIRECTORY (the ``cache_*`` directory of a run, not its output):

* ``pipeline.json`` -- synpp's meta file, one entry per stage keyed by that stage's hash. synpp
  builds the key as ``"<stage name>__<md5 of the stage's resolved config>"`` (``hash_name`` in
  ``synpp/pipeline.py``), or as the BARE stage name when the stage declares no config at all, so
  a stage is located by that prefix; see :func:`resolve_stage_hash`.
* ``<hash>.p`` -- the pickled return value of a stage, for the two stages whose diagnostics ARE
  their return value.

Three blocks are collected:

======================  =========================================================================
block                   source
======================  =========================================================================
``state``               ``braunschweig.synthesis.commute_day.state_stage``'s cache pickle,
                        key ``diagnostics`` (incl. the per-cell pool sizes of issue #378 under
                        ``matching``). REQUIRED.
``trips_day``           ``braunschweig.synthesis.commute_day.trips_day_stage``'s ``info`` in
                        pipeline.json, key ``commute_day_trips_diagnostics``. REQUIRED. That
                        stage's own return value is the reporting-day trips FRAME (it is aliased
                        to ``synthesis.population.trips.final``), so its diagnostics travel via
                        ``context.set_info`` instead -- see that module's ``INFO_KEY``.
``donor_pool``          ``braunschweig.synthesis.commute_day.home_office_donors_stage``'s cache
                        pickle, third element of its ``(attributes, trips, diagnostics)`` tuple.
                        OPTIONAL: a working directory whose entry has been pruned still yields a
                        usable artefact, and the absence is then STATED in the block
                        (``available: false`` with a reason), never silently omitted.
======================  =========================================================================

AGGREGATES ONLY. Both pickles also carry population-sized per-person frames (the ``states`` frame
is one row per worker, ~300 k at 100 % scale; the donor tuple carries two donor frames). None of
them is read into the output: this artefact is meant to be COMMITTED beside a run manifest, and
committing per-person rows of a restricted-data population is not permissible. Only the
``diagnostics`` dicts are copied, through
:func:`braunschweig.analysis.json_output.json_safe`, so ``NaN`` becomes ``null`` and numpy
scalars become plain Python -- the same strict-JSON rule the analysis side-cars follow. That pass
also renders the donor-pool block's TUPLE-keyed ``cells`` entry with ``str``, so those keys appear
as ``"('lt10', True, False)"`` rather than failing the write; the per-matching-cell census
check 4 actually asks for is the state block's ``matching.donor_pool_size_by_hard_cell``, which
is keyed on all four hard dimensions and counts only the donors the matching could use.

Usage (from the repository/worktree root, conda env eqasim)::

    python scripts/extract_commute_day_diagnostics.py \\
        --working-directory /path/to/cache_bs_100pct_cds_on \\
        --out-dir eqasim-data/data/braunschweig/calibration/<run directory>/on

Writes ``state_diagnostics.json`` into ``--out-dir`` (override the name with ``--output-name``).
If a stage name resolves to more than one hash -- two config variants of the same stage in one
working directory, e.g. an A/B -- the script ABORTS and names every candidate rather than
picking one; re-run it with ``--stage-hash <hash>`` per ambiguous stage.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import pickle
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from braunschweig import provenance  # noqa: E402
from braunschweig.analysis import json_output  # noqa: E402
from braunschweig.synthesis.commute_day.trips_day_stage import INFO_KEY  # noqa: E402

logger = logging.getLogger("extract_commute_day_diagnostics")

STATE_STAGE = "braunschweig.synthesis.commute_day.state_stage"
TRIPS_STAGE = "braunschweig.synthesis.commute_day.trips_day_stage"
DONOR_STAGE = "braunschweig.synthesis.commute_day.home_office_donors_stage"

#: synpp's meta file inside a working directory.
PIPELINE_META = "pipeline.json"
#: Default name of the written artefact -- the name the ADR-0104 proof runs already use.
OUTPUT_FILE = "state_diagnostics.json"
#: Separator ``synpp.pipeline.hash_name`` puts between a stage name and its config hash.
HASH_SEPARATOR = "__"


class ExtractionError(SystemExit):
    """Aborts the script with a message that names the working directory and what was missing.

    Derived from ``SystemExit`` so the CLI exits non-zero with the message on stderr and no
    traceback: every one of these is a statement about the working directory the caller passed,
    not a defect in this script.
    """


def _read_meta(working_directory: Path) -> dict:
    path = working_directory / PIPELINE_META
    if not path.is_file():
        raise ExtractionError(
            f"{path} not found -- --working-directory must be a synpp WORKING directory (the "
            "run's cache_* directory, which holds pipeline.json and the <hash>.p cache files), "
            "not its output directory.")
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def resolve_stage_hash(meta: dict, stage_name: str, explicit: str | None) -> str:
    """The single ``pipeline.json`` key belonging to ``stage_name``.

    synpp names a cache entry ``"<stage name>__<md5 of config>"``, or the BARE stage name when
    the stage declares no config (``hash_name``), so both forms are accepted. The separator is
    part of the match, otherwise a sibling stage whose name merely STARTS with this one's (say
    ``..._stage_v2``) would be picked up as a second candidate.

    ``explicit`` is the caller's ``--stage-hash`` choice; it is returned as-is once it is
    confirmed to be present, which is how an ambiguous stage is resolved.

    Raises :class:`ExtractionError` -- never a guess -- when no key matches, or when more than one
    does: choosing one of two config variants of the same stage is a decision about which run's
    numbers are being reported, and belongs to the caller.
    """
    if explicit is not None:
        if explicit not in meta:
            raise ExtractionError(
                f"--stage-hash {explicit!r} is not a key of {PIPELINE_META}; its keys for "
                f"{stage_name!r} are {sorted(_candidates(meta, stage_name)) or 'none'}.")
        return explicit
    candidates = sorted(_candidates(meta, stage_name))
    if not candidates:
        raise ExtractionError(
            f"no cache entry for stage {stage_name!r} in {PIPELINE_META} -- this working "
            "directory holds no run of that stage. Check that the run had the commute-day model "
            "in its run list (commute_day_state_enabled, and the stage reached in the DAG).")
    if len(candidates) > 1:
        raise ExtractionError(
            f"stage {stage_name!r} resolves to {len(candidates)} cache entries in "
            f"{PIPELINE_META}: {candidates}. That means the working directory holds more than "
            "one config variant of it (an A/B, say); which variant's numbers to report is your "
            "decision, not this script's -- re-run with --stage-hash <one of them>.")
    return candidates[0]


def _candidates(meta: dict, stage_name: str) -> list:
    return [key for key in meta
            if key == stage_name or key.startswith(stage_name + HASH_SEPARATOR)]


def _load_pickle(working_directory: Path, stage_hash: str, stage_name: str):
    path = working_directory / f"{stage_hash}.p"
    if not path.is_file():
        raise ExtractionError(
            f"{PIPELINE_META} lists stage {stage_name!r} as {stage_hash}, but its cache file "
            f"{path} is missing -- the entry was pruned (an ephemeral stage) or the directory is "
            "incomplete. The diagnostics cannot be recovered from the meta file alone.")
    with open(path, "rb") as handle:
        return pickle.load(handle)


def state_diagnostics(working_directory: Path, stage_hash: str) -> dict:
    """The state stage's ``diagnostics`` dict -- never its ``states`` frame (see the module doc)."""
    output = _load_pickle(working_directory, stage_hash, STATE_STAGE)
    if not isinstance(output, dict) or "diagnostics" not in output:
        raise ExtractionError(
            f"the cache entry {stage_hash} does not look like {STATE_STAGE}'s output: expected a "
            f"dict with a 'diagnostics' key, found {type(output).__name__} "
            f"{sorted(output) if isinstance(output, dict) else ''}.")
    return output["diagnostics"]


def donor_diagnostics(working_directory: Path, stage_hash: str) -> dict:
    """The donor stage's diagnostics -- the third element of its ``(attributes, trips, diag)``."""
    output = _load_pickle(working_directory, stage_hash, DONOR_STAGE)
    if not (isinstance(output, tuple) and len(output) == 3):
        raise ExtractionError(
            f"the cache entry {stage_hash} does not look like {DONOR_STAGE}'s output: expected a "
            f"3-tuple (attributes, trips, diagnostics), found {type(output).__name__}.")
    return output[2]


def trips_day_info(meta: dict, stage_hash: str) -> dict:
    """The reporting-day trips stage's ``set_info`` payload, from the meta file.

    Read from ``info`` rather than from a cache pickle because that stage's return value is the
    reporting-day trips FRAME itself (see the module docstring). A stage that ran under code
    predating issue #378 has no such entry -- that is reported as the missing instrumentation it
    is, never as an empty result.
    """
    info = (meta.get(stage_hash) or {}).get("info") or {}
    if INFO_KEY not in info:
        raise ExtractionError(
            f"the cache entry {stage_hash} for {TRIPS_STAGE!r} reports no {INFO_KEY!r} "
            f"(it reports {sorted(info) or 'nothing'}). The stage ran under code that predates "
            "issue #378, so its diagnostics exist only in that run's log; re-run the stage to "
            "record them, or read them from the run log as the 2026-09-06 proof had to.")
    return info[INFO_KEY]


def collect(working_directory: Path, stage_hashes: dict) -> dict:
    """Build the artefact payload for one working directory (pure apart from reading it)."""
    meta = _read_meta(working_directory)
    state_hash = resolve_stage_hash(meta, STATE_STAGE, stage_hashes.get(STATE_STAGE))
    trips_hash = resolve_stage_hash(meta, TRIPS_STAGE, stage_hashes.get(TRIPS_STAGE))

    stages = {
        STATE_STAGE: {"hash": state_hash,
                      "source": str(working_directory / f"{state_hash}.p")},
        TRIPS_STAGE: {"hash": trips_hash,
                      "source": f"{PIPELINE_META}:{trips_hash}.info.{INFO_KEY}"},
    }

    # OPTIONAL, and explicitly so: an absent donor block is STATED in the output rather than
    # left out, so a reader can never mistake "not collected" for "not reported by the stage"
    # (CLAUDE.md "Fallback transparency" -- no silent fallbacks).
    try:
        donor_hash = resolve_stage_hash(meta, DONOR_STAGE, stage_hashes.get(DONOR_STAGE))
        donor_block = donor_diagnostics(working_directory, donor_hash)
        stages[DONOR_STAGE] = {"hash": donor_hash,
                               "source": str(working_directory / f"{donor_hash}.p")}
    except ExtractionError as error:
        reason = f"{DONOR_STAGE}: {error}"
        logger.warning("donor pool diagnostics NOT collected -- %s", reason)
        donor_block = {"available": False, "reason": reason}

    return {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "generator": "scripts/extract_commute_day_diagnostics.py",
        "git_commit": provenance.git_commit(str(REPO_ROOT)),
        "working_directory": str(working_directory),
        "stages": stages,
        "state": state_diagnostics(working_directory, state_hash),
        "trips_day": trips_day_info(meta, trips_hash),
        "donor_pool": donor_block,
    }


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--working-directory", required=True,
                        help="the run's synpp WORKING directory (its cache_* directory, holding "
                             "pipeline.json and the <hash>.p cache files)")
    parser.add_argument("--out-dir", required=True,
                        help="directory the artefact is written into (created if absent)")
    parser.add_argument("--output-name", default=OUTPUT_FILE,
                        help=f"file name inside --out-dir (default {OUTPUT_FILE})")
    parser.add_argument("--stage-hash", action="append", default=[], metavar="HASH",
                        help="pipeline.json key to use for the stage it names, for a stage that "
                             "resolves to more than one entry; repeatable")
    args = parser.parse_args(argv)

    working_directory = Path(args.working_directory)
    stage_hashes = {}
    for stage_hash in args.stage_hash:
        stage_name = stage_hash.split(HASH_SEPARATOR)[0]
        stage_hashes[stage_name] = stage_hash

    payload = collect(working_directory, stage_hashes)

    out_path = Path(args.out_dir) / args.output_name
    json_output.write_json(str(out_path), payload)
    logger.info("wrote %s", out_path)

    state = payload["state"]
    matching = state.get("matching") or {}
    logger.info(
        "HEADLINE: %s workers, final states %s; %s matched persons drawn from a single-donor "
        "cell; reporting day %s trips for %s persons, %s persons replaced",
        state.get("n_workers"), state.get("final_state_counts"),
        matching.get("n_persons_matched_from_single_donor_cell"),
        payload["trips_day"].get("n_trips_reporting_day"),
        payload["trips_day"].get("n_persons_reporting_day"),
        (payload["trips_day"].get("diagnostics") or {}).get("n_persons_replaced"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
