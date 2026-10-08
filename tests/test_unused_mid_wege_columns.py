"""Tests for braunschweig.popsim.unused_mid_wege_columns (ADR-0138).

The list names MiD 2023 Wege columns that no first-party code reads, so the trip build may drop
them before the persons x Wege join copies every Wege column once per synthetic trip. Two
properties make that safe, and both are pinned here: the drop removes exactly the listed columns
and nothing else, and no listed column is read anywhere in the repository (the repository scan
below fails the moment code starts reading one, naming the file and the column).
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import pandas as pd

from braunschweig.popsim import unused_mid_wege_columns as unused

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFINING_MODULE = Path("braunschweig", "popsim", "unused_mid_wege_columns.py")
#: The first-party code and configuration roots the scan walks: every package that hosts pipeline
#: stages or their helpers, the scripts, the configuration, CI and the environment files. Nothing
#: else is walked, so a local environment (``.venv``), untracked output or a worktree under
#: ``.claude`` can neither slow the scan down nor fail it on third-party text. Outside the scan by
#: design: ``tests/`` (fixtures may name listed columns), ``docs/`` and ``eqasim-data/``, whose
#: committed scripts are diagnostics of past runs, not pipeline code.
#: ``test_every_registered_code_path_lies_in_a_scanned_root`` keeps this tuple complete.
FIRST_PARTY_ROOTS = ("braunschweig", "synthesis", "matsim", "data", "eqasim_common", "scripts",
                     "configs", "documentation", ".github", "environments")
#: Directory names never descended into inside a first-party root.
PRUNED_DIRECTORY_NAMES = frozenset({"__pycache__", ".pytest_cache", ".ipynb_checkpoints",
                                    "node_modules"})
#: Code AND configuration: a config value naming a column is a read the Python scan cannot see.
SCAN_SUFFIXES = frozenset({".py", ".yml", ".yaml", ".toml", ".json", ".ipynb", ".cfg", ".ini",
                           ".sh", ".ps1"})
SCAN_MAX_FILE_BYTES = 5_000_000
#: A merge that suffixes a column still reads it: pandas' ``_x``/``_y`` and every suffix a merge in
#: the code base uses (``_weg`` in the trip build, ``_hh``, ``_person``, ``_child``, ``_seed``,
#: ``_school``, ``_home``).
MERGE_SUFFIXES = ("_weg", "_x", "_y", "_hh", "_person", "_child", "_seed", "_school", "_home")


def _is_scanned_file(path, relative):
    return (path.is_file() and path.suffix in SCAN_SUFFIXES and relative != DEFINING_MODULE
            and path.stat().st_size <= SCAN_MAX_FILE_BYTES)


def _first_party_files(root=REPO_ROOT):
    for path in sorted(root.iterdir()):
        if _is_scanned_file(path, path.relative_to(root)):
            yield path.relative_to(root), path
    for top in FIRST_PARTY_ROOTS:
        for directory, subdirectories, filenames in os.walk(root / top):
            subdirectories[:] = sorted(name for name in subdirectories
                                       if name not in PRUNED_DIRECTORY_NAMES)
            for filename in sorted(filenames):
                path = Path(directory, filename)
                relative = path.relative_to(root)
                if _is_scanned_file(path, relative):
                    yield relative, path


def test_the_scan_walks_first_party_code_and_configuration_only(tmp_path):
    """A local environment (``.venv``), untracked output next to the code (``brag-output``) or a
    worktree under ``.claude`` is not first-party code: scanning it made the guard fail on
    third-party text that merely contains a listed word (scipy's ``tempo``) and walk hundreds of
    thousands of paths. Only the first-party roots and the root-level files are scanned."""
    for relative, text in {
        "braunschweig/popsim/reader.py": "x = 1\n",
        "configs/base.yml": "key: value\n",
        "run_something.py": "y = 2\n",
        ".venv/Lib/site-packages/scipy/constants.py": "tempo = 1\n",
        "brag-output/cues.json": '{"tempo": 109.96}\n',
        ".claude/worktrees/other/braunschweig/a.py": "tempo = 1\n",
        "eqasim-data/data/diagnostics.py": "tempo = 1\n",
        "docs/verify_data.py": "tempo = 1\n",
        "tests/test_fixture.py": "tempo = 1\n",
        "braunschweig/__pycache__/reader.py": "tempo = 1\n",
    }.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    scanned = {relative.as_posix() for relative, _ in _first_party_files(tmp_path)}

    assert scanned == {"braunschweig/popsim/reader.py", "configs/base.yml", "run_something.py"}


def _wege_with(columns):
    return pd.DataFrame({column: [index, index + 1] for index, column in enumerate(columns)})


def test_drop_removes_the_listed_columns_and_keeps_every_other_column_in_order():
    listed = list(unused.UNUSED_MID_WEGE_COLUMNS[:3])
    kept = ["H_ID", "P_ID", "W_ID", "W_ZWECK", "hvm_imp", "wegkm_imp"]
    interleaved = [kept[0], listed[0], kept[1], kept[2], listed[1], kept[3], kept[4], listed[2],
                   kept[5]]
    wege = _wege_with(interleaved)

    narrowed = unused.drop_unused_mid_wege_columns(wege, log_tag="[test]")

    assert list(narrowed.columns) == kept
    pd.testing.assert_frame_equal(narrowed, wege[kept])


def test_drop_leaves_the_input_frame_untouched():
    columns = ["H_ID", unused.UNUSED_MID_WEGE_COLUMNS[0], "W_ID"]
    wege = _wege_with(columns)
    before = wege.copy()

    unused.drop_unused_mid_wege_columns(wege, log_tag="[test]")

    pd.testing.assert_frame_equal(wege, before)


def test_drop_ignores_listed_columns_the_delivery_does_not_carry():
    # A delivery (or a unit fixture) without any listed column passes through unchanged: the
    # list says which columns MAY be dropped, it is not a schema the input must satisfy.
    wege = _wege_with(["H_ID", "P_ID", "W_ID", "W_ZWECK"])

    narrowed = unused.drop_unused_mid_wege_columns(wege, log_tag="[test]")

    pd.testing.assert_frame_equal(narrowed, wege)


def test_drop_logs_how_many_columns_it_dropped_and_how_many_it_kept(caplog):
    listed = list(unused.UNUSED_MID_WEGE_COLUMNS[:2])
    wege = _wege_with(["H_ID", listed[0], "P_ID", listed[1], "W_ID"])

    with caplog.at_level(logging.INFO, logger=unused.__name__):
        unused.drop_unused_mid_wege_columns(wege, log_tag="[test]")

    messages = [record.getMessage() for record in caplog.records]
    assert any(message.startswith("[test]") and "dropped 2/5" in message and "kept 3" in message
               for message in messages), messages


def test_drop_warns_when_listed_columns_are_not_in_the_frame(caplog):
    """A delivery that renamed its variables would turn the drop into a silent no-op (memory
    only, never results); the listed columns the frame does not carry are therefore a WARNING."""
    wege = _wege_with(["H_ID", unused.UNUSED_MID_WEGE_COLUMNS[0], "W_ID"])

    with caplog.at_level(logging.INFO, logger=unused.__name__):
        unused.drop_unused_mid_wege_columns(wege, log_tag="[test]")

    warnings = [record.getMessage() for record in caplog.records
                if record.levelno == logging.WARNING]
    n_listed = len(unused.UNUSED_MID_WEGE_COLUMNS)
    assert len(warnings) == 1, warnings
    assert warnings[0].startswith("[test]") and f"{n_listed - 1}/{n_listed} listed" in warnings[0]


def test_drop_only_informs_when_the_frame_carries_every_listed_column(caplog):
    wege = _wege_with(["H_ID"] + list(unused.UNUSED_MID_WEGE_COLUMNS) + ["W_ID"])

    with caplog.at_level(logging.INFO, logger=unused.__name__):
        narrowed = unused.drop_unused_mid_wege_columns(wege, log_tag="[test]")

    assert list(narrowed.columns) == ["H_ID", "W_ID"]
    assert [record.levelno for record in caplog.records] == [logging.INFO]


def test_the_list_names_each_column_once():
    names = unused.UNUSED_MID_WEGE_COLUMNS
    assert len(names) == len(set(names))
    assert all(isinstance(name, str) and name for name in names)


def test_no_listed_column_is_read_by_first_party_code():
    """The safety argument of ADR-0138: a column nobody reads cannot change a result.

    Scans every file under the first-party roots (and the root-level files) for an exact token
    of a listed column, also with a merge suffix. A hit means code now reads that column, so
    dropping it before the trip build would hand that code a frame without it: remove the column
    from ``UNUSED_MID_WEGE_COLUMNS`` in the same change.
    """
    names = "|".join(re.escape(name) for name in unused.UNUSED_MID_WEGE_COLUMNS)
    suffixes = "|".join(re.escape(suffix) for suffix in MERGE_SUFFIXES)
    token = re.compile(rf"(?<![A-Za-z0-9_])({names})(?:{suffixes})?(?![A-Za-z0-9_])")

    scanned_python_files = 0
    hits = []
    for relative, path in _first_party_files():
        scanned_python_files += path.suffix == ".py"
        text = path.read_text(encoding="utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), 1):
            for match in token.finditer(line):
                hits.append(f"{relative}:{number}: {match.group(1)!r} in {line.strip()[:100]!r}")

    # A scan that silently found nothing (wrong root, everything excluded) would pass vacuously.
    assert scanned_python_files > 300, scanned_python_files
    assert not hits, (
        "listed MiD Wege columns are read by first-party code; remove them from "
        "braunschweig.popsim.unused_mid_wege_columns.UNUSED_MID_WEGE_COLUMNS (if a hit is not a "
        "column read but prose or an unrelated identifier that happens to spell a listed name, "
        "such as 'tempo', rephrase or rename it instead):\n"
        + "\n".join(hits))


def test_every_registered_code_path_lies_in_a_scanned_root():
    """A first-party root missing from FIRST_PARTY_ROOTS would drop out of the scan without anyone
    noticing. Every code path the Stage and Feature Registries name must therefore lie in a
    scanned root or be a root-level file."""
    import yaml

    registry = REPO_ROOT / "docs" / "registry"
    code_paths = set()
    for kind, key in (("stages", "code"), ("features", "code_paths")):
        for record_path in sorted((registry / kind).glob("*.yml")):
            record = yaml.safe_load(record_path.read_text(encoding="utf-8")) or {}
            code_paths.update(record.get(key) or [])

    # An empty registry read (wrong path, renamed key) would make this test pass vacuously.
    assert len(code_paths) > 100, len(code_paths)
    outside = sorted(path for path in code_paths
                     if len(Path(path).parts) > 1 and Path(path).parts[0] not in FIRST_PARTY_ROOTS)
    assert not outside, outside
