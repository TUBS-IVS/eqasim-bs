"""Tests for the day-absence synpp stage (issue #370, Task 3).

Driven through the same stub-context pattern as ``tests/test_commute_day_stages.py``
(``_ConfigureRecorder`` with synpp's two-argument ``config(name, default)``, ``_StubContext`` with
the real SINGLE-argument ``config(name)`` that refuses anything ``configure`` did not declare), so a
stage reading an undeclared key or stage fails here rather than first on the run server.

The ON path reads the COMMITTED SrV reference tables under ``eqasim-data/data/braunschweig/srv/``
(the same tables ``braunschweig.synthesis.day_absence.absence`` is already tested against in
``tests/test_day_absence.py``); the OFF path must read no file at all, which is asserted by pointing
``data_path`` at an empty ``tmp_path``.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from braunschweig.synthesis.day_absence import absence as D
from braunschweig.synthesis.day_absence import absence_stage as S

REPO = __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))
DATA_PATH = __import__("os").path.join(REPO, "eqasim-data", "data")


class _ConfigureRecorder:
    """Records what ``configure`` declares, with synpp's two-argument config() signature.

    ``config()`` also RETURNS the effective value, because ``configure`` may branch on an option
    it just declared (``context.config(key, default)`` followed by ``if context.config(key):``,
    the conditional-declaration pattern of ``braunschweig.matsim.scenario.population`` that the
    escort-protection input of this stage reuses, issue #425). The declared default is remembered
    on the first call and an override from ``config`` wins, exactly as synpp's
    ``ConfigurationContext`` behaves; a recorder returning ``None`` would make every such branch
    look disabled.
    """

    def __init__(self, config=None):
        self.stages, self.config_keys, self._config = [], {}, config or {}
    def stage(self, name, **_kwargs): self.stages.append(name)
    def config(self, name, default=None):
        if name not in self.config_keys:
            self.config_keys[name] = default
        return self._config.get(name, self.config_keys[name])


class _StubContext:
    """synpp's ExecuteContext contract: ``stage(name)``, SINGLE-arg ``config(name)``.

    Both accessors fail loudly on anything ``configure`` did not declare, so a stage that reads an
    undeclared key or stage cannot pass the way a permissive stub would.
    """

    def __init__(self, declared, stages=None, config=None):
        self._declared, self._stages, self._config = declared, stages or {}, config or {}
    def stage(self, name):
        assert name in self._declared.stages, f"stage {name!r} not declared"
        return self._stages[name]
    def config(self, name):
        assert name in self._declared.config_keys, f"config {name!r} not declared"
        return self._config[name]


def _context(stages, config):
    # configure sees the SAME values execute will read, so a conditionally declared input is
    # declared exactly when execute is going to ask for it.
    recorder = _ConfigureRecorder(config=config); S.configure(recorder)
    return _StubContext(recorder, stages=stages, config=config)


def _enriched():
    return pd.DataFrame({"person_id": range(6), "household_id": [1, 2, 2, 3, 3, 3], "age": [70, 30, 32, 40, 38, 8]})


def _config(**overrides):
    base = {"random_seed": 1234, "data_path": DATA_PATH, S.KEY_ENABLED: True,
            S.KEY_HOUSEHOLD_STAGE: True, S.KEY_MAX_BAND_DEVIATION_PP: 1.0,
            S.KEY_INDIVIDUAL_STAGE_MIN_HOUSEHOLD_SIZE: S.DEFAULT_INDIVIDUAL_STAGE_MIN_HOUSEHOLD_SIZE,
            S.KEY_ESCORT_PROTECTION: S.DEFAULT_ESCORT_PROTECTION}
    base.update(overrides); return base


def _trips(rows):
    """Minimal pre-assignment trips frame: the three columns escort duty is read from."""
    return pd.DataFrame(rows, columns=["person_id", "following_purpose", "preceding_purpose"])


def _certain_reference():
    """Band rate 1.0 everywhere, household stage 0.0: every ELIGIBLE person is drawn
    absent_individual with certainty (a random_sample draw is always < 1.0), so who ends up
    present is decided by the eligibility gates alone -- deterministic, no sampling."""
    return D.AbsenceReference(
        p_absent_by_band={band: 1.0 for band in D.AGE_BAND_LABELS},
        p_all_absent_by_size={k: 0.0 for k in range(1, 6)},
        p_absent_person_by_size={k: float("nan") for k in range(1, 6)})


def test_configure_declares_exactly_the_documented_inputs():
    recorder = _ConfigureRecorder(); S.configure(recorder)
    assert recorder.stages == ["synthesis.population.enriched"]
    assert set(recorder.config_keys) == {"random_seed", "data_path", S.KEY_ENABLED, S.KEY_HOUSEHOLD_STAGE,
                                         S.KEY_MAX_BAND_DEVIATION_PP,
                                         S.KEY_INDIVIDUAL_STAGE_MIN_HOUSEHOLD_SIZE,
                                         S.KEY_ESCORT_PROTECTION}


def test_configure_declares_the_individual_stage_min_household_size_default():
    recorder = _ConfigureRecorder(); S.configure(recorder)
    assert recorder.config_keys[S.KEY_INDIVIDUAL_STAGE_MIN_HOUSEHOLD_SIZE] == 2
    assert S.DEFAULT_INDIVIDUAL_STAGE_MIN_HOUSEHOLD_SIZE == 2


def test_on_path_returns_one_row_per_person_with_the_documented_columns():
    out = S.execute(_context({"synthesis.population.enriched": _enriched()}, _config()))
    absence = out["absence"]
    assert list(absence.columns) == list(D.ABSENCE_COLUMNS) and len(absence) == 6
    assert set(absence["day_absence_state"]) <= set(D.STATES)
    assert out["diagnostics"]["enabled"] is True and "by_band" in out["diagnostics"]


def test_off_path_marks_everyone_present_without_reading_any_file(tmp_path):
    out = S.execute(_context({"synthesis.population.enriched": _enriched()},
                             _config(**{S.KEY_ENABLED: False, "data_path": str(tmp_path)})))
    assert (out["absence"]["day_absence_state"] == D.STATE_PRESENT).all()
    assert (out["absence"]["reason"] == D.REASON_DISABLED).all()
    assert out["diagnostics"] == {"enabled": False}


def test_validate_token_is_an_md5_over_the_pure_module():
    token = S.validate(None)
    assert isinstance(token, str) and len(token) == 32


def test_validate_token_changes_when_srv_absence_module_changes(monkeypatch):
    """Important finding 3 (final-review fix wave): the token must ALSO cover
    braunschweig.calibration.srv_absence, not absence.py alone -- the age-band edges/labels and
    the household-size-class top (what a person is drawn AGAINST) live there, so an edit there
    must devalidate the stage's cache exactly like an edit to the draw rule in absence.py."""
    token = S.validate(None)
    real_getsource = S.inspect.getsource

    def patched_getsource(module):
        if module is S.srv_absence:
            return real_getsource(module) + "\n# a changed age band edge\n"
        return real_getsource(module)

    monkeypatch.setattr(S.inspect, "getsource", patched_getsource)
    assert S.validate(None) != token


def test_band_deviation_guard_fires_when_realised_and_reference_diverge(monkeypatch, caplog):
    """Deferred minor (final-review fix wave): the >= 1,000-person band-deviation WARNING path.

    1,200 single-person households in one age band, with a monkeypatched reference whose
    household-size-1 probability is 1.0: the household stage marks EVERY one of them absent with
    CERTAINTY (a numpy ``random_sample`` draw is always < 1.0), so the band's realised rate is
    exactly 100% against a reference of 5% -- a 95 pp deviation, far above
    ``day_absence_max_band_deviation_pp``'s default 1.0 pp and well past
    ``MIN_PERSONS_FOR_BAND_GUARD`` (1,000), so this is not dismissed as sampling noise.
    """
    persons = pd.DataFrame({
        "person_id": range(1200), "household_id": range(1200), "age": [35] * 1200,
    })
    reference = D.AbsenceReference(
        p_absent_by_band={band: (0.05 if band == "30-44" else 0.0) for band in D.AGE_BAND_LABELS},
        p_all_absent_by_size={1: 1.0, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.0},
        p_absent_person_by_size={k: float("nan") for k in range(1, 6)})
    monkeypatch.setattr(S, "load_absence_reference", lambda _srv_dir: reference)

    with caplog.at_level("WARNING", logger=S.logger.name):
        out = S.execute(_context({"synthesis.population.enriched": persons}, _config()))

    diagnostics = out["diagnostics"]
    band_cell = diagnostics["by_band"]["30-44"]
    assert band_cell["n"] == 1200
    assert band_cell["realised_rate"] == pytest.approx(1.0)
    assert diagnostics["n_band_guard_hits"] >= 1
    assert any("30-44" in message and "exceeds" in message for message in caplog.messages)


def test_off_frame_has_the_same_dtypes_and_derived_attributes_as_the_on_frame():
    persons = _enriched()
    on = S.execute(_context({"synthesis.population.enriched": persons}, _config()))["absence"]
    off = S.execute(_context({"synthesis.population.enriched": persons},
                             _config(**{S.KEY_ENABLED: False})))["absence"]
    assert on.dtypes.equals(off.dtypes)
    on_sorted = on.sort_values("person_id").reset_index(drop=True)
    off_sorted = off.sort_values("person_id").reset_index(drop=True)
    assert (on_sorted["age_band"] == off_sorted["age_band"]).all()
    assert (on_sorted["household_size_class"] == off_sorted["household_size_class"]).all()


# --- Issue #388: day_absence_individual_stage_min_household_size -------------------------------

def test_individual_stage_min_household_size_default_is_passed_through_to_the_draw():
    out = S.execute(_context({"synthesis.population.enriched": _enriched()}, _config()))
    assert out["diagnostics"]["individual_stage_min_household_size"] == 2
    assert "n_persons_ineligible_individual_stage" in out["diagnostics"]


def test_individual_stage_min_household_size_of_zero_raises_naming_the_key():
    context = _context({"synthesis.population.enriched": _enriched()},
                       _config(**{S.KEY_INDIVIDUAL_STAGE_MIN_HOUSEHOLD_SIZE: 0}))
    with pytest.raises(ValueError, match=S.KEY_INDIVIDUAL_STAGE_MIN_HOUSEHOLD_SIZE):
        S.execute(context)


def test_individual_stage_min_household_size_of_2_5_raises_naming_the_key():
    """int(2.5) == 2 would silently truncate a non-integral config value into a plausible-looking
    household size; this must raise rather than accept a rounded/truncated result."""
    context = _context({"synthesis.population.enriched": _enriched()},
                       _config(**{S.KEY_INDIVIDUAL_STAGE_MIN_HOUSEHOLD_SIZE: 2.5}))
    with pytest.raises(ValueError, match=S.KEY_INDIVIDUAL_STAGE_MIN_HOUSEHOLD_SIZE):
        S.execute(context)


def test_individual_stage_min_household_size_accepts_a_numpy_integer():
    """Final-review fix wave, MINOR finding 4: a config value can round-trip through a
    numpy/pandas-backed loader as numpy.int64 rather than plain int; only isinstance(..., int) was
    accepted before, which would have rejected a perfectly valid numpy integer."""
    out = S.execute(_context({"synthesis.population.enriched": _enriched()},
                             _config(**{S.KEY_INDIVIDUAL_STAGE_MIN_HOUSEHOLD_SIZE: np.int64(2)})))
    assert out["diagnostics"]["individual_stage_min_household_size"] == 2


# --- Issue #425: day_absence_escort_protection_enabled -----------------------------------------

def test_configure_declares_escort_protection_with_code_default_off():
    """CODE default False = byte-identical to PR #389 (arm 3); configs/base_bs.yml turns it on --
    the same code-default/config-default split as day_absence_individual_stage_min_household_size."""
    recorder = _ConfigureRecorder(); S.configure(recorder)
    assert recorder.config_keys[S.KEY_ESCORT_PROTECTION] is False
    assert S.DEFAULT_ESCORT_PROTECTION is False


def test_configure_declares_the_trips_stage_only_when_escort_protection_is_on():
    """The pre-assignment trips are needed ONLY to find escort legs, so the input -- and with it
    the DAG edge -- exists only on the ON path (the conditional-declaration pattern of
    braunschweig.matsim.scenario.population). OFF keeps the stage's inputs exactly as before."""
    on = _ConfigureRecorder(config={S.KEY_ESCORT_PROTECTION: True}); S.configure(on)
    off = _ConfigureRecorder(config={S.KEY_ESCORT_PROTECTION: False}); S.configure(off)
    assert "synthesis.population.trips" in on.stages
    assert on.stages[0] == "synthesis.population.enriched"
    assert off.stages == ["synthesis.population.enriched"]


def test_escort_protection_keeps_an_escorter_present_that_the_individual_stage_would_otherwise_take(monkeypatch):
    """_enriched(): person 0 is a single (out by the size gate), persons 1-2 a couple, persons 3-5
    a family. With every eligible person drawn absent with certainty, ONLY the size gate and the
    escort gate decide who stays present: person 0 (single) and person 1 (escort leg)."""
    monkeypatch.setattr(S, "load_absence_reference", lambda _srv_dir: _certain_reference())
    trips = _trips([(1, "escort", "home"), (1, "home", "escort"), (3, "work", "home"), (4, "shop", "home")])
    out = S.execute(_context({"synthesis.population.enriched": _enriched(),
                              "synthesis.population.trips": trips},
                             _config(**{S.KEY_ESCORT_PROTECTION: True})))
    absence = out["absence"].set_index("person_id")["day_absence_state"]
    assert absence[1] == D.STATE_PRESENT
    assert absence[0] == D.STATE_PRESENT
    assert (absence.drop([0, 1]) == D.STATE_ABSENT_INDIVIDUAL).all()
    assert out["diagnostics"]["n_persons_escort_protected"] == 1
    assert out["diagnostics"]["escort_protection"] is True


def test_escort_protection_off_never_reads_the_trips_stage_and_reports_zero_protected(monkeypatch, caplog):
    """The stub context asserts on any access to an undeclared stage, and no trips frame is even
    supplied here -- so this test passing PROVES the OFF path does not touch the trips table."""
    monkeypatch.setattr(S, "load_absence_reference", lambda _srv_dir: _certain_reference())
    with caplog.at_level("INFO", logger=S.logger.name):
        out = S.execute(_context({"synthesis.population.enriched": _enriched()},
                                 _config(**{S.KEY_ESCORT_PROTECTION: False})))
    absence = out["absence"].set_index("person_id")["day_absence_state"]
    assert absence[1] == D.STATE_ABSENT_INDIVIDUAL   # nothing protects the escorter now
    assert out["diagnostics"]["n_persons_escort_protected"] == 0
    assert out["diagnostics"]["escort_protection"] is False
    assert not any("escort protection" in message for message in caplog.messages)


def test_escort_protection_logs_the_protected_rate(monkeypatch, caplog):
    """CLAUDE.md fallback transparency: the gate's effect is logged as an explicit rate."""
    monkeypatch.setattr(S, "load_absence_reference", lambda _srv_dir: _certain_reference())
    trips = _trips([(1, "escort", "home"), (3, "work", "home")])
    with caplog.at_level("INFO", logger=S.logger.name):
        S.execute(_context({"synthesis.population.enriched": _enriched(),
                            "synthesis.population.trips": trips},
                           _config(**{S.KEY_ESCORT_PROTECTION: True})))
    # The dedicated rate line starts "escort protection:"; the combined ineligibility line only
    # says "... by escort protection" and must not be mistaken for it.
    rate_lines = [m for m in caplog.messages if "escort protection:" in m]
    assert rate_lines, caplog.messages
    assert "1/" in rate_lines[0] and "%" in rate_lines[0]


def test_escort_protection_warns_when_trips_have_rows_but_no_escort_leg(monkeypatch, caplog):
    """An EMPTY escort set with a non-empty trips table is the 'escort purpose is off in the
    synthetic trips' defect class braunschweig.synthesis.commute_day.state_stage already warns
    about -- a population that escorts NOBODY is not what it looks like. The draw still runs."""
    monkeypatch.setattr(S, "load_absence_reference", lambda _srv_dir: _certain_reference())
    trips = _trips([(1, "work", "home"), (3, "shop", "home")])
    with caplog.at_level("WARNING", logger=S.logger.name):
        out = S.execute(_context({"synthesis.population.enriched": _enriched(),
                                  "synthesis.population.trips": trips},
                                 _config(**{S.KEY_ESCORT_PROTECTION: True})))
    assert any("escort" in message and "NO person" in message for message in caplog.messages)
    assert out["diagnostics"]["n_persons_escort_protected"] == 0
    assert len(out["absence"]) == 6
