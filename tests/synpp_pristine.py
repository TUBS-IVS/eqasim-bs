"""synpp 1.6.2's pristine ``run()`` for tests that compare the fork's ``run()`` with upstream.

``braunschweig.synpp_fork.install()`` replaces ``synpp.pipeline.run`` and ``synpp.run`` for the rest
of the interpreter, so a test that needs "what synpp itself devalidates" (legacy records, upstream
devalidation of every descendant) must not call ``synpp.run``: depending on which test ran first it
would be either function. ``upstream_run()`` rebuilds upstream's function from the installed file's
text instead; this is the single shared definition (``tests/test_migrate_cache_metadata.py`` and
``tests/test_synpp_fork_staleness.py`` import it).

The rebuilt function resolves ``process_stages`` from a snapshot of the module namespace and
``get_stage_hash`` through ``synpp.pipeline`` at call time, so it follows whichever of those two the
interpreter has installed (the fork's deterministic ``process_stages`` and semantic module hash, or
upstream's); only the loop of ``run()`` itself is guaranteed to be upstream's.
"""
from __future__ import annotations

import pathlib


def upstream_run():
    """Return synpp 1.6.2's ``run()`` exactly as shipped in the installed ``synpp/pipeline.py``."""
    import synpp.pipeline as upstream
    source = pathlib.Path(upstream.__file__).read_text(encoding="utf-8")
    start = source.index("\ndef run(")
    end = source.index("\ndef run_from_yaml(")
    namespace = dict(vars(upstream))
    exec(source[start:end], namespace)
    return namespace["run"]
