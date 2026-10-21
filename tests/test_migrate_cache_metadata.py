# tests/test_migrate_cache_metadata.py
"""A legacy pipeline.json (upstream synpp, md5 module hash, md5 helper tokens) migrates to the
fork's validity fields without recompute, proven against a pre-refactor git revision
(spec §4.7, Rulings F/G/H of the content-addressed stage cache plan).

Every test builds a real git repository in tmp_path whose commit plays the role of the
pre-refactor revision REV, runs synpp 1.6.2's pristine run() with the md5 module hash on the
committed (legacy) code, edits the code, and migrates.
"""
import hashlib
import importlib
import importlib.util
import inspect
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

import pytest
import synpp
import synpp.pipeline
import yaml

_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "migrate_cache_metadata.py"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="the migration proof needs git")

HELPER = "VALUE = 5\n"
# A stage written the way the source-hashing stages were BEFORE Task 13 (raw md5 loop): test data.
LEGACY_STAGE = """\
import hashlib, importlib, inspect
import mig_helper
_HELPER_MODULES = (mig_helper,)
_DEFERRED_HELPER_MODULE_NAMES = ()
def configure(context):
    context.config("trace")
def validate(context):
    digest = hashlib.md5()
    for module in _HELPER_MODULES:
        digest.update(inspect.getsource(module).encode("utf-8"))
    return digest.hexdigest()
def execute(context):
    with open(context.config("trace"), "a") as f:
        f.write("executed\\n")
    return mig_helper.VALUE
"""
# The same stage after the Task 13 refactor: only validate() and the tooling imports differ.
NEW_STAGE = """\
from braunschweig import synpp_tokens
import mig_helper
_HELPER_MODULES = (mig_helper,)
_DEFERRED_HELPER_MODULE_NAMES = ()
def configure(context):
    context.config("trace")
def validate(context):
    return synpp_tokens.source_token(_HELPER_MODULES, _DEFERRED_HELPER_MODULE_NAMES)
def execute(context):
    with open(context.config("trace"), "a") as f:
        f.write("executed\\n")
    return mig_helper.VALUE
"""
# A composite token (module sources + a data file), legacy and refactored form.
LEGACY_COMPOSITE = """\
import hashlib, inspect
import mig_helper
_HELPER_MODULES = (mig_helper,)
def configure(context):
    context.config("trace")
    context.config("data_file")
def validate(context):
    digest = hashlib.md5()
    for module in _HELPER_MODULES:
        digest.update(inspect.getsource(module).encode("utf-8"))
    with open(context.config("data_file"), "rb") as f:
        digest.update(f.read())
    return digest.hexdigest()
def execute(context):
    with open(context.config("trace"), "a") as f:
        f.write("executed\\n")
    return mig_helper.VALUE
"""
NEW_COMPOSITE = """\
import hashlib
from braunschweig import synpp_tokens
import mig_helper
_HELPER_MODULES = (mig_helper,)
def configure(context):
    context.config("trace")
    context.config("data_file")
def validate(context):
    token = synpp_tokens.source_token(_HELPER_MODULES, ())
    with open(context.config("data_file"), "rb") as f:
        token["files"] = {"data": hashlib.sha256(f.read()).hexdigest()}
    return token
def execute(context):
    with open(context.config("trace"), "a") as f:
        f.write("executed\\n")
    return mig_helper.VALUE
"""
PLAIN_STAGE = """\
def configure(context):
    context.config("trace")
def execute(context):
    with open(context.config("trace"), "a") as f:
        f.write("executed\\n")
    return 1
"""
PLAIN_PARENT = """\
def configure(context):
    context.config("trace")
def execute(context):
    with open(context.config("trace"), "a") as f:
        f.write("parent\\n")
    return 2
"""
PLAIN_CHILD = """\
def configure(context):
    context.stage("mig_parent")
def execute(context):
    return context.stage("mig_parent") * 3
"""
_MODULES = ("mig_stage", "mig_helper", "mig_parent", "mig_child", "mig_extra")


def _load():
    spec = importlib.util.spec_from_file_location("migrate_cache_metadata_mod", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "core.autocrlf=false", "-c", "user.name=test",
                           "-c", "user.email=test@example.org", "-c", "commit.gpgsign=false", *args],
                          capture_output=True, text=True, check=True).stdout.strip()


