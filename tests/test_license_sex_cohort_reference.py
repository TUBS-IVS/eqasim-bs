"""Tests for the sex x age-cohort licence reference tables (issue #322).

The tables exist because judging the synthetic licence structure against the committed
one-dimensional MiD marginals produced a spurious "missing 8pp gradient": the gap is
concentrated in the 65+ cohort, so a marginal comparison largely measures the age
composition of the survey sample. Two things therefore need protecting by tests:

* the two extraction builders, on synthetic input whose expected shares are computable
  by hand -- including that a missing cell fails loudly instead of being dropped, and
  that non-binary sex codes and missing licence answers are excluded, not imputed;
* the committed CSVs themselves, so a re-extraction that changes their shape or breaks
  the cell grid cannot land unnoticed.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
SRV_TABLE = (REPO / "eqasim-data" / "data" / "braunschweig" / "srv"
             / "srv2023_car_license_by_sex_cohort_18plus_by_kreis.csv")
MID_TABLE = (REPO / "eqasim-data" / "data" / "braunschweig" / "mid"
             / "mid2023_license_by_sex_cohort.csv")

EXPECTED_CELLS = {("male", "18_64"), ("male", "65plus"),
                  ("female", "18_64"), ("female", "65plus")}


def _load_script(name: str):
    """Import a scripts/ module by path (they are standalone, not a package)."""
    path = REPO / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def srv_script():
    return _load_script("extract_srv_kreis_tables")


@pytest.fixture(scope="module")
def mid_script():
    return _load_script("extract_mid_license_by_sex_cohort")


def _srv_row(age: int, sex: int, licence: int, weight: float = 1.0,
             kreis: str = "03101") -> dict:
    return {"V_ALTER": age, "V_GESCHLECHT": sex, "V_FUEHR_PKW": licence,
            "GEWICHT_P_ZENSUS": weight, "kreis": kreis,
            "ST_CODE": int(kreis[-2:]), "ST_CODE_NAME": f"stratum {kreis}"}


def _srv_persons(rows: list[dict], *, fill_all_kreise: bool = True) -> pd.DataFrame:
    """SrV person frame for the builder.

    ``_iter_levels`` iterates ALL seven SrV Kreise, and the builder (by design) raises
    on an empty cell, so every fixture has to carry a complete 2x2 grid per Kreis. The
    filler rows are a neutral 1:1 licensed/unlicensed pattern in the OTHER six Kreise,
    so assertions on ``level == 'kreis'`` for 03101 and on the arithmetic of the rows
    passed in stay readable; ``total`` mixes both and is asserted only where stated.
    """
    filler: list[dict] = []
    if fill_all_kreise:
        for kreis in ("03102", "03151", "03153", "03154", "03157", "03158"):
            for sex in (1, 2):
                for age in (30, 70):
                    filler.append(_srv_row(age, sex, 1, 1.0, kreis=kreis))
                    filler.append(_srv_row(age, sex, 2, 1.0, kreis=kreis))
    return pd.DataFrame(rows + filler)


def test_srv_builder_computes_weight_weighted_shares_per_cell(srv_script):
    # Two men 18-64: one licensed with weight 3, one unlicensed with weight 1 -> 0.75.
    # Two women 65+: one licensed with weight 1, one unlicensed with weight 3 -> 0.25.
    persons = _srv_persons([
        _srv_row(30, 1, 1, 3.0), _srv_row(40, 1, 2, 1.0),
        _srv_row(70, 1, 1, 1.0), _srv_row(75, 1, 2, 1.0),
        _srv_row(30, 2, 1, 1.0), _srv_row(40, 2, 2, 1.0),
        _srv_row(70, 2, 1, 1.0), _srv_row(75, 2, 2, 3.0),
    ])
    table = srv_script.build_license_by_sex_cohort_table(persons)

    total = (table[(table["level"] == "kreis") & (table["code"] == "03101")]
             .set_index(["sex", "cohort"]))
    assert total.loc[("male", "18_64"), "share_with_license"] == pytest.approx(0.75)
    assert total.loc[("female", "65plus"), "share_with_license"] == pytest.approx(0.25)
    assert total.loc[("male", "65plus"), "share_with_license"] == pytest.approx(0.5)
    # n_unweighted counts respondents, n_weighted sums the expansion weight.
    assert total.loc[("male", "18_64"), "n_unweighted"] == 2
    assert total.loc[("male", "18_64"), "n_weighted"] == pytest.approx(4.0)


def test_srv_builder_cohort_edge_is_inclusive_at_65(srv_script):
    """Age 64 belongs to 18_64 and age 65 to 65plus -- the edge must not drift."""
    persons = _srv_persons([
        _srv_row(64, 1, 1), _srv_row(65, 1, 2),
        _srv_row(64, 2, 1), _srv_row(65, 2, 2),
    ])
    total = (srv_script.build_license_by_sex_cohort_table(persons)
             .query("level == 'kreis' and code == '03101'")
             .set_index(["sex", "cohort"]))
    assert total.loc[("male", "18_64"), "share_with_license"] == pytest.approx(1.0)
    assert total.loc[("male", "65plus"), "share_with_license"] == pytest.approx(0.0)
    assert total.loc[("male", "18_64"), "cohort_hi"] == 64
    assert total.loc[("male", "65plus"), "cohort_lo"] == 65


def test_srv_builder_excludes_under_18_and_missing_and_nonbinary_without_imputing(srv_script):
    """The three exclusions must shrink the universe, never move a share."""
    baseline = _srv_persons([
        _srv_row(30, 1, 1), _srv_row(70, 1, 1), _srv_row(30, 2, 1), _srv_row(70, 2, 1),
    ])
    polluted = _srv_persons([
        _srv_row(30, 1, 1), _srv_row(70, 1, 1), _srv_row(30, 2, 1), _srv_row(70, 2, 1),
        _srv_row(17, 1, 2),   # below the 18+ base
        _srv_row(30, 1, -8),  # missing licence answer
        _srv_row(30, 3, 2),   # non-binary sex code
        _srv_row(70, 4, 2),
    ])
    cols = ["sex", "cohort", "n_unweighted", "share_with_license"]
    query = "level == 'kreis' and code == '03101'"
    left = srv_script.build_license_by_sex_cohort_table(baseline).query(query)
    right = srv_script.build_license_by_sex_cohort_table(polluted).query(query)
    pd.testing.assert_frame_equal(
        left[cols].reset_index(drop=True), right[cols].reset_index(drop=True))


def test_srv_builder_raises_on_an_empty_cell_instead_of_dropping_it(srv_script):
    """A consumer would read a missing cell as zero, so the grid must be complete."""
    persons = _srv_persons([_srv_row(30, 1, 1), _srv_row(70, 1, 1), _srv_row(30, 2, 1)])
    with pytest.raises(RuntimeError, match="no respondents in cell"):
        srv_script.build_license_by_sex_cohort_table(persons)


def _mid_universe(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows)
    frame["has_region"] = frame["BLAND"].notna()
    frame.attrs.update(no_answer_rate=0.0, other_sex_rate=0.0, unmatched_rate=0.0)
    return frame


def _mid_row(age: int, sex: int, licence: int, weight: float = 1.0,
             bland: float | None = 3.0, rs7: float = 71.0) -> dict:
    return {"HP_ALTER": age, "HP_SEX": sex, "P_FSCHEIN": licence, "P_GEW": weight,
            "BLAND": bland, "RegioStaR7": rs7}


def test_mid_builder_reports_every_region_and_weights_by_p_gew(mid_script):
    rows = []
    for rs7 in range(71, 78):
        for sex in (1, 2):
            for age in (30, 70):
                rows.append(_mid_row(age, sex, 1, 3.0, rs7=float(rs7)))
                rows.append(_mid_row(age, sex, 2, 1.0, rs7=float(rs7)))
    table = mid_script.build_license_by_sex_cohort_table(_mid_universe(rows))

    assert set(table["region"]) == {"national", "bland_3",
                                    *[f"rs7_{c}" for c in range(71, 78)]}
    assert set(zip(table["sex"], table["cohort"])) == EXPECTED_CELLS
    # every cell is 3:1 licensed by weight, at every region level
    assert table["share_with_license"].unique().tolist() == [pytest.approx(0.75)]
    assert len(table) == 9 * 4


def test_mid_builder_keeps_region_less_rows_national_only(mid_script):
    """Rows without a household match must count nationally and nowhere else."""
    rows = []
    for rs7 in range(71, 78):
        for sex in (1, 2):
            for age in (30, 70):
                rows.append(_mid_row(age, sex, 1, rs7=float(rs7)))
    # the same four cells again, unlicensed, but with NO household match
    for sex in (1, 2):
        for age in (30, 70):
            rows.append(_mid_row(age, sex, 2, bland=None))
    table = mid_script.build_license_by_sex_cohort_table(_mid_universe(rows))
    national = table.query("region == 'national'").set_index(["sex", "cohort"])
    bland = table.query("region == 'bland_3'").set_index(["sex", "cohort"])
    # nationally: 7 licensed (one per RS7 class) + 1 unlicensed region-less row
    assert national.loc[("male", "18_64"), "n_unweighted"] == 8
    assert national.loc[("male", "18_64"), "share_with_license"] == pytest.approx(7 / 8)
    # regionally: the region-less row is gone, so the cell is fully licensed
    assert bland.loc[("male", "18_64"), "n_unweighted"] == 7
    assert bland.loc[("male", "18_64"), "share_with_license"] == pytest.approx(1.0)


def test_mid_builder_raises_on_an_empty_region(mid_script):
    rows = [_mid_row(30, 1, 1, rs7=71.0), _mid_row(30, 2, 1, rs7=71.0),
            _mid_row(70, 1, 1, rs7=71.0), _mid_row(70, 2, 1, rs7=71.0)]
    with pytest.raises(RuntimeError, match="rs7_72: no respondents at all"):
        mid_script.build_license_by_sex_cohort_table(_mid_universe(rows))


@pytest.mark.parametrize("path,key", [(SRV_TABLE, "level"), (MID_TABLE, "region")])
def test_committed_tables_carry_a_complete_cell_grid_per_group(path, key):
    """Guards the committed artefacts: shape, cell completeness and share bounds."""
    assert path.exists(), f"committed reference table missing: {path}"
    table = pd.read_csv(path, comment="#")
    assert {"sex", "cohort", "n_unweighted", "n_weighted", "share_with_license"} <= set(
        table.columns)
    assert not table.empty
    for group, frame in table.groupby(key):
        assert set(zip(frame["sex"], frame["cohort"])) == EXPECTED_CELLS, (
            f"{path.name}: incomplete cell grid for {key}={group}")
    assert table["share_with_license"].between(0.0, 1.0).all()
    assert (table["n_unweighted"] > 0).all()
    assert (table["n_weighted"] > 0).all()


def test_committed_srv_table_covers_the_seven_srv_kreise_and_omits_wolfsburg():
    """SrV does not cover Wolfsburg; the table must not silently invent it."""
    table = pd.read_csv(SRV_TABLE, comment="#", dtype={"code": str})
    kreise = set(table.query("level == 'kreis'")["code"])
    assert len(kreise) == 7
    assert "03103" not in kreise
    assert {"03101", "03102", "03151", "03153", "03154", "03157", "03158"} == kreise


def test_committed_tables_reproduce_the_cohort_concentrated_gap():
    """The finding the tables exist for: the sex gap is a 65+ phenomenon.

    Both surveys must agree on the SIGN and the concentration, which is what falsified
    the "missing 8pp gradient" premise of issue #322. They deliberately do NOT have to
    agree on the size of the 65+ gap -- that disagreement (about 8pp on the 65+ female
    cell) is the recorded identification limit, see ADR-0093.
    """
    srv = (pd.read_csv(SRV_TABLE, comment="#").query("level == 'total'")
           .set_index(["sex", "cohort"])["share_with_license"])
    mid = (pd.read_csv(MID_TABLE, comment="#").query("region == 'bland_3'")
           .set_index(["sex", "cohort"])["share_with_license"])
    for source in (srv, mid):
        gap_young = source[("male", "18_64")] - source[("female", "18_64")]
        gap_old = source[("male", "65plus")] - source[("female", "65plus")]
        assert 0.0 <= gap_young < 0.05, gap_young
        assert gap_old > gap_young + 0.03, (gap_young, gap_old)
