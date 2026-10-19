"""The two options of the free-parking draw (issue #436, parking cost zones v2, spec Amendment D5 and D6).

``braunschweig.parking.attach.draw_parking_free`` decides once per person whether the person parks free at its
work/education activities (activity attribute ``parkingFree``). Two configuration keys steer it:

* ``parking_free_share_proxy_classes`` (:data:`KEY_PROXY_CLASSES`): an explicit mapping workplace class -> SrV class.
  The draw uses the free share of the SrV class on the right for the persons of the class on the left; the zones keep
  their county ``workplace_class``. Production maps ``"03103"`` (Wolfsburg) to ``"bs_zentrum"`` (ASSUMPTION A1-b).
  The value ``null`` is the explicit "no proxy" marker: ``{"03103": null}`` means "class 03103 uses its own class
  share". It exists because a configuration overlay is deep-merged into the base (``braunschweig.config_compose``): an
  overlay ``{}`` leaves the base mapping unchanged and silently runs the proxy, whereas ``{"03103": null}`` replaces
  the entry and survives the merge. The empty mapping restores the class shares only where no base entry exists.
* ``parking_campus_free_share`` (:data:`KEY_CAMPUS_FREE_SHARE`): the share of the persons with a work or education
  activity on a campus who find a free parking place (ASSUMPTION C2, an owner estimate); a unitless number in [0, 1].

This module only validates. It has no dependencies on the parking data, so the plans-writer wrapper can check the keys
at configure time (before any stage runs) and the draw can check them again on whatever it is handed. The defaults
here switch both options OFF (the v1 draw); the production values are stated in ``configs/base_bs.yml``, so a run
configuration names them explicitly.
"""
from __future__ import annotations

import math

import numpy as np

_LOG_TAG = "[parking]"

KEY_PROXY_CLASSES = "parking_free_share_proxy_classes"
KEY_CAMPUS_FREE_SHARE = "parking_campus_free_share"
#: No campus person parks free: the v1 pricing of campus work/education (ASSUMPTION C1: members pay the day product).
DEFAULT_CAMPUS_FREE_SHARE = 0.0


def default_proxy_classes() -> dict:
    """The empty mapping (every class uses its own SrV share), as a fresh object on every call."""
    return {}


def _is_class_name(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def require_proxy_classes(value) -> dict:
    """Return a plain copy of the mapping ``value`` or raise ``ValueError`` naming the offending entry.

    Structural checks that need no data: a mapping; keys class names given as non-empty text (county keys carry a
    leading zero, so YAML needs them quoted: an unquoted 03103 is an integer); values class names as text or ``None``
    (the explicit no-proxy marker of the module docstring, kept as given); no self-mapping; no chain, i.e. no value
    that is itself a key with a proxy of its own (a proxy is resolved exactly once, so a chain would hide which share
    is used). Whether every key and value is a class row of the SrV table is checked by
    :func:`require_proxy_classes_in_table`.
    """
    if not isinstance(value, dict):
        raise ValueError(f"{_LOG_TAG} {KEY_PROXY_CLASSES} must be a mapping workplace class -> SrV class (for "
                         f"example {{'03103': 'bs_zentrum'}}), {{}} for none; got {value!r}")
    bad = {key: item for key, item in value.items()
           if not (_is_class_name(key) and (item is None or _is_class_name(item)))}
    if bad:
        raise ValueError(f"{_LOG_TAG} {KEY_PROXY_CLASSES}: keys and values must be class names given as text (a value "
                         f"may also be null, the no-proxy marker), found {bad!r}; quote county keys in YAML, an "
                         "unquoted 03103 is an integer")
    for key, item in value.items():
        if key == item:
            raise ValueError(f"{_LOG_TAG} {KEY_PROXY_CLASSES}: self-mapping {key!r} -> {item!r} maps a class to "
                             "itself; remove the entry (a class without an entry uses its own share)")
    chained = {key: item for key, item in value.items() if item is not None and value.get(item) is not None}
    if chained:
        raise ValueError(f"{_LOG_TAG} {KEY_PROXY_CLASSES}: chain {chained!r}: a proxy class must not itself be mapped; "
                         "name the final SrV class directly")
    return dict(value)


def require_proxy_classes_in_table(proxy_classes: dict, class_names) -> None:
    """Raise ``ValueError`` unless every key and every non-null value of ``proxy_classes`` is an SrV class name.

    ``class_names`` are the ``level == "class"`` rows of the shares table (``attach.free_share_by_class(...).index``).
    A key without a class row could never be drawn, a value without one has no share; both are configuration errors.
    A null value (the no-proxy marker) has no value to check, but its key is checked like any other.
    """
    known = set(class_names)
    unknown = sorted({name for pair in proxy_classes.items() for name in pair
                      if name is not None and name not in known})
    if unknown:
        raise ValueError(f"{_LOG_TAG} {KEY_PROXY_CLASSES}: {unknown} have no class row in the SrV shares table "
                         f"(class rows: {sorted(known)}); keys and values must be SrV class rows")


def require_campus_free_share(value) -> float:
    """Return ``parking_campus_free_share`` as a float, raising ``ValueError`` unless it is a number in [0, 1].

    Booleans and text are rejected (``True`` would silently mean 1.0), as are NaN, infinities and percentages such as
    20: the value is a share of persons, not a percentage.
    """
    valid = (not isinstance(value, (bool, np.bool_)) and isinstance(value, (int, float, np.integer, np.floating))
             and math.isfinite(value) and 0.0 <= value <= 1.0)
    if not valid:
        raise ValueError(f"{_LOG_TAG} {KEY_CAMPUS_FREE_SHARE} must be a number in [0, 1] (the share of the persons with "
                         f"a work or education activity on a campus who park free, ASSUMPTION C2; 0.0 keeps the v1 "
                         f"pricing); got {value!r}")
    return float(value)
