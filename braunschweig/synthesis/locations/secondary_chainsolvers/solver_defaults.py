"""The chainsolvers solver this stage uses when the config leaves the choice open.

Its own module because BOTH the package ``__init__`` (the declared config default
and the serial call site) and the ``parallel_solving`` worker need the value, and
submodules of this package must not import from the package ``__init__`` (the #267
split constraint). One constant replaces the three separate ``"carla"`` literals
that preceded it, so the declared default, the serial path and the parallel worker
can no longer drift apart -- a worker solving with a different solver than the
serial path would make the two results incomparable while both still look fine.
"""

from __future__ import annotations

#: Solver name passed to ``chainsolvers.setup``. Must be one the installed
#: chainsolvers package registers (``SOLVER_REGISTRY``); a test pins that.
#:
#: ``carla_sample`` is carla's sampling variant (issue #337), replacing the
#: deterministic ``carla``. Two reasons, both recorded in ADR-0096:
#:
#: 1. The CARLA author recommends it (PERSONAL COMMUNICATION; a paper on the
#:    sampling variant is in preparation -- this is explicitly NOT a citable
#:    published result yet).
#: 2. This model's own 2026-08-12 toggle diagnostic (#257) measured that desired
#:    distances barely propagate into realised distances under the deterministic
#:    ``top_n`` selection: chain anchors and candidate geography dominate the
#:    greedy pick, leaving the distance layers nearly inert. A sampling solver is
#:    the candidate fix.
#:
#: ADOPTED BUT UNVALIDATED IN THIS MODEL: no A/B of runtime or output quality has
#: been run against the deterministic solver here, and the distance-coupling
#: measurement has not been repeated with it. Treat any downstream result as
#: carrying that caveat until a run manifest records the comparison.
DEFAULT_CHAIN_SOLVER = "carla_sample"
