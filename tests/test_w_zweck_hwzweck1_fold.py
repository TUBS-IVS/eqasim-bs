"""Pin the MiD 2023 W_ZWECK x hwzweck1 fold against the committed evidence table (issue #373).

MiD's own main-purpose derivation ``hwzweck1`` folds each ``W_ZWECK`` (Wegezweck) code to one
of its seven main purposes. This test asserts that, for every W_ZWECK code MiD folds
(near-)deterministically to a single hwzweck1 (row share >= 0.99), the eqasim purpose assigned by
``braunschweig.popsim.trips.map_purpose`` agrees with that fold -- except for a small, explicitly
documented set of codes where the eqasim vocabulary deliberately differs (escort split, the
home-recode codes 8/9, which MiD folds to whichever purpose the PREVIOUS leg on the diary had).

The evidence table itself (``eqasim-data/data/braunschweig/mid/mid2023_w_zweck_by_hwzweck1.csv``)
is derived by ``scripts/extract_mid_w_zweck_hwzweck1.py`` from the raw MiD 2023 Wege file and is
an EVIDENCE TABLE for ADR-0111 (issue #373), not a control target (CLAUDE.md: no invented
reference values -- this one is traceable to the committed table, regenerable from the raw
delivery).
"""
import os

import pandas as pd
import pytest

from braunschweig.popsim import trips

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TABLE = os.path.join(REPO, "eqasim-data", "data", "braunschweig", "mid", "mid2023_w_zweck_by_hwzweck1.csv")

#: eqasim purpose of each MiD main purpose (hwzweck1): 1 Arbeit, 2 dienstlich, 3 Ausbildung, 4 Einkauf,
#: 5 Erledigung, 6 Freizeit, 7 Begleitung, 99 keine Angabe.
PURPOSE_BY_HWZWECK1 = {1: "work", 2: "work", 3: "education", 4: "shop", 5: "other", 6: "leisure", 7: "escort", 99: "other"}
#: Codes whose eqasim purpose deliberately differs from the fold: 8/9 home (MiD recodes home to the previous
#: leg), 6/13 the escort family (flag-dependent, tested in test_popsim_trips), 16 leisure (ADR-0091 decision 4
#: agrees with the fold but is listed for the record), 2 business -> work (fold 2 dienstlich = work, agrees).
FOLD_EXCEPTIONS = {8, 9, 6, 13, 2, 16}


def _fold():
    table = pd.read_csv(TABLE, comment="#")
    top = table.sort_values("share_weighted", ascending=False).drop_duplicates("w_zweck").set_index("w_zweck")
    return top


def test_every_non_exception_code_maps_to_the_purpose_of_its_dominant_main_purpose():
    top = _fold()
    mapped = trips.map_purpose(pd.DataFrame({"W_ZWECK": top.index}), w_zweck_10_as_leisure=True)
    for code, row in top.iterrows():
        if code in FOLD_EXCEPTIONS:
            continue
        assert row["share_weighted"] >= 0.99, f"W_ZWECK {code} is not folded to one main purpose"
        expected = PURPOSE_BY_HWZWECK1[int(row["hwzweck1"])]
        got = mapped.loc[mapped["W_ZWECK"] == code, "purpose"].item()
        assert got == expected, f"W_ZWECK {code}: eqasim {got!r} vs MiD fold hwzweck1 {int(row['hwzweck1'])} -> {expected!r}"


def test_code_10_folds_to_leisure_and_the_flag_off_keeps_other():
    top = _fold()
    assert int(top.loc[10, "hwzweck1"]) == 6 and top.loc[10, "share_weighted"] >= 0.99
    off = trips.map_purpose(pd.DataFrame({"W_ZWECK": [10]}), w_zweck_10_as_leisure=False)
    assert off["purpose"].item() == "other"
