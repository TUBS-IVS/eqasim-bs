"""Canonical type aliases used across the Braunschweig pipeline.

Centralising these aliases gives every stage a single source of truth for the
shape of identifier and weight columns in the synpp DataFrames.  Stages MUST
import from here rather than redefining their own aliases — diverging
definitions are a recurring source of subtle joins-on-mismatched-types bugs
(see :doc:`/docs/codebase/CONCERNS.md` BUG-003).

The aliases are intentionally narrow:

* ``CommuneId`` is a zero-padded 8-character ARS string (e.g. ``"03101001"``);
  never an integer.  The leading zero is significant for Niedersachsen.
* ``KreisId`` is the 5-character prefix of ``CommuneId`` (e.g. ``"03101"``).
* ``ARS12`` is the full 12-character ARS code with municipal subdivision.
* ``HouseholdId`` and ``PersonId`` are integers assigned by the synthesis
  stages; their values are stable across runs only when the seed and stage
  ordering are unchanged.
* ``Weight`` is a non-negative float (post-IPF weight).

This module is part of Phase 1 of the refactor (skeleton); type-checking is
not enforced yet because no ``mypy`` configuration ships with the repo.  The
aliases still serve as documentation and as a target for future static
analysis.
"""

from __future__ import annotations

from typing import TypeAlias

CommuneId: TypeAlias = str
"""Eight-character ARS code (e.g. ``"03101001"``); leading zeros preserved."""

KreisId: TypeAlias = str
"""Five-character ARS prefix (e.g. ``"03101"`` for Stadt Braunschweig)."""

ARS12: TypeAlias = str
"""Twelve-character ARS code with municipal subdivision."""

HouseholdId: TypeAlias = int
"""Synthetic household identifier; stable for a given (seed, stage order)."""

PersonId: TypeAlias = int
"""Synthetic person identifier; stable for a given (seed, stage order)."""

Weight: TypeAlias = float
"""Non-negative IPF weight."""

__all__ = [
    "ARS12",
    "CommuneId",
    "HouseholdId",
    "KreisId",
    "PersonId",
    "Weight",
]
