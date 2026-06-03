"""Regression test for the ``synthesis.population.regiostar`` stage.

Guards against the ARS12-vs-AGS8 key mismatch: ``home.locations`` carries the
full 12-digit ARS while the RegioStaR-7 reference keys on the 8-digit AGS. The
stage must convert before merging, otherwise every person silently falls back
to a NaN RegioStaR class.
"""
from __future__ import annotations

import pandas as pd

from braunschweig.synthesis.population import regiostar


class _StubContext:
    def __init__(self, stages):
        self._stages = stages

    def stage(self, name):
        return self._stages[name]


def test_regiostar_matches_twelve_digit_ars_home_commune():
    stages = {
        "synthesis.population.enriched": pd.DataFrame({
            "person_id": [1, 2],
            "household_id": [10, 20],
        }),
        # home.locations carries the 12-digit ARS (Braunschweig, Wolfsburg).
        "synthesis.population.spatial.home.locations": pd.DataFrame({
            "household_id": [10, 20],
            "commune_id": ["031010000000", "031030000000"],
        }),
        # RegioStaR-7 reference keys on the 8-digit AGS.
        "braunschweig.data.bbsr.regiostar": pd.DataFrame({
            "commune_id": ["03101000", "03103000"],
            "regiostar7": [72, 72],
        }),
    }

    df = regiostar.execute(_StubContext(stages))

    assert list(df.columns) == ["person_id", "regiostar7"]
    # Both persons match their home Gemeinde's RS7 class — no silent NaN.
    assert df["regiostar7"].notna().all()
    assert dict(zip(df["person_id"], df["regiostar7"])) == {1: 72, 2: 72}


def test_regiostar_unknown_commune_yields_na():
    stages = {
        "synthesis.population.enriched": pd.DataFrame({
            "person_id": [1],
            "household_id": [10],
        }),
        "synthesis.population.spatial.home.locations": pd.DataFrame({
            "household_id": [10],
            "commune_id": ["099990000000"],  # outside the reference
        }),
        "braunschweig.data.bbsr.regiostar": pd.DataFrame({
            "commune_id": ["03101000"],
            "regiostar7": [72],
        }),
    }

    df = regiostar.execute(_StubContext(stages))

    assert df["regiostar7"].isna().all()
