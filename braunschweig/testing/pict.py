"""Pairwise (PICT-style) combinatorial test-case design for the Braunschweig
pipeline configuration space.

This is a small, dependency-free re-implementation of the part of a "PICT test
designer" we actually need: given a set of configuration FACTORS (each with a
small set of values) and a set of CONSTRAINTS (interdependencies between factor
values), produce a MINIMAL set of full configurations that covers every feasible
PAIR of factor values at least once. Pairwise (2-way) coverage catches the large
majority of interaction defects at a tiny fraction of the cost of the full
Cartesian product -- exactly what we want for a research pipeline whose
end-to-end run is far too slow to exercise every flag combination.

The algorithm is exact and deterministic (no randomness, so it is reproducible
and safe in the synpp-cached environment):

  1. Enumerate the full Cartesian product of all factor values.
  2. Keep only the constraint-VALID assignments (the candidate pool).
  3. Collect every value-pair that occurs in at least one valid assignment
     (the set of FEASIBLE pairs -- infeasible pairs are silently dropped).
  4. Greedily pick valid assignments, each time taking the one that covers the
     most still-uncovered feasible pairs, until all feasible pairs are covered.

The pipeline factor/constraint model (``PIPELINE_FACTORS`` /
``PIPELINE_CONSTRAINTS``) is derived from a project-wide scan of
``context.config(...)`` reads and the documented flag interdependencies. It is
intentionally kept in one place so it doubles as living documentation of the
flag-interaction rules.

Run as a script to write the covering array to disk:

    python -m braunschweig.testing.pict --out-dir docs/testing
"""
from __future__ import annotations

from itertools import combinations, product
from typing import Any, Callable, Dict, List, Sequence, Tuple

# A constraint is a predicate over a full assignment dict; True == feasible.
Assignment = Dict[str, Any]
Constraint = Callable[[Assignment], bool]
Pair = Tuple[str, Any, str, Any]


def _pairs_of(assignment: Assignment, names: Sequence[str]) -> set[Pair]:
    """All ordered-by-name value-pairs present in one assignment."""
    pairs: set[Pair] = set()
    for i, j in combinations(range(len(names)), 2):
        a, b = names[i], names[j]
        pairs.add((a, assignment[a], b, assignment[b]))
    return pairs


def valid_assignments(
    factors: Dict[str, Sequence[Any]],
    constraints: Sequence[Constraint] = (),
) -> List[Assignment]:
    """Every constraint-feasible full assignment, in deterministic order."""
    names = list(factors)
    pools = [list(factors[n]) for n in names]
    out: List[Assignment] = []
    for combo in product(*pools):
        assignment = dict(zip(names, combo))
        if all(predicate(assignment) for predicate in constraints):
            out.append(assignment)
    return out


def feasible_pairs(
    factors: Dict[str, Sequence[Any]],
    constraints: Sequence[Constraint] = (),
) -> set[Pair]:
    """Every value-pair that occurs in at least one feasible assignment."""
    names = list(factors)
    pool = valid_assignments(factors, constraints)
    needed: set[Pair] = set()
    for assignment in pool:
        needed |= _pairs_of(assignment, names)
    return needed


def pairwise(
    factors: Dict[str, Sequence[Any]],
    constraints: Sequence[Constraint] = (),
) -> List[Assignment]:
    """Minimal-ish set of feasible assignments covering all feasible value pairs.

    Greedy set cover over the feasible-assignment pool. Deterministic: ties are
    broken by the enumeration order of the Cartesian product, so the same model
    always yields the same covering array.
    """
    names = list(factors)
    pool = valid_assignments(factors, constraints)
    if not pool:
        return []

    pool_pairs = [(a, _pairs_of(a, names)) for a in pool]
    remaining: set[Pair] = set()
    for _assignment, pairs in pool_pairs:
        remaining |= pairs

    selected: List[Assignment] = []
    while remaining:
        # Pick the candidate covering the most still-uncovered feasible pairs.
        best_assignment, best_pairs = max(
            pool_pairs, key=lambda ap: len(ap[1] & remaining)
        )
        gain = best_pairs & remaining
        if not gain:
            break  # nothing left can be covered (should not happen)
        selected.append(best_assignment)
        remaining -= best_pairs
    return selected


# ---------------------------------------------------------------------------
# The Braunschweig pipeline configuration model.
#
# Factors are the config keys most worth combinatorially testing (boolean
# feature flags + small enums / bounded numerics). Keys use the short dotted
# config names; see CLAUDE.md and the stages' configure() for provenance.
# ---------------------------------------------------------------------------

