"""The analysis collector stages' validate() tokens hash their sub-analyses (issue #454).

The closure gate in tests/test_audit_synpp_helper_hash.py checks statically that the helper
tuples are complete. These tests call the real tokens: every listed module must import, the
token must be stable, and an edit to a sub-analysis must change it.
"""
import inspect

import pytest

from braunschweig.analysis import analysis_suite, simwrapper_export

STAGES = (analysis_suite, simwrapper_export)


class _DisabledContext:
    """synpp validate context with the stage switched off: the module part of the token is what these
    tests check, and the VG250 archive part (tests/test_vg250_cache_tokens.py) is then not computed."""

    @staticmethod
    def config(key):
        return False


_DISABLED = _DisabledContext()


@pytest.mark.parametrize("stage", STAGES, ids=lambda module: module.__name__)
def test_the_token_is_a_stable_structured_token_over_importable_modules(stage):
    first = stage.validate(_DISABLED)
    assert first["format"] == 2 and first["modules"]
    assert all(len(digest) == 64 and int(digest, 16) >= 0 for digest in first["modules"].values())
    assert stage.validate(_DISABLED) == first


@pytest.mark.parametrize("stage, edited_module", [
    (analysis_suite, "braunschweig.analysis.run_mid_validation"),
    (analysis_suite, "braunschweig.analysis.dashboard.run_metrics"),
    (simwrapper_export, "braunschweig.analysis.simwrapper.export"),
], ids=["suite-direct", "suite-closure", "simwrapper-direct"])
def test_an_edit_to_a_sub_analysis_changes_the_token(stage, edited_module, monkeypatch):
    before = stage.validate(_DISABLED)
    real_getsource = inspect.getsource

    def edited_getsource(module):
        source = real_getsource(module)
        return source + "\nZ_PROBE = 1\n" if module.__name__ == edited_module else source

    monkeypatch.setattr(inspect, "getsource", edited_getsource)
    after = stage.validate(_DISABLED)
    assert after != before
    assert {name for name in before["modules"] if after["modules"][name] != before["modules"][name]} == {
        edited_module}


@pytest.mark.parametrize("stage", STAGES, ids=lambda module: module.__name__)
def test_a_comment_edit_to_a_sub_analysis_keeps_the_token(stage, monkeypatch):
    before = stage.validate(_DISABLED)
    edited_module = next(iter(before["modules"]))
    real_getsource = inspect.getsource

    def commented_getsource(module):
        source = real_getsource(module)
        return source + "\n# probe comment\n" if module.__name__ == edited_module else source

    monkeypatch.setattr(inspect, "getsource", commented_getsource)
    assert stage.validate(_DISABLED) == before


def test_a_module_that_cannot_be_imported_fails_the_token(monkeypatch):
    monkeypatch.setattr(analysis_suite, "_DEFERRED_HELPER_MODULE_NAMES",
                        ("braunschweig.analysis.does_not_exist",))
    with pytest.raises(RuntimeError, match="must not be skipped"):
        analysis_suite.validate(_DISABLED)
