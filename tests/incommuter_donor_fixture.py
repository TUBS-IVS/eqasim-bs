"""The donor columns the worker and student in-commuter builders require (ADR-0191), for test fixtures."""
import pandas as pd

DONOR_HOUSEHOLD = dict(
    household_income_eur=4000.0, economic_status="high", household_size=2, consumption_units=1.5,
    donor_weight=1.0, weekday_reporting_day=True, commute_distance_km=35.0, education_distance_km=35.0)


def with_donor_household(persons: pd.DataFrame, **overrides) -> pd.DataFrame:
    """``persons`` with every required donor column (same value on each row unless overridden)."""
    return persons.assign(**{**DONOR_HOUSEHOLD, **overrides})
