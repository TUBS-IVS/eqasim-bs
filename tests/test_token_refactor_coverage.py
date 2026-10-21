"""Every source-hashing stage returns the shared structured token, and the audit still sees it.

The stage set is the AUDIT's own (``scripts/audit_synpp_helper_hash.py``), not a text grep, so a
stage that the closure gate treats as source-hashing cannot keep a private md5 loop unnoticed.
Stages whose token also covers data files (the five ``_COMPOSITE_STAGES``) keep a private
``_legacy_validation_token`` that reproduces the pre-refactor string for the cache migration.
"""
from __future__ import annotations

import ast
import hashlib
import importlib
import importlib.util
import inspect
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]

#: Measured 2026-10-21 at origin/main 205a0fb7 (``scripts/audit_synpp_helper_hash.py .``: "stages
#: whose validate() hashes source: 30"). The design spec's 26 was the 2026-10-16 count. Update this
#: number deliberately, together with the audit note, when a source-hashing stage is added or removed.
EXPECTED_SOURCE_HASHING_STAGES = 30

#: Stages whose token holds a non-source part (data files, sizes) next to the module digests; each
#: keeps ``_legacy_validation_token(context)`` for the migration (scripts/migrate_cache_metadata.py).
_COMPOSITE_STAGES = {
    "braunschweig.parking.zones_stage",
    "braunschweig.data.vrb.zone_polygons",
    "braunschweig.matsim.simulation.prepare",
    "braunschweig.synthesis.commute_day.home_office_donors_stage",
    "braunschweig.synthesis.outside_tour_modes.stage",
}


