"""Tests for all attribute mappers routing through missing.resolve.

Verifies that item-nonresponse codes (9, 99) are IMPUTED (not silently mapped to a
fixed default) and that structural codes (403, 404, 202, 402, 206) are resolved
deterministically, using the uniform missing policy from ``braunschweig.popsim.missing``.

Covers: map_has_license, map_number_of_cars (Task 2) and map_employed, map_economic_status,
map_household_income, map_household_income_eur, map_has_pt_subscription,
map_number_of_bicycles (Task 2b).
"""
from __future__ import annotations

import numpy as np
import pytest
import pandas as pd

from braunschweig.data.mid.reference_tables import PT_TICKET_FLATRATE
from braunschweig.popsim import attributes as a


def test_mid_attr_cols_include_conditioning_columns():
    from braunschweig.popsim import mid
    assert "alter_gr1" in mid.MID_PERSON_ATTR_COLS
    assert "hhgr_gr" in mid.MID_HOUSEHOLD_ATTR_COLS


def test_has_license_structural_child_is_false_nonresponse_imputed():
    persons = pd.DataFrame({
        "P_FSCHEIN": [1, 2, 403, 9],
        "HP_ALTER": [40, 41, 10, 42],
        "alter_gr1": [3, 3, 1, 3],
    })
    out = a.map_has_license(persons, rng=np.random.RandomState(0))
    # code 1 -> True, code 2 -> False, code 403 (structural under-age) -> False
    assert out["has_license"].tolist()[:3] == [True, False, False]
    # code 9 is nonresponse: must be imputed to a bool (True or False), never NaN
    assert out["has_license"].tolist()[3] in (True, False)
    assert out["has_license"].isna().sum() == 0


def test_number_of_cars_missing_is_imputed_not_silently_zero():
    households = pd.DataFrame({"H_ANZAUTO": [0, 2, 99], "hhgr_gr": [1, 2, 2]})
    out = a.map_number_of_cars(households, rng=np.random.RandomState(0))
    # 99 (keine Angabe) must be imputed, not silently set to 0
    assert out["number_of_cars"].isna().sum() == 0
    assert (out["number_of_cars"] >= 0).all()
    # the imputed value for row 2 (hhgr_gr=2, same as row 1 with cars=2) should
    # be drawn from the valid pool for hhgr_gr=2 -> only valid value is 2
    assert out["number_of_cars"].iloc[2] == 2


def test_license_adult_coverage_code_is_imputed_not_false():
    import numpy as np, pandas as pd
    from braunschweig.popsim import attributes
    df = pd.DataFrame({
        "P_FSCHEIN": [1, 1, 1, 1, 202, 404],
        "HP_ALTER": [40, 41, 42, 43, 44, 45],
        "alter_gr1": [5, 5, 5, 5, 5, 5],
    })
    out = attributes.map_has_license(df, rng=np.random.RandomState(0))
    assert out["has_license"].tolist() == [True, True, True, True, True, True]


def test_license_underage_code_403_is_false():
    import numpy as np, pandas as pd
    from braunschweig.popsim import attributes
    df = pd.DataFrame({"P_FSCHEIN": [1, 403], "HP_ALTER": [40, 10], "alter_gr1": [5, 1]})
    out = attributes.map_has_license(df, rng=np.random.RandomState(0))
    assert out["has_license"].tolist() == [True, False]


def test_has_license_no_rng_backward_compatible():
    """Callers that omit rng must not raise; default rng is applied."""
    persons = pd.DataFrame({"P_FSCHEIN": [1, 2, 9], "HP_ALTER": [40, 41, 42]})
    out = a.map_has_license(persons)  # no rng -> must not raise
    assert out["has_license"].isna().sum() == 0


def test_number_of_cars_no_rng_backward_compatible():
    """Callers that omit rng must not raise; default rng is applied."""
    households = pd.DataFrame({"H_ANZAUTO": [0, 1, 99]})
    out = a.map_number_of_cars(households)  # no rng -> must not raise
    assert out["number_of_cars"].isna().sum() == 0
    assert (out["number_of_cars"] >= 0).all()


# ---------------------------------------------------------------------------
# Task 2b: employment, economic status, income, PT subscription, bicycles
# ---------------------------------------------------------------------------

