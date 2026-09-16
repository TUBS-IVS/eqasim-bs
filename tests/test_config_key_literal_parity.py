"""No popsim config key that owns a named constant may also be spelled as a literal.

A synpp config key is declared in `configure()` and read in `execute()`, and the same
key is often used by several stages. When one stage owns a named constant in
`braunschweig/popsim/stage/config_keys.py` while another spells the key as a bare
string literal, the key's name lives in two files -- the shape rule's "one fact, one
file" violated in its least visible form, because both spellings are correct right up
until someone changes one of them.

`escort_passive_education` was exactly that case (#368 review, deferred minor 6):
constant `KEY_ESCORT_PASSIVE_EDUCATION` in `config_keys.py`, two bare-literal sites in
`braunschweig/popsim/distance_distributions.py`. Both sites now import the constant,
and this test keeps it that way.

**What this test does NOT assert.** Plenty of popsim keys have no constant at all
(`escort_purpose`, `secondary_shop_daily_split`, `explicit_round_trip_purposes`, and
the eqasim built-in `random_seed`). Whether every key SHOULD own a constant is a
separate, larger question; a literal without a constant is not a parity defect and is
deliberately not failed here.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_KEYS = REPO_ROOT / "braunschweig" / "popsim" / "stage" / "config_keys.py"

#: Popsim modules that declare config keys outside the `stage` package. Add a module
#: here when it starts declaring keys; the test then covers it on arrival.
KEY_DECLARING_MODULES = (
    REPO_ROOT / "braunschweig" / "popsim" / "distance_distributions.py",
    REPO_ROOT / "braunschweig" / "popsim" / "trips_stage.py",
    REPO_ROOT / "braunschweig" / "popsim" / "completed_donor.py",
    REPO_ROOT / "braunschweig" / "popsim" / "commute_distance.py",
)

#: `context.config("<key>")` with a STRING literal -- the spelling under test.
#: `context.config(KEY_X)` passes a Name node and is deliberately not matched.
_LITERAL_CONFIG_CALL = re.compile(r'context\.config\(\s*"([a-z0-9_]+)"')


def _named_key_constants() -> dict[str, str]:
    """Map every ``KEY_* = "<literal>"`` in config_keys.py to its constant name."""
    tree = ast.parse(CONFIG_KEYS.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or not target.id.startswith("KEY_"):
            continue
        if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            out[node.value.value] = target.id
    return out


def test_key_constants_are_discovered() -> None:
    """Guard against a vacuous suite: the parser must actually find the constants.

    Without this, a rename or a parser regression would empty the mapping and make
    every parity assertion below pass while checking nothing.
    """
    constants = _named_key_constants()
    assert len(constants) >= 40, (
        f"only {len(constants)} KEY_* constants parsed from {CONFIG_KEYS.name}; the "
        f"discovery is broken, so the parity assertions would pass vacuously")
    assert constants.get("escort_passive_education") == "KEY_ESCORT_PASSIVE_EDUCATION", (
        "escort_passive_education is the key this test was written for; if it was "
        "renamed, repoint the test rather than deleting it")


@pytest.mark.parametrize("module_path", KEY_DECLARING_MODULES, ids=lambda p: p.name)
def test_no_literal_spelling_of_a_key_that_owns_a_constant(module_path: Path) -> None:
    constants = _named_key_constants()
    source = module_path.read_text(encoding="utf-8")
    literals = sorted(set(_LITERAL_CONFIG_CALL.findall(source)))

    duplicated = {k: constants[k] for k in literals if k in constants}
    assert not duplicated, (
        f"{module_path.name} spells config key(s) as bare string literals although "
        f"{CONFIG_KEYS.name} owns a constant for each: "
        + ", ".join(f"{k!r} -> {c}" for k, c in sorted(duplicated.items()))
        + ". Import the constant instead, so the key's name lives in one file.")


def test_escort_passive_education_is_read_through_the_constant() -> None:
    """The concrete case from the #368 review, asserted by name.

    The parametrised test would also pass if `distance_distributions` simply stopped
    declaring the key. This one pins that it still declares it AND does so through the
    constant, which is the state the fix established.
    """
    source = (REPO_ROOT / "braunschweig" / "popsim"
              / "distance_distributions.py").read_text(encoding="utf-8")
    assert "KEY_ESCORT_PASSIVE_EDUCATION" in source, (
        "distance_distributions must still declare escort_passive_education, and "
        "through the constant")
    assert 'context.config("escort_passive_education"' not in source, (
        "the bare-literal spelling is back; import the constant instead")
