# Linux parity testing: validate on the WSL mirror before calling a branch green

## The rule

A test run on the Windows development box is not evidence that a branch is
green on Linux, where CI and the production server run. Before calling a
branch's test result final -- in a PR, in a run manifest, in a session close --
validate the same node ids on the WSL mirror of the server environment. Doing
this takes seconds; not doing it lets a platform-specific defect ride into
`main` looking like a pass.

## The invocation, including the Git Bash caveat

The development machine carries a Linux mirror of the server stack inside
WSL: `wsl.exe -d Ubuntu` reaches `/home/felix/miniforge3/envs/eqasim/bin/python`
(Python 3.10.10, numpy 1.23.5, pandas 1.5.3 -- the same pinned stack documented in
[reproducible-environment](reproducible-environment.md)), and the repository is
visible at `/mnt/c/Users/bienzeisler/Documents/GitHub/eqasim-bs`.

Git Bash prefixes the interpreter path with `C:/Program Files/Git` and mangles
the call, so invoke `wsl.exe` from PowerShell, not from the Bash tool:

```powershell
wsl.exe -d Ubuntu -e bash -lc "cd /mnt/c/Users/bienzeisler/Documents/GitHub/eqasim-bs/<checkout> && /home/felix/miniforge3/envs/eqasim/bin/python -m pytest <node ids>"
```

Substitute the actual checkout or worktree path. Reproducing a three-test
failure this way takes about 4 seconds -- there is no excuse to skip it before
a claim of "tests pass".

## A Windows-created worktree cannot resolve `.git` from WSL

Running the suite from WSL against a git worktree that was created on Windows
fails every git-dependent test for an environmental reason, not a code defect:
the worktree's `.git` file holds a Windows absolute path that git inside WSL
cannot follow. `git rev-parse --short HEAD` exits 128 there, and
`braunschweig.provenance` logs "cannot determine git commit for
'/mnt/c/.../<worktree>' ... recording 'unknown'" for every test that depends
on it. The main checkout is unaffected -- its `.git` is a real directory, not a
worktree pointer file -- so this is specific to Windows-created worktrees, not
to WSL itself.

Recognise it by where the failures land: they cluster in
`tests/test_gitignore_root_scratch.py` and `tests/test_run_provenance.py`,
alongside that `git rev-parse` exit-128 warning in the output. Measured on one
branch: a full WSL run against a Windows-created worktree reported 25 failed,
5919 passed, 35 skipped, and all 25 failures came from exactly those two files
(isolating them gives 25 failed, 4 passed) -- no other test was affected. A WSL
run reporting failures only in those two files is a clean run.

So read such a run as covering everything except the git-dependent tests, and
take those from the Windows run or the server, both of which have a `.git`
that git can resolve.

## Division of labour

- **WSL (`eqasim` env)** -- the fast, always-available Linux parity check for the
  test suite. 31 GB RAM / 22 cores, which suits pytest but not a full pipeline run.
- **The Windows environment** -- stays the environment for actually running the
  pipeline (`osm` binaries and paths are Windows-native here); do not try to move
  pipeline execution into WSL.
- **The server** -- the target of both: the authority for full runs and the
  source the WSL mirror was captured from.

## Why a Windows-only result is not evidence

The tests most likely to disagree between platforms are exactly the ones this
project gates on real, restricted input data (see
[test-data-dependencies](test-data-dependencies.md) and
[ci-data-availability-checks](ci-data-availability-checks.md)): they SKIP outright
on any machine without the MiD 2023 delivery, including CI. A byte-identity
golden or a hash comparison over real data is precisely where a platform-level
numeric difference (a default integer width, a floating-point library revision)
would surface -- and precisely what a suite with the data absent can never
exercise, however green it looks.

So do not read a "0 failed" as parity. Compare the **SKIP count** between
platforms too, and treat a shrinking or growing skip count as a signal that the
two environments do not see the same inputs.

## Why this rule exists: the int32/int64 incident (found 2026-09-16)

Three byte-identity/hash golden tests were found RED on Linux (and on
`origin/main`, unrelated to any feature branch) while GREEN on the Windows
development box:

- `tests/test_diary_facts.py::test_compute_diary_facts_output_unchanged_on_the_module_fixture`
- `tests/test_commute_day_donor_pool.py::test_defaults_keep_the_pool_byte_identical`
- `tests/test_day_absence.py::test_individual_stage_min_household_size_1_matches_the_ada06b61_golden_hash`

Cause for the first two: the pinned goldens carried `int32` columns
(`n_direct_legs` in the first test, `n_trips` in the second), and `astype(int)`
-- used throughout `compute_diary_facts` in `braunschweig/popsim/diary_facts.py`
-- resolves to `int32` on Windows but `int64` on Linux. `pandas.testing.assert_frame_equal(check_dtype=True)`
then reported `int64` against `int32` on that column. The third test failed as
a golden hash mismatch; it is plausibly the same dtype root cause, but that was
**not** proven, so do not report it as proven. All three are gated on the
restricted MiD 2023 delivery and therefore skip everywhere that delivery is
absent, which is why this went unnoticed: 37 skipped on the Windows development
box against 33 skipped on the server.

The durable lesson, independent of these three tests, is the general rule: **a
golden must not pin a dtype that came out of `astype(int)` or any other
platform-dependent construction.** When adding or reviewing a byte-identity or
hash golden, either make the production code cast to an explicit,
platform-independent dtype (so the golden is deterministic on every platform),
or compare with `check_dtype=False` and assert the dtype separately, in a
platform-independent way, only where the dtype is genuinely load-bearing. A
golden that silently encodes the author's platform is not a behaviour pin.

The three tests named above were being removed, in a parallel change, at the
time of writing, judged unsound for exactly this reason -- if you do not find
them in the suite, that is why; do not assume they were never wired up. If a
Linux-only failure shows up in a test that still exists, the first check is to
re-run the same node ids, unmodified, against an untouched `origin/main` before
treating it as caused by your branch (see the next section).

## Before blaming a branch for a Linux-only failure

Re-run the same failing node ids, unmodified, against an untouched
`origin/main` using the invocation above. If they are already red there, the
branch did not cause them -- cite this note and the incident above rather than
re-deriving the cause. If `origin/main` is green on those node ids and your
branch is not, the branch is the cause.