def _upstream_run():
    """synpp 1.6.2's pristine run(), executed from the installed file's text so the test does
    not depend on whether another test already installed the fork in this interpreter."""
    import synpp.pipeline as upstream
    source = pathlib.Path(upstream.__file__).read_text(encoding="utf-8")
    start = source.index("\ndef run(")
    end = source.index("\ndef run_from_yaml(")
    namespace = dict(vars(upstream))
    exec(source[start:end], namespace)
    return namespace["run"]


def _md5_stage_hash(descriptor):
    return hashlib.md5(inspect.getsource(descriptor).encode("utf-8")).hexdigest()


class Project:
    """A tmp git repository with stage modules, a synpp working directory and a trace file."""

    def __init__(self, root: pathlib.Path, monkeypatch):
        self.root = root
        self.monkeypatch = monkeypatch
        self.working = root / "cache"
        self.working.mkdir()
        self.trace = root / "trace.txt"
        self.config = {"trace": str(self.trace)}
        monkeypatch.syspath_prepend(str(root))
        _git(root, "init", "-q")

    def write(self, name: str, text: str) -> None:
        (self.root / name).write_bytes(text.encode("utf-8"))   # LF bytes on every platform

    def commit(self) -> str:
        _git(self.root, "add", "--", *[p.name for p in self.root.glob("*.py")])
        _git(self.root, "commit", "-q", "-m", "legacy code")
        return _git(self.root, "rev-parse", "HEAD")

    def reload(self) -> None:
        for name in _MODULES:
            self.monkeypatch.delitem(sys.modules, name, raising=False)
        importlib.invalidate_caches()

    def legacy_run(self, descriptor: str, config: dict = None) -> None:
        """synpp 1.6.2 pristine run() with the md5 module hash: the cache as it exists today."""
        run = _upstream_run()
        saved = synpp.pipeline.get_stage_hash
        synpp.pipeline.get_stage_hash = _md5_stage_hash
        try:
            self.reload()
            run([{"descriptor": descriptor}], config or self.config, working_directory=str(self.working),
                rerun_required=False)
        finally:
            synpp.pipeline.get_stage_hash = saved

    def registry(self, descriptor: str, config: dict = None) -> dict:
        from braunschweig import synpp_fork
        synpp_fork.install()
        self.reload()
        return synpp.pipeline.process_stages([{"descriptor": descriptor}], config or self.config)

    def meta(self) -> dict:
        return json.loads((self.working / "pipeline.json").read_text(encoding="utf-8"))

    def fork_run(self, descriptor: str) -> None:
        from braunschweig import synpp_fork
        synpp_fork.install()
        self.reload()
        synpp.run([{"descriptor": descriptor}], self.config, working_directory=str(self.working),
                  rerun_required=False)

    def migrate(self, descriptor: str, rev: str, *, registry_config: dict = None, dry_run: bool = False,
                legacy_source=None):
        module = _load()
        stages = self.registry(descriptor, registry_config)
        meta = self.meta()
        source = legacy_source or module.GitLegacySource(self.root, rev)
        counts = module.migrate(stages, str(self.working), meta, dry_run=dry_run, legacy_source=source)
        if not dry_run:
            from braunschweig.synpp_fork import metadata
            metadata.write_meta(meta, str(self.working))
        return counts, meta

    def trace_lines(self) -> list:
        return self.trace.read_text(encoding="utf-8").splitlines() if self.trace.exists() else []


@pytest.fixture
def project(tmp_path, monkeypatch):
    return Project(tmp_path, monkeypatch)


def _only(meta: dict, prefix: str) -> dict:
    (record,) = [record for entry, record in meta.items() if entry.startswith(prefix)]
    return record


# --- (1) the end-to-end acceptance path -------------------------------------------------------

