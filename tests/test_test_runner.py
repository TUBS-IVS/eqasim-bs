"""Exercise the public test command in a fresh process and unrelated directory."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


RUNNER = Path(__file__).resolve().parents[1] / "scripts" / "run_tests.py"


def _run(tmp_path, source, *options):
    test_file = tmp_path / "test_probe.py"
    test_file.write_text(source, encoding="utf-8")
    env = dict(os.environ, PYTHONUTF8="1", EQASIM_BS_RUN_PIPELINE="1")
    env.pop("PYTHONPATH", None)
    # The repository ini also sets pytest's default conftest boundary. Override
    # both boundaries so external probes never scan unrelated shared Temp paths.
    return subprocess.run(
        [sys.executable, str(RUNNER), "--no-evidence", *options, str(test_file), "-q",
         f"--rootdir={tmp_path}", f"--confcutdir={tmp_path}"],
        cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8",
        timeout=60,
    )


def test_runner_uses_repository_imports_and_excludes_pipeline_by_default(tmp_path):
    result = _run(tmp_path, '''
import pathlib
import braunschweig
import pytest

def test_import_and_utf8():
    import sys
    import os
    assert sys.flags.utf8_mode == 1
    assert "EQASIM_BS_RUN_PIPELINE" not in os.environ
    assert pathlib.Path("configs/base_bs.yml").is_file()

@pytest.mark.pipeline
def test_real_pipeline_is_not_started():
    raise AssertionError("Pipeline must be selected explicitly")
''')
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed, 1 deselected" in result.stdout


def test_runner_propagates_pytest_failure(tmp_path):
    result = _run(tmp_path, "def test_failure():\n    assert False\n")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "1 failed" in result.stdout


def test_pipeline_collection_selects_only_pipeline_without_requiring_data(tmp_path):
    result = _run(tmp_path, '''
import json
import os
import pytest

@pytest.mark.pipeline
def test_pipeline_opt_in():
    assert os.environ["EQASIM_BS_RUN_PIPELINE"] == "1"

def test_unit_is_not_selected():
    raise AssertionError("Only pipeline tests should be selected")
''', "--pipeline", "--collect-only")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1/2 tests collected (1 deselected)" in result.stdout


def test_pipeline_input_preflight_failure_stops_before_pytest(tmp_path, monkeypatch, capfd):
    from scripts import run_tests

    monkeypatch.setattr(run_tests, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(run_tests, "check_environment", lambda: {
        "python": sys.executable, "python_version": "3.10", "errors": [],
    })
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "verify_braunschweig_inputs.py").write_text(
        'print("Missing required inputs")\nraise SystemExit(1)\n', encoding="utf-8",
    )
    assert run_tests.main(["--pipeline"]) == 1
    assert "Missing required inputs" in capfd.readouterr().out


def _linked_worktree(tmp_path, pointer):
    """Lay out a main repository's worktree admin dir and a linked worktree as git does.

    ``<mount>/c/repo/main/.git/worktrees/wt`` is the admin dir (with ``commondir``);
    ``<mount>/c/repo/wt/.git`` is the pointer file whose content is ``pointer``. ``mount``
    stands in for WSL's ``/mnt`` so a Windows path ``C:/repo/...`` maps to ``<mount>/c/repo/...``.
    """
    mount = tmp_path / "mnt"
    admin = mount / "c" / "repo" / "main" / ".git" / "worktrees" / "wt"
    admin.mkdir(parents=True)
    (admin / "commondir").write_text("../..\n", encoding="utf-8")
    checkout = mount / "c" / "repo" / "wt"
    checkout.mkdir()
    (checkout / ".git").write_text(pointer, encoding="utf-8")
    return mount, checkout


@pytest.mark.parametrize("pointer, repaired, message", [
    # Windows git wrote an absolute Windows path: POSIX git inside WSL reads it as a
    # relative path and fails. The repair must make it relative to the checkout.
    ("gitdir: C:/repo/main/.git/worktrees/wt\n", "../main/.git/worktrees/wt", "repaired worktree pointer"),
    ("gitdir: C:\\repo\\main\\.git\\worktrees\\wt\n", "../main/.git/worktrees/wt", "repaired worktree pointer"),
    # Already resolvable by both gits: leave untouched (idempotence).
    ("gitdir: ../main/.git/worktrees/wt\n", None, ""),
    ("gitdir: /mnt/c/repo/main/.git/worktrees/wt\n", None, ""),
    # The admin dir the pointer names does not exist under the mount: refuse loudly,
    # never write a pointer that cannot be verified.
    ("gitdir: C:/elsewhere/.git/worktrees/wt\n", None, "cannot repair worktree pointer"),
])
def test_repair_worktree_pointer_makes_windows_gitdir_relative(tmp_path, capfd, pointer, repaired, message):
    from scripts import run_tests

    mount, checkout = _linked_worktree(tmp_path, pointer)
    assert run_tests.repair_worktree_pointer(checkout, drive_mount=mount) == repaired
    expected = pointer if repaired is None else f"gitdir: {repaired}\n"
    assert (checkout / ".git").read_text(encoding="utf-8") == expected
    captured = capfd.readouterr()
    assert message in captured.out + captured.err
    # A second run finds nothing left to repair and changes nothing.
    assert run_tests.repair_worktree_pointer(checkout, drive_mount=mount) is None
    assert (checkout / ".git").read_text(encoding="utf-8") == expected


def test_repair_worktree_pointer_ignores_a_main_checkout(tmp_path):
    from scripts import run_tests

    (tmp_path / ".git").mkdir()  # a real repository directory, not a linked worktree
    assert run_tests.repair_worktree_pointer(tmp_path, drive_mount=tmp_path / "mnt") is None


@pytest.mark.parametrize("system, options, expected", [
    ("Linux", [], "gitdir: ../main/.git/worktrees/wt\n"),
    ("Linux", ["--keep-worktree-pointer"], "gitdir: C:/repo/main/.git/worktrees/wt\n"),
    ("Windows", [], "gitdir: C:/repo/main/.git/worktrees/wt\n"),
])
def test_runner_repairs_worktree_pointer_only_on_linux_unless_opted_out(
        tmp_path, monkeypatch, system, options, expected):
    from scripts import run_tests

    mount, checkout = _linked_worktree(tmp_path, "gitdir: C:/repo/main/.git/worktrees/wt\n")
    monkeypatch.setattr(run_tests, "REPO_ROOT", checkout)
    monkeypatch.setattr(run_tests, "WSL_DRIVE_MOUNT", mount)
    monkeypatch.setattr(run_tests.platform, "system", lambda: system)
    monkeypatch.setattr(run_tests, "check_environment", lambda: {
        "python": sys.executable, "python_version": "3.10", "errors": [],
    })
    monkeypatch.setattr(run_tests.subprocess, "call", lambda *args, **kwargs: 0)
    assert run_tests.main([*options, "-q"]) == 0
    assert (checkout / ".git").read_text(encoding="utf-8") == expected


@pytest.mark.parametrize("pipeline", [False, True])
def test_caller_marker_filters_within_enforced_boundary(tmp_path, pipeline):
    options = (["--pipeline", "-mnot slow"] if pipeline
               else ["--", "-m", "not slow"])
    options.extend(["--collect-only", "-o", "markers=pipeline: real data\nslow: slow test"])
    result = _run(tmp_path, '''
import pytest
def test_fast_unit(): pass
@pytest.mark.slow
def test_slow_unit(): pass
@pytest.mark.pipeline
def test_fast_pipeline(): pass
@pytest.mark.pipeline
@pytest.mark.slow
def test_slow_pipeline(): pass
''', *options)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1/4 tests collected (3 deselected)" in result.stdout
    assert ("test_fast_pipeline" if pipeline else "test_fast_unit") in result.stdout


def _committed_repo(path):
    """A git repository with one commit; returns its HEAD sha."""
    def git(*args):
        return subprocess.run(["git", *args], cwd=path, capture_output=True, text=True,
                              check=True).stdout.strip()
    git("init", "-q")
    git("config", "user.email", "t@example.org")
    git("config", "user.name", "t")
    (path / "a.txt").write_text("a\n", encoding="utf-8")
    git("add", "a.txt")
    git("commit", "-q", "-m", "init")
    return git("rev-parse", "HEAD")


@pytest.fixture
def evidence_runner(tmp_path, monkeypatch):
    """run_tests with REPO_ROOT on a committed tmp repo and pytest replaced by ``exit_code``."""
    from scripts import run_tests

    sha = _committed_repo(tmp_path)
    calls = []

    def fake_call(command, **kwargs):
        calls.append(command)
        return fake_call.exit_code
    fake_call.exit_code = 0
    monkeypatch.setattr(run_tests, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(run_tests, "check_environment", lambda: {
        "python": sys.executable, "python_version": "3.10", "errors": [],
    })
    monkeypatch.setattr(run_tests.subprocess, "call", fake_call)
    return run_tests, tmp_path, sha, fake_call, calls


def _evidence(root):
    from braunschweig.gate_evidence import platform_tag
    path = root / ".claude" / "gate" / f"tests-{platform_tag()}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def test_runner_writes_platform_tagged_evidence_for_a_full_run(evidence_runner):
    from braunschweig.gate_evidence import platform_tag

    run_tests, root, sha, _, calls = evidence_runner
    assert run_tests.main(["-q"]) == 0
    evidence = _evidence(root)
    assert evidence["head"] == sha and evidence["dirty"] is False
    assert evidence["scope"] == "full" and evidence["exit"] == 0
    assert evidence["platform"] == platform_tag()
    # Without a caller-supplied --junitxml the runner adds one, so counts are recorded.
    assert evidence["junitxml"].endswith(f"junit-{platform_tag()}.xml")
    assert any(arg.startswith("--junitxml=") for arg in calls[0])


def test_runner_marks_focused_failing_runs(evidence_runner):
    run_tests, root, _, fake_call, _ = evidence_runner
    fake_call.exit_code = 1
    assert run_tests.main(["-k", "probe"]) == 1
    evidence = _evidence(root)
    assert evidence["scope"] == "focused" and evidence["exit"] == 1


def test_runner_check_mode_writes_no_evidence(evidence_runner):
    run_tests, root, _, _, _ = evidence_runner
    assert run_tests.main(["--check"]) == 0
    assert _evidence(root) is None


def test_runner_no_evidence_option_writes_nothing(evidence_runner):
    # The fresh-process probes above run the real runner on throwaway tests; they must
    # not overwrite the checkout's real gate evidence.
    run_tests, root, _, _, _ = evidence_runner
    assert run_tests.main(["--no-evidence", "-q"]) == 0
    assert _evidence(root) is None
