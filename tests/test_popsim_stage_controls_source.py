from __future__ import annotations

import pandas as pd
from braunschweig.popsim import stage


def test_controls_source_catalog_renders_same_controls_as_csv() -> None:
    # controls_source == "catalog" must build controls_df from the catalog (mid seed)
    # equal to the production CSV baseline (modulo row order).
    # The committed CSV is the PRE-#320 control set, so this equivalence is a flag-OFF
    # property; the ON path adds the four fine teen-band controls by design.
    rendered = stage.build_controls_df(controls_source="catalog", seed="mid",
                                       fine_teen_age_bands=False)
    baseline = pd.read_csv("tests/fixtures/prep3_controls_baseline.csv", sep=";")
    key = ["target", "geography", "seed_table", "importance", "control_field", "expression"]
    pd.testing.assert_frame_equal(
        rendered[key].sort_values(key).reset_index(drop=True),
        baseline[key].sort_values(key).reset_index(drop=True),
        check_dtype=False,
    )


def test_controls_source_csv_reads_external_file() -> None:
    df = stage.build_controls_df(controls_source="csv",
                                 controls_path="tests/fixtures/prep3_controls_baseline.csv")
    assert list(df.columns)[:2] == ["target", "geography"]
