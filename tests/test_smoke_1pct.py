"""1% smoke baseline regression for the Braunschweig synthesis pipeline.

Drives ``configs/fixtures/config_local_braunschweig.yml`` end-to-end (``synthesis.output``)
and validates the resulting CSV row counts against the locked baseline
in ``tests/baselines/smoke_1pct_baseline.txt``.

Tolerance
---------

By default the baseline counts are compared with a ``±2%`` relative
tolerance to absorb RNG noise introduced by the cache invalidation that
happens whenever module paths change during the refactor (Phases 2-4).

Strict mode (``EQASIM_BS_STRICT_SMOKE=1``) tightens the check to exact
row counts AND validates ``sha256_12`` hashes byte-for-byte. This is the
mode the Phase 4 final-smoke run will use to confirm that the refactor
remains behaviour-preserving.

Gating
------

The test is opt-in via ``EQASIM_BS_RUN_PIPELINE=1`` *and* the presence
of ``eqasim-data/data/braunschweig/``. Skipped by default so the unit-test
gate stays fast and CI-clean. End-to-end runtime: ~10 minutes on a
laptop with a warm cache, longer on a cold one.

Created in Phase 3.3 of the eqasim-bs refactor (plan/refactor-eqasim-bs.md).
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from pathlib import Path

import pandas as pd
import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SMOKE_CONFIG = REPO_ROOT / "configs" / "fixtures" / "config_local_braunschweig.yml"
BASELINE = REPO_ROOT / "tests" / "baselines" / "smoke_1pct_baseline.txt"
DATA_PATH = REPO_ROOT / "eqasim-data" / "data"
BS_DATA_PATH = DATA_PATH / "braunschweig"

OPT_IN_VAR = "EQASIM_BS_RUN_PIPELINE"
STRICT_VAR = "EQASIM_BS_STRICT_SMOKE"
TOLERANCE = 0.02  # ±2% on row counts in non-strict mode

pytestmark = pytest.mark.skipif(
    os.environ.get(OPT_IN_VAR) != "1" or not BS_DATA_PATH.is_dir(),
    reason=(
        f"Set {OPT_IN_VAR}=1 and provide eqasim-data/data/braunschweig/ "
        "to run the 1% smoke baseline regression "
        "(see eqasim-data/DOWNLOAD_CHECKLIST_BS.md). "
        f"Set {STRICT_VAR}=1 for byte-for-byte hash validation."
    ),
)


# ---------------------------------------------------------------------------
# Baseline parsing
# ---------------------------------------------------------------------------

_BASELINE_LINE_RE = re.compile(
    r"^(?P<file>\S+\.csv):\s+rows=(?P<rows>[\d,]+)\s+"
    r"cols=(?P<cols>\d+)\s+sha256_12=(?P<sha>[0-9a-f]+)\s*$"
)


def _read_baseline_text() -> str:
    """Read the baseline file, tolerating UTF-16 BOM written by PowerShell."""
    raw = BASELINE.read_bytes()
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16")
    return raw.decode("utf-8")


def _parse_baseline() -> dict[str, dict]:
    """Return ``{filename: {"rows": int, "cols": int, "sha": str}}``."""
    out: dict[str, dict] = {}
    for line in _read_baseline_text().splitlines():
        m = _BASELINE_LINE_RE.match(line.strip())
        if not m:
            continue
        out[m.group("file")] = {
            "rows": int(m.group("rows").replace(",", "")),
            "cols": int(m.group("cols")),
            "sha": m.group("sha"),
        }
    if not out:
        raise RuntimeError(
            f"Could not parse any baseline lines from {BASELINE}; "
            "check encoding or format."
        )
    return out


def _sha256_12(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


# ---------------------------------------------------------------------------
# Smoke run
# ---------------------------------------------------------------------------

def _run_smoke(tmp_path: Path) -> Path:
    """Run synthesis.output via configs/fixtures/config_local_braunschweig.yml; return output dir."""
    import synpp

    with SMOKE_CONFIG.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    config = dict(raw.get("config", {}))
    aliases = raw.get("aliases", {})

    cache_path = tmp_path / "cache"
    output_path = tmp_path / "output"
    cache_path.mkdir()
    output_path.mkdir()

    # Keep the input data path as-is so cached stages can be reused, but
    # send all outputs to the pytest tmp_path. Mirror the smoke prefix so
    # the baseline filenames match exactly.
    config["data_path"] = str(DATA_PATH)
    config["output_path"] = str(output_path)
    config["output_prefix"] = "braunschweig_1pct_"

    stages = [{"descriptor": s} for s in raw.get("run", ["synthesis.output"])]
    synpp.run(stages, config, working_directory=str(cache_path), aliases=aliases)
    return output_path


def test_smoke_1pct_matches_baseline(tmp_path):
    baseline = _parse_baseline()
    strict = os.environ.get(STRICT_VAR) == "1"

    output_dir = _run_smoke(tmp_path)

    failures: list[str] = []
    for fname, expected in baseline.items():
        path = output_dir / fname
        if not path.is_file():
            failures.append(f"missing output: {fname}")
            continue

        df = pd.read_csv(path, sep=";", nrows=0)  # cheap col-count probe
        got_cols = len(df.columns)
        got_rows = sum(1 for _ in path.open("r", encoding="utf-8")) - 1
        got_sha = _sha256_12(path)

        if got_cols != expected["cols"]:
            failures.append(
                f"{fname}: cols {got_cols} != baseline {expected['cols']}"
            )

        if strict:
            if got_rows != expected["rows"]:
                failures.append(
                    f"{fname}: rows {got_rows} != baseline {expected['rows']} (strict)"
                )
            if got_sha != expected["sha"]:
                failures.append(
                    f"{fname}: sha256_12 {got_sha} != baseline {expected['sha']} (strict)"
                )
        else:
            rel = abs(got_rows - expected["rows"]) / max(expected["rows"], 1)
            if rel > TOLERANCE:
                failures.append(
                    f"{fname}: rows {got_rows} drift {rel:.2%} > "
                    f"{TOLERANCE:.0%} from baseline {expected['rows']}"
                )

    assert not failures, "Smoke baseline drift:\n  - " + "\n  - ".join(failures)
