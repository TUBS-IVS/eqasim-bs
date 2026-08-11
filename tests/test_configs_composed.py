# tests/test_configs_composed.py
"""Contract tests on the composed production configs (base + each overlay).

Guards against the two failure classes that motivated the composition redesign:
(1) a stale PopulationSim settings file (the float-seed ~20x slowdown, ADR-0056)
being wired into any scale again, and (2) feature blocks drifting apart between
scales (the 100pct config once silently lost the smart-'other' block).
"""
from pathlib import Path

import pytest

from braunschweig.config_compose import compose

REPO_ROOT = Path(__file__).resolve().parents[1]
BASE = REPO_ROOT / "configs" / "base_bs.yml"
OVERLAYS = REPO_ROOT / "configs" / "overlays"

ALL_OVERLAYS = ["test.yml", "test_matsim.yml", "test_1pct.yml", "test_25pct.yml",
                "test_100pct.yml"]
SCALE_OVERLAYS = ["test_1pct.yml", "test_25pct.yml", "test_100pct.yml"]

# Every feature flag the base must switch ON for every scale (drift guard).
FEATURE_FLAGS_ON = [
    "freight_enabled", "work_building_potentials", "secondary_building_potentials",
    "secondary_other_smart_potential", "secondary_distance_by_purpose",
    "secondary_shop_daily_split", "secondary_leisure_subtype_split",
    "secondary_other_subtype_split", "leisure_visit_building_potential",
    "education_building_distribution", "education_gravity_enabled",
    "fleet_model_enabled", "fleet_model_brands", "fleet_hsn_tsn_attributes",
    "cordon_enabled", "enable_urban_parking", "remode_carless_car_legs",
    "braunschweig.home_density_weighting",
    "braunschweig.population.popsim.income_spatial_tilt",
    "braunschweig.population.popsim.income_kreis_control",
    "escort_purpose", "escort_household_link", "escort_distance_by_type",
]


def _cfg(overlay_name):
    return compose(str(BASE), str(OVERLAYS / overlay_name))


# Global ceiling that keeps the per-batch PopulationSim pipeline.h5 under HDF5's VLArray
# write limit. A single dense Kreis (03101) at 3000 cells/batch produced a ~23 GB h5 that
# raised "OverflowError: value too large to convert to int". 750 is the value the 100% run
# proved safe on every Kreis (~9 GB h5, smaller still under the int-seed regime). This guard
# fails CI if any overlay re-introduces a too-large max_cells, so the overflow cannot recur.
MAX_CELLS_H5_SAFE_CEILING = 750


@pytest.mark.parametrize("overlay", ALL_OVERLAYS)
def test_max_cells_within_h5_overflow_ceiling(overlay):
    mc = _cfg(overlay)["config"].get("braunschweig.population.popsim.max_cells")
    assert mc is not None, f"{overlay} must set max_cells (per-scale key)"
    assert mc <= MAX_CELLS_H5_SAFE_CEILING, (
        f"{overlay} max_cells={mc} exceeds the {MAX_CELLS_H5_SAFE_CEILING} pipeline.h5 "
        f"VLArray-overflow ceiling (a dense Kreis at 3000 overflowed at ~23 GB). Lower it, "
        f"or re-validate the per-batch h5 size on the densest Kreis before raising this.")


@pytest.mark.parametrize("overlay", ALL_OVERLAYS)
def test_settings_path_is_the_intseed_numba_regime(overlay):
    cfg = _cfg(overlay)["config"]
    assert cfg["braunschweig.population.popsim.settings_path"].endswith(
        "settings_tier3_mef100_intseed_numba.yaml"), (
        "ADR-0056 regime not wired -- the float-seed trap must never recur")


@pytest.mark.parametrize("overlay", ALL_OVERLAYS)
def test_all_feature_flags_on_everywhere(overlay):
    cfg = _cfg(overlay)["config"]
    for flag in FEATURE_FLAGS_ON:
        assert cfg.get(flag) is True, f"{flag} not ON in composed {overlay}"
    assert cfg["braunschweig.population.method"] == "popsim_mid"
    assert cfg["braunschweig.population.popsim.control_tiers"] == "tier0,tier1,tier2,tier3"
    assert cfg["braunschweig.population.popsim.employment_grid"] == "on"
    assert cfg["vehicles_method"] == "household"
    assert cfg["fleet_electric_calibration"] == "kreis_mix_gemeinde_bev_tilt"


