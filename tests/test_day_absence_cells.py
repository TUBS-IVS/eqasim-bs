"""Day-absence cells: students by home area, other adults by age band (issue #140, Phase 4, Task 6).

Covers the pure module :mod:`braunschweig.synthesis.day_absence.cells` and the cells path of
:func:`braunschweig.synthesis.day_absence.absence.draw_absence` (design A3 "Away from home all day",
A25; ADR-0212 amends ADR-0110). The stage plumbing is tested in ``tests/test_day_absence_stage.py``.

The rates of the expectation test are TEST INPUTS of a toy population, not reference values; the
loader test reads the committed SrV tables and compares with what the tables hold.
"""
from __future__ import annotations

import math
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from braunschweig.calibration.mid_student_reference import P_TAET_NO_ANSWER
from braunschweig.data.education import student_flows_inputs as SFI
from braunschweig.synthesis.day_absence import absence as D
from braunschweig.synthesis.day_absence import cells as C

SRV_DIR = Path(__file__).resolve().parents[1] / "eqasim-data" / "data" / "braunschweig" / "srv"
AREA_OF_KREIS = SFI.area_by_kreis(SFI.default_home_area_kreise(), SFI.ZGB_KREISE)
STUDENT = SFI.STUDENT_P_TAET
NON_STUDENT = 1   # a readable P_TAET code other than the student code

#: Test inputs (not reference values): the three populated cells of the toy population and the
#: rates of the cells it does not populate, so the dict looks like the production reference.
TOY_CELL_RATES = {"0-5": 0.03, "6-17": 0.019, "adult:18-29": 0.063, "adult:30-44": 0.044,
                  "adult:45-64": 0.049, "adult:65-74": 0.059, "adult:75+": 0.058,
                  "student:braunschweig": 0.17, "student:surrounding": 0.17, "student:wolfsburg": 0.12}
TOY_REFERENCE = D.AbsenceReference(
    p_absent_by_band={"0-5": 0.03, "6-17": 0.019, "18-29": 0.083, "30-44": 0.044, "45-64": 0.049,
                      "65-74": 0.059, "75+": 0.058},
    p_all_absent_by_size={1: 0.06, 2: 0.03, 3: 0.012, 4: 0.008, 5: 0.006},
    p_absent_person_by_size={k: float("nan") for k in range(1, 6)})
#: Households per size: 200,000 persons in total (see _toy_population for why that many).
TOY_HOUSEHOLDS_BY_SIZE = {1: 40_000, 2: 30_000, 3: 12_000, 4: 8_000, 5: 6_400}


def _household_frame(sizes):
    sizes = np.asarray(sizes)
    household_id = np.repeat(np.arange(len(sizes)), sizes)
    position = np.arange(len(household_id)) - np.repeat(np.cumsum(sizes) - sizes, sizes)
    return household_id, position, sizes[household_id]


def _toy_population():
    """200,000 persons in households of sizes 1 to 5, all in Braunschweig, in three cells.

    The first two members are adults aged 18 to 29 (a student with probability 0.8 when living
    alone, 0.4 otherwise, so about 40 % of the students live alone, as SrV shows for Braunschweig,
    design A3); members three to five are aged 6 to 17. The first member of every household with a
    child carries an escort leg. Size: 3 standard errors of the smallest cell rate (1.9 % over about
    47,000 children) are 0.19 pp, small enough to catch a draw at the wrong rate or over the wrong
    pool, which are off by whole percentage points.
    """
    sizes = np.concatenate([np.full(n, size) for size, n in TOY_HOUSEHOLDS_BY_SIZE.items()])
    household_id, position, size_of = _household_frame(sizes)
    rng = np.random.RandomState(140)
    n = len(household_id)
    adult = position < 2
    is_student = adult & (rng.random_sample(n) < np.where(size_of == 1, 0.8, 0.4))
    persons = pd.DataFrame({"person_id": np.arange(n), "household_id": household_id,
                            "age": np.where(adult, rng.randint(18, 30, n), rng.randint(6, 18, n)),
                            "P_TAET": np.where(is_student, STUDENT, NON_STUDENT), "kreis": "03101"})
    escorters = set(persons.loc[(position == 0) & (size_of >= 3), "person_id"])
    return persons, escorters, size_of


@pytest.fixture(scope="module")
def toy_draws():
    """The toy population drawn once by cell and once by band, under the production settings."""
    persons, escorters, size_of = _toy_population()
    person_cells = C.assign_cells(persons, AREA_OF_KREIS)
    settings = {"individual_stage_min_household_size": 2, "escort_protected_person_ids": escorters}
    by_cell, diagnostics = D.draw_absence(persons, TOY_REFERENCE, np.random.RandomState(7), **settings,
                                          cell_rates=TOY_CELL_RATES, cells=person_cells)
    by_band, _ = D.draw_absence(persons, TOY_REFERENCE, np.random.RandomState(7), **settings)
    return persons, escorters, size_of, by_cell, diagnostics, by_band


