"""Tests for scripts.extract_commute_day_diagnostics (issue #378).

The script replaces the ad-hoc server snippet the ADR-0104 Phase B proofs used to build
``state_diagnostics.json`` by hand. It reads a synpp WORKING DIRECTORY -- ``pipeline.json`` plus
the per-stage cache pickles -- and writes one strict-JSON file of AGGREGATES ONLY.

Everything here runs against a tiny synthetic working directory built by :func:`_working_directory`
(CLAUDE.md "Tests" -- deterministic, small, synthetic); no real cache and no real run is touched.
Covered: the happy path, the two sources (a stage pickle vs. the ``info`` synpp persists for a
stage whose own output is a DataFrame), the aggregates-only guarantee, strict JSON, and the two
ways a working directory can fail to answer -- a stage that is absent, and a stage name that
resolves to more than one hash.
"""
from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from braunschweig.synthesis.commute_day.trips_day_stage import INFO_KEY
from scripts.extract_commute_day_diagnostics import (
    DONOR_STAGE,
    OUTPUT_FILE,
    STATE_STAGE,
    TRIPS_STAGE,
    main,
    resolve_stage_hash,
)

STATE_HASH = f"{STATE_STAGE}__aaaa1111"
TRIPS_HASH = f"{TRIPS_STAGE}__bbbb2222"
DONOR_HASH = f"{DONOR_STAGE}__cccc3333"


def _state_diagnostics():
    """A state-stage diagnostics dict shaped like the real one, incl. the #378 pool-size keys."""
    return {
        "enabled": True,
        "n_workers": 6,
        "n_redraw_eligible": 2,
        "share_donor_source_primary": 1.0,
        "final_state_counts": {"at_workplace": 4, "home": 1, "absent": 1},
        "matching": {
            "n_persons": 1,
            "matched_by_level": {0: 1, 1: 0, 2: 0, 3: 0, 4: 0, 5: 0, 6: 0},
            "n_not_replaceable": 0,
            "donor_pool_size_by_hard_cell": [
                {"distance_class": "10_25", "has_active_escort": False,
                 "has_children_u14": False, "has_car": True, "n_donors": 3},
            ],
            "matched_cell_size_by_level": {
                "0": {"n_persons": 1, "min": 3, "p25": 3.0, "median": 3.0, "p75": 3.0,
                      "max": 3, "n_persons_single_donor_cell": 0}},
            "n_persons_matched_from_single_donor_cell": 0,
        },
    }


def _trips_info():
    return {
        "commute_day_state_enabled": True,
        "day_absence_enabled": True,
        "n_trips_reporting_day": 11,
        "n_persons_reporting_day": 5,
        "diagnostics": {"n_persons_replaced": 1, "n_donors_immobile": 1,
                        "n_donors_without_trips": 0, "n_trips_removed": 3, "n_trips_added": 2},
    }


def _states_frame():
    """One row per worker -- the PER-PERSON half of the state stage's output.

    Deliberately carries a column no aggregate may ever leak (``person_id``): the extract must
    read the diagnostics beside it and never the frame itself.
    """
    return pd.DataFrame({"person_id": [f"p{i}" for i in range(6)],
                         "commute_day_state": ["at_workplace"] * 4 + ["home", "absent"]})


def _working_directory(tmp_path, *, meta=None, with_donor=True, with_state_pickle=True):
    """A synthetic synpp working directory: pipeline.json plus the per-stage cache pickles."""
    directory = tmp_path / "cache"
    directory.mkdir()
    if meta is None:
        meta = {
            STATE_HASH: {"info": {}, "updated": 1.0},
            TRIPS_HASH: {"info": {INFO_KEY: _trips_info()}, "updated": 2.0},
            # An unrelated stage, to prove the resolver matches on the stage NAME prefix and not
            # on a substring of some other stage's hash.
            "synthesis.population.enriched__dddd4444": {"info": {}, "updated": 0.5},
        }
        if with_donor:
            meta[DONOR_HASH] = {"info": {}, "updated": 0.5}
    (directory / "pipeline.json").write_text(json.dumps(meta), encoding="utf-8")

    if with_state_pickle:
        with open(directory / f"{STATE_HASH}.p", "wb") as handle:
            pickle.dump({"states": _states_frame(), "diagnostics": _state_diagnostics()}, handle)
    if with_donor:
        with open(directory / f"{DONOR_HASH}.p", "wb") as handle:
            pickle.dump((pd.DataFrame({"donor_id": ["d1"]}), pd.DataFrame({"donor_id": ["d1"]}),
                         {"n_donors": 1, "n_immobile": 0}), handle)
    return directory


