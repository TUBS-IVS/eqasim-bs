"""The equivalence harness must detect scalar type drift and keep inputs read-only."""
from pathlib import Path
import runpy
import subprocess
import sys

import numpy as np
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/benchmark_performance_equivalence.py"
assert_exact = runpy.run_path(str(SCRIPT))["assert_exact"]


@pytest.mark.parametrize("actual,expected", [
    (np.int32(1), np.int64(1)),
    (True, 1),
    (1, 1.0),
])
def test_donor_tuple_comparison_rejects_equal_values_with_different_scalar_types(actual, expected):
    with pytest.raises(AssertionError):
        assert_exact([(actual, 2, 0)], [(expected, 2, 0)])


def test_donor_tuple_comparison_accepts_equal_scalar_types_and_values():
    assert_exact([(np.int64(1), 2, False)], [(np.int64(1), 2, False)])


def test_worker_does_not_write_bytecode_into_a_fresh_checkout(tmp_path):
    checkout = tmp_path / "source"
    package = checkout / "braunschweig/popsim"
    package.mkdir(parents=True)
    (checkout / "braunschweig/__init__.py").write_text("", encoding="utf-8")
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "plan_validation.py").write_text(
        "from dataclasses import dataclass\n"
        "@dataclass\n"
        "class Report:\n"
        "    count: int\n"
        "class PlanValidator:\n"
        "    def validate_trips(self, trips):\n"
        "        return Report(len(trips))\n",
        encoding="utf-8",
    )
    output = tmp_path / "result.pickle"
    subprocess.run([
        sys.executable, str(SCRIPT), "--mode", "original", "--case", "validation",
        "--repository-root", str(checkout), "--size", "1", "--repeat", "1",
        "--output", str(output),
    ], check=True, capture_output=True, text=True)
    assert output.is_file()
    assert not list(checkout.rglob("__pycache__"))