def test_legacy_entries_migrate_and_the_fork_run_executes_nothing(project):
    from braunschweig.synpp_fork import registry, staleness
    project.write("mig_helper.py", HELPER)
    project.write("mig_stage.py", LEGACY_STAGE)
    rev = project.commit()
    project.legacy_run("mig_stage")
    legacy = _only(project.meta(), "mig_stage")
    assert len(legacy["module_hash"]) == 32 and isinstance(legacy["validation_token"], str)
    assert "output_hash" not in legacy
    # The code is refactored to the structured token (Task 13 shape), not committed.
    project.write("mig_stage.py", NEW_STAGE)
    config_path = project.root / "config.yml"
    config_path.write_text(yaml.safe_dump({"working_directory": str(project.working), "run": ["mig_stage"],
                                           "config": project.config}), encoding="utf-8")
    project.reload()
    assert _load().main([str(config_path), "--legacy-source-repo", str(project.root),
                         "--legacy-source-rev", rev]) == 0
    migrated = _only(project.meta(), "mig_stage")
    assert len(migrated["module_hash"]) == 64
    assert migrated["validation_token"]["format"] == 2
    assert list(migrated["validation_token"]["modules"]) == ["mig_helper"]
    assert migrated["output_hash"]["format"] == 1 and migrated["fork"] == {"format": 1}
    assert migrated["updated"] == legacy["updated"] and migrated["dependencies"] == legacy["dependencies"]
    assert (project.working / "pipeline.json.bk").exists()
    # The explainer reports no own-reason stale stage on the migrated cache ...
    project.reload()
    stages = registry.build_registry_from_settings(yaml.safe_load(config_path.read_text(encoding="utf-8")))
    assessments = staleness.assess(stages, str(project.working), project.meta(), rerun_required=False)
    assert [a.reasons for a in assessments.values()] == [[]]
    assert "legacy md5 module hash" not in staleness.format_report(assessments)
    # ... and a fork run reuses everything.
    project.fork_run("mig_stage")
    assert project.trace_lines() == ["executed"]


# --- (2) an edit outside validate() is a real change -----------------------------------------

def test_stage_body_edit_outside_validate_is_a_module_hash_mismatch(project):
    project.write("mig_helper.py", HELPER)
    project.write("mig_stage.py", LEGACY_STAGE)
    rev = project.commit()
    project.legacy_run("mig_stage")
    before = project.meta()
    project.write("mig_stage.py", NEW_STAGE.replace("return mig_helper.VALUE", "return mig_helper.VALUE + 1"))
    counts, meta = project.migrate("mig_stage", rev)
    assert counts["module_hash_mismatch"] == 1 and counts["module_hash_migrated"] == 0
    assert counts["kept_legacy"] == 1 and counts["migrated"] == 0
    assert meta == before                                     # record exactly unchanged
    assert project.meta() == before
    project.fork_run("mig_stage")
    assert project.trace_lines() == ["executed", "executed"]  # genuinely stale: recomputed


# --- (3) code unchanged since REV --------------------------------------------------------------

class _NoLegacySource:
    """A legacy source that must not be consulted: the current source alone proves the entry."""

    def rev_text(self, path):
        raise AssertionError("the pre-refactor revision must not be needed for %s" % path)

    def patched_getsource(self):
        raise AssertionError("no replay expected")


def test_unchanged_plain_stage_migrates_from_the_current_source_alone(project):
    project.write("mig_stage.py", PLAIN_STAGE)
    project.commit()
    project.legacy_run("mig_stage")
    meta = project.meta()
    meta["gone_stage__0123"] = {"config": {}, "updated": 1.0, "dependencies": {}, "module_hash": "f" * 32}
    (project.working / "pipeline.json").write_text(json.dumps(meta), encoding="utf-8")
    counts, meta = project.migrate("mig_stage", "unused", legacy_source=_NoLegacySource())
    assert counts["module_hash_migrated"] == 1 and counts["token_not_applicable"] == 1
    assert counts["output_hashed"] == 1 and counts["migrated"] == 1
    assert counts["not_in_dag"] == 1 and counts["kept_legacy"] == 1
    assert meta["gone_stage__0123"] == {"config": {}, "updated": 1.0, "dependencies": {},
                                        "module_hash": "f" * 32}
    record = _only(meta, "mig_stage")
    assert record["fork"] == {"format": 1} and record["validation_token"] is None
    project.fork_run("mig_stage")
    assert project.trace_lines() == ["executed"]


