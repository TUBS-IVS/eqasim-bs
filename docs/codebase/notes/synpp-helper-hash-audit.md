# synpp stage helper-hash coverage audit

How the repository checks that a synpp stage's cache key covers the Python code the stage runs,
what the check can and cannot see, and where its live results are. The method is code
(`scripts/audit_synpp_helper_hash.py`); this note says WHY each step is drawn where it is. It
holds no inventory: per-stage lists and counts go stale with the next commit, so the script's
output is their only home.

## The problem

synpp's `get_stage_hash` (semantic AST digest in the fork, `docs/codebase/notes/synpp-fork.md`)
covers only a stage module's OWN source. Code the stage calls in another first-party module is
outside its cache key: edit that helper and a partial rerun silently reuses output produced by
the old code. That holds for a declared synpp dependency too when the stage imports it and runs
its code: the DAG edge carries the parent's OUTPUT, and under content-addressed validity
(ADR-0149) an unchanged parent output spares the child, so an edit to the parent's library code
re-executes the parent only (ADR-0194). A stage closes the gap with a `validate()` token over its helper modules:
`_HELPER_MODULES` (module objects, for module-level imports) and `_DEFERRED_HELPER_MODULE_NAMES`
(dotted names, for function-level imports and for modules reached only through a helper), passed
to `braunschweig.synpp_tokens.source_token`. The import site decides the tuple; each module is
listed once. A stage whose token also covers data files returns that part next to the module
digests (`_COMPOSITE_STAGES` in `tests/test_token_refactor_coverage.py`).

## Where the live state is

- `python scripts/audit_synpp_helper_hash.py . --json <out.json>`: per stage its first-party
  imports, declared stage edges, `required_helpers`, whether `validate()` hashes source, and for
  those that do `covered`, `uncovered`, `transitive_uncovered`, `transitive_exempt`,
  `closure_boundaries` and `hashed_twice`. The `covered` field also answers "which stages
  re-execute when I edit module X".
- The gates, in `tests/test_audit_synpp_helper_hash.py`:
  `test_every_source_hashing_stage_covers_its_required_helpers` (the stage module's own imports,
  register `EXPECTED_UNCOVERED`) and `test_every_source_hashing_stage_hashes_its_whole_import_closure`
  (the whole closure, ADR-0136, register `DEFERRED_TRANSITIVE_GAPS`). Each register entry carries
  its reason; the gate fails when the set changes in either direction.