def test_each_cell_reaches_its_rate_in_expectation_under_the_production_settings(toy_draws):
    """The realised rate of each cell lies within 3 standard errors of the cell rate, with singles
    drawn by the household stage only and escorters spared by the individual stage (A25).

    The standard error is the binomial one of the cell rate over the cell, sqrt(r (1 - r) / n). It
    bounds the spread of the draw from above: given the household stage, only the individual stage
    is random, and its variance n_eligible p (1 - p) is smaller than n r (1 - r).
    """
    persons, escorters, size_of, out, diagnostics, _ = toy_draws
    absent = out["day_absence_state"] != D.STATE_PRESENT
    for cell in ("student:braunschweig", "adult:18-29", "6-17"):
        in_cell = out[D.CELL_COLUMN] == cell
        n, rate = int(in_cell.sum()), TOY_CELL_RATES[cell]
        assert n > 40_000, cell
        assert abs(absent[in_cell].mean() - rate) <= 3 * math.sqrt(rate * (1 - rate) / n), cell
        assert diagnostics["by_cell"][cell]["n"] == n
    assert diagnostics["n_cells_unreachable"] == 0 and diagnostics["n_cells_overshoot"] == 0
    individual = out["day_absence_state"] == D.STATE_ABSENT_INDIVIDUAL
    alone = pd.Series(size_of == 1, index=out.index)
    assert not individual[alone].any()
    assert not individual[out["person_id"].isin(escorters)].any()
    # A25: within the student cell, the rate above the one-person rate falls on the larger households.
    students = out[D.CELL_COLUMN] == "student:braunschweig"
    assert absent[students & alone].mean() < TOY_CELL_RATES["student:braunschweig"] < absent[students & ~alone].mean()


def test_persons_under_18_keep_exactly_the_state_of_the_band_draw(toy_draws):
    persons, _, _, by_cell, _, by_band = toy_draws
    minors = (persons.sort_values(["household_id", "person_id"])["age"] < 18).to_numpy()
    assert minors.sum() > 40_000
    columns = list(D.ABSENCE_COLUMNS)
    pd.testing.assert_frame_equal(by_cell.loc[minors, columns], by_band.loc[minors, columns], check_exact=True)
    # ... while the student cell is drawn at its own rate, not at the band's.
    assert not by_cell["day_absence_state"].equals(by_band["day_absence_state"])


def _srv_copy(tmp_path, edit=None):
    """The committed SrV tables the two loaders read, copied; ``edit`` rewrites the by-age table."""
    for name in (D.ABSENCE_BY_AGE_TABLE, D.ABSENCE_HOUSEHOLD_TABLE, SFI.PARAMETERS_TABLE,
                 SFI.PARAMETERS_BY_AREA_TABLE):
        shutil.copyfile(SRV_DIR / name, tmp_path / name)
    if edit is not None:
        by_age = pd.read_csv(tmp_path / D.ABSENCE_BY_AGE_TABLE, comment="#", encoding="utf-8")
        edit(by_age).to_csv(tmp_path / D.ABSENCE_BY_AGE_TABLE, index=False, encoding="utf-8")
    return tmp_path


def _all_band_population(n_households=4000):
    """Households of sizes 1 to 5 with ages over all seven bands and NO adult student: every adult
    has a readable non-student code (an adult of unknown status may be a student and is drawn in a
    cell of its own); persons under 18 carry any code -- non-student, no answer, missing, or the
    student code of pupils aged 14 to 17 -- and stay in their band."""
    rng = np.random.RandomState(31)
    household_id, position, size_of = _household_frame(rng.randint(1, 6, size=n_households))
    n = len(household_id)
    age = np.where(position < 2, rng.randint(18, 95, n), rng.randint(0, 18, n))
    p_taet = np.where(age >= 18, rng.choice([NON_STUDENT, 2, 7], size=n),
                      rng.choice([NON_STUDENT, P_TAET_NO_ANSWER], size=n)).astype(float)
    p_taet[(age < 18) & (rng.random_sample(n) < 0.05)] = np.nan
    p_taet[(age < 18) & (age >= 14)] = STUDENT
    persons = pd.DataFrame({"person_id": np.arange(n), "household_id": household_id, "age": age,
                            "P_TAET": p_taet, "kreis": rng.choice(list(AREA_OF_KREIS), size=n)})
    escorters = set(persons.loc[(position == 0) & (size_of >= 3), "person_id"])
    return persons, escorters


