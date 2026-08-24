# CI data-availability checking

## What CI can and cannot ask about data

Two different questions look alike and are not:

| Question | Answered by | Runnable in CI |
|---|---|---|
| Are the input FILES present on this machine? | `scripts/verify_braunschweig_inputs.py` (default mode) | **No** — a runner has no `eqasim-data/` |
| Are the documented DOWNLOAD SOURCE URLs still reachable? | `scripts/verify_braunschweig_inputs.py --check-urls` | **Yes** — network only, touches no data |

Only the second is a CI-shaped question, so it is the one
`.github/workflows/data.yml` runs weekly (plus `workflow_dispatch` for a manual
trigger). It detects the failure mode that actually bites us: a statistical
portal silently moving, renaming, or retiring a source the pipeline documents.

## Why the French verifier is no longer scheduled

`scripts/verify_data.py` is upstream Île-de-France code: it checks **36 French
open-data URLs** and contains zero German or Braunschweig references. It is
still in the repository and **untouched** — only its scheduling was the problem.
Do not delete it and do not "fix" its URLs; it belongs to upstream.

It was the weekly cron (Sunday 10:00) inherited with the fork, and it failed on
both Sundays it ever ran here (2026-08-16, 2026-08-23), mailing a failure every
week for data this project never reads. Measured on 2026-08-23 from a German
machine, nine of its sources have rotted upstream:

- 3× SIRENE and 1× BPE-2021 return **404** (moved or renamed).
- The 5 BAN `adresses-{78,92,93,94,95}.csv.gz` paths return **0 bytes after
  30–40 s** on both HEAD and GET, while the same host's homepage answers 200 in
  0.3 s — i.e. server-side, not a runner block.

Meanwhile our own verifier was in no workflow at all. The repurposing fixes both
halves: the weekly mail now means something, and the check covers our inputs.

## How `--check-urls` behaves

Iterates the same `INPUTS` catalog as the file-presence mode, so the two modes
can never disagree about what an input is.

- **Skipped, with the reason printed:** `restricted=True` (obtain via a usage
  agreement), `generated=True` (produced locally by a script), and prose-only
  sources with no URL. Nothing is skipped silently.
- A `source` field is documentation prose in the general case, so the **first
  whitespace-delimited token** is what gets tested for `http(s)://`. Testing the
  whole string would classify every "URL followed by a parenthesised hint" entry
  as prose and shrink the check to a third of the catalog.
- **HEAD, retried, then a ranged GET** before anything is called unreachable:
  several statistical portals answer GET but not HEAD, so a HEAD-only signal is
  never sufficient evidence that a source is dead.
- **One probe per DISTINCT URL** (`probe_url` memoises by URL). The catalog is one
  entry per *file*, which the file-presence mode needs, but many entries share one
  source page: the six A3 ENTD files, both regionalstatistik tables, both
  Pendleratlas exports, both INKAR entries — 21 checked inputs are only ~13
  distinct URLs. Probing per input would hit one host up to six times with up to
  four requests each and turn a single outage into six identical failure lines,
  i.e. reproduce the noise this workflow was repurposed to stop. The summary
  therefore reports **both** counts: `N inputs over M distinct sources`.
- **Exit 1** if any non-optional public source is unreachable; an `optional=True`
  failure is a warning only. The exit reason is always printed.
- **A transport failure and a dead URL get different remediation.** Only the
  exception shape tells them apart, so the failure detail keeps a truncated
  `str(exc)` next to the class name, and the exit block prints "the source likely
  moved or was renamed — fix the URL here AND in `DOWNLOAD_CHECKLIST_BS.md`" only
  for failures that actually returned an HTTP status. A failure that produced *no*
  HTTP status is reported as a transport error (TLS/DNS/timeout) pointing back at
  this note — telling someone to edit a URL that is perfectly fine is the wrong
  action.

### A3 / ENTD stays required (checked 2026-08-23)

The six ENTD entries share one French URL, which raised the question of whether
they should be `optional=True`. Per `DOWNLOAD_CHECKLIST_BS.md`'s own section A:

> A3 is the upstream travel-pattern donor; the BS pipeline does not yet have a
> German HTS replacement.

The checklist states A1 and A2 "is required" in the same paragraph and does **not**
label A3 reference-pipeline-only, so the flags were left alone: ENTD is documented
as still in use, and downgrading it on inference would make the catalog *less*
truthful. The amplification problem is solved by the per-URL dedupe above, which
collapses the six entries onto one probe. If ENTD is genuinely retired from the
production path, correct the checklist sentence and the flags together.

### Local runs can show false UNREACHABLE (verified 2026-08-23)

On a workstation behind a TLS-inspecting middlebox, `www.inkar.de` and
`www-genesis.destatis.de` fail the handshake with
`SSLError(ASN1: NOT_ENOUGH_DATA)` — even with `verify=False`, and sometimes
surfacing as `ConnectTimeout` instead — while DNS and TCP:443 succeed and `curl`
(OS trust store) gets **200** and **307** from the same URLs. Both sources are
alive. A clean CI runner is therefore the authority on reachability; treat a
local UNREACHABLE for a German portal as suspect, and cross-check with `curl`
before touching a URL. The script deliberately does **not** fall back to an
insecure or external client — that would be a silent fallback.

## Rule for a new pipeline input

Add it to `scripts/verify_braunschweig_inputs.py` **and**
`eqasim-data/DOWNLOAD_CHECKLIST_BS.md` in the same change — the script's own
module docstring states this requirement; it is not restated here. Adding it to
the catalog is what puts it into both the local presence check and the weekly CI
reachability check, so there is nothing else to wire up.
