"""The analysis collector stages' validate() tokens hash their sub-analyses (issue #454).

The closure gate in tests/test_audit_synpp_helper_hash.py checks statically that the helper
tuples are complete. These tests call the real tokens: every listed module must import, the
token must be stable, and an edit to a sub-analysis must change it.
"""
import inspect

import pytest

from braunschweig.analysis import analysis_suite, simwrapper_export

STAGES = (analysis_suite, simwrapper_export)


@pytest.mark.parametrize("stage", STAGES, ids=lambda module: module.__name__)
def test_the_token_is_a_stable_md5_over_importable_modules(stage):
    first = stage.validate(None)
    assert len(first) == 32 and int(first, 16) >= 0
    assert stage.validate(None) == first


@pytest.mark.parametrize("stage, edited_module", [
    (analysis_suite, "braunschweig.analysis.run_mid_validation"),
    (analysis_suite, "braunschweig.analysis.dashboard.run_metrics"),
    (simwrapper_export, "braunschweig.analysis.simwrapper.export"),
], ids=["suite-direct", "suite-closure", "simwrapper-direct"])
def test_an_edit_to_a_sub_analysis_changes_the_token(stage, edited_module, monkeypatch):
    before = stage.validate(None)
    real_getsource = inspect.getsource

    def edited_getsource(module):
        source = real_getsource(module)
        return source + "\n# edited\n" if module.__name__ == edited_module else source

    monkeypatch.setattr(stage.inspect, "getsource", edited_getsource)
    assert stage.validate(None) != before


def test_a_module_that_cannot_be_imported_fails_the_token(monkeypatch):
    monkeypatch.setattr(analysis_suite, "_DEFERRED_HELPER_MODULE_NAMES",
                        ("braunschweig.analysis.does_not_exist",))
    with pytest.raises(RuntimeError, match="must not be skipped"):
        analysis_suite.validate(None)
