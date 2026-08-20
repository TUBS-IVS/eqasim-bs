"""Pin EntdSource's public surface after the facade docstring dedup (issue #295).

Context: ``braunschweig/popsim/sources/entd.py`` is a delegating class facade
(issue #267 split, PR #287). Until #295, every ``EntdSource`` method carried a
full docstring that was a byte-identical duplicate of the docstring on the
sibling module function it delegates to -- kept deliberately by #287 (a
byte-identical relocation) but a documentation-drift risk going forward (the
#267 programme found stale docstring claims repeatedly). #295 removed the
duplicate copy from the facade and instead forwards each method's ``__doc__``
from its delegate at import time (see the assignment block at the bottom of
``entd.py``), so ``help()`` / ``inspect.getdoc`` on the public adapter still
returns the full text without a second hand-maintained copy.

This test pins the property that change created and must never regress:

- every public ``EntdSource`` method still resolves to a NON-EMPTY docstring
  via :func:`inspect.getdoc` (an adapter whose ``help()`` output goes empty is
  a regression, not a cleanup -- this is the exact failure mode #295 warns
  against);
- the resolved docstring is byte-identical to the delegate function's own
  docstring (proving the forwarding wiring is intact, not just "some text");
- the eight method names and their ``inspect.signature`` stay exactly what
  PR #287 pinned (the facade's public surface must not regress).
"""

from __future__ import annotations

import inspect

import pandas as pd

from braunschweig.popsim.sources.entd import EntdSource
from braunschweig.popsim.sources.entd_attributes import (
    map_person_attributes as _map_person_attributes,
)
from braunschweig.popsim.sources.entd_donor import (
    cell_stratum as _cell_stratum,
    donor_stratum as _donor_stratum,
    load_donor as _load_donor,
)
from braunschweig.popsim.sources.entd_seed import (
    build_seed as _build_seed,
    built_seed_columns as _built_seed_columns,
    seed_columns as _seed_columns,
)
from braunschweig.popsim.sources.entd_trips import build_trips as _build_trips

# Method name -> the module-level delegate function it forwards to. This is
# the same mapping entd.py itself wires as the one-line delegation targets
# and the __doc__ forwarding assignments (issue #295).
_DELEGATE_BY_METHOD = {
    "seed_columns": _seed_columns,
    "built_seed_columns": _built_seed_columns,
    "build_seed": _build_seed,
    "load_donor": _load_donor,
    "map_person_attributes": _map_person_attributes,
    "donor_stratum": _donor_stratum,
    "cell_stratum": _cell_stratum,
    "build_trips": _build_trips,
}

# inspect.signature() strings pinned from the state of EntdSource on `main`
# before #295 (PR #287's original guarantee); #295 must not change these,
# since it touches documentation only.
_EXPECTED_SIGNATURES = {
    "seed_columns": "(self) -> 'SeedColumns'",
    "built_seed_columns": "(self) -> 'SeedColumns'",
    "build_seed": "(self, households: 'pd.DataFrame', persons: 'pd.DataFrame') -> 'tuple'",
    "load_donor": (
        "(self, data_dir: 'Union[str, Path]', *, "
        "injected: 'Optional[Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]]' = None) "
        "-> 'Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]'"
    ),
    "map_person_attributes": (
        "(self, persons: 'pd.DataFrame', households: 'pd.DataFrame', *, rng=None) "
        "-> 'Tuple[pd.DataFrame, pd.DataFrame]'"
    ),
    "donor_stratum": "(self, seed_households: 'pd.DataFrame') -> 'pd.Series'",
    "cell_stratum": "(self, cells: 'pd.DataFrame') -> 'pd.Series'",
    # explicit_round_trip_purposes was added 2026-08-20: trips_stage.execute passes
    # it to every source adapter (issue #241 threaded it only into the
    # implementation, so a 100 % run raised TypeError here). The pin moves WITH the
    # deliberate protocol change -- see tests/test_trips_adapter_signature_parity.py,
    # which requires the keyword on all four layers.
    "build_trips": (
        "(self, persons: 'pd.DataFrame', donor_trips: 'pd.DataFrame', *, "
        "random_seed: 'int', escort_purpose: 'bool' = False, "
        "escort_passive_education: 'bool' = False, "
        "explicit_round_trip_purposes: 'bool' = True) -> 'pd.DataFrame'"
    ),
}


def test_entd_source_public_method_names_unchanged():
    """EntdSource's public surface (method names) must stay exactly the #287 set."""
    public_names = {name for name in vars(EntdSource) if not name.startswith("_")}
    # "name" is the class attribute ("entd"), not a method; keep it in the
    # expected set so this assertion also pins that no OTHER public name
    # appeared or disappeared.
    expected = set(_DELEGATE_BY_METHOD) | {"name"}
    assert public_names == expected


def test_entd_source_method_signatures_pinned():
    """Every EntdSource method's inspect.signature stays exactly what #287 pinned."""
    for method_name, expected_signature in _EXPECTED_SIGNATURES.items():
        method = getattr(EntdSource, method_name)
        actual_signature = str(inspect.signature(method))
        assert actual_signature == expected_signature, (
            f"EntdSource.{method_name} signature changed: "
            f"expected {expected_signature!r}, got {actual_signature!r}"
        )


def test_entd_source_methods_have_non_empty_resolved_docstrings():
    """inspect.getdoc() must return non-empty text for every public EntdSource method.

    This is the regression #295 explicitly guards against: inspect.getdoc()
    follows class INHERITANCE but does not follow a delegation call inside a
    method body, so stripping the facade's own docstring without the __doc__
    forwarding assignment in entd.py would silently empty out help() on the
    public adapter.
    """
    for method_name in _DELEGATE_BY_METHOD:
        method = getattr(EntdSource, method_name)
        resolved_doc = inspect.getdoc(method)
        assert resolved_doc, (
            f"EntdSource.{method_name} resolves to an empty docstring; "
            "help() on the public adapter must never go empty (issue #295)."
        )


def test_entd_source_docstrings_are_forwarded_from_the_delegate_not_duplicated():
    """Each method's resolved docstring must be the DELEGATE's docstring, forwarded.

    This pins the single-source-of-truth property #295 introduced: the
    documentation text lives once, on the sibling module function; the
    facade's __doc__ is that same object (or an identical copy), not a
    second hand-maintained transcription that could drift from it.
    """
    for method_name, delegate in _DELEGATE_BY_METHOD.items():
        method = getattr(EntdSource, method_name)
        assert method.__doc__ == delegate.__doc__, (
            f"EntdSource.{method_name}.__doc__ is not forwarded from its "
            f"delegate ({delegate.__module__}.{delegate.__name__}); the "
            "facade and the sibling have drifted apart."
        )
        # inspect.getdoc also applies dedent/cleanup; confirm it agrees with
        # the delegate's own resolved docstring too (belt and suspenders).
        assert inspect.getdoc(method) == inspect.getdoc(delegate)
