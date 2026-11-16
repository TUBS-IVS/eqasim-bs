"""Every ``run:`` target of every committed config names a stage module that exists on disk.

synpp 1.6.2 builds its ``ConfigurationContext`` from the run targets WITHOUT the config's
``aliases:`` (``synpp.pipeline.ConfigurationContext.__init__`` resolves them with an empty alias map,
and ``braunschweig.synpp_deterministic.process_stages`` passes none either). A run target that
exists only as an alias key therefore resolves to ``None`` and the run, or its dry run, aborts with
``AttributeError: 'NoneType' object has no attribute 'instance'``. Package G of the 2026-11-11
repository audit deleted the inherited ``data/census/filtered.py``; the overlay
``smoke_kreis_control_fit.yml`` still named ``data.census.filtered`` (an alias of
``braunschweig.popsim.stage``) and broke exactly this way. A run target must be the stage module
itself.
"""
from pathlib import Path

import pytest
import yaml

from braunschweig.config_compose import compose

REPO_ROOT = Path(__file__).resolve().parents[1]
BASE = REPO_ROOT / "configs" / "base_bs.yml"
OVERLAYS = sorted((REPO_ROOT / "configs" / "overlays").glob("*.yml"))
FIXTURES = sorted((REPO_ROOT / "configs" / "fixtures").glob("*.yml"))


def _module_exists(descriptor: str) -> bool:
    """True when ``descriptor`` is a first-party module file or package in the repository."""
    base = REPO_ROOT.joinpath(*descriptor.split("."))
    return base.with_suffix(".py").is_file() or (base / "__init__.py").is_file()


def run_targets_without_module(document: dict) -> list:
    """The run targets of ``document`` that do not name an existing stage module."""
    targets = []
    for item in document.get("run") or []:
        targets.append(next(iter(item)) if isinstance(item, dict) else item)
    return [target for target in targets if not _module_exists(target)]


def test_the_check_flags_a_run_target_that_exists_only_as_an_alias():
    """Positive control: the overlay's former target is an alias key whose module is gone."""
    document = {"run": ["data.census.filtered", {"braunschweig.popsim.stage": {}}],
                "aliases": {"data.census.filtered": "braunschweig.popsim.stage"}}
    assert run_targets_without_module(document) == ["data.census.filtered"]


@pytest.mark.parametrize("overlay", OVERLAYS, ids=lambda path: path.name)
def test_every_overlay_run_target_is_a_module(overlay):
    assert run_targets_without_module(compose(str(BASE), str(overlay))) == []


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda path: path.name)
def test_every_fixture_run_target_is_a_module(fixture):
    with open(fixture, encoding="utf-8") as handle:
        document = yaml.safe_load(handle) or {}
    assert run_targets_without_module(document) == []
