"""Content token of the stage ``matsim.runtime.eqasim`` (issue #468).

The token is a digest of the Java source CONTENT, not the newest mtime, so checkouts,
copies and agent worktrees inside the Java repository do not rebuild the jar (and with it
every Java-dependent stage), while a real edit still does.
"""
import os
import subprocess
import time

import pytest

from matsim.runtime import eqasim
from tests import jar_reproducibility


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def java_repo(tmp_path):
    repo = tmp_path / "java"
    (repo / "core/src").mkdir(parents=True)
    (repo / "core/src/A.java").write_text("class A {}\n", encoding="utf-8")
    (repo / "pom.xml").write_text("<project/>\n", encoding="utf-8")
    (repo / ".gitignore").write_text("target/\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "add", ".")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")
    return repo


def test_digest_is_stable_under_mtime_changes(java_repo):
    before = eqasim.source_tree_digest(str(java_repo))
    future = time.time() + 1000
    os.utime(java_repo / "core/src/A.java", (future, future))
    assert eqasim.source_tree_digest(str(java_repo)) == before


def test_digest_changes_on_content_edit(java_repo):
    before = eqasim.source_tree_digest(str(java_repo))
    (java_repo / "core/src/A.java").write_text("class A { int x; }\n", encoding="utf-8")
    assert eqasim.source_tree_digest(str(java_repo)) != before


def test_source_tree_digest_sees_untracked_java_files(java_repo):
    before = eqasim.source_tree_digest(str(java_repo))
    (java_repo / "core/src/B.java").write_text("class B {}\n", encoding="utf-8")
    assert eqasim.source_tree_digest(str(java_repo)) != before


def test_target_and_claude_worktrees_are_ignored(java_repo):
    before = eqasim.source_tree_digest(str(java_repo))
    (java_repo / "core/target").mkdir()
    (java_repo / "core/target/C.java").write_text("class C {}\n", encoding="utf-8")
    (java_repo / ".claude/worktrees/x").mkdir(parents=True)
    (java_repo / ".claude/worktrees/x/D.java").write_text("class D {}\n", encoding="utf-8")
    assert eqasim.source_tree_digest(str(java_repo)) == before


def test_non_source_files_do_not_change_the_digest(java_repo):
    before = eqasim.source_tree_digest(str(java_repo))
    (java_repo / "README.md").write_text("notes\n", encoding="utf-8")
    assert eqasim.source_tree_digest(str(java_repo)) == before


def test_crlf_and_lf_checkouts_of_the_same_content_give_the_same_digest(java_repo):
    # core.autocrlf rewrites line endings per platform; the same commit must key the same jar.
    source = java_repo / "core/src/A.java"
    source.write_bytes(b"class A {\n  int x;\n}\n")
    lf = eqasim.source_tree_digest(str(java_repo))
    source.write_bytes(b"class A {\r\n  int x;\r\n}\r\n")
    assert eqasim.source_tree_digest(str(java_repo)) == lf
    source.write_bytes(b"class A {\r\n  int y;\r\n}\r\n")
    assert eqasim.source_tree_digest(str(java_repo)) != lf


@pytest.mark.parametrize("relative, content", [
    ("pom.xml", "<project><version>2</version></project>\n"),
    ("core/src/app.properties", "key=value\n"),
])
def test_xml_and_properties_edits_change_the_digest(java_repo, relative, content):
    path = java_repo / relative
    if not path.exists():
        path.write_text("key=other\n", encoding="utf-8")
        _git(java_repo, "add", relative)
    before = eqasim.source_tree_digest(str(java_repo))
    path.write_text(content, encoding="utf-8")
    assert eqasim.source_tree_digest(str(java_repo)) != before


