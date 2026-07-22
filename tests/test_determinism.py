"""Determinism gate for the Braunschweig synthesis pipeline.

Runs the 0.1% dryrun config twice in independent caches and compares the
SHA-256 of the canonical CSV outputs. Like ``test_pipeline.py`` this is
opt-in via ``EQASIM_BS_RUN_PIPELINE=1`` and the presence of the
Braunschweig input data.

Replaced the IDF region-10/11 determinism reference in Phase 3.1 of the
eqasim-bs refactor.
"""

from __future__ import annotations

import hashlib
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
        "to run the determinism integration test "
        "(see eqasim-data/DOWNLOAD_CHECKLIST_BS.md)."
    ),
)


def _hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_dryrun_config():
    with DRYRUN_CONFIG.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _run_pipeline(tmp_path: Path, run_index: int) -> dict[str, str]:
    import synpp

    raw = _load_dryrun_config()
    config = dict(raw.get("config", {}))
    aliases = raw.get("aliases", {})

    cache_path = tmp_path / f"cache_{run_index}"
    output_path = tmp_path / f"output_{run_index}"
    cache_path.mkdir()
    output_path.mkdir()

    config["data_path"] = str(DATA_PATH)
    config["output_path"] = str(output_path)
    config["output_prefix"] = "bs_dryrun_"

    stages = [{"descriptor": s} for s in raw.get("run", ["synthesis.output"])]
    synpp.run(stages, config, working_directory=str(cache_path), aliases=aliases)

    prefix = config["output_prefix"]
    targets = [f"{prefix}{name}.csv" for name in ("households", "persons", "activities", "trips")]
    return {name: _hash_file(output_path / name) for name in targets}


def test_determinism(tmp_path):
    """Two independent runs of the dryrun config must produce identical CSVs."""
    first = _run_pipeline(tmp_path, 0)
    second = _run_pipeline(tmp_path, 1)
    assert first == second, (
        "Non-deterministic pipeline output:\n"
        f"  first  = {first}\n"
        f"  second = {second}"
    )