@pytest.mark.parametrize("min_size, escort", [(1, False), (2, True)], ids=["legacy_formula", "production_settings"])
def test_without_students_and_with_equal_rates_the_cells_draw_is_the_band_draw_bit_for_bit(tmp_path, min_size, escort):
    """No adult student and p_absent_non_student == p_absent: every cell is a band under another
    name, so the cells draw must reproduce the band draw exactly (same random stream, same residual
    formula), for both formula selections."""
    srv = _srv_copy(tmp_path, edit=lambda t: t.assign(p_absent_non_student=t["p_absent"]))
    persons, escorters = _all_band_population()
    person_cells = C.assign_cells(persons, AREA_OF_KREIS)
    assert not person_cells.str.startswith("student:").any()
    reference = D.load_absence_reference(str(srv))
    settings = {"individual_stage_min_household_size": min_size,
                "escort_protected_person_ids": escorters if escort else None}
    by_cell, _ = D.draw_absence(persons, reference, np.random.RandomState(5), **settings,
                                cell_rates=C.load_cell_reference(str(srv), AREA_OF_KREIS), cells=person_cells)
    by_band, _ = D.draw_absence(persons, reference, np.random.RandomState(5), **settings)
    assert list(by_cell.columns) == list(D.CELL_ABSENCE_COLUMNS)
    pd.testing.assert_frame_equal(by_cell[list(D.ABSENCE_COLUMNS)], by_band, check_exact=True)


def test_load_cell_reference_reads_each_cell_from_its_owner():
    rates = C.load_cell_reference(str(SRV_DIR), AREA_OF_KREIS)
    by_age = pd.read_csv(SRV_DIR / D.ABSENCE_BY_AGE_TABLE, comment="#", encoding="utf-8").set_index("band")
    away = SFI.away_share_by_area(SRV_DIR)
    expected = {**{band: by_age.loc[band, "p_absent"] for band in D.AGE_BAND_LABELS},
                **{f"adult:{band}": by_age.loc[band, "p_absent_non_student"]
                   for band in ("18-29", "30-44", "45-64", "65-74", "75+")},
                **{f"student:{area}": away[area] for area in ("braunschweig", "surrounding", "wolfsburg")}}
    assert rates == {cell: float(rate) for cell, rate in expected.items()}


def _nan_rate(table):
    table = table.copy()
    table.loc[table["band"] == "45-64", "p_absent_non_student"] = np.nan
    return table


@pytest.mark.parametrize("edit, area_of_kreis, match", [
    (_nan_rate, AREA_OF_KREIS, "45-64.*p_absent_non_student"),
    (lambda t: t.drop(columns="p_absent_non_student"), AREA_OF_KREIS, "p_absent_non_student"),
    (None, {**AREA_OF_KREIS, "03101": "elsewhere"}, "elsewhere"),
], ids=["nan_rate", "missing_column", "area_without_share"])
def test_load_cell_reference_raises_on_a_missing_rate(tmp_path, edit, area_of_kreis, match):
    srv = _srv_copy(tmp_path, edit=edit)
    with pytest.raises(ValueError, match=match):
        C.load_cell_reference(str(srv), area_of_kreis)


def test_assign_cells_puts_each_person_in_its_cell(caplog):
    persons = pd.DataFrame({
        "person_id": [10, 11, 12, 13, 14, 15, 16, 17, 18],
        "age": [22, 31, 24, 45, 16, 3, 50, 19, 70],
        "P_TAET": [STUDENT, STUDENT, STUDENT, NON_STUDENT, STUDENT, np.nan, P_TAET_NO_ANSWER, NON_STUDENT, np.nan],
        "kreis": ["03101", "03103", "03151", "03101", "03101", "03102", "03157", "03158", "03154"]})
    with caplog.at_level("INFO", logger=C.logger.name):
        result = C.assign_cells(persons, AREA_OF_KREIS)
    # Persons 16 and 18 are adults of unknown status: their own band, never the non-student cell.
    expected = pd.Series(["student:braunschweig", "student:wolfsburg", "student:surrounding", "adult:45-64",
                          "6-17", "0-5", "45-64", "adult:18-29", "65-74"],
                         index=pd.Index([10, 11, 12, 13, 14, 15, 16, 17, 18], name="person_id"),
                         name=D.CELL_COLUMN, dtype=object)
    pd.testing.assert_series_equal(result, expected)
    assert C.count_adults(result) == (7, 2)
    # Fallback transparency: 2 of the 7 adults have no readable P_TAET (person 15 is a minor).
    assert any("primary 5/7" in message and "fallback 2" in message for message in caplog.messages)