def test_a_tracked_but_deleted_source_file_is_skipped(java_repo, tmp_path):
    (java_repo / "core/src/B.java").write_text("class B {}\n", encoding="utf-8")
    _git(java_repo, "add", "core/src/B.java")
    with_b = eqasim.source_tree_digest(str(java_repo))
    (java_repo / "core/src/B.java").unlink()  # still listed by git ls-files --cached
    after_delete = eqasim.source_tree_digest(str(java_repo))
    assert after_delete != with_b
    # Same as a tree that never had B.java (same relative paths and contents).
    other = tmp_path / "other"
    (other / "core/src").mkdir(parents=True)
    (other / "core/src/A.java").write_text("class A {}\n", encoding="utf-8")
    (other / "pom.xml").write_text("<project/>\n", encoding="utf-8")
    (other / ".gitignore").write_text("target/\n", encoding="utf-8")
    _git(other, "init", "-q")
    assert eqasim.source_tree_digest(str(other)) == after_delete


def test_source_tree_digest_raises_outside_a_git_work_tree(tmp_path):
    # Assumes pytest's tmp_path is not inside a git work tree (it lives under the OS temp dir).
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "A.java").write_text("class A {}\n", encoding="utf-8")
    # No git work tree means no file listing; there must be no silent fallback to mtimes.
    with pytest.raises(RuntimeError, match="plain"):
        eqasim.source_tree_digest(str(plain))


def test_shade_plugin_rewrite_of_the_dependency_reduced_pom_does_not_change_the_digest(java_repo):
    # The fork tracks braunschweig/dependency-reduced-pom.xml; every local shade build rewrites it.
    reduced = java_repo / "braunschweig/dependency-reduced-pom.xml"
    reduced.parent.mkdir()
    reduced.write_text("<project>a</project>\n", encoding="utf-8")
    _git(java_repo, "add", "braunschweig/dependency-reduced-pom.xml")
    before = eqasim.source_tree_digest(str(java_repo))
    reduced.write_text("<project>b</project>\n", encoding="utf-8")
    assert eqasim.source_tree_digest(str(java_repo)) == before


def test_a_source_dir_ignored_by_an_enclosing_repo_raises(tmp_path):
    # `git -C sub ls-files` would resolve the parent repo and list nothing: a constant token.
    outer = tmp_path / "outer"
    (outer / "sub").mkdir(parents=True)
    (outer / ".gitignore").write_text("sub/\n", encoding="utf-8")
    (outer / "sub/A.java").write_text("class A {}\n", encoding="utf-8")
    _git(outer, "init", "-q")
    with pytest.raises(RuntimeError, match="root of its own git work tree"):
        eqasim.source_tree_digest(str(outer / "sub"))


def test_a_plain_subdirectory_of_a_repo_raises(java_repo):
    with pytest.raises(RuntimeError, match="root of its own git work tree"):
        eqasim.source_tree_digest(str(java_repo / "core"))


def test_a_work_tree_without_any_source_file_raises(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "README.md").write_text("notes\n", encoding="utf-8")
    _git(empty, "init", "-q")
    with pytest.raises(RuntimeError, match="no source files"):
        eqasim.source_tree_digest(str(empty))


def test_a_missing_git_binary_raises_naming_it(java_repo):
    with pytest.raises(RuntimeError, match="no-such-git-binary"):
        eqasim.source_tree_digest(str(java_repo), git_binary="no-such-git-binary")


def test_validate_passes_the_configured_git_binary(java_repo):
    class _Context:
        def config(self, key):
            return {"eqasim_source_path": str(java_repo), "eqasim_path": "",
                    "git_binary": "no-such-git-binary"}[key]
    with pytest.raises(RuntimeError, match="no-such-git-binary"):
        eqasim.validate(_Context())


def test_a_non_utf8_file_name_raises_runtime_error(java_repo, monkeypatch):
    real_run = subprocess.run

    def fake_run(command, *args, **kwargs):
        if "ls-files" in command:
            return subprocess.CompletedProcess(command, 0, stdout=b"core/\xff.java\x00", stderr=b"")
        return real_run(command, *args, **kwargs)
    monkeypatch.setattr(eqasim.sp, "run", fake_run)
    with pytest.raises(RuntimeError, match="not valid UTF-8"):
        eqasim.source_tree_digest(str(java_repo))


