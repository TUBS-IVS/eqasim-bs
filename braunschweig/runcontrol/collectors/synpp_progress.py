"""Parse synpp stage progress from a timestamped run log.

Reuses braunschweig.analysis.runtime.parse_stage_runtimes (same regexes that
power the post-run stage_runtime.csv) for completed stages; the active stage
is the last 'Executing stage' without a matching 'Finished running'. Expected
stage order/weights come from a historical *_stage_runtime.csv when
available; otherwise weights fall back to 1.0 each, flagged via
weights_source='equal_fallback' (never silently)."""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

import pandas as pd

from braunschweig.analysis.runtime import parse_stage_runtimes

_EXEC_RE = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}).*Executing stage (\S+)")
_HASH_RE = re.compile(r"__[0-9a-f]+$")


@dataclass
class StageProgress:
    done: list[dict] = field(default_factory=list)          # {stage_short, duration_s, end}
    active: str | None = None                               # stage_short
    active_since_iso: str | None = None
    expected: list[dict] = field(default_factory=list)      # {stage_short, weight, state}
    weights_source: str = "equal_fallback"                  # "runtime_csv" | "equal_fallback"


def expected_from_runtime_csv(csv_text: str) -> list[tuple[str, float]]:
    df = pd.read_csv(io.StringIO(csv_text))
    df = df.sort_values("start")
    return list(zip(df["stage_short"], df["duration_s"].astype(float)))


def parse(log_text: str, expected: list[tuple[str, float]] | None) -> StageProgress:
    df = parse_stage_runtimes(log_text)
    done = [{"stage_short": r.stage_short, "duration_s": float(r.duration_s), "end": r.end.isoformat()}
            for r in df.sort_values("end").itertuples()]
    done_names = {d["stage_short"] for d in done}

    active, active_since = None, None
    for m in _EXEC_RE.finditer(log_text):
        short = _HASH_RE.sub("", m.group(2))
        if short not in done_names:
            active, active_since = short, m.group(1)

    if expected:
        weights_source = "runtime_csv"
        exp = [{"stage_short": n, "weight": float(w)} for n, w in expected]
    else:
        weights_source = "equal_fallback"
        names = [d["stage_short"] for d in done] + ([active] if active else [])
        exp = [{"stage_short": n, "weight": 1.0} for n in names]

    for e in exp:
        e["state"] = ("done" if e["stage_short"] in done_names
                      else "active" if e["stage_short"] == active else "pending")
    return StageProgress(done=done, active=active, active_since_iso=active_since,
                         expected=exp, weights_source=weights_source)