def test_unchanged_source_hashing_stage_keeps_its_proven_string_token(project):
    project.write("mig_helper.py", HELPER)
    project.write("mig_stage.py", LEGACY_STAGE)
    rev = project.commit()
    project.legacy_run("mig_stage")
    token = _only(project.meta(), "mig_stage")["validation_token"]
    counts, meta = project.migrate("mig_stage", rev)
    assert counts["module_hash_migrated"] == 1 and counts["token_migrated"] == 1 and counts["migrated"] == 1
    assert _only(meta, "mig_stage")["validation_token"] == token   # what run() computes for this code
    project.fork_run("mig_stage")
    assert project.trace_lines() == ["executed"]


# --- (4) a helper edited outside validate() ----------------------------------------------------

def test_helper_edit_is_a_token_mismatch(project):
    project.write("mig_helper.py", HELPER)
    project.write("mig_stage.py", LEGACY_STAGE)
    rev = project.commit()
    project.legacy_run("mig_stage")
    before = project.meta()
    project.write("mig_stage.py", NEW_STAGE)
    project.write("mig_helper.py", "VALUE = 6\n")
    counts, meta = project.migrate("mig_stage", rev)
    assert counts["module_hash_migrated"] == 1
    assert counts["token_mismatch"] == 1 and counts["token_migrated"] == 0
    assert counts["kept_legacy"] == 1 and meta == before
    project.fork_run("mig_stage")
    assert project.trace_lines() == ["executed", "executed"]


def test_helper_edited_in_a_comment_only_is_proven_equivalent(project):
    project.write("mig_helper.py", HELPER)
    project.write("mig_stage.py", LEGACY_STAGE)
    rev = project.commit()
    project.legacy_run("mig_stage")
    project.write("mig_stage.py", NEW_STAGE)
    project.write("mig_helper.py", '"""Helper."""\n# a comment\nVALUE = 5\n')
    counts, _ = project.migrate("mig_stage", rev)
    assert counts["token_migrated"] == 1 and counts["migrated"] == 1
    project.fork_run("mig_stage")
    assert project.trace_lines() == ["executed"]


# --- composite tokens and off-registry entries --------------------------------------------------

def test_composite_token_in_the_registry_migrates_with_its_data_part(project):
    project.write("mig_helper.py", HELPER)
    project.write("mig_stage.py", LEGACY_COMPOSITE)
    rev = project.commit()
    data = project.root / "data.bin"
    data.write_bytes(b"payload")
    project.config["data_file"] = str(data)
    project.legacy_run("mig_stage")
    project.write("mig_stage.py", NEW_COMPOSITE)
    counts, meta = project.migrate("mig_stage", rev)
    assert counts["token_migrated"] == 1 and counts["migrated"] == 1
    assert set(_only(meta, "mig_stage")["validation_token"]) == {"format", "python", "modules", "files"}
    project.fork_run("mig_stage")
    assert project.trace_lines() == ["executed"]


def test_composite_token_with_changed_data_is_a_token_mismatch(project):
    project.write("mig_helper.py", HELPER)
    project.write("mig_stage.py", LEGACY_COMPOSITE)
    rev = project.commit()
    data = project.root / "data.bin"
    data.write_bytes(b"payload")
    project.config["data_file"] = str(data)
    project.legacy_run("mig_stage")
    project.write("mig_stage.py", NEW_COMPOSITE)
    data.write_bytes(b"other payload")
    counts, _ = project.migrate("mig_stage", rev)
    assert counts["token_mismatch"] == 1 and counts["kept_legacy"] == 1


def test_off_registry_entries_pure_loop_migrates_composite_stays_legacy(project):
    project.write("mig_helper.py", HELPER)
    project.write("mig_stage.py", LEGACY_STAGE)
    rev = project.commit()
    project.legacy_run("mig_stage")                                  # variant A
    project.write("mig_stage.py", NEW_STAGE)
    other = {"trace": str(project.root / "other_trace.txt")}         # registry holds variant B only
    counts, meta = project.migrate("mig_stage", rev, registry_config=other)
    assert counts["token_migrated"] == 1 and counts["migrated"] == 1
    assert _only(meta, "mig_stage")["validation_token"]["modules"].keys() == {"mig_helper"}


