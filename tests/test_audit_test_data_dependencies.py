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

_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts",
                       "audit_test_data_dependencies.py")


def _load():
    spec = importlib.util.spec_from_file_location("audit_data_deps", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_failing_test_that_probed_an_absent_input_is_a_false_red():
    audit = _load()

    classified = audit.classify([
        ("failed", "tests/test_x.py::test_a", ["eqasim-data/data/braunschweig/x.csv"]),
    ])

    assert classified[audit.CLASS_FALSE_RED] == [
        ("tests/test_x.py::test_a", ["eqasim-data/data/braunschweig/x.csv"])]


def test_a_passing_test_that_probed_an_absent_input_is_a_vacuous_green_candidate():
    audit = _load()

    classified = audit.classify([
        ("passed", "tests/test_x.py::test_b", ["eqasim-data/data/braunschweig/y.csv"]),
    ])

    assert [entry[0] for entry in classified[audit.CLASS_CANDIDATE]] == \
        ["tests/test_x.py::test_b"]


def test_a_skipped_test_has_declared_its_dependency():
    audit = _load()

    classified = audit.classify([
        ("skipped", "tests/test_x.py::test_c", ["eqasim-data/data/braunschweig/z.csv"]),
    ])

    assert [entry[0] for entry in classified[audit.CLASS_DECLARED]] == \
        ["tests/test_x.py::test_c"]


def test_a_test_that_probed_nothing_absent_is_not_classified_at_all():
    """The audit must not turn every passing test into a finding."""
    audit = _load()

    classified = audit.classify([("passed", "tests/test_x.py::test_d", [])])

    assert all(not entries for entries in classified.values())


def test_an_intentionally_absent_probe_path_is_recognised_as_a_negative_test():
    """Tests that assert "a missing input raises" point at a path that is absent BY
    DESIGN; counting those as findings would bury the real ones."""
    audit = _load()

    classified = audit.classify([
        ("passed", "tests/test_x.py::test_missing_csv_raises",
         ["eqasim-data/data/_does_not_exist/table.csv"]),
    ])

    assert classified[audit.CLASS_CANDIDATE] == []
    assert [entry[0] for entry in classified[audit.CLASS_BY_DESIGN]] == \
        ["tests/test_x.py::test_missing_csv_raises"]
