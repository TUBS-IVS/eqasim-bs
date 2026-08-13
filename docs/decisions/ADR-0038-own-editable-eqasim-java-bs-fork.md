# ADR-0038 · 2026-06 · Own editable `eqasim-java-bs` fork
- **Status:** active
- **Context:** The MATSim/Java side needed project-specific changes (parking, freight injection,
  mode availability, SimWrapper contrib) not in the upstream bavaria Java.
- **Decision:** Build our own editable Java project (the `braunschweig` Java module) wired via
  `eqasim_source_path` (`../eqasim-java-bs`), pinned to MATSim `2025.0-PR3568`, instead of the
  upstream bavaria clone.
- **Rationale:** The pipeline builds our editable Java, so Java-side features land in our fork
  (memory `eqasim-java-bs-own-fork`; UPSTREAM_DELTA.md).
- **Consequences:** Java features (ADR-0039 parking, ADR-0030 freight injection) live in the fork.
- **Evidence:** `docs/UPSTREAM_DELTA.md`; memory `eqasim-java-bs-own-fork`; PROJECT_STATUS.md §2.8.

