"""Escort purpose in the MiD validation crosswalks (issue #201)."""
import pandas as pd
import pytest

from braunschweig.analysis.population_validation import trip_coherence as tc


def test_escort_maps_to_begleitung_without_raise():
    mapped = tc.mid_purpose_from_eqasim(pd.Series(["work", "escort", "home"]))
    assert list(mapped) == ["arbeit", "begleitung", "heimweg"]


def test_scored_purposes_selection_is_presence_based():
    with_escort = {"arbeit": 0.3, "ausbildung": 0.1, "einkauf": 0.2,
                   "freizeit": 0.3, "begleitung": 0.1}
    without = {"arbeit": 0.4, "ausbildung": 0.1, "einkauf": 0.2, "freizeit": 0.3}
    assert tc.scored_mid_purposes(with_escort) == tc.SCORED_MID_PURPOSES_WITH_ESCORT
    assert tc.scored_mid_purposes(without) == tc.SCORED_MID_PURPOSES


def test_renormalize_scored_with_escort_sums_to_one():
    dist = {"arbeit": 0.3, "ausbildung": 0.1, "einkauf": 0.1, "freizeit": 0.3,
            "begleitung": 0.1, "sonstiges": 0.1}
    out = tc.renormalize_scored(dist, scored_purposes=tc.SCORED_MID_PURPOSES_WITH_ESCORT)
    assert set(out) == set(tc.SCORED_MID_PURPOSES_WITH_ESCORT)
    assert sum(out.values()) == pytest.approx(1.0)


def test_w12_map_with_escort():
    assert tc.W12_PURPOSE_BY_MID_WITH_ESCORT["Begleitung"] == "escort"
    assert "Begleitung" not in tc.W12_PURPOSE_BY_MID


# ---------------------------------------------------------------------------
# Issue #372 task 4 (ADR-0112): with escort_passive_from_adult ON only part of the
# passive (W_ZWECK 13) W1 mass is education, because a paired child follows the
# accompanying adult's purpose.
# ---------------------------------------------------------------------------

def test_active_adjustment_folds_the_whole_passive_remainder_by_default():
    """OFF path (issue #256): the full passive remainder becomes ausbildung, mass-preserving."""
    shares = {"begleitung": 10.0, "ausbildung": 5.0}
    out = tc.apply_escort_active_adjustment(shares, 0.72)
    assert out["begleitung"] == pytest.approx(7.2)
    assert out["ausbildung"] == pytest.approx(5.0 + 2.8)
    assert sum(out.values()) == pytest.approx(sum(shares.values()))


def test_active_adjustment_narrows_the_fold_under_escort_passive_from_adult():
    """ON path: only the pinned education share of the passive remainder folds in; the rest
    leaves the scored total (the model sends it to shop/home/leisure/other)."""
    shares = {"begleitung": 10.0, "ausbildung": 5.0}
    out = tc.apply_escort_active_adjustment(shares, 0.72, passive_education_share=0.2)
    assert out["begleitung"] == pytest.approx(7.2)
    assert out["ausbildung"] == pytest.approx(5.0 + 2.8 * 0.2)
    assert sum(out.values()) < sum(shares.values())


def test_active_adjustment_rejects_an_out_of_range_passive_education_share():
    with pytest.raises(ValueError, match="passive_education_share"):
        tc.apply_escort_active_adjustment({"begleitung": 1.0}, 0.72,
                                          passive_education_share=1.5)


def test_w1_scored_target_requires_passive_education_for_the_pairing_flag():
    with pytest.raises(ValueError, match="escort_passive_from_adult"):
        tc.w1_scored_target("unused", scored_purposes=tc.SCORED_MID_PURPOSES_WITH_ESCORT,
                            escort_passive_education=False, escort_passive_from_adult=True)


def test_load_passive_education_share_reads_the_pinned_column():
    """The share must come from the committed table (never a literal): it is a measured
    reference, and CLAUDE.md forbids inventing one."""
    from pathlib import Path
    data_path = str(Path(__file__).resolve().parents[1] / "eqasim-data" / "data")
    share = tc.load_passive_education_share(data_path)
    assert 0.0 <= share <= 1.0
    # Independent read of the same committed cell, so a wrong row/column lookup fails here.
    table = pd.read_csv(
        f"{data_path}/braunschweig/mid/mid2023_escort_w_zweck_split.csv", comment="#"
    ).set_index("w_zweck")
    assert share == pytest.approx(
        float(table.loc["code_13", "code_13_to_education_share_under_pairing"]))
