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


def test_active_adjustment_moves_the_passive_remainder_to_the_measured_purposes():
    """ON path (fix round 1, ruling C-R11): the passive remainder is MOVED to the purposes the
    measured fold names -- einkauf and freizeit included -- not dropped from the target."""
    shares = {"begleitung": 10.0, "ausbildung": 5.0, "einkauf": 20.0, "freizeit": 30.0}
    fold = {"ausbildung": 0.2, "einkauf": 0.15, "freizeit": 0.35, "heimweg": 0.12,
            "sonstiges": 0.18}
    out = tc.apply_escort_active_adjustment(shares, 0.72, passive_purpose_fold=fold)
    remainder = 10.0 * (1.0 - 0.72)
    assert out["begleitung"] == pytest.approx(7.2)
    assert out["ausbildung"] == pytest.approx(5.0 + remainder * 0.2)
    assert out["einkauf"] == pytest.approx(20.0 + remainder * 0.15)
    assert out["freizeit"] == pytest.approx(30.0 + remainder * 0.35)
    assert out["heimweg"] == pytest.approx(remainder * 0.12)
    # Mass-preserving: nothing leaves the adjusted shares (heimweg/sonstiges are only dropped
    # later, by the caller's restriction to scored_purposes -- as W1's own such mass is).
    assert sum(out.values()) == pytest.approx(sum(shares.values()))


def test_active_adjustment_rejects_a_fold_that_does_not_sum_to_one():
    with pytest.raises(ValueError, match="must sum to 1"):
        tc.apply_escort_active_adjustment({"begleitung": 1.0}, 0.72,
                                          passive_purpose_fold={"ausbildung": 0.5})


def test_w1_target_moves_passive_mass_into_einkauf_and_freizeit(tmp_path, monkeypatch):
    """End-to-end on a SYNTHETIC W1 row + a synthetic pinned split: turning
    escort_passive_from_adult on must raise the einkauf/freizeit targets by exactly the
    fold's share of the passive remainder (renormalised over the scored purposes)."""
    row = pd.Series({"arbeit": 20.0, "ausbildung": 10.0, "einkauf": 20.0, "freizeit": 40.0,
                     "begleitung": 10.0})
    fold = {"ausbildung": 0.2, "einkauf": 0.15, "freizeit": 0.35, "heimweg": 0.12,
            "sonstiges": 0.18}
    monkeypatch.setattr(tc, "_zgb_overall_row", lambda data_path, table: row)
    monkeypatch.setattr(tc, "load_escort_active_share", lambda data_path: 0.72)
    monkeypatch.setattr(tc, "load_passive_purpose_fold", lambda data_path: fold)

    scored = tc.SCORED_MID_PURPOSES_WITH_ESCORT
    off = tc.w1_scored_target("unused", scored_purposes=scored, escort_passive_education=True)
    on = tc.w1_scored_target("unused", scored_purposes=scored, escort_passive_education=True,
                             escort_passive_from_adult=True)

    remainder = 10.0 * (1.0 - 0.72)
    expected_on = {"arbeit": 20.0, "ausbildung": 10.0 + remainder * 0.2,
                   "einkauf": 20.0 + remainder * 0.15,
                   "freizeit": 40.0 + remainder * 0.35, "begleitung": 10.0 * 0.72}
    total = sum(expected_on.values())
    for purpose, value in expected_on.items():
        assert on[purpose] == pytest.approx(value / total)
    # The OFF path put the WHOLE remainder on ausbildung, so einkauf/freizeit gain here.
    assert on["einkauf"] > off["einkauf"] and on["freizeit"] > off["freizeit"]
    assert on["ausbildung"] < off["ausbildung"]


def test_load_passive_purpose_fold_sums_to_one_over_w1_names():
    from pathlib import Path
    data_path = str(Path(__file__).resolve().parents[1] / "eqasim-data" / "data")
    fold = tc.load_passive_purpose_fold(data_path)
    assert sum(fold.values()) == pytest.approx(1.0)
    # Translated to W1 names, so the caller can add them to a W1 row directly.
    assert set(fold) <= set(tc.EQASIM_TO_MID_PURPOSE.values())
    assert fold["einkauf"] > 0.0 and fold["freizeit"] > 0.0


def test_w1_scored_target_requires_passive_education_for_the_pairing_flag():
    with pytest.raises(ValueError, match="escort_passive_from_adult"):
        tc.w1_scored_target("unused", scored_purposes=tc.SCORED_MID_PURPOSES_WITH_ESCORT,
                            escort_passive_education=False, escort_passive_from_adult=True)