def test_off_registry_composite_entry_is_token_unverifiable(project):
    project.write("mig_helper.py", HELPER)
    project.write("mig_stage.py", LEGACY_COMPOSITE)
    rev = project.commit()
    data = project.root / "data.bin"
    data.write_bytes(b"payload")
    project.config["data_file"] = str(data)
    project.legacy_run("mig_stage")
    before = project.meta()
    project.write("mig_stage.py", NEW_COMPOSITE)
    other = dict(project.config, trace=str(project.root / "other_trace.txt"))
    counts, meta = project.migrate("mig_stage", rev, registry_config=other)
    assert counts["token_unverifiable"] == 1 and counts["kept_legacy"] == 1 and meta == before


# --- (5) dry run ---------------------------------------------------------------------------------

def test_dry_run_writes_nothing(project, tmp_path):
    project.write("mig_helper.py", HELPER)
    project.write("mig_stage.py", LEGACY_STAGE)
    rev = project.commit()
    project.legacy_run("mig_stage")
    project.write("mig_stage.py", NEW_STAGE)
    before = (project.working / "pipeline.json").read_bytes()
    listing = sorted(os.listdir(project.working))
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump({"working_directory": str(project.working), "run": ["mig_stage"],
                                           "config": project.config}), encoding="utf-8")
    project.reload()
    assert _load().main([str(config_path), "--dry-run", "--legacy-source-repo", str(project.root),
                         "--legacy-source-rev", rev]) == 0
    assert (project.working / "pipeline.json").read_bytes() == before
    assert sorted(os.listdir(project.working)) == listing            # no .bk, no .new
    meta = json.loads(before)
    counts = _load().migrate(project.registry("mig_stage"), str(project.working), meta, dry_run=True,
                             legacy_source=_load().GitLegacySource(project.root, rev))
    assert counts["migrated"] == 1 and meta == json.loads(before)    # the passed meta is not mutated


# --- (6) dependency output hashes ---------------------------------------------------------------

def test_dependency_output_hashes_are_filled_for_a_chain(project):
    project.write("mig_parent.py", PLAIN_PARENT)
    project.write("mig_child.py", PLAIN_CHILD)
    rev = project.commit()
    project.legacy_run("mig_child")
    counts, meta = project.migrate("mig_child", rev)
    assert counts["migrated"] == 2 and counts["dependency_hashes_filled"] == 1
    parent_hash = next(entry for entry in meta if entry.startswith("mig_parent"))
    child = _only(meta, "mig_child")
    assert child["dependency_output_hashes"] == {parent_hash: meta[parent_hash]["output_hash"]["digest"]}
    assert meta[parent_hash]["dependency_output_hashes"] == {}
    project.fork_run("mig_child")
    assert project.trace_lines() == ["parent"]


def test_a_child_older_than_its_parent_gets_no_parent_digest(project):
    from braunschweig.synpp_fork import staleness
    project.write("mig_parent.py", PLAIN_PARENT)
    project.write("mig_child.py", PLAIN_CHILD)
    rev = project.commit()
    project.legacy_run("mig_child")
    meta = project.meta()
    parent_hash = next(entry for entry in meta if entry.startswith("mig_parent"))
    meta[parent_hash]["updated"] += 1000.0                # parent recomputed after the child was built
    (project.working / "pipeline.json").write_text(json.dumps(meta), encoding="utf-8")
    counts, meta = project.migrate("mig_child", rev)
    assert counts["dependency_hashes_skipped_stale"] == 1 and counts["dependency_hashes_filled"] == 0
    assert _only(meta, "mig_child")["dependency_output_hashes"] == {}
    assessments = staleness.assess(project.registry("mig_child"), str(project.working), meta,
                                   rerun_required=False)
    child = next(a for a in assessments.values() if a.stage == "mig_child")
    assert child.reasons == ["parent_updated:mig_parent"]   # still stale: no mismatch turned into a hit


# --- (7) git and the revision must exist ------------------------------------------------------

def test_missing_revision_raises_naming_it(project):
    module = _load()
    project.write("mig_helper.py", HELPER)
    project.commit()
    missing = "0" * 40
    with pytest.raises(RuntimeError) as error:
        module.GitLegacySource(project.root, missing)
    assert missing in str(error.value) and "git fetch origin main" in str(error.value)