def test_validate_returns_a_content_token(java_repo):
    class _Context:
        def config(self, key):
            return {"eqasim_source_path": str(java_repo), "eqasim_path": "", "git_binary": "git"}[key]
    token = eqasim.validate(_Context())
    assert token == {"format": 1, "source": eqasim.source_tree_digest(str(java_repo))}


def test_validate_follows_execute_precedence_when_both_keys_are_set(java_repo, tmp_path):
    # execute() builds from the prebuilt eqasim_path jar first; the token must describe that jar.
    jar = tmp_path / "braunschweig.jar"
    jar.write_bytes(b"jar-content")

    class _Context:
        def config(self, key):
            return {"eqasim_source_path": str(java_repo), "eqasim_path": str(jar),
                    "git_binary": "git"}[key]
    token = eqasim.validate(_Context())
    assert token == {"format": 1, "jar": eqasim._sha256_file(str(jar))}


def test_validate_returns_a_jar_content_token(tmp_path):
    jar = tmp_path / "braunschweig.jar"
    jar.write_bytes(b"jar-content")

    class _Context:
        def config(self, key):
            return {"eqasim_source_path": "", "eqasim_path": str(jar)}[key]
    first = eqasim.validate(_Context())
    future = time.time() + 1000
    os.utime(jar, (future, future))
    assert eqasim.validate(_Context()) == first
    jar.write_bytes(b"other-content")
    assert eqasim.validate(_Context()) != first


class _BuildContext:
    def __init__(self, cfg, path):
        self._cfg, self._path = cfg, path

    def config(self, key, default=None):
        return self._cfg[key]

    def stage(self, name, *args, **kwargs):
        return None

    def path(self, *args):
        return self._path


def test_execute_passes_the_fixed_output_timestamp_to_maven(tmp_path, monkeypatch):
    # Without a fixed project.build.outputTimestamp the jar plugins stamp every entry with the
    # build time (#474); the gated test below proves the effect on real builds. The value is
    # written out so that changing it, which rebuilds the jar and its consumers once, is deliberate.
    source = tmp_path / "fork"
    (source / "braunschweig").mkdir(parents=True)
    (source / "braunschweig/pom.xml").write_text(
        "<project><parent><version>9.9.9</version></parent></project>", encoding="utf-8")
    stage = tmp_path / "stage"
    stage.mkdir()
    calls = []

    def fake_maven_run(context, arguments, cwd=None):
        calls.append(arguments)
        jar = stage / "eqasim-java/braunschweig/target/braunschweig-9.9.9.jar"
        jar.parent.mkdir(parents=True)
        jar.write_bytes(b"jar")
    monkeypatch.setattr(eqasim.maven, "run", fake_maven_run)
    cfg = {"eqasim_version": None, "eqasim_path": "", "eqasim_source_path": str(source)}
    eqasim.execute(_BuildContext(cfg, str(stage)))
    [arguments] = calls
    assert [a for a in arguments if "outputTimestamp" in a] == [
        "-Dproject.build.outputTimestamp=2026-01-01T00:00:00Z"]


@pytest.mark.skipif(not os.environ.get("EQASIM_BS_EQASIM_SOURCE"),
                    reason="Maven build of the eqasim jar; set EQASIM_BS_EQASIM_SOURCE to an "
                           "eqasim-java-bs work tree to run")
def test_execute_builds_a_byte_reproducible_jar(tmp_path):
    # Two real multi-module Maven builds (about 3 min each locally, a ~190 MB shaded jar each):
    # reproducibility can only be shown on the real plugins of the fork. Issue #474.
    cfg = {"eqasim_version": None, "eqasim_path": "",
           "eqasim_source_path": os.environ["EQASIM_BS_EQASIM_SOURCE"],
           "maven_binary": "mvn", "maven_skip_tests": True, "java_home": ""}
    first, second = jar_reproducibility.build_twice(
        eqasim, lambda path: _BuildContext(cfg, path), tmp_path)
    assert first == second
