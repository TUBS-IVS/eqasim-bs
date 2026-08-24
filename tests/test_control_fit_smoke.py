"""CI-sized control smoke (issue #282).

The per-run control-fit check used to be ad hoc: a control defect surfaced only in a full
synthesis, hours in, on the server. These tests exercise the reusable checks in
``braunschweig.analysis.population_validation.control_fit_smoke`` against the ACTIVE
production catalog, so the class of defect that a real run would expose late is caught here
instead. They deliberately do NOT run PopulationSim (it lives in a separate uv project and
is invoked as a subprocess), so what is verified is the CONTROL SPECIFICATION, not the
balancer's numerical result -- the region-scoped smoke config covers that half.

Concretely these checks would have caught, during this session:
  * a Kreis control whose category predicates do not partition the seed universe (a person
    counted twice, or not at all, silently shrinks the control total);
  * a control whose census_source column is neither in the cell parquet nor produced by the
    aggregation map (PopulationSim raises "<field> not in index" only at run time);
  * a target table that does not sum to 1 per Kreis, or misses a Kreis entirely.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from braunschweig.analysis.population_validation import control_fit_smoke as cfs
from braunschweig.popsim import control_spec as cs
from braunschweig.popsim import kreis_attribute_control as kac

DATA_PATH = "eqasim-data/data"
ZGB_KREISE = ("03101", "03102", "03103", "03151", "03153", "03154", "03157", "03158")


def _seed_persons() -> pd.DataFrame:
    """A tiny synthetic MiD-shaped seed carrying every column the person controls read.

    Values span the valid code ranges AND the imputed/coverage codes, because a partition
    that holds only for the clean codes is the bug this catches.
    """
    return pd.DataFrame({
        "HP_ALTER": [5, 13, 16, 18, 25, 45, 67, 81],
        "HP_SEX": [1, 2, 1, 2, 1, 2, 1, 2],
        "P_GEW": [1.0] * 8,
        "pt_ticket_group": ["not_flatrate", "not_flatrate", "deutschlandticket",
                            "other_flatrate", "deutschlandticket", "other_flatrate",
                            "not_flatrate", "not_flatrate"],
        # The four-group refinement (issue #329) collapses onto the three-group column
        # above: never_pt + occasional_ticket == not_flatrate, row by row.
        "pt_ticket_group4": ["never_pt", "occasional_ticket", "deutschlandticket",
                             "other_flatrate", "deutschlandticket", "other_flatrate",
                             "never_pt", "occasional_ticket"],
        "employment_status": ["nicht_erwerbstaetig", "nicht_erwerbstaetig", "in_ausbildung",
                              "in_ausbildung", "vollzeit", "teilzeit", "geringfuegig",
                              "sonstiges"],
        # trip_class is the CLASS INDEX 0..3 ({0, 1_2, 3_4, 5plus}), not a trip count --
        # a value of 4 makes the partition check fail, correctly.
        "trip_class": [0, 1, 2, 3, 0, 1, 2, 3],
        "work_participation": [0, 0, 0, 1, 1, 1, 0, 0],
        "leisure_participation": [1, 1, 0, 0, 1, 0, 1, 1],
        "education_participation": [1, 1, 1, 0, 0, 0, 0, 0],
        "escort_participation": [0, 1, 0, 1, 0, 0, 1, 0],
    })


def _seed_households() -> pd.DataFrame:
    """Household-level seed. The column names are the DERIVED seed columns the registry
    entries read (``number_of_cars``, ``number_of_bicycles``), not the raw MiD codes
    (``anzauto`` / ``anzpedrad``) -- getting that wrong makes the check skip the entry, which
    the vacuous-pass guard in the test above catches."""
    return pd.DataFrame({
        "H_GEW": [1.0] * 5,
        "oek_status": [1, 2, 3, 4, 5],
        "number_of_cars": [0, 1, 2, 3, 4],
        "number_of_bicycles": [0, 1, 2, 3, 4],
        "has_ebike": [0, 1, 0, 1, 0],
    })


def test_every_kreis_control_partitions_the_seed_universe():
    """Each Kreis control's categories must be mutually exclusive AND exhaustive.

    The rendered category expressions are evaluated on the synthetic seed; every row inside
    the control's universe must match exactly one category. A row matching two categories
    double-counts, a row matching none silently drops out of a control total that is
    supposed to partition the per-Kreis total.
    """
    report = cfs.check_category_partition(
        kac.REGISTRY, persons=_seed_persons(), households=_seed_households())
    assert report.failures == [], report.failures
    # Guard against a vacuous pass: the synthetic seed must actually reach every entry.
    assert report.n_controls_checked == len(kac.REGISTRY)


def test_partition_check_detects_a_gap():
    """The check must FAIL on a control whose categories leave a code uncovered -- otherwise
    it proves nothing about the ones that pass."""
    broken = kac.KreisAttributeControl(
        name="broken_gap", seed_column="trip_class", level="person",
        categories=(("a", "== 0"), ("b", "== 1")),  # class indices 2 and 3 uncovered
        target_csv_relpath="unused.csv", target_columns=("a", "b"), tier="soft")
    report = cfs.check_category_partition(
        [broken], persons=_seed_persons(), households=_seed_households())
    assert any("uncovered" in f for f in report.failures), report.failures


def test_partition_check_detects_an_overlap():
    """And it must FAIL on overlapping categories (a person counted twice)."""
    broken = kac.KreisAttributeControl(
        name="broken_overlap", seed_column="HP_ALTER", level="person",
        categories=(("young", "< 30"), ("adult", "< 70"), ("rest", ">= 70")),
        target_csv_relpath="unused.csv", target_columns=("young", "adult", "rest"),
        tier="soft")
    report = cfs.check_category_partition(
        [broken], persons=_seed_persons(), households=_seed_households())
    assert any("overlap" in f for f in report.failures), report.failures


def test_every_active_control_source_column_is_obtainable():
    """No control may reference a census column that neither exists in the cell parquet nor
    is produced by the aggregation map. PopulationSim reports that only at run time."""
    catalog = cs.controls_for_seed(cs.full_catalog(("tier0", "tier1", "tier2")), "mid")
    grid = [c for c in catalog if c.geography in (cs.GEO_100M, cs.GEO_1KM)]
    available = cfs.cell_parquet_columns(DATA_PATH)
    if available is None:
        pytest.skip("prepared cell parquet not present (local-only data)")
    report = cfs.check_census_sources_available(
        grid, available_columns=available,
        aggregation_map=cs.build_aggregation_map(grid))
    assert report.failures == [], report.failures
    assert report.n_controls_checked == len(grid)


def test_census_sources_available_with_both_grids_on():
    """The ACTIVE production catalog runs both grids; their census sources are injected
    per cell by the stage, and the ownership grid's dwelling INPUT columns must exist
    in the parquet (a cleaned-name mismatch here is exactly the silent-zero-control
    class of defect this smoke exists to catch)."""
    from braunschweig.popsim import ownership_grid as og

    catalog = cs.controls_for_seed(
        cs.full_catalog(("tier0", "tier1", "tier2"), include_employment_grid=True,
                        include_ownership_grid=True), "mid")
    grid = [c for c in catalog if c.geography in (cs.GEO_100M, cs.GEO_1KM)]
    available = cfs.cell_parquet_columns(DATA_PATH)
    if available is None:
        pytest.skip("prepared cell parquet not present (local-only data)")
    missing_inputs = set(og.DWELLING_INPUT_COLUMNS) - set(available)
    assert missing_inputs == set(), (
        f"ownership-grid dwelling input columns absent from the cell parquet: {missing_inputs}")
    report = cfs.check_census_sources_available(
        grid, available_columns=available,
        aggregation_map=cs.build_aggregation_map(grid),
        injected_columns=cfs.injected_cell_columns())
    assert report.failures == [], report.failures


def test_every_kreis_target_loads_and_partitions_every_kreis():
    """Every registered Kreis control's committed target must load, cover all 8 Kreise and
    sum to 1 per row -- a missing Kreis is a fail-fast at run time, a non-normalised row
    silently biases the counts."""
    report = cfs.check_kreis_targets(kac.REGISTRY, DATA_PATH, expected_ars5=ZGB_KREISE)
    assert report.failures == [], report.failures
    assert report.n_controls_checked == len(kac.REGISTRY)


def test_control_fit_reports_per_category_deviation():
    """The fit utility itself: realised vs target shares per category, in pp."""
    realised = pd.DataFrame({"category": ["a", "b"], "count": [30.0, 70.0]})
    target = pd.DataFrame({"category": ["a", "b"], "target_share": [0.25, 0.75]})
    fit = cfs.control_fit(realised, target)
    assert np.isclose(fit.loc[fit["category"] == "a", "delta_pp"].iloc[0], 5.0)
    assert np.isclose(fit.loc[fit["category"] == "b", "delta_pp"].iloc[0], -5.0)
    assert np.isclose(fit["abs_delta_pp"].max(), 5.0)
