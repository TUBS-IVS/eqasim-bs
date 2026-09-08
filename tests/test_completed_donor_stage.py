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
from braunschweig.popsim import diary_facts, diary_plan_match
from braunschweig.popsim import mid, seed as seedmod, weekend_plan_match


def _write_mid_attribute_fixture(tmp_path):
    """Two complete weekday households (kept by the default day filter) plus a
    weekend-reporting household (kernwo=4) so the weekend-plan-match branch runs.
    Mirrors tests/test_popsim_member_completion_integration.py.

    Also writes a MiD2023_Wege.csv fixture (two direct legs H->work->home for
    every fixture person) and carries the diary-plan-match mobility columns
    (mobil, mobil_diff, feiertag) on the persons fixture, needed by
    diary_plan_match.reassign_diaryless_plan_sources (Task 3, issue #365)."""
    # Households are numbered (1, 2, 3), not lettered: the diary-plan-match test
    # helper (_make_one_person_diaryless, tests/test_completed_donor_diary_match.py)
    # casts H_ID/P_ID to int, matching the real MiD delivery's numeric ids.
    (tmp_path / "MiD2023_Haushalte.csv").write_text(
        "H_ID,oek_status,hheink_gr1,H_ANZAUTO,H_ANZRAD,anzpedrad,H_ANZPED,RegioStaR7,hhgr_gr,H_GR,H_GEW,H_MIETE,haustyp\n"
        "1,3,4,1,2,2,0,73,4,4,1.0,1,1\n"
        "2,3,4,1,2,2,0,73,4,4,1.0,2,2\n"
        "3,3,4,1,2,2,0,73,4,4,1.0,2,2\n",
        encoding="utf-8",
    )
    # anzwege1 (diary trip count) is part of MID_PERSON_ATTR_COLS (person-level
    # trip_class control), so the fixture includes it after alter_gr1. mobil /
    # mobil_diff / feiertag are optional MID_PERSON_OPTIONAL_COLS (Task 1); all
    # fixture persons are valid mobile weekday reporters (mobil=1, mobil_diff=1,
    # feiertag=0) so the diary plan match runs without a fixture-induced remap.
    (tmp_path / "MiD2023_Personen.csv").write_text(
        "H_ID,P_ID,HP_ALTER,HP_SEX,P_TAET,P_FSCHEIN,P_FKARTE,P_BKAT,alter_gr1,anzwege1,P_GEW,kernwo,mobil,mobil_diff,feiertag\n"
        "1,1,40,1,1,1,3,1,5,3,1.0,1,1,1,0\n"
        "1,2,38,2,1,1,3,1,5,2,1.0,1,1,1,0\n"
        "2,1,41,1,1,1,3,1,5,4,1.0,1,1,1,0\n"
        "2,2,39,2,1,1,3,1,5,0,1.0,1,1,1,0\n"
        "2,3,10,1,5,2,3,4,2,1,1.0,1,1,1,0\n"
        "2,4,8,2,5,2,3,4,1,1,1.0,1,1,1,0\n"
        "3,1,41,1,1,1,3,1,5,3,1.0,4,1,1,0\n"
        "3,2,39,2,1,1,3,1,5,2,1.0,4,1,1,0\n"
        "3,3,10,1,5,2,3,4,2,1,1.0,4,1,1,0\n"
        "3,4,8,2,5,2,3,4,1,1,1.0,4,1,1,0\n",
        encoding="utf-8",
    )
    # MiD Wege (trip) fixture: two direct legs H->work->home for every fixture
    # person (W_ZWECK 1 = way to work, then 8 = way home; W_RBW 0 = not a
    # rbW summary leg; W_SO1 1 on the first leg = diary starts at home, 809 =
    # not-applicable placeholder on the second leg, since only the FIRST leg's
    # W_SO1 feeds diary_facts.compute_diary_facts). Columns copied verbatim
    # from braunschweig.popsim.mid.donor.MID_WEGE_REQUIRED_COLS.
    _wege_persons = [
        (1, 1), (1, 2), (2, 1), (2, 2), (2, 3), (2, 4),
        (3, 1), (3, 2), (3, 3), (3, 4),
    ]
    _wege_rows = [
        "H_ID,P_ID,W_ID,W_ZWECK,hvm_imp,W_SZS,W_SZM,W_AZS,W_AZM,wegkm_imp,wegmin_imp1,W_RBW,W_SO1"
    ]
    for h_id, p_id in _wege_persons:
        _wege_rows.append(f"{h_id},{p_id},1,1,4,7,0,7,30,10.0,30,0,1")
        _wege_rows.append(f"{h_id},{p_id},2,8,4,17,0,17,30,10.0,30,0,809")
    (tmp_path / "MiD2023_Wege.csv").write_text("\n".join(_wege_rows) + "\n", encoding="utf-8")


