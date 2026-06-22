"""Byte-identity + determinism gate for the extracted completed-donor build.

Tier B2 moves the MiD member-completion + weekend-plan-match donor build out of
``braunschweig.popsim.stage.execute`` into ``build_completed_donor``. Member
completion and weekend-plan match share ONE seeded RNG instance
(``np.random.RandomState(random_seed + 74513)``); the extracted function MUST
reproduce the EXACT frames the current inline sequence produces. If this test
cannot be made green, Tier B2 is dropped (Tier A + B1 still stand).

See docs/superpowers/specs/2026-06-22-tier-a-b-caching-design.md.
"""
import numpy as np
import pandas as pd

from braunschweig.popsim import completed_donor as cd
from braunschweig.popsim import mid, seed as seedmod, weekend_plan_match


def _write_mid_attribute_fixture(tmp_path):
    """Two complete weekday households (kept by the default day filter) plus a
    weekend-reporting household (kernwo=4) so the weekend-plan-match branch runs.
    Mirrors tests/test_popsim_member_completion_integration.py."""
    (tmp_path / "MiD2023_Haushalte.csv").write_text(
        "H_ID,oek_status,hheink_gr1,H_ANZAUTO,H_ANZRAD,RegioStaR7,hhgr_gr,H_GR,H_GEW,H_MIETE,haustyp\n"
        "A,3,4,1,2,73,4,4,1.0,1,1\n"
        "B,3,4,1,2,73,4,4,1.0,2,2\n"
        "C,3,4,1,2,73,4,4,1.0,2,2\n",
        encoding="utf-8",
    )
    (tmp_path / "MiD2023_Personen.csv").write_text(
        "H_ID,P_ID,HP_ALTER,HP_SEX,P_TAET,P_FSCHEIN,P_FKARTE,P_BKAT,alter_gr1,P_GEW,kernwo\n"
        "A,1,40,1,1,1,3,1,5,1.0,1\n"
        "A,2,38,2,1,1,3,1,5,1.0,1\n"
        "B,1,41,1,1,1,3,1,5,1.0,1\n"
        "B,2,39,2,1,1,3,1,5,1.0,1\n"
        "B,3,10,1,5,2,3,4,2,1.0,1\n"
        "B,4,8,2,5,2,3,4,1,1.0,1\n"
        "C,1,41,1,1,1,3,1,5,1.0,4\n"
        "C,2,39,2,1,1,3,1,5,1.0,4\n"
        "C,3,10,1,5,2,3,4,2,1.0,4\n"
        "C,4,8,2,5,2,3,4,1,1.0,4\n",
        encoding="utf-8",
    )


def _inline_reference(mid_dir, *, random_seed, weekend_plan_match_on):
    """The CURRENT inline sequence from stage.execute (the reference to match)."""
    completion_rng = np.random.RandomState(random_seed + 74513)
    day_filter = seedmod.ALL_REPORTING_KERNWO if weekend_plan_match_on else None
    households, persons, completeness_report, completion_report = mid.load_completed_donor(
        mid_dir, completion_rng=completion_rng, day_filter_values=day_filter,
    )
    if weekend_plan_match_on:
        persons, _trace, _report = weekend_plan_match.reassign_weekend_plan_sources(
            households, persons, rng=completion_rng,
        )
    return households, persons


def test_build_completed_donor_matches_inline_with_weekend_match(tmp_path):
    _write_mid_attribute_fixture(tmp_path)
    ref_hh, ref_persons = _inline_reference(
        tmp_path, random_seed=1234, weekend_plan_match_on=True,
    )
    result = cd.build_completed_donor(
        tmp_path, random_seed=1234, seed_day_filter=None, weekend_plan_match_on=True,
    )
    pd.testing.assert_frame_equal(result.households, ref_hh)
    pd.testing.assert_frame_equal(result.persons, ref_persons)


def test_build_completed_donor_matches_inline_without_weekend_match(tmp_path):
    _write_mid_attribute_fixture(tmp_path)
    ref_hh, ref_persons = _inline_reference(
        tmp_path, random_seed=1234, weekend_plan_match_on=False,
    )
    result = cd.build_completed_donor(
        tmp_path, random_seed=1234, seed_day_filter=None, weekend_plan_match_on=False,
    )
    pd.testing.assert_frame_equal(result.households, ref_hh)
    pd.testing.assert_frame_equal(result.persons, ref_persons)


def test_build_completed_donor_is_deterministic(tmp_path):
    _write_mid_attribute_fixture(tmp_path)
    a = cd.build_completed_donor(
        tmp_path, random_seed=7, seed_day_filter=None, weekend_plan_match_on=True,
    )
    b = cd.build_completed_donor(
        tmp_path, random_seed=7, seed_day_filter=None, weekend_plan_match_on=True,
    )
    pd.testing.assert_frame_equal(a.persons, b.persons)
    pd.testing.assert_frame_equal(a.households, b.households)