def _run(directory, out_dir, *extra):
    assert main(["--working-directory", str(directory), "--out-dir", str(out_dir), *extra]) == 0
    return json.loads((Path(out_dir) / OUTPUT_FILE).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- happy path


def test_extract_writes_the_state_and_trips_diagnostics_into_one_json(tmp_path):
    payload = _run(_working_directory(tmp_path), tmp_path / "out")

    assert payload["state"]["n_workers"] == 6
    assert payload["state"]["matching"]["donor_pool_size_by_hard_cell"][0]["n_donors"] == 3
    # The R8/R9 counters ADR-0104 check 4 previously had to quote from a log excerpt.
    assert payload["trips_day"]["diagnostics"]["n_donors_immobile"] == 1
    assert payload["trips_day"]["diagnostics"]["n_persons_replaced"] == 1
    assert payload["donor_pool"]["n_donors"] == 1


def test_extract_records_which_hash_each_block_came_from(tmp_path):
    """Provenance: a reader must be able to point at the exact cache entry behind a number."""
    directory = _working_directory(tmp_path)

    payload = _run(directory, tmp_path / "out")

    assert payload["stages"][STATE_STAGE]["hash"] == STATE_HASH
    assert payload["stages"][TRIPS_STAGE]["hash"] == TRIPS_HASH
    assert payload["stages"][STATE_STAGE]["source"].endswith(f"{STATE_HASH}.p")
    assert payload["stages"][TRIPS_STAGE]["source"] == f"pipeline.json:{TRIPS_HASH}.info.{INFO_KEY}"
    assert payload["working_directory"] == str(directory)
    assert payload["generator"] == "scripts/extract_commute_day_diagnostics.py"


def test_extract_writes_aggregates_only_never_a_per_person_row(tmp_path):
    """The state pickle holds one row per worker next to the diagnostics; none of it may leak."""
    payload = _run(_working_directory(tmp_path), tmp_path / "out")

    text = json.dumps(payload)
    assert "person_id" not in text and "donor_id" not in text
    assert not any(f'"p{index}"' in text for index in range(6))
    assert set(payload) == {"generated_at", "generator", "git_commit", "working_directory",
                            "stages", "state", "trips_day", "donor_pool"}


def test_extract_output_is_strict_json(tmp_path):
    """NaN / numpy scalars must not reach the file: jq and every non-Python reader reject them."""
    directory = _working_directory(tmp_path)
    diagnostics = _state_diagnostics()
    diagnostics["share_donor_source_primary"] = np.float64("nan")
    diagnostics["n_workers"] = np.int64(6)
    with open(directory / f"{STATE_HASH}.p", "wb") as handle:
        pickle.dump({"states": _states_frame(), "diagnostics": diagnostics}, handle)

    payload = _run(directory, tmp_path / "out")

    raw = (tmp_path / "out" / OUTPUT_FILE).read_text(encoding="utf-8")
    assert "NaN" not in raw and "Infinity" not in raw
    assert payload["state"]["share_donor_source_primary"] is None
    assert payload["state"]["n_workers"] == 6


# --------------------------------------------------------------------------- failure modes


def test_extract_raises_naming_the_stage_when_it_is_absent(tmp_path):
    directory = _working_directory(tmp_path)
    meta = json.loads((directory / "pipeline.json").read_text(encoding="utf-8"))
    del meta[TRIPS_HASH]
    (directory / "pipeline.json").write_text(json.dumps(meta), encoding="utf-8")

    with pytest.raises(SystemExit, match=TRIPS_STAGE):
        main(["--working-directory", str(directory), "--out-dir", str(tmp_path / "out")])


def test_extract_raises_naming_both_hashes_when_a_stage_is_ambiguous(tmp_path):
    """Two config variants of one stage in the same working directory: the caller must choose."""
    directory = _working_directory(tmp_path)
    meta = json.loads((directory / "pipeline.json").read_text(encoding="utf-8"))
    meta[f"{TRIPS_STAGE}__eeee5555"] = {"info": {INFO_KEY: _trips_info()}, "updated": 3.0}
    (directory / "pipeline.json").write_text(json.dumps(meta), encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        main(["--working-directory", str(directory), "--out-dir", str(tmp_path / "out")])
    message = str(excinfo.value)
    assert TRIPS_HASH in message and f"{TRIPS_STAGE}__eeee5555" in message
    assert "--stage-hash" in message


def test_extract_accepts_an_explicit_hash_for_an_ambiguous_stage(tmp_path):
    directory = _working_directory(tmp_path)
    meta = json.loads((directory / "pipeline.json").read_text(encoding="utf-8"))
    meta[f"{TRIPS_STAGE}__eeee5555"] = {"info": {INFO_KEY: {"n_trips_reporting_day": 99}},
                                        "updated": 3.0}
    (directory / "pipeline.json").write_text(json.dumps(meta), encoding="utf-8")

    payload = _run(directory, tmp_path / "out", "--stage-hash", f"{TRIPS_STAGE}__eeee5555")

    assert payload["trips_day"]["n_trips_reporting_day"] == 99


def test_extract_reports_an_absent_donor_pool_explicitly_rather_than_omitting_it(tmp_path):
    """A missing optional block must be stated, never silently left out (no silent fallbacks)."""
    payload = _run(_working_directory(tmp_path, with_donor=False), tmp_path / "out")

    assert payload["donor_pool"]["available"] is False
    assert DONOR_STAGE in payload["donor_pool"]["reason"]


def test_extract_raises_when_the_state_cache_entry_has_no_pickle(tmp_path):
    """pipeline.json knows the stage but its cache file is gone (an ephemeral/pruned entry)."""
    directory = _working_directory(tmp_path, with_state_pickle=False)

    with pytest.raises(SystemExit, match="cache file"):
        main(["--working-directory", str(directory), "--out-dir", str(tmp_path / "out")])


def test_extract_raises_when_the_stage_reported_no_info(tmp_path):
    """The stage ran under code that predates issue #378: say so, do not report an empty dict."""
    directory = _working_directory(tmp_path)
    meta = json.loads((directory / "pipeline.json").read_text(encoding="utf-8"))
    meta[TRIPS_HASH]["info"] = {}
    (directory / "pipeline.json").write_text(json.dumps(meta), encoding="utf-8")

    with pytest.raises(SystemExit, match=INFO_KEY):
        main(["--working-directory", str(directory), "--out-dir", str(tmp_path / "out")])


# --------------------------------------------------------------------------- resolver


def test_resolve_stage_hash_matches_the_name_prefix_not_a_substring(tmp_path):
    """``hash_name`` is ``"<stage name>__<md5>"``, so the separator must be part of the match."""
    meta = {f"{STATE_STAGE}__aaaa1111": {}, f"{STATE_STAGE}_extra__bbbb2222": {}}

    assert resolve_stage_hash(meta, STATE_STAGE, None) == f"{STATE_STAGE}__aaaa1111"


def test_resolve_stage_hash_accepts_an_unconfigured_stage_named_by_itself(tmp_path):
    """``hash_name`` returns the BARE name when a stage declares no config at all."""
    assert resolve_stage_hash({STATE_STAGE: {}}, STATE_STAGE, None) == STATE_STAGE


# ------------------------------------------- PR #417 review: absence vs. defect, and #378 keys
# The donor block is OPTIONAL only in the sense of ABSENT (no cache entry, or a pruned cache
# file). An AMBIGUOUS donor stage is a decision the caller must make, and a wrong-shaped pickle
# is a defect -- swallowing either into `available: false` would ship a committed artefact that
# silently omits diagnostics from a stage that is actually present.


def test_extract_tolerates_a_donor_entry_whose_cache_file_was_pruned(tmp_path):
    """Meta lists the stage, the .p is gone (an ephemeral/pruned entry): still a usable artefact."""
    directory = _working_directory(tmp_path)
    (directory / f"{DONOR_HASH}.p").unlink()

    payload = _run(directory, tmp_path / "out")

    assert payload["donor_pool"]["available"] is False
    assert "cache file" in payload["donor_pool"]["reason"]
    assert payload["state"]["n_workers"] == 6          # the required blocks are unaffected


def test_extract_raises_when_the_donor_stage_is_ambiguous(tmp_path):
    """Two config variants of the donor stage: which one to report is the caller's decision."""
    directory = _working_directory(tmp_path)
    meta = json.loads((directory / "pipeline.json").read_text(encoding="utf-8"))
    meta[f"{DONOR_STAGE}__ffff6666"] = {"info": {}, "updated": 4.0}
    (directory / "pipeline.json").write_text(json.dumps(meta), encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        main(["--working-directory", str(directory), "--out-dir", str(tmp_path / "out")])
    assert "--stage-hash" in str(excinfo.value)


def test_extract_raises_when_the_donor_pickle_has_the_wrong_shape(tmp_path):
    """A present-but-malformed donor entry is a defect, not an absence."""
    directory = _working_directory(tmp_path)
    with open(directory / f"{DONOR_HASH}.p", "wb") as handle:
        pickle.dump({"not": "a 3-tuple"}, handle)

    with pytest.raises(SystemExit, match="3-tuple"):
        main(["--working-directory", str(directory), "--out-dir", str(tmp_path / "out")])


def test_extract_raises_when_the_state_stage_predates_the_pool_size_diagnostics(tmp_path):
    """Symmetry with trips_day_info: an artefact that cannot answer check 4 must say so, rather
    than being written as though the pool sizes had been measured and found absent."""
    directory = _working_directory(tmp_path)
    diagnostics = _state_diagnostics()
    del diagnostics["matching"]["donor_pool_size_by_hard_cell"]
    with open(directory / f"{STATE_HASH}.p", "wb") as handle:
        pickle.dump({"states": _states_frame(), "diagnostics": diagnostics}, handle)

    with pytest.raises(SystemExit) as excinfo:
        main(["--working-directory", str(directory), "--out-dir", str(tmp_path / "out")])
    message = str(excinfo.value)
    assert "donor_pool_size_by_hard_cell" in message and "#378" in message


def test_extract_accepts_a_disabled_state_stage_without_a_matching_block(tmp_path):
    """The OFF path emits {"enabled": False} and no matching block at all -- still valid."""
    directory = _working_directory(tmp_path)
    with open(directory / f"{STATE_HASH}.p", "wb") as handle:
        pickle.dump({"states": _states_frame(), "diagnostics": {"enabled": False}}, handle)

    payload = _run(directory, tmp_path / "out")

    assert payload["state"] == {"enabled": False}