def test_employed_valid_codes_map_to_existing_semantics():
    """MiD `erwerb`: P_TAET in {1,2,3,4,6,8} -> True; 5 (Elternzeit), 7 (FSJ/Wehrdienst)
    and 9..17 -> False; no NaN.

    Pinned to the official MiD ``erwerb`` definition (``EMPLOYED_TAET`` / ``map_employed``,
    commit d6556b6 "unify employed = MiD erwerb"). Note 8 (Auszubildende) -> True and
    7 (FSJ/Wehrdienst) -> False, which the earlier naive "1..7 True / 8..16 False"
    placeholder got backwards.
    """
    persons = pd.DataFrame({"P_TAET": [1, 8, 5, 7, 16]})
    out = a.map_employed(persons, rng=np.random.RandomState(0))
    assert out["employed"].tolist() == [True, True, False, False, False]
    assert out["employed"].isna().sum() == 0


def test_employed_pupils_code9_map_to_false_not_imputed():
    """Schueler (P_TAET=9) must map to employed=False, not be imputed.

    Regression for issue #96: the generic item-nonresponse set contains 9, which
    for the two-digit P_TAET field is the substantive category 'Schueler/in'
    (keine Angabe is 99). Before the fix, every pupil was treated as nonresponse
    and imputed from the non-pupil valid pool of its age band -- in the 14-17
    band that pool is dominated by Azubis (P_TAET=8 -> True), inflating pupil
    employment to ~96 %. All pupils must instead be deterministically False.
    """
    # A 14-17 age band: 900 Schueler (9) + 100 Azubi (8), one alter_gr1 band.
    persons = pd.DataFrame({
        "P_TAET": [9] * 900 + [8] * 100,
        "alter_gr1": [3] * 1000,
    })
    out = a.map_employed(persons, rng=np.random.RandomState(0))
    pupils = out.iloc[:900]
    azubis = out.iloc[900:]
    assert pupils["employed"].sum() == 0          # no pupil is employed
    assert azubis["employed"].all()               # every Azubi stays employed
    assert out["employed"].isna().sum() == 0


def test_household_income_group9_maps_to_bracket_not_imputed():
    """hheink_gr1=9 is the substantive income group 4000-4600 EUR (keine Angabe
    is 99), so it must map to the group-9 midpoint, not be imputed.

    Same field-width collision as issue #96: 9 is in the generic nonresponse set
    but is a valid enumerated value_map key here.
    """
    hh = pd.DataFrame({"hheink_gr1": [9, 9, 9, 9], "hhgr_gr": [2, 2, 2, 2]})
    out = a.map_household_income_eur(hh, rng=np.random.RandomState(0))
    assert (out["household_income_eur"] == 4300.0).all()
    assert out["household_income_eur"].isna().sum() == 0


def test_employed_nonresponse_is_imputed():
    """P_TAET=99 (keine Angabe) must be imputed from the valid pool, never NaN."""
    persons = pd.DataFrame({"P_TAET": [1, 2, 8, 99], "alter_gr1": [3, 3, 3, 3]})
    out = a.map_employed(persons, rng=np.random.RandomState(0))
    assert out["employed"].iloc[3] in (True, False)
    assert out["employed"].isna().sum() == 0


def test_employed_no_rng_backward_compatible():
    """Callers that omit rng must not raise; default rng is applied."""
    persons = pd.DataFrame({"P_TAET": [1, 8, 99]})
    out = a.map_employed(persons)
    assert out["employed"].isna().sum() == 0


def test_economic_status_valid_codes_map_correctly():
    """oek_status 1..5 map to the existing class strings; no NaN in output."""
    hh = pd.DataFrame({"oek_status": [1, 2, 3, 4, 5]})
    out = a.map_economic_status(hh, rng=np.random.RandomState(0))
    assert list(out["economic_status"]) == ["very_low", "low", "medium", "high", "very_high"]
    assert out["economic_status"].isna().sum() == 0


def test_economic_status_nonresponse_imputed_within_group():
    """oek_status=9 is imputed from the valid pool in the same hhgr_gr group."""
    hh = pd.DataFrame({
        "oek_status": [3, 4, 9],
        "hhgr_gr": [2, 2, 2],
    })
    out = a.map_economic_status(hh, rng=np.random.RandomState(0))
    # imputed value must be a valid class string, not None/NaN
    assert out["economic_status"].iloc[2] in ("very_low", "low", "medium", "high", "very_high")
    assert out["economic_status"].isna().sum() == 0


