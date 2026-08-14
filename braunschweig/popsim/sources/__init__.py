"""Donor-source registry for the popsim workflows.

The registry maps a short source name (e.g. ``"mid"``) to a
:class:`braunschweig.popsim.sources.base.PopsimSource` instance.

Usage
-----
::

    from braunschweig.popsim import sources

    src = sources.get_source("mid")
    hh, persons, trips = src.load_donor(mid_dir)

Registered sources
------------------
``"mid"``
    :class:`braunschweig.popsim.sources.mid.MidSource` — wraps the existing
    MiD 2023 I/O and attribute-mapping functions.

``"entd"``
    :class:`braunschweig.popsim.sources.entd.EntdSource` — open ENTD donor
    adapter (popsim_open Phase 2).  Uses canonical eqasim column names directly;
    no pseudonymisation (open data).

Lazy adapter resolution (issue #292)
-------------------------------------
``_REGISTRY`` stores each entry as a ``"module:ClassName"`` dotted string, NOT
an imported class, and :func:`get_source` resolves and imports the module only
when that specific source is actually requested. Importing this package (or
calling ``get_source`` for one source) therefore no longer imports every OTHER
registered adapter module and its transitive dependencies -- before this
change, importing this ``__init__`` unconditionally imported BOTH ``mid.py``
and ``entd.py`` (plus, after the #287 split, ``entd.py``'s seven sibling
modules), even on a MiD-only run that never selects "entd".

``MidSource`` and ``EntdSource`` stay reachable as attributes of this package
(``sources.MidSource``, ``from braunschweig.popsim.sources import EntdSource``)
via the module-level ``__getattr__`` below (PEP 562): the attribute lookup
itself triggers the one-time import of the owning module, so the public names
are unchanged but nothing is imported until something actually asks for it.
``PopsimSource`` (the shared Protocol interface every adapter implements) is
the one exception and stays eagerly imported above, since it carries no
adapter-specific dependencies and callers need it for type annotations without
selecting any concrete source.

IMPORTANT SCOPE NOTE -- this laziness does NOT reach every consumer. The
popsim_mid synpp stage (``braunschweig.popsim.stage``) imports the ``mid``,
``entd`` and all seven ``entd_*`` sibling modules at ITS OWN module level
(one level deep, alongside this package's own ``__init__``) so their source
text participates in its synpp cache-validation token (``validate()``,
``_HELPER_MODULES``) -- ``inspect.getsource`` needs the module object, not a
dotted name, to hash it. Importing ``braunschweig.popsim.stage`` therefore
still imports the ENTD adapter regardless of what this registry does; the
only consumers that actually benefit from the laziness here are call sites
that import ``braunschweig.popsim.sources`` WITHOUT importing
``braunschweig.popsim.stage`` first (e.g. ``braunschweig.popsim.trips_stage``,
which imports ``sources`` inside its ``execute()`` function body). See
``docs/codebase/notes/popsim-sources-lazy-registry.md`` for the full
before/after picture and why the stage's own eager import was deliberately
left unchanged here.
"""

from __future__ import annotations

import importlib

from braunschweig.popsim.sources.base import PopsimSource

# Source name -> "module:ClassName" dotted string, resolved lazily by
# get_source() on first use (see the module docstring's "Lazy adapter
# resolution" section). Each entry is resolved and instantiated fresh on every
# call, so callers always get a new instance, exactly as when _REGISTRY held
# imported classes directly.
_REGISTRY: dict[str, str] = {
    "mid": "braunschweig.popsim.sources.mid:MidSource",
    "entd": "braunschweig.popsim.sources.entd:EntdSource",
}

# Sources that are planned but not yet implemented.
_PLANNED: set[str] = set()

# Names resolved lazily via module-level __getattr__ (PEP 562) below, and the
# dotted "module:ClassName" path each resolves to. Kept as a single lookup
# table so the registry's dotted paths above and the public attribute names
# below cannot silently drift apart.
_LAZY_ATTRIBUTES: dict[str, str] = {
    "MidSource": _REGISTRY["mid"],
    "EntdSource": _REGISTRY["entd"],
}


def _resolve_adapter_class(dotted_path: str) -> type:
    """Import the module named in ``dotted_path`` and return its class.

    Parameters
    ----------
    dotted_path:
        ``"module.path:ClassName"``, e.g.
        ``"braunschweig.popsim.sources.mid:MidSource"``.

    Returns
    -------
    type
        The class object, imported on demand.
    """
    module_name, _, class_name = dotted_path.partition(":")
    module = importlib.import_module(module_name)
    return getattr(module, class_name)


def get_source(name: str) -> PopsimSource:
    """Return a new PopsimSource instance for the given source name.

    The adapter's module is imported here, on first use, not at package
    import time (issue #292): requesting ``"mid"`` never imports the ``entd``
    module or its dependencies, and vice versa.

    Parameters
    ----------
    name:
        Short lowercase source identifier, e.g. ``"mid"`` or ``"entd"``.

    Returns
    -------
    PopsimSource
        A fresh instance of the registered adapter.

    Raises
    ------
    NotImplementedError
        If ``name`` is a planned-but-not-yet-implemented source.
    ValueError
        If ``name`` is not recognised at all.
    """
    if name in _REGISTRY:
        return _resolve_adapter_class(_REGISTRY[name])()
    if name in _PLANNED:
        raise NotImplementedError(
            f"Donor source '{name}' is planned but not yet implemented. "
            f"Register it in braunschweig.popsim.sources._REGISTRY to enable it."
        )
    raise ValueError(
        f"Unknown donor source '{name}'. "
        f"Registered sources: {sorted(_REGISTRY)}. "
        f"Planned (not yet implemented): {sorted(_PLANNED)}."
    )


def __getattr__(name: str):
    """PEP 562 module ``__getattr__``: resolve ``MidSource`` / ``EntdSource`` lazily.

    Keeps both names reachable as attributes of this package
    (``sources.MidSource``, ``from braunschweig.popsim.sources import
    EntdSource``) without importing either adapter module at package-import
    time; the owning module is imported only when the attribute is actually
    looked up. Any other attribute name raises ``AttributeError`` exactly as
    normal module attribute access would.
    """
    if name in _LAZY_ATTRIBUTES:
        return _resolve_adapter_class(_LAZY_ATTRIBUTES[name])
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["PopsimSource", "MidSource", "EntdSource", "get_source"]