def test_missing_git_raises_naming_the_revision(project):
    module = _load()
    with pytest.raises(RuntimeError) as error:
        module.GitLegacySource(project.root, "abc123", git="no-such-git-executable-xyz")
    assert "abc123" in str(error.value)


def test_main_exits_one_when_the_revision_is_missing(project, tmp_path):
    project.write("mig_stage.py", PLAIN_STAGE)
    project.commit()
    project.legacy_run("mig_stage")
    before = (project.working / "pipeline.json").read_bytes()
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump({"working_directory": str(project.working), "run": ["mig_stage"],
                                           "config": project.config}), encoding="utf-8")
    project.reload()
    assert _load().main([str(config_path), "--legacy-source-repo", str(project.root),
                         "--legacy-source-rev", "0" * 40]) == 1
    assert (project.working / "pipeline.json").read_bytes() == before


# --- (8) Ruling H: the explainer points at the migration ---------------------------------------

def test_explainer_hints_at_the_migration_for_legacy_md5_module_hashes(project):
    from braunschweig.synpp_fork import metadata, staleness
    project.write("mig_stage.py", PLAIN_STAGE)
    rev = project.commit()
    project.legacy_run("mig_stage")
    assessments = staleness.assess(project.registry("mig_stage"), str(project.working),
                                   metadata.read_meta(str(project.working)), rerun_required=False)
    (assessment,) = assessments.values()
    assert assessment.reasons == ["module_hash_changed"] and assessment.legacy_module_hash
    report = staleness.format_report(assessments)
    assert ("# WARNING: 1 entries carry a legacy md5 module hash: run scripts/migrate_cache_metadata.py first"
            in report.splitlines())
    assert report.splitlines()[-1].startswith("# 1 stages:")      # the summary stays the last line
    project.migrate("mig_stage", rev)
    assessments = staleness.assess(project.registry("mig_stage"), str(project.working),
                                   metadata.read_meta(str(project.working)), rerun_required=False)
    assert not any(a.legacy_module_hash for a in assessments.values())
    assert "legacy md5 module hash" not in staleness.format_report(assessments)


# --- (9) refactor equivalence ---------------------------------------------------------------------

def test_refactor_equivalence_ignores_validate_and_exactly_the_tooling_imports():
    module = _load()
    assert module.refactor_equivalent(LEGACY_STAGE, NEW_STAGE)
    base = "import os\nVALUE = 1\n"
    assert module.refactor_equivalent("import hashlib\nimport inspect\nimport importlib\n" + base,
                                      "from braunschweig import synpp_tokens\n" + base)
    assert module.refactor_equivalent("import hashlib, os\nVALUE = 1\n", base)           # os kept
    assert not module.refactor_equivalent("import hashlib, os\nVALUE = 1\n", "VALUE = 1\n")
    assert module.refactor_equivalent("from braunschweig import synpp_tokens, config_compose\n" + base,
                                      "from braunschweig import config_compose\n" + base)


def test_refactor_equivalence_keeps_every_other_import_and_every_constant():
    module = _load()
    base = "VALUE = 1\ndef validate(context):\n    return 1\n"
    assert not module.refactor_equivalent(base, "import json\n" + base)
    assert not module.refactor_equivalent(base, "import hashlib as h\n" + base)      # aliased: not tooling
    assert not module.refactor_equivalent(base, "from hashlib import md5\n" + base)
    assert not module.refactor_equivalent(base, "import importlib.util\n" + base)
    assert not module.refactor_equivalent(base, base.replace("VALUE = 1", "VALUE = 2"))
    assert not module.refactor_equivalent(base, base + "def helper():\n    return 2\n")
    # a nested validate (a method) is ordinary code, not the stage hook
    klass = "class A:\n    def validate(self):\n        return {}\n"
    assert not module.refactor_equivalent(klass, klass.replace("{}", "[]"))
    assert module.refactor_equivalent(base, '"""Doc."""\n# comment\n' + base.replace("return 1", "return 2"))


# --- atomic rewrite -----------------------------------------------------------------------------

