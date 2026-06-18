"""Map the MiD core-week flag ``kernwo`` to a weekday/weekend ``day_type``.

kernwo (MiD 2023): 1,2,3 = weekday; 4,5,6,7 = weekend; 99 = no answer.
The household reporting day is assigned per household, so all persons of a
household share one ``kernwo`` class; a genuinely-mixed household is
extremely rare (data noise) and is resolved by majority vote rather than
raising, so the pipeline continues with a logged warning.
"""
from __future__ import annotations

import logging

import pandas as pd

from braunschweig.popsim.seed import WEEKDAY_KERNWO, ALL_REPORTING_KERNWO

logger = logging.getLogger(__name__)

WEEKEND_KERNWO = tuple(v for v in ALL_REPORTING_KERNWO if v not in WEEKDAY_KERNWO)


def person_day_type(kernwo: pd.Series) -> pd.Series:
    weekday = set(WEEKDAY_KERNWO)
    weekend = set(WEEKEND_KERNWO)
    unknown = set(pd.unique(kernwo)) - weekday - weekend
    if unknown:
        raise ValueError(
            f"Unexpected kernwo values {sorted(unknown)}; expected weekday "
            f"{WEEKDAY_KERNWO} or weekend {WEEKEND_KERNWO} (99 'no answer' must "
            f"be removed by the completeness filter before tagging)."
        )
    return pd.Series(
        ["weekday" if v in weekday else "weekend" for v in kernwo],
        index=kernwo.index,
    )


def household_day_type(persons, *, household_id="H_ID", kernwo_col="kernwo") -> pd.Series:
    dt = person_day_type(persons[kernwo_col])
    by_hh = dt.groupby(persons[household_id])
    n_classes = by_hh.nunique()
    mixed = n_classes[n_classes > 1]
    if len(mixed):
        logger.warning(
            "[popsim.day_type] %d household(s) have mixed reporting day "
            "(weekday+weekend) across members (e.g. %s); resolving each by "
            "majority vote (ties -> 'weekday').",
            len(mixed),
            list(mixed.index[:5]),
        )
        # Resolve per household by majority; mode() returns sorted values so
        # ties resolve deterministically to "weekday" (alphabetically first).
        return by_hh.agg(lambda s: s.mode().iat[0])
    return by_hh.first()
