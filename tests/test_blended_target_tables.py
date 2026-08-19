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


def test_pt_ticket_group_blends_mid_and_srv_on_a_matched_universe():
    """MiD P24.1 x SrV E_OEV_FK, both on the 14+ base (issue #321).

    The blend rules must be VISIBLE in the source column, because which rule fired is the
    scientific content of this table: the two surveys agree within the tolerance for
    Braunschweig / Peine / Wolfenbuettel (-> blend), disagree by up to 8pp for Salzgitter /
    Gifhorn / Helmstedt / Goslar (-> MiD shrunk toward Gesamt, no arbiter exists), and
    Wolfsburg has no SrV coverage at all (-> MiD).
    """
    t = load("target2026_pt_ticket_group_by_kreis.csv").set_index("ars5")
    assert t.loc["03101", "source"] == "blend"
    assert t.loc["03157", "source"] == "blend"
    assert t.loc["03158", "source"] == "blend"
    for ars5 in ("03102", "03151", "03153", "03154"):
        assert t.loc[ars5, "source"] == "mid_shrunk", ars5
    # Wolfsburg is documented as outside the SrV survey area, and the region row is the
    # shrinkage prior, so both stay pure MiD.
    assert t.loc["03103", "source"] == "mid"
    assert t.loc["Gesamt", "source"] == "mid"


def test_pt_ticket_group_flatrate_level_stays_between_the_two_surveys():
    """The blended flatrate share must lie between the two measured levels, not outside.

    Measured on the matched 14+ universe (2026-08-18): Braunschweig SrV 25.65% vs MiD
    26.00%; region SrV 17.38% vs MiD 19.00%. The blend is a precision-weighted mean, so a
    value outside that interval would mean the collapse or the weighting broke. The
    Gesamt row is pure MiD by construction and therefore pinned to the MiD level.
    """
    t = load("target2026_pt_ticket_group_by_kreis.csv").set_index("ars5")
    flatrate = t["deutschlandticket"] + t["other_flatrate"]
    assert 0.2565 - 1e-3 <= flatrate.loc["03101"] <= 0.26 + 1e-3
    assert abs(flatrate.loc["Gesamt"] - 0.19) < 1e-3
    # The Deutschlandticket disagreement must survive in the record: MiD reads 10.0% for the
    # region against SrV's 6.93% on the same 14+ base. The region row keeps the MiD level,
    # so a later "harmonisation" toward SrV cannot pass unnoticed.
    assert abs(t.loc["Gesamt", "deutschlandticket"] - 0.10) < 1e-3


def test_ebike_is_srv_with_wob_assumption():
    t = load("target2026_has_ebike_by_kreis.csv").set_index("ars5")
    assert t.loc["03151", "source"] == "srv"
    assert t.loc["03151", "ebike_yes"] == pytest.approx(0.3108, abs=0.005)
    assert t.loc["03103", "source"] == "srv_region_total_assumption"
    assert t.loc["03103", "ebike_yes"] == pytest.approx(0.2450, abs=0.005)
