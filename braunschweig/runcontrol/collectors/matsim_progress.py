"""MATSim iteration progress + honestly-labelled ETA from the run log.

Iteration markers: MATSim's '### ITERATION <n> BEGINS' lines (case-insensitive)
with the run log's ISO timestamps. ETA = mean observed iteration duration *
remaining iterations; ALWAYS carries estimated=True -- it is a projection,
not a measurement. No iterations observed -> everything None (never guessed)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

_ITER_RE = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}).*### ITERATION (\d+) BEGINS", re.IGNORECASE)


@dataclass
class MatsimProgress:
    iteration: int | None
    last_iteration: int | None
    eta_seconds: float | None
    estimated: bool
    iteration_seconds_avg: float | None


def parse(log_text: str, last_iteration: int | None) -> MatsimProgress:
    marks = [(datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S"), int(m.group(2)))
             for m in _ITER_RE.finditer(log_text)]
    if not marks:
        return MatsimProgress(None, last_iteration, None, True, None)
    iteration = marks[-1][1]
    avg = None
    if len(marks) >= 2:
        spans = [(b[0] - a[0]).total_seconds() for a, b in zip(marks, marks[1:])]
        avg = sum(spans) / len(spans)
    eta = None
    if avg is not None and last_iteration is not None:
        eta = avg * max(0, last_iteration - iteration)
    return MatsimProgress(iteration, last_iteration, eta, True, avg)