def _inline_reference(mid_dir, *, random_seed, weekend_plan_match_on, diary_plan_match_on):
    """The reference sequence to match, at two possible stopping points.

    With ``diary_plan_match_on=False`` this STOPS after member completion + weekend
    match -- the exact pre-Task-3 pipeline (``mid.load_completed_donor`` +
    ``weekend_plan_match.reassign_weekend_plan_sources``, sharing ONE completion_rng,
    and nothing else): this is the real OFF byte-identity contract (fix round 1,
    Important-1). With ``diary_plan_match_on=True`` it CONTINUES with the diary plan
    match + plan-source fact attachment (Task 3, issue #365), with their default
    flags (ON), continuing the SAME completion_rng -- mirrors
    build_completed_donor's default ON path exactly."""
    completion_rng = np.random.RandomState(random_seed + 74513)
    day_filter = seedmod.ALL_REPORTING_KERNWO if weekend_plan_match_on else None
    households, persons, completeness_report, completion_report = mid.load_completed_donor(
        mid_dir, completion_rng=completion_rng, day_filter_values=day_filter,
    )
    if weekend_plan_match_on:
        persons, _trace, _report = weekend_plan_match.reassign_weekend_plan_sources(
            households, persons, rng=completion_rng,
        )
    if not diary_plan_match_on:
        return households, persons
    wege = mid.load_mid_wege(mid_dir)
    facts = diary_facts.compute_diary_facts(wege)
    persons, _diary_trace, _diary_report = diary_plan_match.reassign_diaryless_plan_sources(
        persons, persons, facts, rng=completion_rng, exclude_rbw_legs=True,
        exclude_holidays=True, drop_leading_arrive_home_leg=True,
        # Mirrors build_completed_donor's default (Task 6, issue #368): the employment
        # key is never relaxed. match_person draws exactly ONE rng value per call
        # regardless of the flag, so the shared completion stream stays in lockstep.
        hard_employment=True,
    )
    persons = diary_facts.attach_plan_source_facts(persons, facts)
    return households, persons


def test_build_completed_donor_matches_inline_with_weekend_match(tmp_path):
    _write_mid_attribute_fixture(tmp_path)
    ref_hh, ref_persons = _inline_reference(
        tmp_path, random_seed=1234, weekend_plan_match_on=True, diary_plan_match_on=True,
    )
    result = cd.build_completed_donor(
        tmp_path, random_seed=1234, seed_day_filter=None, weekend_plan_match_on=True,
    )
    pd.testing.assert_frame_equal(result.households, ref_hh)
    pd.testing.assert_frame_equal(result.persons, ref_persons)


def test_build_completed_donor_matches_inline_without_weekend_match(tmp_path):
    _write_mid_attribute_fixture(tmp_path)
    ref_hh, ref_persons = _inline_reference(
        tmp_path, random_seed=1234, weekend_plan_match_on=False, diary_plan_match_on=True,
    )
    result = cd.build_completed_donor(
        tmp_path, random_seed=1234, seed_day_filter=None, weekend_plan_match_on=False,
    )
    pd.testing.assert_frame_equal(result.households, ref_hh)
    pd.testing.assert_frame_equal(result.persons, ref_persons)


def test_build_completed_donor_off_matches_pre_task_inline_pipeline(tmp_path):
    """REAL OFF byte-identity contract (fix round 1, Important-1): with
    diary_plan_match_on=False, build_completed_donor's persons frame must match an
    inline reference that runs ONLY member completion + weekend match (the
    pre-Task-3 pipeline) and STOPS there -- no Wege load, no diary match, no fact
    attachment. Only source_H_ID/source_P_ID (plus the natural H_ID/P_ID keys) are
    compared: the result ALSO carries the src_* fact columns (attached
    unconditionally, R8/Task 3), which the stopped-early reference never computes."""
    _write_mid_attribute_fixture(tmp_path)
    ref_hh, ref_persons = _inline_reference(
        tmp_path, random_seed=1234, weekend_plan_match_on=True, diary_plan_match_on=False,
    )
    result = cd.build_completed_donor(
        tmp_path, random_seed=1234, seed_day_filter=None, weekend_plan_match_on=True,
        diary_plan_match_on=False,
    )
    pd.testing.assert_frame_equal(result.households, ref_hh)
    pd.testing.assert_frame_equal(
        result.persons[["H_ID", "P_ID", "source_H_ID", "source_P_ID"]],
        ref_persons[["H_ID", "P_ID", "source_H_ID", "source_P_ID"]],
    )


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