@pytest.mark.parametrize("overlay", ALL_OVERLAYS)
def test_every_overlay_has_distinct_working_and_output_dirs(overlay):
    doc = _cfg(overlay)
    assert doc["working_directory"]
    assert doc["config"]["output_path"]


def test_working_directories_are_pairwise_distinct():
    dirs = [_cfg(o)["working_directory"] for o in ALL_OVERLAYS]
    assert len(set(dirs)) == len(dirs)


@pytest.mark.parametrize("overlay", SCALE_OVERLAYS)
def test_scale_overlays_run_full_pipeline_with_zero_iterations(overlay):
    doc = _cfg(overlay)
    for stage in ["synthesis.output", "matsim.output",
                  "braunschweig.analysis.cordon_validation",
                  "braunschweig.analysis.simwrapper_export",
                  "braunschweig.analysis.analysis_suite",
                  "braunschweig.analysis.verbindungen_validation"]:
        assert stage in doc["run"], f"{stage} missing from {overlay}"
    assert doc["config"]["matsim_last_iteration"] == 0


def test_scale_knobs_per_overlay():
    c1 = _cfg("test_1pct.yml")["config"]
    c25 = _cfg("test_25pct.yml")["config"]
    c100 = _cfg("test_100pct.yml")["config"]
    assert (c1["sampling_rate"], c25["sampling_rate"], c100["sampling_rate"]) == (0.01, 0.25, 1.0)
    assert c100["braunschweig.population.popsim.max_cells"] == 750
    assert c100["braunschweig.population.popsim.num_workers"] == 3
    assert c100["braunschweig.population.popsim.importance_profile"] == "optimized_2026_06_30"
    assert c100["braunschweig.population.popsim.work_dir"] == "eqasim-data/popsim_work_allfeat_opt"


@pytest.mark.parametrize("overlay", ["test.yml", "test_matsim.yml"])
def test_test_overlays_are_isolated_and_never_seed_the_store(overlay):
    cfg = _cfg(overlay)["config"]
    assert cfg["cache_share_export"] is False
    assert cfg["braunschweig.population.popsim.work_dir"] == "eqasim-data/popsim_work_test"
    assert cfg["braunschweig.political_prefix"] == ["03101"]   # list REPLACED, not merged


def test_test_overlay_is_synthesis_only():
    assert _cfg("test.yml")["run"] == ["synthesis.output"]


def test_test_matsim_overlay_runs_matsim_with_zero_iterations():
    doc = _cfg("test_matsim.yml")
    assert doc["run"] == ["matsim.output"]
    assert doc["config"]["matsim_last_iteration"] == 0


@pytest.mark.parametrize("overlay", ALL_OVERLAYS)
def test_aliases_present_in_composed_config(overlay):
    doc = _cfg(overlay)
    assert doc["aliases"]["data.census.filtered"] == "braunschweig.popsim.stage"
    assert doc["aliases"]["matsim.simulation.prepare"] == "braunschweig.matsim.simulation.prepare"


def test_base_config_is_free_of_per_scale_keys():
    """The base MUST NOT carry any per-scale key -- those live only in overlays.
    A per-scale key re-introduced into the base would let a scale silently inherit
    it (drift) instead of the overlay owning it; this locks the feature's anti-drift
    guarantee at the source, not just at the resolved (base+overlay) config."""
    import yaml
    with open(BASE, encoding="utf-8") as f:
        base = yaml.safe_load(f)
    assert "working_directory" not in base, "base must not set working_directory (overlay-only)"
    assert "run" not in base, "base must not set run (overlay-only)"
    cfg = base.get("config", {})
    per_scale_keys = [
        "sampling_rate", "output_path", "analysis_working_directory", "output_prefix",
        "matsim_last_iteration", "cache_share_export", "cache_share_recompute",
        "braunschweig.population.popsim.work_dir",
        "braunschweig.population.popsim.max_cells",
        "braunschweig.population.popsim.num_workers",
        "braunschweig.population.popsim.importance_profile",
    ]
    leaked = [k for k in per_scale_keys if k in cfg]
    assert not leaked, f"per-scale keys leaked into base_bs.yml (belong in overlays): {leaked}"
