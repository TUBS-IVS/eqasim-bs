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
    Not yet implemented (Phase 2).  Calling ``get_source("entd")`` raises
    :class:`NotImplementedError` with a clear message.
"""

from __future__ import annotations

from braunschweig.popsim.sources.base import PopsimSource
from braunschweig.popsim.sources.mid import MidSource

# Source name -> callable that returns a PopsimSource instance.
# Each entry is a zero-argument factory so callers always get a fresh instance.
_REGISTRY: dict[str, type] = {
    "mid": MidSource,
}

# Sources that are planned but not yet implemented.
_PLANNED: set[str] = {"entd"}


def get_source(name: str) -> PopsimSource:
    """Return a new PopsimSource instance for the given source name.

    Parameters
    ----------
    name:
        Short lowercase source identifier, e.g. ``"mid"``.

    Returns
    -------
    PopsimSource
        A fresh instance of the registered adapter.

    Raises
    ------
    NotImplementedError
        If ``name`` is a planned-but-not-yet-implemented source (``"entd"``).
    ValueError
        If ``name`` is not recognised at all.
    """
    if name in _REGISTRY:
        return _REGISTRY[name]()
    if name in _PLANNED:
        raise NotImplementedError(
            f"Donor source '{name}' is planned but not yet implemented. "
            "Implement braunschweig.popsim.sources.entd.EntdSource and register "
            "it in braunschweig.popsim.sources._REGISTRY to enable it (Phase 2)."
        )
    raise ValueError(
        f"Unknown donor source '{name}'. "
        f"Registered sources: {sorted(_REGISTRY)}. "
        f"Planned (not yet implemented): {sorted(_PLANNED)}."
    )


__all__ = ["PopsimSource", "MidSource", "get_source"]
