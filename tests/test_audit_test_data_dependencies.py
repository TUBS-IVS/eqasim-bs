"""The classifier of the test-data-dependency audit (scripts/audit_test_data_dependencies.py).

The audit exists because a test that reads a local-only (gitignored) input without
declaring that dependency misleads in one of two directions: it reports a FALSE RED
on every checkout without the data drop -- which reads like a code regression and
cost a full investigation once -- or it passes while the code under test silently
fell back, in which case the green says nothing about the method the test claims to
cover.

Only the classification is unit-tested here; the recording half is a pytest plugin
and is exercised by running it over the suite (see
docs/codebase/notes/test-data-dependencies.md).
"""
from __future__ import annotations

import importlib.util
import os

import pytest

_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts",
                       "audit_test_data_dependencies.py")


def _load():
    spec = importlib.util.spec_from_file_location("audit_data_deps", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_LOCAL_ONLY_INPUT = "eqasim-data/data/braunschweig/x.csv"


@pytest.mark.parametrize("outcome, test_id, probes, expected_class", [
    pytest.param("failed", "tests/test_x.py::test_a", [_LOCAL_ONLY_INPUT], "CLASS_FALSE_RED",
                 id="failing-test-that-probed-an-absent-input-is-a-false-red"),
    pytest.param("passed", "tests/test_x.py::test_b", [_LOCAL_ONLY_INPUT], "CLASS_CANDIDATE",
                 id="passing-test-that-probed-an-absent-input-is-a-vacuous-green-candidate"),
    pytest.param("skipped", "tests/test_x.py::test_c", [_LOCAL_ONLY_INPUT], "CLASS_DECLARED",
                 id="skipped-test-has-declared-its-dependency"),
    # The audit must not turn every passing test into a finding.
    pytest.param("passed", "tests/test_x.py::test_d", [], None,
                 id="test-that-probed-nothing-absent-is-not-classified"),
    # Tests that assert "a missing input raises" point at a path that is absent BY DESIGN;
    # counting those as findings would bury the real ones.
    pytest.param("passed", "tests/test_x.py::test_missing_csv_raises",
                 ["eqasim-data/data/_does_not_exist/table.csv"], "CLASS_BY_DESIGN",
                 id="intentionally-absent-probe-path-is-a-negative-test"),
])
def test_classify_puts_each_outcome_into_exactly_one_class(outcome, test_id, probes, expected_class):
    audit = _load()

    classified = audit.classify([(outcome, test_id, probes)])

    expected = getattr(audit, expected_class) if expected_class else None
    for name, entries in classified.items():
        assert [entry[0] for entry in entries] == ([test_id] if name == expected else []), name
    if expected_class == "CLASS_FALSE_RED":
        assert classified[audit.CLASS_FALSE_RED] == [(test_id, probes)]
