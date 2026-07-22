"""Guard the Tier A + B1 caching config conventions on the composed all-features
configs (base + scale overlays):

- test_1pct and test_25pct MUST pin the SAME fixed PopulationSim ``work_dir``
  (Tier B1), so ``braunschweig.popsim.stage`` hashes identically across
  sampling rates and the cache_share store can share the donor build + the
  per-1km batches;
- ALL scale overlays MUST list the full set of confirmed-shareable stages in
  ``cache_share_stages`` (Tier A + freight chain + Tier B), so they are primed
  from / exported to the shared store instead of being recomputed every run.

See docs/superpowers/specs/2026-06-22-tier-a-b-caching-design.md.
"""
from pathlib import Path

import pytest

from braunschweig.config_compose import compose

REPO_ROOT = Path(__file__).resolve().parents[1]
BASE = str(REPO_ROOT / "configs" / "base_bs.yml")
OVERLAYS = REPO_ROOT / "configs" / "overlays"

SCALE_OVERLAYS = ["test_1pct.yml", "test_25pct.yml", "test_100pct.yml"]

# Tier B1: one fixed work_dir shared by the 1pct/25pct run configs (NOT a per-cache scratch path).
SHARED_WORK_DIR = "eqasim-data/popsim_work_allfeat"
WORK_DIR_KEY = "braunschweig.population.popsim.work_dir"

# Tier A: the 32 verified sampling- AND path-independent stages (identical hash at
# 1% and 25%), plus Tier B1's popsim.stage and its trivial upstream distance build.
EXPECTED_SHAREABLE_STAGES = {
    "braunschweig.data.buildings",
    "braunschweig.data.census.employees",
    "braunschweig.data.census.employment",
    "braunschweig.data.cordon_network",
    "braunschweig.data.cordon_pt_gates",
    "braunschweig.data.locations",
    "braunschweig.data.mid.zones",
    "braunschweig.data.schools.kita_facilities",
    "braunschweig.data.schools.university_facilities",
    "braunschweig.freight.extraction",
    "braunschweig.freight.trips",
    "braunschweig.locations.secondary",
    "braunschweig.locations.work",
    "braunschweig.synthesis.cordon_gates",
    "data.gtfs.cleaned",
    "data.hts.entd.cleaned",
    "data.hts.entd.filtered",
    "data.hts.entd.reweighted",
    "data.hts.selected",
    "data.osm.cleaned",
    "data.spatial.departments",
    "data.spatial.municipalities",
    "eqasim_common.data.osm.chunked",
    "eqasim_common.data.osm.locations",
    "eqasim_common.data.spatial.iris",
    "eqasim_common.gravity.distance_matrix",
    "eqasim_common.locations.education",
    "eqasim_common.spatial.codes",
    "eqasim_common.spatial.entd_codes",
    "matsim.scenario.supply.gtfs",
    "matsim.scenario.supply.osm",
    "matsim.scenario.supply.processed",
    # Tier B1:
    "braunschweig.popsim.stage",
    "braunschweig.popsim.distance_distributions",
    # Tier B2:
    "braunschweig.popsim.completed_donor",
}


def _cfg(overlay_name):
    return compose(BASE, str(OVERLAYS / overlay_name))["config"]


def test_1pct_and_25pct_pin_the_shared_work_dir():
    for overlay in ("test_1pct.yml", "test_25pct.yml"):
        cfg = _cfg(overlay)
        assert cfg[WORK_DIR_KEY] == SHARED_WORK_DIR, (
            f"{overlay}: {WORK_DIR_KEY} must be the shared fixed path "
            f"{SHARED_WORK_DIR!r} (Tier B1), got {cfg[WORK_DIR_KEY]!r}"
        )


@pytest.mark.parametrize("overlay", SCALE_OVERLAYS)
def test_scale_configs_list_all_shareable_stages(overlay):
    stages = set(_cfg(overlay).get("cache_share_stages", []))
    missing = EXPECTED_SHAREABLE_STAGES - stages
    assert not missing, f"{overlay}: cache_share_stages missing {sorted(missing)}"
