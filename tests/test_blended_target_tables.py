"""Pin tests for the committed blended per-Kreis target tables. The expected
`source` values below are the worked-through outcome of the 2026-07-08
decision rules on the committed inputs — if a pin fails after an input table
changed, re-derive the expectation, do not blindly update it."""
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from scripts.build_blended_kreis_targets import (  # noqa: E402
    BlendConfig, build_pt_ticket_group, build_pt_ticket_group4)

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


def test_build_pt_ticket_group_reads_the_raw_german_p24_1_headers(tmp_path):
    # PT_RAW_FIXTURE_OK: mid2023_P24_1.csv is the committed raw-CSV boundary
    # (codebook-German column headers, see
    # braunschweig.data.mid.reference_tables.P24_RAW_COLUMN_BY_CATEGORY); this
    # fixture reproduces that EXACT header row (hardcoded, not derived from the
    # boundary dict, so the test would also catch a bug in the dict itself) to
    # prove build_pt_ticket_group reads the real committed layout without
    # raising KeyError (issue #329 -- this exact bug class was found twice
    # elsewhere: scripts/extract_mid_p24_by_car_availability.py and
    # tests/test_key_matching_leading_zeros.py).
    mid_dir = tmp_path / "mid"
    srv_dir = tmp_path / "srv"
    mid_dir.mkdir()
    srv_dir.mkdir()
    (mid_dir / "mid2023_P24_1.csv").write_text(
        "kreis,ars5,n_weighted,n_unweighted,einzelfahrschein,mehrfachkarte,"
        "deutschlandticket,wochen_monat_ohne_abo,monat_abo_jahreskarte,"
        "jobticket_semesterticket,anderes,fahre_nie,keine_angabe\n"
        "Gesamt,03ZGB,4719.0,9642.0,37.0,5.0,10.0,3.0,2.0,4.0,3.0,36.0,0.0\n"
        "Braunschweig,03101,949.0,1774.0,42.0,15.0,13.0,4.0,3.0,6.0,3.0,14.0,0.0\n",
        encoding="utf-8",
    )
    (srv_dir / "srv2023_ticket_groups_14plus_by_kreis.csv").write_text(
        "level,code,name,n_unweighted,n_weighted,deutschlandticket,"
        "other_flatrate,not_flatrate\n"
        "kreis,03101,Braunschweig,3844,223111.43,0.099,0.1575,0.7435\n",
        encoding="utf-8",
    )
    out = build_pt_ticket_group(tmp_path, BlendConfig()).set_index("ars5")
    assert set(out.index) == {"Gesamt", "03101"}
    cats = ["deutschlandticket", "other_flatrate", "not_flatrate"]
    assert ((out[cats].sum(axis=1) - 1.0).abs() < 1e-6).all()


def test_build_pt_ticket_group4_renormalizes_no_answer_and_splits(tmp_path):
    # PT_RAW_FIXTURE_OK: same reason as
    # test_build_pt_ticket_group_reads_the_raw_german_p24_1_headers above --
    # this fixture reproduces the committed raw-CSV boundary (codebook-German
    # column headers) hardcoded (not derived from P24_RAW_COLUMN_BY_CATEGORY)
    # so a bug in that dict itself would also be caught (issue #329).
    #
    # MiD P24.1 fixture (raw German columns), Gesamt + one Kreis (Braunschweig)
    # + Wolfsburg: integer percents 10 DT / 10 other-flatrate-total / 20 never /
    # 50 occasional-total / 10 no-answer for Gesamt and Braunschweig (identical,
    # so the SrV side can be built to agree exactly with Braunschweig). Expected
    # after renormalization over the 8 producible categories (total 90): dt
    # 10/90, other_flatrate 10/90, never_pt 20/90, occasional_ticket 50/90.
    root = tmp_path / "braunschweig"
    mid_dir = root / "mid"
    srv_dir = root / "srv"
    mid_dir.mkdir(parents=True)
    srv_dir.mkdir(parents=True)
    (mid_dir / "mid2023_P24_1.csv").write_text(
        "kreis,ars5,n_weighted,n_unweighted,einzelfahrschein,mehrfachkarte,"
        "deutschlandticket,wochen_monat_ohne_abo,monat_abo_jahreskarte,"
        "jobticket_semesterticket,anderes,fahre_nie,keine_angabe\n"
        "Gesamt,03ZGB,4719.0,9642.0,30.0,10.0,10.0,10.0,0.0,0.0,10.0,20.0,10.0\n"
        "Braunschweig,03101,949.0,1774.0,30.0,10.0,10.0,10.0,0.0,0.0,10.0,20.0,10.0\n"
        "Wolfsburg,03103,512.0,988.0,25.0,15.0,5.0,5.0,0.0,0.0,10.0,30.0,10.0\n",
        encoding="utf-8",
    )
    (srv_dir / "srv2023_ticket_groups4_14plus_by_kreis.csv").write_text(
        "level,code,name,n_unweighted,n_weighted,deutschlandticket,"
        "other_flatrate,never_pt,occasional_ticket\n"
        "kreis,03101,Braunschweig,1000,100000.0,0.1111,0.1111,0.2222,0.5556\n",
        encoding="utf-8",
    )
    out = build_pt_ticket_group4(root, BlendConfig())
    row = out[out["ars5"] == "03101"].iloc[0]
    # SrV agrees exactly in this fixture -> precision blend keeps the shares.
    assert abs(row["never_pt"] - 20.0 / 90.0) < 1e-3
    assert abs(sum(row[c] for c in
               ("deutschlandticket", "other_flatrate", "never_pt",
                "occasional_ticket")) - 1.0) < 1e-6
    # Wolfsburg (absent from SrV) must fall back to MiD-only.
    assert out[out["ars5"] == "03103"]["source"].iloc[0].startswith("mid")


def test_ebike_is_srv_with_wob_assumption():
    t = load("target2026_has_ebike_by_kreis.csv").set_index("ars5")
    assert t.loc["03151", "source"] == "srv"
    assert t.loc["03151", "ebike_yes"] == pytest.approx(0.3108, abs=0.005)
    assert t.loc["03103", "source"] == "srv_region_total_assumption"
    assert t.loc["03103", "ebike_yes"] == pytest.approx(0.2450, abs=0.005)
