"""Pipeline-level integration tests for the Braunschweig synthesis pipeline.

These tests drive the real synpp DAG via ``configs/fixtures/config_dryrun_braunschweig.yml``
(0.1% sampling, full ``synthesis.output`` target). They are **opt-in**:
running them requires the full set of input data described in
``eqasim-data/DOWNLOAD_CHECKLIST_BS.md`` and is gated on the environment
variable ``EQASIM_BS_RUN_PIPELINE=1``.

Default behaviour: the tests are skipped, so the unit-test gate stays fast.

This module replaced the IDF region-10/11 tests inherited from upstream
eqasim in Phase 3.1 of the eqasim-bs refactor (plan/refactor-eqasim-bs.md).
The IDF testdata fixture (``tests/testdata.py``) is no longer compatible
with the post-fork DAG; it remains in the tree for reference but is
unused.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DRYRUN_CONFIG = REPO_ROOT / "configs" / "fixtures" / "config_dryrun_braunschweig.yml"
DATA_PATH = REPO_ROOT / "eqasim-data" / "data"
BS_DATA_PATH = DATA_PATH / "braunschweig"

OPT_IN_VAR = "EQASIM_BS_RUN_PIPELINE"

pytestmark = pytest.mark.skipif(
    os.environ.get(OPT_IN_VAR) != "1" or not BS_DATA_PATH.is_dir(),
    reason=(
        f"Set {OPT_IN_VAR}=1 and provide eqasim-data/data/braunschweig/ "
        "to run the full-pipeline integration test "
        "(see eqasim-data/DOWNLOAD_CHECKLIST_BS.md)."
    ),
)


def _load_dryrun_config():
    with DRYRUN_CONFIG.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_dryrun_pipeline_runs(tmp_path):
    """The 0.1% dryrun config must reach synthesis.output without error."""
    import synpp

    raw = _load_dryrun_config()
    config = dict(raw.get("config", {}))
    aliases = raw.get("aliases", {})

    # Redirect outputs into the pytest tmp_path so we don't pollute the
    # repository's working directories.
    cache_path = tmp_path / "cache"
    output_path = tmp_path / "output"
    cache_path.mkdir()
    output_path.mkdir()

    config["data_path"] = str(DATA_PATH)
    config["output_path"] = str(output_path)
    config["output_prefix"] = "bs_dryrun_"

    stages = [{"descriptor": stage} for stage in raw.get("run", ["synthesis.output"])]

    synpp.run(
        stages,
        config,
        working_directory=str(cache_path),
        aliases=aliases,
    )

    prefix = config["output_prefix"]
    assert (output_path / f"{prefix}households.csv").is_file()
    assert (output_path / f"{prefix}persons.csv").is_file()
    assert (output_path / f"{prefix}activities.csv").is_file()
    assert (output_path / f"{prefix}trips.csv").is_file()
