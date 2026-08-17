"""Guards over the documentation checker (braunschweig.documentation.checks).

The decisive test runs the FULL deterministic check battery over the real
repository and requires zero FAIL findings -- a renamed test, a dead flag, an
unreachable 'active' claim, a stale generated view or an uncommitted 'committed'
reference breaks the suite instead of degrading silently (readiness-register
design generalized). WARN findings are the honest unproven states and must stay
reportable without failing.

DAG re-extraction (K4) is excluded here: it needs the full scientific stack and
minutes of configure() work; the committed-snapshot guards live in
test_documentation_dag.py and K4 runs in the CLI when synpp is available.
"""
from __future__ import annotations

import os

import pytest

from braunschweig.documentation import checks, render

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def context():
    return checks.CheckContext(REPO_ROOT, use_dag_extraction=False)


@pytest.fixture(scope="module")
def findings(context):
    return checks.run_all_checks(context)


def test_no_fail_findings(findings):
    failures = [finding for finding in findings if finding.severity == checks.FAIL]
    details = "\n".join(str(finding) for finding in failures)
    assert not failures, f"documentation check FAILs:\n{details}"


def test_registry_counts_hold_the_migration_baseline(context):
    assert len(context.features) >= 69
    assert len(context.stages) >= 114
    assert len(context.datasets) >= 52
    assert len(context.adrs) >= 76
    assert len(context.manifests) >= 20


def test_canonical_production_config_resolves(context):
    values = context.config_values
    assert values, "the canonical production config must compose in the dev env"
    assert values.get("braunschweig.population.method") == "popsim_mid"
    assert values.get("mode_choice") is False, (
        "mode_choice flipped in the canonical config -- if intentional, this is a "
        "major model change: update the registries and the ADR record first")


def test_issue_255_state_is_encoded(context):
    """The readiness branch's core discovery stays encoded until issue #255 is
    resolved: the seven legacy enrichment features must not claim production."""
    seven = {"economic_status_bayes", "household_income_distribution",
             "pt_subscription_conditioned", "driving_licence_enrichment",
             "consistent_car_availability", "income_aware_cars",
             "reactivated_person_attributes"}
    for feature in context.features:
        if feature["feature"] in seven:
            assert feature["production"]["enabled"] is False
            assert feature["pipelines"]["popsim_mid"] != "active"


def test_render_is_deterministic(context):
    first = render.render_all(context)
    second = render.render_all(context)
    assert first == second
    assert set(first) == {"STATUS.md", "PIPELINE.md", "STAGES.md", "FEATURES.md",
                          "DATA.md", "LINEAGE.md", "DECISIONS.md", "RUNS.md"}
    for text in first.values():
        assert text.startswith("<!-- THIS FILE IS GENERATED.")