def test_economic_status_no_rng_backward_compatible():
    """Callers that omit rng must not raise."""
    hh = pd.DataFrame({"oek_status": [1, 9]})
    out = a.map_economic_status(hh)
    assert out["economic_status"].isna().sum() == 0


def test_household_income_valid_codes_map_correctly():
    """hheink_gr1 1..15 map to the existing class strings; no NaN for valid codes."""
    hh = pd.DataFrame({"hheink_gr1": [1, 3, 15]})
    out = a.map_household_income(hh, rng=np.random.RandomState(0))
    assert list(out["household_income"]) == ["under_500", "900_1500", "over_7000"]
    assert out["household_income"].isna().sum() == 0


def test_household_income_nonresponse_imputed():
    """hheink_gr1=99 is imputed from the valid pool in the same hhgr_gr group."""
    hh = pd.DataFrame({
        "hheink_gr1": [3, 5, 99],
        "hhgr_gr": [2, 2, 2],
    })
    out = a.map_household_income(hh, rng=np.random.RandomState(0))
    assert out["household_income"].iloc[2] in (
        "under_500", "500_900", "900_1500", "1500_2000", "2000_2600",
        "2600_3000", "3000_3600", "3600_4000", "4000_4600", "4600_5000",
        "5000_5600", "5600_6000", "6000_6600", "6600_7000", "over_7000",
    )
    assert out["household_income"].isna().sum() == 0


def test_household_income_eur_valid_codes_map_correctly():
    """hheink_gr1 1..15 map to EUR midpoints; no NaN for valid codes."""
    hh = pd.DataFrame({"hheink_gr1": [1, 3, 15]})
    out = a.map_household_income_eur(hh, rng=np.random.RandomState(0))
    assert list(out["household_income_eur"]) == [250.0, 1200.0, 8000.0]
    assert out["household_income_eur"].isna().sum() == 0


def test_household_income_eur_nonresponse_imputed():
    """hheink_gr1=99 is imputed from the valid pool, producing a numeric EUR value."""
    hh = pd.DataFrame({
        "hheink_gr1": [3, 5, 99],
        "hhgr_gr": [2, 2, 2],
    })
    out = a.map_household_income_eur(hh, rng=np.random.RandomState(0))
    assert pd.notna(out["household_income_eur"].iloc[2])
    assert out["household_income_eur"].iloc[2] > 0.0
    assert out["household_income_eur"].isna().sum() == 0


def test_pt_subscription_structural_and_nonresponse():
    """P_FKARTE structural (402 = Kind<14) resolves to False; 99 is imputed."""
    persons = pd.DataFrame({"P_FKARTE": [3, 8, 402, 99], "alter_gr1": [3, 3, 1, 3]})
    out = a.map_has_pt_subscription(
        a.map_pt_subscription_type(persons, rng=np.random.RandomState(0)))
    assert out["has_pt_subscription"].tolist()[:3] == [True, False, False]
    assert out["has_pt_subscription"].isna().sum() == 0


def test_pt_subscription_adult_coverage_codes_are_imputed_not_false():
    """Adult interview-mode codes 202 (PAPI) and 206 (Proxy, adult >=14) are imputed.

    They are NOT "no ticket" -- they are coverage / interview-mode design-missings on
    persons of subscription age (MiD 2023 Handbuch Tab. 3, first-digit 2 = Interviewart;
    206 = Erwachsener ab 14 Proxy/Stellvertreter). They must be imputed from comparable
    adult respondents, not forced to False. With a valid pool of all-True donors in the
    same age band, the imputed value is therefore True.
    """
    persons = pd.DataFrame({
        "P_FKARTE": [5, 5, 5, 202, 206],
        "alter_gr1": [5, 5, 5, 5, 5],
    })
    out = a.map_has_pt_subscription(
        a.map_pt_subscription_type(persons, rng=np.random.RandomState(0)))
    # 202/206 imputed from the valid pool (all code 5 -> True) -> True, not deterministic False
    assert out["has_pt_subscription"].tolist() == [True, True, True, True, True]
    assert out["has_pt_subscription"].isna().sum() == 0