def test_write_meta_replaces_atomically_and_keeps_a_backup(tmp_path):
    from braunschweig.synpp_fork import metadata
    (tmp_path / "pipeline.json").write_bytes(b'{"a": {"updated": 1}}')
    metadata.write_meta({"b": {"updated": 2}}, str(tmp_path))
    assert json.loads((tmp_path / "pipeline.json").read_text(encoding="utf-8")) == {"b": {"updated": 2}}
    assert (tmp_path / "pipeline.json.bk").read_bytes() == b'{"a": {"updated": 1}}'
    assert not (tmp_path / "pipeline.json.new").exists()


def test_write_meta_failure_leaves_the_old_file_in_place(tmp_path):
    from braunschweig.synpp_fork import metadata
    (tmp_path / "pipeline.json").write_bytes(b'{"a": {"updated": 1}}')
    with pytest.raises(TypeError):
        metadata.write_meta({"b": object()}, str(tmp_path))
    assert (tmp_path / "pipeline.json").read_bytes() == b'{"a": {"updated": 1}}'


# --- review minors (N1, N2, N4, N5, N8) -------------------------------------------------------

def test_a_refactored_validate_that_hashes_an_extra_module_is_not_migrated(project, caplog):
    """N1: the replay proves only the modules the legacy token covered; a wider new token is a change."""
    project.write("mig_helper.py", HELPER)
    project.write("mig_extra.py", "EXTRA = 1\n")
    project.write("mig_stage.py", LEGACY_STAGE)
    rev = project.commit()
    project.legacy_run("mig_stage")
    before = project.meta()
    project.write("mig_stage.py", NEW_STAGE.replace(
        "source_token(_HELPER_MODULES, _DEFERRED_HELPER_MODULE_NAMES)", 'source_token(_HELPER_MODULES, ("mig_extra",))'))
    project.monkeypatch.delitem(sys.modules, "mig_extra", raising=False)
    with caplog.at_level("WARNING"):
        counts, meta = project.migrate("mig_stage", rev)
    assert counts["module_hash_migrated"] == 1
    assert counts["token_mismatch"] == 1 and counts["token_migrated"] == 0
    assert counts["kept_legacy"] == 1 and meta == before
    assert "token_coverage_widened" in caplog.text and "mig_extra" in caplog.text


@pytest.mark.parametrize("stored", ["a" * 64, None])
def test_a_non_legacy_module_hash_that_differs_is_a_mismatch(project, stored):
    """N2: a 64-hex hash that is not the current digest, or a missing one, never migrates."""
    project.write("mig_stage.py", PLAIN_STAGE)
    rev = project.commit()
    project.legacy_run("mig_stage")
    meta = project.meta()
    entry = next(iter(meta))
    if stored is None:
        del meta[entry]["module_hash"]
    else:
        meta[entry]["module_hash"] = stored
    (project.working / "pipeline.json").write_text(json.dumps(meta), encoding="utf-8")
    before = project.meta()
    counts, meta = project.migrate("mig_stage", rev)
    assert counts["module_hash_mismatch"] == 1 and counts["migrated"] == 0 and counts["kept_legacy"] == 1
    assert meta == before


def test_getsource_is_restored_after_a_replay_that_raises(project):
    """N4: a legacy validate() that raises leaves inspect.getsource unpatched."""
    project.write("mig_helper.py", HELPER)
    project.write("mig_stage.py", LEGACY_COMPOSITE)
    rev = project.commit()
    data = project.root / "data.bin"
    data.write_bytes(b"payload")
    project.config["data_file"] = str(data)
    project.legacy_run("mig_stage")
    project.write("mig_stage.py", NEW_COMPOSITE)
    data.unlink()                                            # the replayed validate() raises
    real = inspect.getsource
    project.registry("mig_stage")                            # import before migrate's own registry build
    counts, _ = project.migrate("mig_stage", rev)
    assert counts["token_unverifiable"] == 1 and counts["kept_legacy"] == 1
    assert inspect.getsource is real
    source = _load().GitLegacySource(project.root, rev)
    with pytest.raises(ValueError):
        with source.patched_getsource():
            raise ValueError("boom")
    assert inspect.getsource is real