def _load_audit():
    spec = importlib.util.spec_from_file_location("audit_synpp_helper_hash_cov",
                                                  REPO / "scripts" / "audit_synpp_helper_hash.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def source_hashing_stages():
    audit = _load_audit()
    report = audit.build_report(REPO)
    names = sorted(name for name, entry in report.items() if entry["hashes_source"])
    paths = {}
    for name in names:
        base = REPO.joinpath(*name.split("."))
        paths[name] = base / "__init__.py" if base.is_dir() else base.with_suffix(".py")
    return paths


def _validate_node(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "validate":
            return node
    raise AssertionError(f"{path}: no top-level validate()")


def _attribute_calls(node):
    """Dotted names of every ``a.b(...)`` call inside ``node``."""
    calls = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
            calls.add(ast.unparse(child.func))
    return calls


def test_the_audit_counts_exactly_the_known_source_hashing_stages(source_hashing_stages):
    assert len(source_hashing_stages) == EXPECTED_SOURCE_HASHING_STAGES


def test_every_source_hashing_stage_calls_the_shared_token_builder(source_hashing_stages):
    offenders = [name for name, path in source_hashing_stages.items()
                 if "synpp_tokens.source_token" not in _attribute_calls(_validate_node(path))]
    assert offenders == []


def test_no_validate_keeps_a_private_md5_or_source_loop(source_hashing_stages):
    """The legacy hooks are separate functions and exempt; validate() itself must not hash text."""
    offenders = {}
    for name, path in source_hashing_stages.items():
        calls = _attribute_calls(_validate_node(path))
        bad = sorted(call for call in calls if call in {"hashlib.md5", "inspect.getsource"})
        if bad:
            offenders[name] = bad
    assert offenders == {}


def test_the_audit_still_recognises_the_structured_form(source_hashing_stages):
    out = subprocess.run([sys.executable, str(REPO / "scripts" / "audit_synpp_helper_hash.py"), str(REPO)],
                         cwd=REPO, capture_output=True, text=True, check=True)
    line = next(l for l in out.stdout.splitlines() if l.startswith("stages whose validate() hashes source:"))
    assert int(line.rsplit(":", 1)[1]) == EXPECTED_SOURCE_HASHING_STAGES


def test_composite_stages_keep_a_private_legacy_validation_token(source_hashing_stages):
    assert _COMPOSITE_STAGES <= set(source_hashing_stages)
    missing = [name for name in sorted(_COMPOSITE_STAGES)
               if not callable(getattr(importlib.import_module(name), "_legacy_validation_token", None))]
    assert missing == []


def test_the_legacy_loop_token_of_every_pure_stage_is_still_reproducible(source_hashing_stages):
    """The migration proves a stored md5 string with ``legacy_source_token``; its inputs are the stage's
    own tuples (read from the module, never copied), and the value has the old 32-hex shape."""
    from braunschweig import synpp_tokens
    for name in sorted(set(source_hashing_stages) - _COMPOSITE_STAGES):
        module = importlib.import_module(name)
        token = synpp_tokens.legacy_source_token(getattr(module, "_HELPER_MODULES", ()),
                                                 getattr(module, "_DEFERRED_HELPER_MODULE_NAMES", ()))
        assert len(token) == 32 and set(token) <= set("0123456789abcdef"), name


def _md5_over(helpers, deferred):
    digest = hashlib.md5()
    for module in helpers:
        digest.update(inspect.getsource(module).encode("utf-8"))
    for module_name in deferred:
        digest.update(inspect.getsource(importlib.import_module(module_name)).encode("utf-8"))
    return digest.hexdigest()


def test_the_legacy_hook_of_prepare_reproduces_the_pre_refactor_algorithm():
    """prepare's committed inputs need no config: independent re-implementation of the old body."""
    from braunschweig.data.vrb import fare_model_export
    from braunschweig.matsim.simulation import prepare
    digest = hashlib.md5()
    for module in prepare._HELPER_MODULES:
        digest.update(inspect.getsource(module).encode("utf-8"))
    for path in fare_model_export.committed_input_paths():
        digest.update(fare_model_export.content_sha256(path).encode("ascii"))
    for module_name in prepare._DEFERRED_HELPER_MODULE_NAMES:
        digest.update(inspect.getsource(importlib.import_module(module_name)).encode("utf-8"))
    assert prepare._legacy_validation_token(None) == digest.hexdigest()


class _Config:
    def __init__(self, values):
        self.values = values

    def config(self, key, default=None):
        return self.values.get(key, default)


def test_the_legacy_hook_of_zone_polygons_reproduces_the_pre_refactor_algorithm(tmp_path):
    from braunschweig.data.vrb import fare_model_export, zone_polygons
    polygons = tmp_path / "polygons.geojson"
    polygons.write_bytes(b'{"type": "FeatureCollection", "features": []}\n')
    context = _Config({"data_path": str(tmp_path), "vrb_zone_polygons_path": "polygons.geojson"})
    digest = hashlib.sha256(polygons.read_bytes())
    digest.update(pathlib.Path(fare_model_export.committed_input_paths()[1]).read_bytes())
    for module in zone_polygons._HELPER_MODULES:
        digest.update(inspect.getsource(module).encode("utf-8"))
    assert zone_polygons._legacy_validation_token(context) == digest.hexdigest()


def test_the_legacy_hook_of_home_office_donors_reproduces_the_pre_refactor_algorithm(tmp_path):
    from braunschweig.synthesis.commute_day import home_office_donors_stage as stage
    sizes = 0
    for index, name in enumerate(stage.RAW_FILES):
        (tmp_path / name).write_bytes(b"x" * (10 + index))
        sizes += 10 + index
    context = _Config({stage.KEY_ENABLED: True, stage.KEY_MID_DIR: str(tmp_path)})
    expected = f"{sizes}-{_md5_over(stage._HELPER_MODULES, stage._DEFERRED_HELPER_MODULE_NAMES)}"
    assert stage._legacy_validation_token(context) == expected


def test_the_legacy_hook_of_outside_tour_modes_reproduces_the_pre_refactor_algorithm(tmp_path):
    from braunschweig.synthesis.outside_tour_modes import stage
    table = tmp_path.joinpath(*stage.SRV_SUBDIR, stage._srv_table_module.TABLE_FILE_NAME)
    table.parent.mkdir(parents=True)
    table.write_bytes(b"band,share\n0,1\n")
    context = _Config({stage.KEY_ENABLED: True, "data_path": str(tmp_path)})
    digest = hashlib.md5()
    for module in stage._HELPER_MODULES:
        digest.update(inspect.getsource(module).encode("utf-8"))
    for module_name in stage._DEFERRED_HELPER_MODULE_NAMES:
        digest.update(inspect.getsource(importlib.import_module(module_name)).encode("utf-8"))
    digest.update(table.read_bytes())
    assert stage._legacy_validation_token(context) == digest.hexdigest()