def test_pt_under14_floor_kept_adult_coverage_imputed():
    import numpy as np, pandas as pd
    from braunschweig.popsim import attributes
    df = pd.DataFrame({
        "P_FKARTE":  [3, 3, 3, 402, 206],
        "alter_gr1": [5, 5, 5, 1, 5],
    })
    out = attributes.map_pt_subscription_type(df, rng=np.random.RandomState(0))
    assert out["pt_subscription_type"].iloc[3] == "never_pt"          # under-14 floor kept
    assert out["pt_subscription_type"].iloc[4] in attributes.FKARTE_TO_CATEGORY.values()  # imputed to a real category
    # With an all-deutschlandticket donor pool in the same age band, the adult coverage
    # code (206) must be imputed to that category -- not deterministically forced to
    # "never_pt" (which the previous structural mapping would have done).
    assert out["pt_subscription_type"].iloc[4] == "deutschlandticket"


def test_pt_subscription_nonresponse_imputed():
    """P_FKARTE=99 (keine Angabe) is imputed to a bool, never NaN."""
    persons = pd.DataFrame({
        "P_FKARTE": [3, 8, 99],
        "alter_gr1": [2, 2, 2],
    })
    out = a.map_has_pt_subscription(
        a.map_pt_subscription_type(persons, rng=np.random.RandomState(0)))
    assert out["has_pt_subscription"].iloc[2] in (True, False)
    assert out["has_pt_subscription"].isna().sum() == 0


def test_pt_subscription_type_no_rng_backward_compatible():
    """Callers that omit rng must not raise.

    The rng belongs to the CATEGORY mapper only: since #319 the boolean is a pure
    derivation and takes no rng at all.
    """
    persons = pd.DataFrame({"P_FKARTE": [3, 99]})
    out = a.map_has_pt_subscription(a.map_pt_subscription_type(persons))
    assert out["has_pt_subscription"].isna().sum() == 0
    assert out["pt_subscription_type"].isna().sum() == 0


def test_number_of_bicycles_valid_codes_map_correctly():
    """anzpedrad 0..10 map identity; no NaN for valid codes.

    anzpedrad = bicycles INCLUDING pedelecs/e-bikes (MiD H12.3 / SrV alle-Raeder
    construct; the default source column since the 2026-07-08 construct fix)."""
    hh = pd.DataFrame({"anzpedrad": [0, 3, 10]})
    out = a.map_number_of_bicycles(hh, rng=np.random.RandomState(0))
    assert list(out["number_of_bicycles"]) == [0, 3, 10]
    assert out["number_of_bicycles"].isna().sum() == 0


def test_number_of_bicycles_missing_is_imputed_not_silently_zero():
    """anzpedrad=99 is imputed from the valid pool in the same hhgr_gr group, not forced to 0."""
    hh = pd.DataFrame({"anzpedrad": [0, 2, 99], "hhgr_gr": [1, 2, 2]})
    out = a.map_number_of_bicycles(hh, rng=np.random.RandomState(0))
    assert out["number_of_bicycles"].isna().sum() == 0
    assert (out["number_of_bicycles"] >= 0).all()
    # hhgr_gr=2 valid pool contains only value 2 -> imputed value must be 2
    assert out["number_of_bicycles"].iloc[2] == 2


def test_number_of_bicycles_no_rng_backward_compatible():
    """Callers that omit rng must not raise."""
    hh = pd.DataFrame({"anzpedrad": [0, 1, 99]})
    out = a.map_number_of_bicycles(hh)
    assert out["number_of_bicycles"].isna().sum() == 0
    assert (out["number_of_bicycles"] >= 0).all()


def test_pt_subscription_boolean_equals_flatrate_of_resolved_type():
    """``has_pt_subscription`` must be exactly the flatrate subset of the resolved
    ``pt_subscription_type``.

    Both attributes are derived from the SAME MiD column ``P_FKARTE``. Resolving them
    through two INDEPENDENT ``missing.resolve`` draws lets them disagree for the imputed
    codes 99 / 202 / 206 -- measured at 9,723 persons (0.86%) on the 100% population,
    bidirectionally (issue #319). The boolean is what
    ``BraunschweigPtCostModel.calculateCost_MU`` reads (holders pay zero fare on every PT
    trip); the category is what the MiD P24.1 validation compares. A disagreement means
    the simulated and the validated population are not the same people.

    The fixture deliberately mixes flatrate (3, 5) and non-flatrate (1, 8) donors inside
    the same conditioning band, because a single-valued donor pool would agree by
    construction and prove nothing. The rng is ADVANCED before the type is resolved for
    the same reason: both mappers default to ``RandomState(0)``, so an unadvanced stream
    makes an independent second draw coincide with the first by accident and the test
    passes while the defect is still there.
    """
    rng = np.random.RandomState(0)
    rng.rand(37)
    persons = pd.DataFrame({
        "P_FKARTE":  [3, 1, 5, 8, 99, 202, 206, 99, 202, 206, 402],
        "alter_gr1": [5, 5, 5, 5, 5,   5,   5,   4,   4,   4,   1],
    })
    out = a.map_pt_subscription_type(persons, rng=rng)
    out = a.map_has_pt_subscription(out)
    expected = out["pt_subscription_type"].isin(PT_TICKET_FLATRATE).tolist()
    assert out["has_pt_subscription"].tolist() == expected


