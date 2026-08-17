"""Guards for the TASK-010 (Kreis x hh_size x employed) IPF margin (issue #252).

Background
----------
``braunschweig.ipf.use_employment_margin`` gates an ADDITIONAL joint margin that
needs a real Kreis-level cross-tab supplied through
``braunschweig.ipf.employment_by_hhsize_path``. That path was never set in any
committed config, so the flag -- ``true`` in nine committed configs -- always hit
a fallback that synthesised the joint targets as the OUTER PRODUCT of the
existing employment and hh_size marginals.

The fallback described itself as "a pure marginal-consistency check that does not
add information beyond what the existing IPF already enforces". That is false,
and ``test_outer_product_joint_destroys_the_seed_correlation`` below is the
executable proof: pinning every joint cell to the product of its marginals adds
exactly one constraint that the two marginals do NOT imply -- statistical
independence of employment and household size -- and the IPF then overwrites the
donor seed's correlation with it. All base margins stay satisfied throughout,
which is why the post-IPF margin check could never catch it.

ADR-0080 records the decision to remove the proxy and park the flag.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from braunschweig.ipf.model import (  # noqa: E402
    load_employment_by_hhsize_targets, run_ipf_iterations)

FIXTURES = REPO_ROOT / "configs" / "fixtures"
BASE_CONFIG = REPO_ROOT / "configs" / "base_bs.yml"

FLAG_KEY = "braunschweig.ipf.use_employment_margin"
PATH_KEY = "braunschweig.ipf.employment_by_hhsize_path"
FILTERED_ALIAS_KEY = "data.census.filtered"
IPF_PRODUCER = "braunschweig.ipf.attributed"


# ---------------------------------------------------------------------------
# Loader contract: no usable cross-tab is a hard error, never a substitute
# ---------------------------------------------------------------------------

class TestLoadEmploymentByHhsizeTargets:
    """The primary method is the only method: an absent cross-tab must raise."""

    def test_unset_path_raises_and_names_both_config_keys(self, tmp_path):
        with pytest.raises(RuntimeError) as excinfo:
            load_employment_by_hhsize_targets(None, str(tmp_path))
        message = str(excinfo.value)
        assert PATH_KEY in message
        assert FLAG_KEY in message

    def test_missing_file_raises_and_names_the_resolved_path(self, tmp_path):
        with pytest.raises(RuntimeError) as excinfo:
            load_employment_by_hhsize_targets("absent_crosstab.csv", str(tmp_path))
        message = str(excinfo.value)
        assert "absent_crosstab.csv" in message
        assert PATH_KEY in message

    def test_missing_columns_raise(self, tmp_path):
        csv_path = tmp_path / "crosstab.csv"
        csv_path.write_text(
            "departement_id,hh_size,weight\n03101,1,100.0\n", encoding="utf-8")
        with pytest.raises(RuntimeError) as excinfo:
            load_employment_by_hhsize_targets("crosstab.csv", str(tmp_path))
        assert "employed" in str(excinfo.value)

    def test_valid_crosstab_loads_with_normalised_dtypes(self, tmp_path):
        csv_path = tmp_path / "crosstab.csv"
        csv_path.write_text(
            "departement_id,hh_size,employed,weight\n"
            "03101,1,True,300.0\n"
            "03101,1,False,700.0\n",
            encoding="utf-8",
        )
        df = load_employment_by_hhsize_targets("crosstab.csv", str(tmp_path))
        assert len(df) == 2
        # Kreis codes keep their leading zero, hh_size stays categorical-as-str,
        # employed becomes a real bool so the selector key matches df_model.
        assert df["departement_id"].tolist() == ["03101", "03101"]
        assert df["hh_size"].tolist() == ["1", "1"]
        assert df["employed"].tolist() == [True, False]

    def test_absolute_path_is_not_joined_to_data_path(self, tmp_path):
        csv_path = tmp_path / "crosstab.csv"
        csv_path.write_text(
            "departement_id,hh_size,employed,weight\n03101,1,True,300.0\n",
            encoding="utf-8",
        )
        df = load_employment_by_hhsize_targets(
            str(csv_path), str(tmp_path / "unused_data_path"))
        assert len(df) == 1


# ---------------------------------------------------------------------------
# Config guards: the flag may not be on without the cross-tab it needs
# ---------------------------------------------------------------------------

def _load(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _run_configs() -> list[Path]:
    """Every committed run config, plus the canonical production base.

    Deliberately scans ``configs/fixtures/`` only for the fixtures: root-level
    ``config_local_*.yml`` files are gitignored, so globbing the repository root
    would make this guard's coverage depend on the machine it runs on.
    """
    return sorted(FIXTURES.glob("*.yml")) + [BASE_CONFIG]


ALL_CONFIGS = _run_configs()
IPF_CONFIGS = [
    p for p in ALL_CONFIGS
    if (_load(p).get("aliases") or {}).get(FILTERED_ALIAS_KEY) == IPF_PRODUCER
]
POPSIM_CONFIGS = [p for p in ALL_CONFIGS if p not in IPF_CONFIGS]


def test_config_discovery_is_not_vacuous() -> None:
    """Guard the guard: an empty parametrisation would pass without asserting."""
    assert IPF_CONFIGS, "No config with the IPF population producer was discovered."
    assert POPSIM_CONFIGS, "No config with the popsim producer was discovered."


@pytest.mark.parametrize("config_path", ALL_CONFIGS, ids=lambda p: p.name)
def test_flag_is_never_enabled_without_a_crosstab(config_path: Path) -> None:
    """``use_employment_margin: true`` without a path is now a run-time error.

    Committing that combination would ship a config that cannot execute, so the
    guard rejects it here rather than at run time.
    """
    config = _load(config_path).get("config") or {}
    if config.get(FLAG_KEY) is True:
        assert config.get(PATH_KEY), (
            f"{config_path.name} sets {FLAG_KEY}: true but no {PATH_KEY}. "
            f"braunschweig.ipf.model raises on that combination since ADR-0080 "
            f"(the outer-product substitute it used to build imposed an "
            f"unsupported independence assumption)."
        )


@pytest.mark.parametrize("config_path", POPSIM_CONFIGS, ids=lambda p: p.name)
def test_popsim_configs_do_not_carry_the_inert_flag(config_path: Path) -> None:
    """Under the popsim methods ``braunschweig.ipf.model`` is off the DAG.

    It appears in the ``simple_ipf_open`` snapshot only, so both IPF keys are
    unreadable there and carrying them would be a dead key -- the defect class
    issue #251 / ADR-0078 removed.
    """
    config = _load(config_path).get("config") or {}
    assert FLAG_KEY not in config, (
        f"{config_path.name} sets {FLAG_KEY}, but braunschweig.ipf.model is not "
        f"on the popsim DAG -- that is a dead key."
    )
    assert PATH_KEY not in config, (
        f"{config_path.name} sets {PATH_KEY}, but braunschweig.ipf.model is not "
        f"on the popsim DAG -- that is a dead key."
    )


# ---------------------------------------------------------------------------
# Scientific pin: why the outer-product proxy had to go
# ---------------------------------------------------------------------------

class TestOuterProductJointIsNotNeutral:
    """The removed fallback claimed to add no information. It added independence.

    Minimal faithful replica of the constraint set for one Kreis:
    cells are ``(hh_size, employed)`` for ``hh_size in {1, 2}``; the base margins
    are the unconditional Kreis employment total and the per-hh_size person
    totals; the TASK-010 block adds the joint cells.

    The seed numbers are an ASSUMPTION chosen to make the structural question
    decidable (1-person households employed at a lower rate than 2-person ones);
    they are not measured data and no reference value is claimed. The conclusion
    does not depend on them: pinning every joint cell to a product of marginals
    forces the odds ratio to exactly 1 for any seed.
    """

    # cell order: (hh1, employed), (hh1, non), (hh2, employed), (hh2, non)
    SEED = np.array([300.0, 700.0, 1400.0, 600.0])
    SEL_EMPLOYED_TOTAL = np.array([0, 2])
    SEL_HH1 = np.array([0, 1])
    SEL_HH2 = np.array([2, 3])
    SEL_JOINT_CELLS = [np.array([0]), np.array([1]), np.array([2]), np.array([3])]

    TARGET_EMPLOYED_TOTAL = 1800.0
    TARGET_HH1_PERSONS = 1000.0
    TARGET_HH2_PERSONS = 2000.0

    @staticmethod
    def _odds_ratio(weights: np.ndarray) -> float:
        return (weights[0] * weights[3]) / (weights[1] * weights[2])

    @staticmethod
    def _employment_rate_by_size(weights: np.ndarray) -> tuple[float, float]:
        return (weights[0] / (weights[0] + weights[1]),
                weights[2] / (weights[2] + weights[3]))

    def _outer_product_targets(self) -> list[float]:
        """Verbatim arithmetic of the removed fallback."""
        total_persons = self.TARGET_HH1_PERSONS + self.TARGET_HH2_PERSONS
        targets: list[float] = []
        for persons_in_size in (self.TARGET_HH1_PERSONS, self.TARGET_HH2_PERSONS):
            share = persons_in_size / total_persons
            employed_in_size = self.TARGET_EMPLOYED_TOTAL * share
            targets.append(employed_in_size)
            targets.append(persons_in_size - employed_in_size)
        return targets

    def _run(self, selectors, targets):
        weights, _iteration, converged, _factors = run_ipf_iterations(
            selectors, targets, self.SEED.copy(),
            max_iterations=5000, tolerance=1e-10, batched=False,
            log=lambda *args, **kwargs: None,
        )
        assert converged
        return weights

    @property
    def _base(self):
        return ([self.SEL_EMPLOYED_TOTAL, self.SEL_HH1, self.SEL_HH2],
                [self.TARGET_EMPLOYED_TOTAL, self.TARGET_HH1_PERSONS,
                 self.TARGET_HH2_PERSONS])

    def test_base_margins_alone_preserve_the_seed_correlation(self):
        """Without the joint block the IPF keeps the donor's odds ratio."""
        selectors, targets = self._base
        weights = self._run(selectors, targets)
        assert self._odds_ratio(weights) == pytest.approx(
            self._odds_ratio(self.SEED), rel=1e-9)

    def test_outer_product_joint_destroys_the_seed_correlation(self):
        """With it, the odds ratio is forced to exactly 1 -- independence."""
        selectors, targets = self._base
        weights = self._run(selectors + self.SEL_JOINT_CELLS,
                            targets + self._outer_product_targets())
        assert self._odds_ratio(weights) == pytest.approx(1.0, rel=1e-9)
        rate_hh1, rate_hh2 = self._employment_rate_by_size(weights)
        assert rate_hh1 == pytest.approx(rate_hh2, rel=1e-9)

    def test_the_distortion_is_invisible_to_the_base_margin_check(self):
        """Every base margin still matches, which is why this went unnoticed.

        The post-IPF margin validation in ``braunschweig.ipf.model`` compares
        achieved cell sums to targets; under the proxy they all agree, so no
        existing guard could have flagged the fabricated joint structure.
        """
        selectors, targets = self._base
        weights = self._run(selectors + self.SEL_JOINT_CELLS,
                            targets + self._outer_product_targets())
        assert weights[self.SEL_EMPLOYED_TOTAL].sum() == pytest.approx(
            self.TARGET_EMPLOYED_TOTAL)
        assert weights[self.SEL_HH1].sum() == pytest.approx(self.TARGET_HH1_PERSONS)
        assert weights[self.SEL_HH2].sum() == pytest.approx(self.TARGET_HH2_PERSONS)
