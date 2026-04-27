"""End-to-end MATSim build for the Braunschweig scenario.

Drives ``matsim.output`` against ``config_dryrun_braunschweig.yml``. Opt-in
via ``EQASIM_BS_RUN_PIPELINE=1`` plus the presence of the Braunschweig
input data **and** a working Java/Maven toolchain. Skipped by default so
the unit-test gate stays fast and Java-free.

Replaced the IDF region-10/11 simulation test in Phase 3.1 of the
eqasim-bs refactor (although the plan only named test_pipeline.py and
test_determinism.py, this file shared the same broken IDF fixture and is
converted in the same pass to keep the test gate green).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DRYRUN_CONFIG = REPO_ROOT / "config_dryrun_braunschweig.yml"
DATA_PATH = REPO_ROOT / "eqasim-data" / "data"
BS_DATA_PATH = DATA_PATH / "braunschweig"

OPT_IN_VAR = "EQASIM_BS_RUN_PIPELINE"


def _missing_toolchain() -> bool:
    return shutil.which("java") is None or shutil.which("mvn") is None


pytestmark = pytest.mark.skipif(
    os.environ.get(OPT_IN_VAR) != "1"
    or not BS_DATA_PATH.is_dir()
    or _missing_toolchain(),
    reason=(
        f"Set {OPT_IN_VAR}=1, provide eqasim-data/data/braunschweig/, and "
        "ensure java + mvn are on PATH to run the MATSim build "
        "(see eqasim-data/DOWNLOAD_CHECKLIST_BS.md)."
    ),
)


def test_matsim_build(tmp_path):
    """End-to-end MATSim build via the dryrun config."""
    import synpp

    with DRYRUN_CONFIG.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    config = dict(raw.get("config", {}))
    aliases = raw.get("aliases", {})

    cache_path = tmp_path / "cache"
    output_path = tmp_path / "output"
    cache_path.mkdir()
    output_path.mkdir()

    config["data_path"] = str(DATA_PATH)
    config["output_path"] = str(output_path)
    config["output_prefix"] = "bs_dryrun_"
    config["maven_skip_tests"] = True

    stages = [{"descriptor": "matsim.output"}]
    synpp.run(stages, config, working_directory=str(cache_path), aliases=aliases)

    prefix = config["output_prefix"]
    for suffix in (
        "population.xml.gz",
        "network.xml.gz",
        "households.xml.gz",
        "facilities.xml.gz",
    ):
        assert (output_path / f"{prefix}{suffix}").is_file(), (
            f"missing MATSim output: {prefix}{suffix}"
        )