def test_refactor_equivalence_removes_only_a_module_level_def_validate():
    """N4: an assignment named validate and an async def validate are ordinary code."""
    module = _load()
    assert not module.refactor_equivalent("validate = 1\nX = 1\n", "validate = 2\nX = 1\n")
    assert not module.refactor_equivalent("validate = 1\nX = 1\n", "X = 1\n")
    assert not module.refactor_equivalent("async def validate(c):\n    return 1\n",
                                          "async def validate(c):\n    return 2\n")


@pytest.mark.parametrize("target", ["copy2", "replace"])
def test_write_meta_failure_after_the_new_file_keeps_the_old_file_and_cleans_up(tmp_path, monkeypatch, target):
    """N5: a failing backup copy or rename leaves pipeline.json intact and no pipeline.json.new."""
    from braunschweig.synpp_fork import metadata

    def fail(*args, **kwargs):
        raise OSError("simulated %s failure" % target)

    (tmp_path / "pipeline.json").write_bytes(b'{"a": {"updated": 1}}')
    if target == "copy2":
        monkeypatch.setattr(metadata.shutil, "copy2", fail)
    else:
        monkeypatch.setattr(metadata.os, "replace", fail)
    with pytest.raises(OSError):
        metadata.write_meta({"b": {"updated": 2}}, str(tmp_path))
    assert (tmp_path / "pipeline.json").read_bytes() == b'{"a": {"updated": 1}}'
    assert not (tmp_path / "pipeline.json.new").exists()


def test_rev_text_drops_a_utf8_bom_like_getsource(project):
    """N8: inspect.getsource strips a UTF-8 BOM; the legacy texts must match it byte for byte."""
    (project.root / "bom_changed.py").write_bytes(b"\xef\xbb\xbfVALUE = 1\n")
    (project.root / "bom_same.py").write_bytes(b"\xef\xbb\xbfVALUE = 3\n")
    rev = project.commit()
    (project.root / "bom_changed.py").write_bytes(b"\xef\xbb\xbfVALUE = 2\n")
    source = _load().GitLegacySource(project.root, rev)
    assert source.rev_text(project.root / "bom_changed.py") == "VALUE = 1\n"
    assert source.rev_text(project.root / "bom_same.py") == "VALUE = 3\n"
    project.monkeypatch.delitem(sys.modules, "bom_same", raising=False)
    importlib.invalidate_caches()
    assert inspect.getsource(importlib.import_module("bom_same")) == "VALUE = 3\n"


# --- review fix wave (FW3, FW6) -------------------------------------------------------------------

def test_the_per_entry_line_and_the_summary_carry_the_output_hashing_seconds(project, caplog):
    project.write("mig_stage.py", PLAIN_STAGE)
    rev = project.commit()
    project.legacy_run("mig_stage")
    config_path = project.root / "config.yml"
    config_path.write_text(yaml.safe_dump({"working_directory": str(project.working), "run": ["mig_stage"],
                                           "config": project.config}), encoding="utf-8")
    project.reload()
    with caplog.at_level("INFO"):
        assert _load().main([str(config_path), "--legacy-source-repo", str(project.root),
                             "--legacy-source-rev", rev]) == 0
    assert re.search(r"migrated \(output hash over \d+ files, \d+ bytes, \d+\.\d+ s\)", caplog.text)
    assert re.search(r"output hashing took \d+\.\d+ s in total", caplog.text)


def test_the_launcher_logs_the_unmigrated_cache_hint_at_error_level(project, caplog):
    project.write("mig_stage.py", PLAIN_STAGE)
    project.commit()
    project.legacy_run("mig_stage")
    config_path = project.root / "launch.yml"
    config_path.write_text(yaml.safe_dump({"working_directory": str(project.working), "run": ["mig_stage"],
                                           "config": project.config}), encoding="utf-8")
    project.reload()
    spec = importlib.util.spec_from_file_location(
        "run_synpp_hint_mod", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "run_synpp.py")
    launcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launcher)
    with caplog.at_level("INFO"):
        launcher.explain_before_run(str(config_path))        # no gate: the launch continues
    hint = [r for r in caplog.records if "legacy md5 module hash" in r.getMessage()]
    assert hint and all(r.levelname == "ERROR" for r in hint)
