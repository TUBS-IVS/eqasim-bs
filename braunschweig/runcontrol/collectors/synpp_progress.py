"""Parse synpp stage progress from a live (or historical) pipeline log.

runcontrol tails ``logs/rc_<id>.log`` while the pipeline runs. Depending on how
the log was produced, ``Executing stage <name>`` / ``Finished running <name>``
lines come in one of three real-world shapes:

* **iso** -- the ISO-timestamped ``%(asctime)s %(levelname)s %(name)s %(message)s``
  file log written by ``braunschweig.logging_setup.setup_logging`` (the FILE
  handler). This is also the format ``braunschweig.analysis.runtime`` parses
  for the post-run ``stage_runtime.csv``. Example::

      2026-07-08T10:00:00 INFO Executing stage data.osm.cleaned__abc

* **console** -- the live coloured-console format from
  ``braunschweig.logging_setup.ColorFormatter`` (``HH:MM:SS`` only, no date,
  fields separated by U+2502 box-drawing bars). This is what
  ``run_pipeline.sh`` tees into ``logs/rc_<id>.log`` on a real run, so it is
  the format runcontrol most commonly has to read live. Example::

      13:47:16 │ INFO    │ synpp            │ Executing stage matsim.runtime.java__33163fea50c0df3e4
      13:47:16 │ INFO    │ synpp            │ Finished running matsim.runtime.java__33163fea50c0df3e

* **bare** -- the legacy plain Python default logging format
  (``%(levelname)s:%(name)s:%(message)s``), with no timestamp at all, seen in
  older run logs. Example::

      INFO:synpp:Executing stage braunschweig.synthesis.something__34b450886790162340ff1eeb03f35ffd

Only the **iso** format carries a full date, so only iso-format stages get a
directly comparable ``end`` timestamp usable across days. **console** yields a
same-day duration (with midnight wrap-around handled) but ``active_since_iso``
then holds a bare ``HH:MM:SS`` string, not a real ISO timestamp. **bare** has
no timestamp at all, so ``duration_s`` and ``active_since_iso`` are ``None``
for it -- this is a real information loss of that log format, not a bug, and
is surfaced explicitly via ``StageProgress.log_format`` rather than silently
guessed at (see CLAUDE.md "no silent fallbacks").

Expected stage order/weights come from a historical ``*_stage_runtime.csv``
when available; otherwise weights fall back to 1.0 each, flagged via
``weights_source='equal_fallback'`` (again, never silently).
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

_HASH_RE = re.compile(r"__[0-9a-f]+$")

_ISO_TS = r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})"
_ISO_EXEC_RE = re.compile(_ISO_TS + r".*Executing stage (\S+)")
_ISO_FINISH_RE = re.compile(_ISO_TS + r".*Finished running (\S+?)\.?\s*$")

# braunschweig.logging_setup.ColorFormatter: "HH:MM:SS | LEVEL | stage | message"
# (with U+2502 box-drawing bars as separators; color codes auto-disable on the
# non-tty stream that run_pipeline.sh tees into a file, so no ANSI here).
_CONSOLE_TS = r"(\d{2}:\d{2}:\d{2})"
_CONSOLE_SEP = "│"
_CONSOLE_EXEC_RE = re.compile(
    r"^" + _CONSOLE_TS + r"\s*" + _CONSOLE_SEP + r".*" + _CONSOLE_SEP + r".*" + _CONSOLE_SEP
    + r"\s*Executing stage (\S+)")
_CONSOLE_FINISH_RE = re.compile(
    r"^" + _CONSOLE_TS + r"\s*" + _CONSOLE_SEP + r".*" + _CONSOLE_SEP + r".*" + _CONSOLE_SEP
    + r"\s*Finished running (\S+?)\.?\s*$")

# Legacy "%(levelname)s:%(name)s:%(message)s" default logging format: no timestamp.
_BARE_EXEC_RE = re.compile(r"Executing stage (\S+)")
_BARE_FINISH_RE = re.compile(r"Finished running (\S+?)\.?\s*$")


@dataclass
class StageProgress:
    done: list[dict] = field(default_factory=list)          # {stage_short, duration_s, end}
    active: str | None = None                               # stage_short
    active_since_iso: str | None = None
    expected: list[dict] = field(default_factory=list)      # {stage_short, weight, state}
    weights_source: str = "equal_fallback"                  # "runtime_csv" | "equal_fallback"
    log_format: str = "unknown"                              # "iso" | "console" | "bare" | "unknown"


def expected_from_runtime_csv(csv_text: str) -> list[tuple[str, float]]:
    df = pd.read_csv(io.StringIO(csv_text))
    df = df.sort_values("start")
    return list(zip(df["stage_short"], df["duration_s"].astype(float)))


def _match_line(line: str) -> tuple[str | None, str | None, str | None, str | None]:
    """Try iso, then console, then bare patterns; return (format, kind, timestamp, stage)."""
    m = _ISO_EXEC_RE.search(line)
    if m:
        return "iso", "exec", m.group(1), m.group(2)
    m = _ISO_FINISH_RE.search(line)
    if m:
        return "iso", "finish", m.group(1), m.group(2)
    m = _CONSOLE_EXEC_RE.search(line)
    if m:
        return "console", "exec", m.group(1), m.group(2)
    m = _CONSOLE_FINISH_RE.search(line)
    if m:
        return "console", "finish", m.group(1), m.group(2)
    m = _BARE_EXEC_RE.search(line)
    if m:
        return "bare", "exec", None, m.group(1)
    m = _BARE_FINISH_RE.search(line)
    if m:
        return "bare", "finish", None, m.group(1)
    return None, None, None, None


def _seconds_of_day(hhmmss: str) -> int:
    hours, minutes, seconds = (int(part) for part in hhmmss.split(":"))
    return hours * 3600 + minutes * 60 + seconds


def _compute_duration(start_format: str, start_ts: str | None,
                       end_ts: str | None) -> tuple[float | None, str | None]:
    """Return (duration_s, end_repr) honestly per format: bare logs carry no
    timestamp at all, so duration is genuinely unknown -- None, not a guess."""
    if start_format == "iso":
        start = datetime.strptime(start_ts, "%Y-%m-%dT%H:%M:%S")
        end = datetime.strptime(end_ts, "%Y-%m-%dT%H:%M:%S")
        return (end - start).total_seconds(), end.isoformat()
    if start_format == "console":
        start_seconds = _seconds_of_day(start_ts)
        end_seconds = _seconds_of_day(end_ts)
        if end_seconds < start_seconds:
            end_seconds += 24 * 3600  # stage ran across midnight
        return float(end_seconds - start_seconds), end_ts
    return None, None  # "bare": no timestamp was ever captured


def parse(log_text: str, expected: list[tuple[str, float]] | None) -> StageProgress:
    starts: dict[str, tuple[str, str | None]] = {}  # stage_short -> (format, start_ts)
    done: list[dict] = []
    active: str | None = None
    active_since: str | None = None
    log_format: str | None = None

    for line in log_text.splitlines():
        line_format, kind, ts, raw_name = _match_line(line)
        if line_format is None:
            continue
        if log_format is None:
            log_format = line_format
        short = _HASH_RE.sub("", raw_name)
        if kind == "exec":
            starts[short] = (line_format, ts)
            active, active_since = short, ts
        else:  # kind == "finish"
            start_entry = starts.pop(short, None)
            if start_entry is None:
                continue  # "Finished running" without a matching start in this log window
            start_format, start_ts = start_entry
            duration_s, end_repr = _compute_duration(start_format, start_ts, ts)
            done.append({"stage_short": short, "duration_s": duration_s, "end": end_repr})
            if active == short:
                active, active_since = None, None

    done_names = {d["stage_short"] for d in done}

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
                         expected=exp, weights_source=weights_source,
                         log_format=log_format or "unknown")
