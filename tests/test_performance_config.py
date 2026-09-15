"""Resolved performance switches must remain explicit and independently reversible."""
from pathlib import Path

import pytest
import yaml

from braunschweig.config_compose import compose

ROOT = Path(__file__).resolve().parents[1]
FLAGS = (
    "braunschweig.performance.fleet_home_lookup",
    "braunschweig.performance.home_coordinates",
    "braunschweig.performance.plan_validation",
    "braunschweig.performance.donor_matching",
    "cache_share_metadata",
)


@pytest.mark.parametrize("overlay", ["test_1pct.yml", "test_25pct.yml", "test_100pct.yml"])
def test_performance_defaults_and_explicit_off(overlay, tmp_path):
    base, scale = ROOT / "configs/base_bs.yml", ROOT / "configs/overlays" / overlay
    config = compose(str(base), str(scale))["config"]
    assert {flag: config.get(flag) for flag in FLAGS} == dict.fromkeys(FLAGS, True)
    off = tmp_path / "off.yml"
    off_overlay = yaml.safe_load(scale.read_text(encoding="utf-8"))
    off_overlay["config"].update(dict.fromkeys(FLAGS, False))
    off.write_text(yaml.safe_dump(off_overlay), encoding="utf-8")
    disabled = compose(str(base), str(off))["config"]
    assert {flag: disabled[flag] for flag in FLAGS} == dict.fromkeys(FLAGS, False)
    assert {key: value for key, value in disabled.items() if key not in FLAGS} == {
        key: value for key, value in config.items() if key not in FLAGS}


def test_cache_reuse_keeps_scientifically_distinct_profiles_separate():
    configs = [compose(str(ROOT / "configs/base_bs.yml"),
                       str(ROOT / "configs/overlays" / name))["config"]
               for name in ("test_1pct.yml", "test_25pct.yml", "test_100pct.yml")]
    work = "braunschweig.population.popsim.work_dir"
    importance = "braunschweig.population.popsim.importance_profile"
    assert configs[0][work] == configs[1][work]
    assert configs[2][work] != configs[0][work]
    assert configs[0].get(importance, "uniform") == configs[1].get(importance, "uniform") == "uniform"
    assert configs[2][importance] == "optimized_2026_06_30"