- `tests/test_synpp_helper_hash_invariant.py` `ALLOWED_VIOLATIONS`: the narrower own-package
  slice (a stage's sibling submodules), also checked for stages without a token, as a shrinking
  allow-list.
- `EXPECTED_SOURCE_HASHING_STAGES` in `tests/test_token_refactor_coverage.py` pins how many stages
  hash source; its comment lines record when and why the number moved.

## Method

A static pass over the Python AST of the first-party roots (`ROOTS` in the script); nothing is
imported or executed.

1. **Enumerate stages.** A module is a stage when its top-level body defines `configure` and
   `execute`; a package counts through its `__init__.py`. Static and `def`-only, so a stage
   produced by a factory that binds `configure = ...` at module level would be missed (none
   exists; `braunschweig/synthesis/incommuter_merge/_base.py` defines such a factory but nothing
   binds a module-level stage with it).
2. **Collect each module's imports.** The whole AST, at any nesting depth: module-level and lazy
   (inside a function body) imports both count, because lazy imports are how many helpers are
   reached. `if TYPE_CHECKING:` blocks are excluded. `from X import Y` gives ONE reading: `X.Y`
   when that is a module on disk, otherwise `X` (adding both would list every parent package as a
   helper). Relative imports resolve against the importing module's package, exactly as
   `importlib` does (`bits = package.rsplit('.', level - 1)`); a package `__init__` is its own
   anchor. Only first-party modules are kept; third-party versions are pinned by the environment.
3. **Declared stage edges are reported, not subtracted.** The literal `context.stage("name")`
   calls are listed (`declared`), but `required_helpers` is every first-party import, a declared
   stage module included: the edge covers that stage's output, not code this stage imports from
   it (see "The problem"). Until 2026-11-12 such imports were subtracted as "covered by synpp's
   hash propagation", which the early cutoff of ADR-0149 had made false (ADR-0194).
4. **Read the token.** A stage hashes source when its top-level `validate()` CALLS
   `inspect.getsource` or `synpp_tokens.source_token` (a mention in a comment or string does not
   count). Its tuples are read statically, following both import binding forms (`import a.b as
   x`, `from . import name`) and re-exports of a module object through another module. A tuple
   counts only when `validate()` reads it, and a package entry covers its own `__init__.py` only
   (`inspect.getsource` of a package returns that file, never its submodules).
5. **Walk the closure (ADR-0136).** For a source-hashing stage the same imports are followed
   through every first-party module it reaches. The walk starts at the stage module AND at every
   module its helper tuples name: a module the stage loads by name through `importlib` (the
   donor-source registry of `braunschweig.popsim.trips_stage`) has no static import edge from the
   stage, yet it runs inside the stage together with what it imports (audit finding P1-F4,
   2026-11-12). The walk goes through declared stage modules like through any other module
   (step 3). It stops at:
   - a module whose `_SYNPP_TOKEN_EXEMPTION` states that it never shapes a stage result (progress
     output, terminal colours, process monitoring, the token builder itself, runtime-only
     checks); an exemption without a non-empty reason is an error;
   - a stage module the walking stage lists in `_TOKEN_CLOSURE_BOUNDARIES` with a reason: a stage
     it reaches through its helpers but whose code it never runs. A boundary must be a stage, must
     not be imported by the walking stage itself, and must still be reached
     (`test_closure_boundaries_are_stages_the_stage_reaches_without_importing_them`). A stage
     module whose functions or constants the walking stage does use is hashed, not bounded.
   What the token misses of the closure is `transitive_uncovered`.

The walk is static, so it over-approximates: it can ask a token to hash a module the stage never
calls, never the other way round. Over-hashing costs a one-time rebuild when that module changes;
under-hashing serves a stale cache. Where covering a module would be clearly worse (a pure
rendering helper of a multi-hour stage), the right fix is usually to narrow the import to a leaf
module; only if that cannot help does the module go into a register with its reason.

## What a token change costs

Closing a gap changes the token, so the stage re-executes once. A code edit to a helper changes
the token of every stage that hashes it (the `covered` field); comment, docstring and formatting
edits change no digest (ADR-0149). Children are spared only where the re-executed parent
reproduces its output byte for byte (content-addressed validity, `docs/codebase/notes/synpp-fork.md`).

## Limitations

Checked rather than assumed; each is a place where the check can say "covered" while a cache is
stale, or say nothing at all.

- **Stages without a source token are not gated.** The two closure gates look only at stages
  whose `validate()` hashes source. A stage with first-party helpers and no token is listed in
  the script output (`hashes_source: false`, non-empty `required_helpers`) but fails no test; the
  own-package invariant catches only sibling submodules. Whether such a stage is reached by the
  production config is not part of the check (which stages were given a token and which were
  left without one, and why: ADR-0194).
- **Data files are invisible.** The check covers Python source only. An input file a stage reads
  must be in its `validate()` by hand (a content digest via `synpp_tokens.file_sha256`, or
  `synpp_tokens.text_file_sha256` for a committed text file, which normalises CRLF so a Windows
  and a Linux checkout give one token). No scan finds a file read that `validate()` misses.
- **Computed import names.** A module loaded through `importlib` with a name that is not in a
  helper tuple is invisible to the walk. The registry pattern is covered only because its module
  names are listed in `_DEFERRED_HELPER_MODULE_NAMES` (step 5).
- **PEP 562 `__getattr__`.** Three first-party modules define a module-level one.
  `braunschweig/data/census/household_income.py` computes a value from data and
  `braunschweig/synthesis/locations/secondary_chainsolvers/__init__.py` forwards names to an
  already imported sibling; neither reaches a module the scan would miss.
  `braunschweig/popsim/sources/__init__.py` resolves the donor adapter classes by importing
  their modules by name: the scan does not see those imports, so every stage that uses the
  registry must list the adapter modules in `_DEFERRED_HELPER_MODULE_NAMES` (the previous
  bullet).
- **`declared` is informational.** It lists literal `context.stage(...)` names as written
  (no alias resolution, no computed names); no gate reads it since ADR-0194.
- **Scan roots.** Only `ROOTS` are scanned. The production stage `documentation.meta_output` lives
  outside them, so its coverage is unknown, not derived.

## History

Issue #290 produced the first, hand-made inventory (2026-08-14); #327 turned the method into the
script and closed the gaps it found; ADR-0136 extended the gate to the whole import closure; the
2026-11-11 repository audit (package C) fixed the walk start (P1-F4) and gave the
production-reachable stages with helpers a token (ADR-0194). The dated inventories of those passes are in
this file's git history (`git log -p -- docs/codebase/notes/synpp-helper-hash-audit.md`), not
here.
