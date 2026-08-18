"""Pin tests for the committed blended per-Kreis target tables. The expected
`source` values below are the worked-through outcome of the 2026-07-08
decision rules on the committed inputs — if a pin fails after an input table
changed, re-derive the expectation, do not blindly update it."""
from pathlib import Path

import pandas as pd
import pytest

TARGETS = (Path(__file__).resolve().parents[1] / "eqasim-data" / "data"
           / "braunschweig" / "targets")
ALL_KREISE = {"03101", "03102", "03103", "03151", "03153", "03154",
              "03157", "03158"}


def load(name: str) -> pd.DataFrame:
    return pd.read_csv(TARGETS / name, comment="#", dtype={"ars5": str})


@pytest.mark.parametrize("name,cats", [
    ("target2026_economic_status_by_kreis.csv",
     ["very_low", "low", "medium", "high", "very_high"]),
    ("target2026_number_of_cars_by_kreis.csv",
     ["cars_0", "cars_1", "cars_2", "cars_3plus"]),
    ("target2026_has_ebike_by_kreis.csv", ["ebike_yes", "ebike_no"]),
    ("target2026_number_of_bicycles_by_kreis.csv",
     ["bikes_0", "bikes_1", "bikes_2", "bikes_3", "bikes_4plus"]),
    ("target2026_pt_ticket_group_by_kreis.csv",
     ["deutschlandticket", "other_flatrate", "not_flatrate"]),
])
def test_structure(name, cats):
    t = load(name)
    assert set(t["ars5"]) == ALL_KREISE | {"Gesamt"}
    # Shares are stored rounded to 4 decimals, so a row summing to 1.0 in float
    # can land at 0.9999/1.0001; tolerate the rounding, still catch real errors.
    assert ((t[cats].sum(axis=1) - 1.0).abs() < 1e-3).all()
    # Wolfsburg has no SrV coverage -> MiD fallback, except the SrV-only ebike
    # attribute, where it uses the documented SrV region-total assumption.
    expected_wob = "srv_region_total_assumption" if "ebike" in name else "mid"
    assert t.set_index("ars5").loc["03103", "source"] == expected_wob


def test_status_salzgitter_is_srv_arbitrated():
    t = load("target2026_economic_status_by_kreis.csv").set_index("ars5")
    # LSN register: SZ poorest Kreis -> SrV ranking wins over the thin MiD cell.
    assert t.loc["03102", "source"] == "srv_arbitrated"
    assert t.loc["03102", "high"] == pytest.approx(0.243, abs=0.01)
    # Braunschweig: under ZENSUS weights, the SrV rebuild agrees with MiD H4
    # within the precision-blend tolerance -> blend (was mid_arbitrated under
    # the stratum-internal standard weights, fixed 2026-07-08).
    assert t.loc["03101", "source"] == "blend"


def test_cars_sources():
    t = load("target2026_number_of_cars_by_kreis.csv").set_index("ars5")
    # Gifhorn + Peine agree within 5pp -> blend; Goslar diverges, no arbiter
    # for cars -> MiD shrunk toward Gesamt.
    assert t.loc["03151", "source"] == "blend"
    assert t.loc["03157", "source"] == "blend"
    assert t.loc["03153", "source"] == "mid_shrunk"
    # shrunk Goslar 0-car: 0.7*0.22 + 0.3*0.15 = 0.199
    assert t.loc["03153", "cars_0"] == pytest.approx(0.199, abs=0.005)


def test_pt_ticket_group_is_mid_only_and_reproduces_the_p24_1_flatrate_share():
    """MiD P24.1 only (issue #321): SrV is not a valid target for subscription OWNERSHIP
    (ADR-0060) and its Deutschlandticket table is all-ages against MiD's 14+ base, so every
    row must be sourced 'mid' -- no blend, no shrinkage.

    The two flatrate groups must also add back up to the published P24.1 flatrate share, or
    the control would steer a different quantity than has_pt_subscription: region 19%,
    Braunschweig 26%.
    """
    t = load("target2026_pt_ticket_group_by_kreis.csv").set_index("ars5")
    assert set(t["source"]) == {"mid"}
    flatrate = t["deutschlandticket"] + t["other_flatrate"]
    assert abs(flatrate.loc["Gesamt"] - 0.19) < 1e-3
    assert abs(flatrate.loc["03101"] - 0.26) < 1e-3
    # Deutschlandticket is kept separate precisely because it is the one flatrate category
    # with a second committed survey; the committed SrV figure (6.08% region, all ages) is
    # BELOW the MiD 14+ target, which is the corridor recorded in the ADR -- pin the
    # direction so a later "harmonisation" cannot quietly erase the disagreement.
    assert t.loc["Gesamt", "deutschlandticket"] > 0.0608


def test_ebike_is_srv_with_wob_assumption():
    t = load("target2026_has_ebike_by_kreis.csv").set_index("ars5")
    assert t.loc["03151", "source"] == "srv"
    assert t.loc["03151", "ebike_yes"] == pytest.approx(0.3108, abs=0.005)
    assert t.loc["03103", "source"] == "srv_region_total_assumption"
    assert t.loc["03103", "ebike_yes"] == pytest.approx(0.2450, abs=0.005)