class _FakeContext:
    """Minimal synpp ExecuteContext stand-in for the completed_donor stage.

    Records config() lookups against a dict, exposes a cache path, and captures
    set_info() calls. Mirrors the surface the stage uses.
    """
    def __init__(self, config, cache_path):
        self._config = config
        self._cache_path = str(cache_path)
        self.info = {}

    def config(self, key, default=None):
        return self._config.get(key, default)

    def path(self):
        return self._cache_path

    def set_info(self, key, value):
        self.info[key] = value


def test_completed_donor_stage_execute_returns_frames_and_writes_trace(tmp_path):
    (tmp_path / "mid").mkdir()
    _write_mid_attribute_fixture(tmp_path / "mid")
    cache = tmp_path / "cache"
    cache.mkdir()
    ctx = _FakeContext(
        {
            "braunschweig.population.popsim.mid_raw_path": str(tmp_path / "mid"),
            "random_seed": 1234,
            "braunschweig.population.popsim.seed_day_filter": "default",
            "braunschweig.population.popsim.weekend_plan_match": True,
            "braunschweig.population.popsim.diary_plan_match": True,
            "braunschweig.population.popsim.exclude_holiday_plan_sources": True,
            "braunschweig.population.popsim.exclude_rbw_legs": True,
            "braunschweig.population.popsim.drop_leading_arrive_home_leg": True,
        },
        cache,
    )
    result = cd.execute(ctx)
    assert len(result.households) > 0
    assert len(result.persons) > 0
    # Weekend trace persisted into the stage cache dir.
    assert (cache / cd.WEEKEND_TRACE_FILE).is_file()
    # Diary plan match trace persisted into the stage cache dir.
    assert (cache / cd.DIARY_TRACE_FILE).is_file()
    # Build reports surfaced as run info.
    assert "member_completion_filled" in ctx.info
    assert "seed_completeness_rate" in ctx.info


class _RecordingConfigureContext:
    """Minimal synpp ConfigurationContext stand-in that records config() lookups.

    Unlike _FakeContext (an ExecuteContext stand-in backed by a value dict), this
    mirrors the CONFIGURE phase: every context.config(key, default) call is recorded
    with the default the stage declared, so a test can assert what a stage registers
    without depending on config values (there are none yet during configure). No
    ``stage()`` method: completed_donor.configure() never calls context.stage()."""
    def __init__(self):
        self.calls = {}

    def config(self, key, default=None):
        self.calls[key] = default
        return default


def test_completed_donor_configure_registers_diary_plan_match_keys():
    from braunschweig.popsim.stage import (
        KEY_DIARY_MATCH_HARD_EMPLOYMENT, KEY_DIARY_PLAN_MATCH,
        KEY_DROP_LEADING_ARRIVE_HOME_LEG, KEY_EXCLUDE_HOLIDAY_PLAN_SOURCES,
        KEY_EXCLUDE_RBW_LEGS,
    )
    ctx = _RecordingConfigureContext()
    cd.configure(ctx)
    assert ctx.calls[KEY_DIARY_PLAN_MATCH] is True
    assert ctx.calls[KEY_EXCLUDE_HOLIDAY_PLAN_SOURCES] is True
    assert ctx.calls[KEY_EXCLUDE_RBW_LEGS] is True
    assert ctx.calls[KEY_DROP_LEADING_ARRIVE_HOME_LEG] is True
    # Task 6 (issue #368): the un-relaxable employment boundary must be part of THIS
    # stage's config hash, or flipping it would silently reuse the cached donor build.
    assert ctx.calls[KEY_DIARY_MATCH_HARD_EMPLOYMENT] is True


import inspect

from braunschweig.popsim import stage as popsim_stage
from tests.conftest import popsim_stage_package_source_text


def test_popsim_stage_consumes_completed_donor_stage():
    # Pinned against the WHOLE stage package rather than inspect.getsource(
    # popsim_stage.execute): execute() is decomposed into named orchestration
    # steps, so the completed_donor delegation lives in the seed-build step
    # (_build_populationsim_seed), not in execute()'s own source text. The
    # package-wide helper keeps the positive assertion working wherever the
    # delegation sits and makes both negative assertions STRICTER (the patterns
    # must now be absent from every module of the package, not just one file).
    src = popsim_stage_package_source_text()
    # The inline member-completion build is gone (delegated to the stage)...
    assert "mid.load_completed_donor(" not in src
    assert "reassign_weekend_plan_sources(" not in src
    # ...and the stage is consumed instead.
    assert 'context.stage("completed_donor")' in src


def test_popsim_stage_configure_registers_completed_donor_for_mid():
    src = inspect.getsource(popsim_stage.configure)
    assert "completed_donor" in src