def test_an_adult_of_unknown_status_is_drawn_at_the_band_rate_of_all_persons():
    """The cell <band> of an adult carries the band's p_absent (not p_absent_non_student), and the
    draw routes the adults of unknown status there: with that cell at rate 1 and the non-student cell
    at 0 (household stage off) exactly the adults of unknown status are absent."""
    rates = C.load_cell_reference(str(SRV_DIR), AREA_OF_KREIS)
    by_age = pd.read_csv(SRV_DIR / D.ABSENCE_BY_AGE_TABLE, comment="#", encoding="utf-8").set_index("band")
    assert rates["45-64"] == float(by_age.loc["45-64", "p_absent"]) != rates["adult:45-64"]
    persons = pd.DataFrame({"person_id": range(6), "household_id": range(6), "age": 50, "kreis": "03101",
                            "P_TAET": [NON_STUDENT, P_TAET_NO_ANSWER, NON_STUDENT, np.nan, NON_STUDENT, 2]})
    out, _ = D.draw_absence(persons, TOY_REFERENCE, np.random.RandomState(2), household_stage=False,
                            cell_rates={**rates, "45-64": 1.0, "adult:45-64": 0.0},
                            cells=C.assign_cells(persons, AREA_OF_KREIS))
    absent = out.set_index("person_id")["day_absence_state"] != D.STATE_PRESENT
    assert absent[absent].index.tolist() == [1, 3]


def test_assign_cells_warns_above_the_unreadable_share_and_raises_when_no_adult_code_is_readable(caplog):
    persons = pd.DataFrame({"person_id": [1, 2, 3, 4], "age": [30, 40, 50, 60],
                            "P_TAET": [P_TAET_NO_ANSWER, STUDENT, NON_STUDENT, NON_STUDENT], "kreis": "03101"})
    with caplog.at_level("WARNING", logger=C.logger.name):
        C.assign_cells(persons, AREA_OF_KREIS, max_unreadable_share=0.2)
    assert any(r.levelname == "WARNING" and "P_TAET" in r.getMessage() for r in caplog.records)
    with pytest.raises(ValueError, match="P_TAET"):
        C.assign_cells(persons.assign(P_TAET=np.nan), AREA_OF_KREIS)


def test_assign_cells_raises_on_a_kreis_outside_the_region():
    persons = pd.DataFrame({"person_id": [1, 2], "age": [30, 8], "P_TAET": [NON_STUDENT, NON_STUDENT],
                            "kreis": ["03101", "09162"]})
    with pytest.raises(ValueError, match="09162"):
        C.assign_cells(persons, AREA_OF_KREIS)


def test_a_cell_that_cannot_reach_its_rate_warns_naming_the_cell(caplog):
    """100 students living alone and 10 student couples at a cell rate of 50 %: with the minimum
    household size 2 only the 20 couple members are eligible, so the cell cannot reach its rate."""
    household_id, _, _ = _household_frame([1] * 100 + [2] * 10)
    persons = pd.DataFrame({"person_id": np.arange(len(household_id)), "household_id": household_id, "age": 23,
                            "P_TAET": STUDENT, "kreis": "03101"})
    rates = {**TOY_CELL_RATES, "student:braunschweig": 0.5}
    with caplog.at_level("WARNING", logger=D.logger.name):
        _, diagnostics = D.draw_absence(persons, TOY_REFERENCE, np.random.RandomState(3),
                                        individual_stage_min_household_size=2, cell_rates=rates,
                                        cells=C.assign_cells(persons, AREA_OF_KREIS))
    assert diagnostics["n_cells_unreachable"] == 1
    assert any("student:braunschweig" in message and "cannot reach" in message for message in caplog.messages)


def test_draw_absence_refuses_cells_without_rates_and_persons_without_a_cell():
    persons, _, _ = _toy_population()
    persons = persons.head(50)
    person_cells = C.assign_cells(persons, AREA_OF_KREIS)
    with pytest.raises(ValueError, match="together"):
        D.draw_absence(persons, TOY_REFERENCE, np.random.RandomState(1), cells=person_cells)
    with pytest.raises(ValueError, match="no absence cell"):
        D.draw_absence(persons, TOY_REFERENCE, np.random.RandomState(1), cell_rates=TOY_CELL_RATES,
                       cells=person_cells.iloc[1:])
    without_student_rate = {k: v for k, v in TOY_CELL_RATES.items() if k != "student:braunschweig"}
    with pytest.raises(ValueError, match="student:braunschweig"):
        D.draw_absence(persons, TOY_REFERENCE, np.random.RandomState(1), cell_rates=without_student_rate,
                       cells=person_cells)


def test_the_repeated_no_answer_code_equals_its_owner():
    """cells.py repeats the MiD no-answer code because of the import boundary; pinned here."""
    assert C.P_TAET_NO_ANSWER == P_TAET_NO_ANSWER
