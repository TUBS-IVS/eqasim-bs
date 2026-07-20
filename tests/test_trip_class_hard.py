"""Task 6 (feature #224): promote ``trip_class`` from a SOFT to a HARD per-Kreis
PopulationSim STEERING control.

Measured motivation (feature #224 brainstorm): the synthetic Mobilitaetsquote (share of
persons with >=1 weekday trip, i.e. 1 - trips_0) misses its SrV 2023 Braunschweig+RGB
target under the SOFT tier (synthetic immobility ~26.5% vs. SrV target ~11.2%, the
largest single SrV gap of all Kreis controls). Registering trip_class ``tier="hard"``
raises its rendered KREIS control columns into the "kreis_hard" importance group (2000,
par with economic_status/number_of_cars/work_participation/leisure_participation/
education_participation) via control_spec.importance_group_for_field, which classifies a
registry entry purely from its ``tier`` -- no separate IMPORTANCE_PROFILES entry is
needed (same auto-routing mechanism verified for work_participation, see
tests/test_work_participation_control.py::
test_apply_importance_profile_sets_hard_weight_for_work_participation, mirrored here).

number_of_bicycles, has_ebike, and employment_status stay tier="soft" -- this change is
deliberately scoped to trip_class only.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.popsim import control_spec as cs  # noqa: E402
from braunschweig.popsim.kreis_attribute_control import REGISTRY  # noqa: E402


def _entry(name):
    return next(c for c in REGISTRY if c.name == name)


def test_trip_class_is_hard():
    assert _entry("trip_class").tier == "hard"


def test_trip_class_columns_map_to_kreis_hard():
    from braunschweig.popsim.stage import build_controls_df

    on = build_controls_df(
        controls_source="catalog", seed="mid", tiers=("tier0",),
        kreis_control_names=("trip_class",),
        importance_profile="optimized_2026_06_30",
    )
    rows = on[on["control_field"].isin({
        "trip_class_0_KREIS", "trip_class_1_2_KREIS",
        "trip_class_3_4_KREIS", "trip_class_5plus_KREIS",
    })]
    assert len(rows) == 4
    assert (rows["importance"] == cs.IMPORTANCE_PROFILES["optimized_2026_06_30"]["kreis_hard"]).all()
