"""C5c: the Gemeinde tilt of a car that cannot take it is "not applicable", never a fallback.

The Gemeinde EV table covers the ZGB Kreise only and an in-commuter from outside the ZGB carries no
home-Gemeinde label, so its Gemeinde tilt can never fire. Counted as a fallback it read 100 % and
warned on every run (docs/codebase/notes/fallback-counter-states.md). A car that COULD take the tilt
but misses it is still a fallback, judged against the applicable cars only.
"""

from __future__ import annotations

import logging

import numpy as np
import pytest

from braunschweig.synthesis.vehicles import fleet_sampling_de as fs

POWERTRAINS = ["petrol", "diesel", "bev", "phev", "hybrid", "gas", "other", "hydrogen"]
TABLE_KREIS = "03158"     # the fixture's Gemeinde table has rows for this Kreis
OUTSIDE_KREIS = "03241"   # a Kreis the table has no rows for (an in-commuter origin)


def _model() -> fs.PowertrainModel:
    base = np.full((1, len(POWERTRAINS)), 1.0 / len(POWERTRAINS))
    return fs.PowertrainModel(
        segments=["klein"],
        powertrains=list(POWERTRAINS),
        kreis_segment_powertrain={TABLE_KREIS: base, OUTSIDE_KREIS: base},
        national_segment_powertrain=base,
        kreis_private_electric_share={TABLE_KREIS: {fs.COMBINED_ELECTRIC_KEY: 0.10}},
        gemeinde_private_electric_share={(TABLE_KREIS, "BEVTOWN"): {fs.COMBINED_ELECTRIC_KEY: 0.10}},
    )


def _tilt_rate_records(caplog, model, label):
    with caplog.at_level(logging.INFO, logger=fs.logger.name):
        model.log_fallback_rate(label)
    return [r for r in caplog.records if "powertrain Gemeinde tilt: primary" in r.getMessage()]


def test_cars_without_a_label_outside_the_table_are_not_applicable_and_quiet(caplog):
    """The in-commuters of the production run: no origin Gemeinde, origin Kreis outside the table."""
    model = _model()
    for _ in range(25):
        model.powertrain_probabilities("klein", OUTSIDE_KREIS, None)
    (rate,) = _tilt_rate_records(caplog, model, "in-commuters (origin outside ZGB)")
    assert rate.levelname == "INFO"
    assert "not applicable 25" in rate.getMessage()                    # the rate stays logged, apart
    assert (model._gemeinde_primary, model._gemeinde_fallback, model._gemeinde_not_applicable) == (0, 0, 25)


@pytest.mark.parametrize("kreis, gemeinde", [
    pytest.param(TABLE_KREIS, None, id="table-kreis-but-no-label"),
    pytest.param(OUTSIDE_KREIS, "SOMETOWN", id="label-but-no-table-rows-for-the-kreis"),
    pytest.param(TABLE_KREIS, "UNKNOWNTOWN", id="table-kreis-label-not-in-the-table"),
])
def test_a_car_that_could_take_the_tilt_but_misses_it_is_a_loud_fallback(caplog, kreis, gemeinde):
    """A deviation from the expected structure stays a fallback and warns: labels that cannot be used,
    a broken join inside a covered Kreis, or a table that now covers the in-commuters' Kreise."""
    model = _model()
    for _ in range(25):
        model.powertrain_probabilities("klein", kreis, gemeinde)
    (rate,) = _tilt_rate_records(caplog, model, "residents")
    assert rate.levelname == "WARNING"
    assert (model._gemeinde_primary, model._gemeinde_fallback) == (0, 25)


def test_not_applicable_cars_do_not_dilute_the_fallback_rate_of_the_applicable_ones(caplog):
    """1 primary + 3 fallback among the applicable cars is a 75 % fallback rate however many
    not-applicable cars sit beside them: they must not hide a broken primary path."""
    model = _model()
    model.powertrain_probabilities("klein", TABLE_KREIS, "BEVTOWN")
    for _ in range(3):
        model.powertrain_probabilities("klein", TABLE_KREIS, "UNKNOWNTOWN")
    for _ in range(1000):
        model.powertrain_probabilities("klein", OUTSIDE_KREIS, None)
    (rate,) = _tilt_rate_records(caplog, model, "residents")
    assert rate.levelname == "WARNING"
    assert "primary 1/4" in rate.getMessage()
