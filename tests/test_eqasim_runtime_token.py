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


def test_validate_returns_a_content_token(java_repo):
    class _Context:
        def config(self, key):
            return {"eqasim_source_path": str(java_repo), "eqasim_path": ""}[key]
    token = eqasim.validate(_Context())
    assert token == {"format": 1, "source": eqasim.source_tree_digest(str(java_repo))}


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