def test_has_pt_subscription_requires_the_resolved_type_column():
    """Without ``pt_subscription_type`` the boolean cannot be derived, and drawing it
    independently is the bug of #319 -- so the mapper must fail loudly instead of
    silently re-drawing (CLAUDE.md: no silent fallbacks)."""
    persons = pd.DataFrame({"P_FKARTE": [3, 99], "alter_gr1": [5, 5]})
    with pytest.raises(KeyError, match="pt_subscription_type"):
        a.map_has_pt_subscription(persons)


# ---------------------------------------------------------------------------
# Issue #321: the three-group PT ticket control seed column
# ---------------------------------------------------------------------------


def test_pt_ticket_group_collapses_the_resolved_type_into_three_groups():
    """The control steers 3 groups, not the 9 P24.1 categories.

    ``BraunschweigPtCostModel.calculateCost_MU`` returns 0.0 for every flatrate holder, so
    the four flatrate TYPES are simulation-equivalent and the non-flatrate split has no
    effect at all. Controlling all 9 categories per Kreis would spend 72 control columns on
    mostly simulation-neutral structure; these 3 groups spend 24 and keep the
    Deutschlandticket separately steerable (the only flatrate category with a second
    committed survey).
    """
    persons = pd.DataFrame({"pt_subscription_type": [
        "deutschlandticket", "monthly_or_annual_subscription", "job_or_semester_ticket",
        "weekly_monthly_no_subscription", "single_ticket", "never_pt", "other_ticket",
        "multi_ride_ticket"]})
    out = a.map_pt_ticket_group(persons)
    assert out["pt_ticket_group"].tolist() == [
        "deutschlandticket", "other_flatrate", "other_flatrate", "other_flatrate",
        "not_flatrate", "not_flatrate", "not_flatrate", "not_flatrate"]


def test_pt_ticket_group_flatrate_groups_reproduce_the_flatrate_definition():
    """``deutschlandticket`` + ``other_flatrate`` must be exactly PT_TICKET_FLATRATE.

    This is what makes the control steer the quantity eqasim reads: the sum of the two
    flatrate groups is ``has_pt_subscription`` by construction, so raking the groups cannot
    drift away from the boolean (the #319 failure mode).
    """
    covered = {"deutschlandticket"} | set(a.PT_TICKET_OTHER_FLATRATE)
    assert covered == set(PT_TICKET_FLATRATE)
    assert "deutschlandticket" not in a.PT_TICKET_OTHER_FLATRATE


def test_pt_ticket_group_requires_the_resolved_type_column():
    """Deriving the group from the raw P_FKARTE code would re-open the #319 defect (a
    second independent resolution of the imputed codes), so the mapper demands the
    already-resolved category and fails loudly without it."""
    with pytest.raises(KeyError, match="pt_subscription_type"):
        a.map_pt_ticket_group(pd.DataFrame({"P_FKARTE": [3, 99]}))


def test_pt_ticket_group_is_a_registered_soft_person_control_on_the_mid_14plus_base():
    """The #321 control: person level, soft tier, 14+ universe, three categories.

    soft rather than hard because the level itself is uncertain -- the MiD P24.1
    Deutschlandticket component sits ~4pp above the committed SrV figure, so its flatrate
    aggregate may be biased high; a hard control would force a level the evidence does not
    pin down. min_age=14 because P24.1 is a 14+ table (the #97 universe trap).
    """
    from braunschweig.popsim import kreis_attribute_control as kac

    entry = next(c for c in kac.REGISTRY if c.name == "pt_ticket_group")
    assert entry.level == "person"
    assert entry.tier == "soft"
    assert entry.min_age == 14
    assert entry.seed_column == "pt_ticket_group"
    assert tuple(label for label, _ in entry.categories) == a.PT_TICKET_GROUPS
    assert entry.target_columns == a.PT_TICKET_GROUPS
    assert entry.target_csv_relpath.endswith("target2026_pt_ticket_group_by_kreis.csv")


