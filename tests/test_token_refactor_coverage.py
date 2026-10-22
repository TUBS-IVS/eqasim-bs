"""Every source-hashing stage returns the shared structured token, and the audit still sees it.

The stage set is the AUDIT's own (``scripts/audit_synpp_helper_hash.py``), not a text grep, so a
stage that the closure gate treats as source-hashing cannot keep a private md5 loop unnoticed.
Stages whose token also covers data files (the five ``_COMPOSITE_STAGES``) add that part under its
own key next to the module digests. The pre-refactor token is never kept as code in the
stages: the migration replays the validate() of the pre-refactor revision (task-13 ruling G).
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]

#: Measured 2026-10-21 at origin/main 205a0fb7 (``scripts/audit_synpp_helper_hash.py .``: "stages
#: whose validate() hashes source: 30"). The design spec's 26 was the 2026-10-16 count. Update this
#: number deliberately, together with the audit note, when a source-hashing stage is added or removed.
EXPECTED_SOURCE_HASHING_STAGES = 30

#: Stages whose token holds a non-source part (data files, sizes) next to the module digests.
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
    """validate() itself must not hash text: it only builds the shared structured token."""
    offenders = {}
    for name, path in source_hashing_stages.items():
        calls = _attribute_calls(_validate_node(path))
        bad = sorted(call for call in calls if call in {"hashlib.md5", "inspect.getsource"})
        if bad:
            offenders[name] = bad
    assert offenders == {}


_HELPER_TUPLES = ("_HELPER_MODULES", "_DEFERRED_HELPER_MODULE_NAMES")


def _module_level_names(tree):
    names = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def test_every_validate_reads_every_helper_tuple_its_module_defines(source_hashing_stages):
    """A helper tuple that validate() never reads hashes nothing: the token would silently ignore
    the modules it lists. A module without ``_DEFERRED_HELPER_MODULE_NAMES`` passes ``()`` (the
    commute_day trio, ruling E), which is why only DEFINED tuples are required."""
    offenders = {}
    for name, path in source_hashing_stages.items():
        defined = _module_level_names(ast.parse(path.read_text(encoding="utf-8")))
        read = {child.id for child in ast.walk(_validate_node(path))
                if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)}
        missing = sorted(t for t in _HELPER_TUPLES if t in defined and t not in read)
        if missing:
            offenders[name] = missing
    assert offenders == {}


def test_the_audit_still_recognises_the_structured_form(source_hashing_stages):
    out = subprocess.run([sys.executable, str(REPO / "scripts" / "audit_synpp_helper_hash.py"), str(REPO)],
                         cwd=REPO, capture_output=True, text=True, check=True)
    line = next(l for l in out.stdout.splitlines() if l.startswith("stages whose validate() hashes source:"))
    assert int(line.rsplit(":", 1)[1]) == EXPECTED_SOURCE_HASHING_STAGES


def test_the_legacy_loop_token_of_every_pure_stage_is_still_reproducible(source_hashing_stages):
    """``legacy_source_token`` still builds the old 32-hex md5 from the stage's own tuples."""
    from braunschweig import synpp_tokens
    for name in sorted(set(source_hashing_stages) - _COMPOSITE_STAGES):
        module = importlib.import_module(name)
        token = synpp_tokens.legacy_source_token(getattr(module, "_HELPER_MODULES", ()),
                                                 getattr(module, "_DEFERRED_HELPER_MODULE_NAMES", ()))
        assert len(token) == 32 and set(token) <= set("0123456789abcdef"), name
