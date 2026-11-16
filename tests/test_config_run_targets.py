"""Every ``run:`` target of every committed config names a stage module that exists on disk.

A style guard. Since the fork patch ``run-target-aliases`` (tests/test_synpp_run_target_aliases.py)
an alias-only run target no longer crashes the graph build, but a target that names the stage
module reads the same in every config, whatever its ``aliases:`` say. The guard does not flag an
alias key that is also a module on disk (``synthesis.output``): there the alias wins. Package G of the
2026-11-11 repository audit deleted the inherited ``data/census/filtered.py`` while the overlay
``smoke_kreis_control_fit.yml`` still named ``data.census.filtered`` (an alias of
``braunschweig.popsim.stage``); before the patch that broke the run.
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


def test_the_run_target_checks_have_configs_to_check():
    """A moved config directory would make both globs empty and the parametrized checks vanish."""
    assert OVERLAYS and FIXTURES


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