def test_pt_ticket_group_control_target_loads_and_partitions_every_kreis():
    """The committed target must load through the production loader for all 8 Kreise --
    a missing or non-normalised row would only surface at run time otherwise."""
    from braunschweig.popsim import kreis_attribute_control as kac

    entry = next(c for c in kac.REGISTRY if c.name == "pt_ticket_group")
    target = kac.load_kreis_target(
        "eqasim-data/data", entry,
        expected_ars5=("03101", "03102", "03103", "03151", "03153", "03154",
                       "03157", "03158"),
        share_tolerance=1e-3)
    # 8 Kreise + the region row, which the loader keeps as the shrinkage prior.
    assert set(target["ars5"]) == {"03101", "03102", "03103", "03151", "03153",
                                   "03154", "03157", "03158", "Gesamt"}
    assert list(target.columns) == ["ars5", *a.PT_TICKET_GROUPS]


def test_pt_ticket_group_rendered_control_carries_the_age_clause():
    """Both halves of the control must share the 14+ base: the target is a 14+ table, so
    the seed expression has to restrict the synthetic side the same way."""
    from braunschweig.popsim import control_spec as cs
    from braunschweig.popsim import kreis_attribute_control as kac

    entry = [c for c in kac.REGISTRY if c.name == "pt_ticket_group"]
    rendered = {c.name: c.seed_expressions["mid"] for c in cs.attribute_kreis_controls(entry)}
    assert rendered["pt_ticket_group_deutschlandticket"] == (
        "(persons.pt_ticket_group == 'deutschlandticket') & (persons.HP_ALTER >= 14)")
    assert set(rendered) == {f"pt_ticket_group_{g}" for g in a.PT_TICKET_GROUPS}


# ---------------------------------------------------------------------------
# license_underage (smoke finding 2026-08-19): the under-16 structural floor
# ---------------------------------------------------------------------------


def test_license_coverage_code_on_a_child_is_structurally_false():
    """P_FSCHEIN=202 on an under-16 person resolves to False, never to an imputed value.

    The codebook basis of the structural code 403 is 'Person unter 16 Jahren', but PAPI
    households carry 202 ('im PAPI nicht erhoben') for their children instead of 403.
    202 sits in impute_codes (correct for adults, the #131 fix), and an under-16 band has
    NO valid donor codes at all, so the imputation fell through to the global adult pool:
    61.6% of 202-children received has_license=True (7,088 persons in the 03101 smoke,
    2.8-3.8% of the population in every run since). A person under 16 cannot hold a Pkw
    licence regardless of WHY the item was not collected.

    The donor pool here is all-True adults on purpose: if the floor were missing, the
    child would be imputed True and this test fails.
    """
    persons = pd.DataFrame({
        "P_FSCHEIN": [1, 1, 1, 202, 202],
        "HP_ALTER": [40, 45, 50, 10, 15],
        "alter_gr1": [5, 5, 5, 1, 2],
    })
    out = a.map_has_license(persons, rng=np.random.RandomState(0))
    assert out["has_license"].tolist()[:3] == [True, True, True]
    assert out["has_license"].tolist()[3:] == [False, False]


def test_license_coverage_code_on_an_adult_is_still_imputed():
    """The adult 202/404 imputation (the #131 fix) must survive the under-16 floor:
    forcing adults back to False would re-open the 52%-licence-share defect."""
    persons = pd.DataFrame({
        "P_FSCHEIN": [1, 1, 1, 202],
        "HP_ALTER": [40, 45, 50, 42],
        "alter_gr1": [5, 5, 5, 5],
    })
    out = a.map_has_license(persons, rng=np.random.RandomState(0))
    # all-True valid pool in the same band -> the adult 202 imputes to True
    assert out["has_license"].tolist() == [True, True, True, True]


def test_license_floor_requires_the_age_column():
    """Without the raw age the floor cannot be applied; silently skipping it would
    reintroduce the defect for exactly the frames where it hid before (no silent
    fallback)."""
    persons = pd.DataFrame({"P_FSCHEIN": [1, 202], "alter_gr1": [5, 1]})
    with pytest.raises(KeyError, match="HP_ALTER"):
        a.map_has_license(persons, rng=np.random.RandomState(0))
