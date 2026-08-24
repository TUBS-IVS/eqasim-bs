# ADR-0098 · 2026-08-21 · The java project's pom owns the jar version; the pipeline reads it instead of pinning a copy

- **Status:** active
- **Context:** `matsim.runtime.eqasim` builds the braunschweig module from source
  (`eqasim_source_path`) and then looks for `braunschweig-<version>.jar` **by
  name**, aborting the stage if that file is absent. The version string is
  therefore not documentation — it is the filename the build has to produce, and
  `eqasim-java-bs/braunschweig/pom.xml` is what decides it. Until now the
  pipeline kept a second copy of that number in `DEFAULT_EQASIM_VERSION`.
  That duplication has broken twice, both times for the same reason: the fork's
  `release-please` configuration lists `braunschweig/pom.xml` among its
  `extra-files`, so **every** fork release renames our jar. v2.3.0 forced one
  manual bump (recorded in the `eqasim_java_fork` feature entry) and v2.3.1
  (2026-08-21, fork PR #24) forced another — while carrying no code at all: 13
  files changed, all version bumps plus CHANGELOG/README. The release workflow
  runs on every merge to main, so the next `fix:` PR in the fork would rename the
  jar again. Issue #347, found while closing #330/#344.
  Worth stating precisely, because the run manifest first recorded the opposite:
  a release is NOT what delivers a java fix here. The pipeline builds from source
  and `validate()` keys its cache on the newest `.java`/`.xml`/`.properties`
  mtime under `eqasim_source_path`, so a plain `git pull` in that tree already
  delivers the fix and triggers the rebuild. A release only relabels — i.e. it
  creates work rather than delivering any.
- **Decision:** For the source-build path, READ the version from the pom of the
  tree that is actually built (`resolve_source_version` /
  `module_version_from_pom`, reading the `<parent>` block of the COPIED tree's
  `braunschweig/pom.xml`). `eqasim_version` now defaults to `None`, meaning
  "derive"; an explicit value still wins, which the legacy upstream-clone path
  needs (it builds `bavaria-<upstream version>.jar` and must be pinned
  deliberately). `DEFAULT_EQASIM_VERSION` survives only as the fallback for the
  two paths that have no braunschweig pom — a prebuilt `eqasim_path` jar and the
  legacy clone — and is kept current so the fallback is not misleading.
  - **Why the pom and not the constant:** one fact, one owner. The pom is the
    input to the build; anything else is a guess about the build's output.
  - **Read from the COPIED tree, not the original source.** That is the tree
    maven builds, and a pom change already devalidates this stage through
    `validate()`'s mtime key, so the derived value never disagrees with the cache.
  - **The fallback is loud.** An unreadable pom logs `WARNING!` with the reason
    before using the constant; a silent fallback here would resurface much later
    as a confusing "JAR not built" abort with the real cause lost.
  - **Rejected: bump the constant and add a guard test.** That was the first fix
    (a test comparing the constant against the checked-out pom). It catches the
    mismatch at test time rather than mid-run, which is an improvement, but it
    still requires a manual bump on every fork release and leaves the number
    duplicated. The guard is superseded by derivation; the test file now pins the
    derivation semantics instead.
  - **Rejected: parse the pom with ElementTree.** The pom declares a default
    namespace, which turns every path expression into namespace bookkeeping for
    one string. The `<parent>` block is unambiguous, and the regex is confined to
    it so the dependency entries below (same number) cannot be picked up.
  - **Not decided here:** whether the fork should keep cutting releases at all.
    They deliver nothing to this pipeline, and dropping `braunschweig/pom.xml`
    from the release automation's `extra-files` would keep the jar name stable
    across them. That is a change in the java repository and stays open in #347.
- **Consequences:**
  - A fork release can no longer break the pipeline: the name follows the pom
    automatically, and the pom change devalidates the stage so the jar is rebuilt.
  - `eqasim_version` changes meaning from "the version" to "an override". No
    committed config sets it, so no run changes behaviour; the stage's config
    hash does change (the declared default moves from `"2.3.0"` to `None`), which
    devalidates `matsim.runtime.eqasim` and its dependents once. The jar would
    have been rebuilt anyway, because the server's source tree was pulled to the
    v2.3.1 commit on the same day.
  - The dropped guarantee is worth naming: nothing now asserts that the fork's
    version is a *deliberate* one. If release automation produced a nonsense
    version, the pipeline would faithfully build and use it.
- **Verification:** `tests/test_eqasim_java_version_pin.py` — pom parsing
  (including that the dependency block's identical number is not picked up), the
  precedence rules (pom over constant, explicit config over both), the loud
  fallback on a missing pom, and an end-to-end resolution against the real
  sibling checkout (skipped when absent). Separately, the pipeline's own maven
  command was run on the server against a copy of the pulled tree: it produced
  `braunschweig-2.3.1.jar`, 184.5 MB, 5016 `org/matsim` classes — the shaded fat
  jar, not the 0.1 MB `original-*.jar` — with the `System.exit` guard present
  twice in the compiled `RunSimulation`. That double check is deliberate: a
  stopgap build on 2026-08-20 produced a 55 KB thin jar and broke an unrelated
  java step while a class-presence check came back green.
