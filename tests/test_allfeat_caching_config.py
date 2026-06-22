"""Guard the Tier A + B1 caching config conventions on the two all-features
server configs:

- both configs MUST pin the SAME fixed PopulationSim ``work_dir`` (Tier B1), so
  ``braunschweig.popsim.stage`` hashes identically across sampling rates and the
  cache_share store can share the donor build + the per-1km batches;
- both configs MUST list the full set of confirmed-shareable stages in
  ``cache_share_stages`` (Tier A + B1), so they are primed from / exported to the
  shared store instead of being recomputed every run.

See docs/superpowers/specs/2026-06-22-tier-a-b-caching-design.md.
"""
import yaml

CONFIGS = [
    "config_server_braunschweig_1pct_allfeat_popsim.yml",
    "config_server_braunschweig_25pct_allfeat_popsim.yml",
]

# Tier B1: one fixed work_dir shared by all run configs (NOT a per-cache scratch path).
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


def _load_cfg(path):
    with open(path, encoding="utf-8") as f:
        return (yaml.safe_load(f) or {}).get("config", {}) or {}


def test_both_configs_pin_the_shared_work_dir():
    for path in CONFIGS:
        cfg = _load_cfg(path)
        assert cfg[WORK_DIR_KEY] == SHARED_WORK_DIR, (
            f"{path}: {WORK_DIR_KEY} must be the shared fixed path "
            f"{SHARED_WORK_DIR!r} (Tier B1), got {cfg[WORK_DIR_KEY]!r}"
        )


def test_both_configs_list_all_shareable_stages():
    for path in CONFIGS:
        cfg = _load_cfg(path)
        listed = set(cfg.get("cache_share_stages", []))
        missing = EXPECTED_SHAREABLE_STAGES - listed
        assert not missing, f"{path}: cache_share_stages missing {sorted(missing)}"