PIPELINE_FACTORS: Dict[str, Sequence[Any]] = {
    "use_household_size_margin": (False, True),
    "use_joint_age_size_margin": (False, True),
    "age_aware_chunking": (False, True),
    "use_employment_margin": (False, True),
    "sex_aware_couples": (False, True),
    "chainsolvers_parallel": (False, True),
    "chainsolvers_fallback": ("rda", "random"),
    "cordon_enabled": (False, True),
    "cordon_network_buffer_fraction": (0.10, 0.20),
    "education_gravity_enabled": (False, True),
    "sampling_rate": (0.01, 0.25, 1.0),
    "dirichlet_prior_strength": (0.0, 0.5, 1.0),
}


def _requires(assignment: Assignment, flag: str, prerequisite: str) -> bool:
    """``flag`` may only be True if ``prerequisite`` is True."""
    return (not assignment[flag]) or assignment[prerequisite]


# Each constraint returns True when the assignment is FEASIBLE. The first five
# mirror hard guards / architectural requirements in the production code
# (braunschweig/ipf/attributed.py + model.py); the last two are canonicalisation
# rules that drop no-op variants (a parameter that has no effect when its gating
# flag is off is pinned to its default so the model does not generate
# meaningless rows).
def _c_joint_requires_size(a: Assignment) -> bool:
    return _requires(a, "use_joint_age_size_margin", "use_household_size_margin")


def _c_ageaware_requires_size(a: Assignment) -> bool:
    return _requires(a, "age_aware_chunking", "use_household_size_margin")


def _c_employment_requires_size(a: Assignment) -> bool:
    return _requires(a, "use_employment_margin", "use_household_size_margin")


def _c_sexaware_requires_ageaware(a: Assignment) -> bool:
    return _requires(a, "sex_aware_couples", "age_aware_chunking")


def _c_dirichlet_requires_employment(a: Assignment) -> bool:
    # A non-zero Dirichlet prior only affects the employment-margin seed.
    return a["dirichlet_prior_strength"] == 0.0 or a["use_employment_margin"]


def _c_buffer_only_when_cordon(a: Assignment) -> bool:
    # cordon_network_buffer_fraction is a no-op when the cordon is off -> pin it
    # to the default 0.10 so we do not generate meaningless 0.20-without-cordon.
    return a["cordon_enabled"] or a["cordon_network_buffer_fraction"] == 0.10


PIPELINE_CONSTRAINTS: Tuple[Constraint, ...] = (
    _c_joint_requires_size,
    _c_ageaware_requires_size,
    _c_employment_requires_size,
    _c_sexaware_requires_ageaware,
    _c_dirichlet_requires_employment,
    _c_buffer_only_when_cordon,
)


def pipeline_covering_array() -> List[Assignment]:
    """The pairwise covering array for the pipeline configuration model."""
    return pairwise(PIPELINE_FACTORS, PIPELINE_CONSTRAINTS)


# ---------------------------------------------------------------------------
# CLI: write the covering array to CSV + Markdown.
# ---------------------------------------------------------------------------

def _to_csv(rows: List[Assignment], names: Sequence[str]) -> str:
    lines = [",".join(["case"] + list(names))]
    for idx, row in enumerate(rows, start=1):
        lines.append(",".join([str(idx)] + [str(row[n]) for n in names]))
    return "\n".join(lines) + "\n"


def _to_markdown(rows: List[Assignment], names: Sequence[str]) -> str:
    header = "| case | " + " | ".join(names) + " |"
    sep = "|" + "---|" * (len(names) + 1)
    body = []
    for idx, row in enumerate(rows, start=1):
        body.append("| " + str(idx) + " | " + " | ".join(str(row[n]) for n in names) + " |")
    intro = (
        f"# Pairwise (PICT) configuration covering array\n\n"
        f"{len(rows)} configurations covering every feasible pair of the "
        f"{len(names)} pipeline factors.\n"
        f"Generated by `python -m braunschweig.testing.pict`.\n\n"
    )
    return intro + "\n".join([header, sep] + body) + "\n"


def main(argv=None) -> None:
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(
        description="Generate the pairwise pipeline-config covering array."
    )
    parser.add_argument("--out-dir", default="docs/testing")
    args = parser.parse_args(argv)

    names = list(PIPELINE_FACTORS)
    rows = pipeline_covering_array()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "pict_pipeline_cases.csv").write_text(_to_csv(rows, names), encoding="utf-8")
    (out_dir / "pict_pipeline_cases.md").write_text(_to_markdown(rows, names), encoding="utf-8")
    print(f"[pict] {len(rows)} pairwise cases over {len(names)} factors")
    print(f"[pict] wrote {out_dir / 'pict_pipeline_cases.csv'}")
    print(f"[pict] wrote {out_dir / 'pict_pipeline_cases.md'}")


if __name__ == "__main__":
    main()
