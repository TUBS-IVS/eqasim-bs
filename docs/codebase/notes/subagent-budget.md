# Subagent budget (rule for every agent dispatch)

What the rule is, why it exists, and how to apply it when a Claude session dispatches
subagents (subagent-driven development, reviews, fan-out). It complements the canonical
feature workflow in `CONTRIBUTING.md`; it does not replace any of its steps.

## The rule

1. **Size the task, not the agent.** One implementer should finish a task in about
   80 tool calls. If the plan cannot name the 1-3 files and the tests a task touches,
   split the task before dispatching it.
2. **Every dispatch prompt names the model and the budget**, for example:
   "Model: sonnet. Budget: about 80 tool calls. At about 120, stop, commit what passes,
   and report REMAINING." A subagent never inherits the session model by omission.
3. **Reviews stay, but bounded.** One task review (spec compliance and code quality in
   one dispatch, sonnet), then one scoped re-review of the fix diff only. After the second
   failed fix round the controller takes the task over instead of running further rounds.
   The most capable model is reserved for the final whole-branch review.
4. **Keep shell output short inside subagents too:** `tail`/`grep` long output, never
   paste full logs into the context. `scripts/run_tests.py` already runs pytest with
   `-q --tb=short` for every selection, test paths included; do not add `-v` or a longer
   `--tb` unless a failure needs it.
5. **Record the call count per task** in the plan ledger. A task that overran its budget
   is split before the next task of its kind is dispatched.
6. **Fan-out needs the owner's OK** (model, plan, scale) before dispatch, and at most
   three implementers run in parallel.
7. **Dispatch the tool-restricted agent type, not `general-purpose`, when one fits.**
   Where the session offers them (user-level agents in `~/.claude/agents/`, not part of
   this repository): `implementer` for one well-specified task (a plan step, a bug fix
   with a known root cause), `reviewer` for task and re-reviews, `explorer` for
   read-only lookups. `general-purpose` only when the job needs tools these lack (web,
   MCP, spawning agents) or is open-ended research. This also applies to the
   `superpowers` subagent-driven-development dispatches, whose templates default to
   `general-purpose`. See "Measured: tool-restricted agent types" below.

## Why: where the tokens went

Local measurement, 2026-10-21, over 13 eqasim-bs Claude sessions (September to October
2026). Source: the session transcripts under `~/.claude/projects/` (local, not in the
repo). Effective cost weights relative to one regular input token: cache read 0.1,
cache write 2, output 5. The numbers are approximate and NOT reproducible from the
repository; treat them as the motivating observation, not as validated evidence.

| Share of all effective usage | |
|---|---|
| Subagents | 63 % |
| Shell commands and their output | 18 % |
| File operations | 7 % |
| Start context (instructions, tools, CLAUDE.md, memory) | 6 % |
| Messages | 5 % |

Within the 326 subagents, length dominates, not count:

| Tool calls per subagent | Subagents | Share of subagent cost | Cost per call |
|---|---|---|---|
| under 100 | 263 | 25 % | 25k to 30k |
| 100 to 200 | 39 | 24 % | about 42k |
| over 200 | 23 | 52 % | 62k to 67k |

A long agent re-reads its whole accumulated context on every call, so each call costs
more than twice as much as in a short one. By role, implementers took 70 % of the
subagent cost, task reviews 18 %, re-reviews 4 %, fix rounds 3 %. Cutting reviews was
therefore rejected as the lever: it saves little and costs quality.

## Expected effect (assumption)

ASSUMPTION: if agents beyond about 120 calls are avoided, the same work runs in shorter
agents at roughly 30k instead of 65k per call, minus the extra start contexts of the
additional agents. That would save roughly a third of the subagent cost, about a fifth
of total usage. This is arithmetic on the table above, not a measurement; re-measure
over several sessions before claiming a saving.

## Measured: tool-restricted agent types

Local measurement, 2026-10-22, over the subagent transcripts of eqasim-bs sessions since
2026-10-01 (`~/.claude/projects/*eqasim-bs*/*/subagents/`, agent type from each
`.meta.json`; assistant records deduplicated by message id; same weights as above).
Only jobs that ran on Sonnet 5.5, so the model does not confound the comparison. The
first-call context is the fixed start cost (system prompt, tool schemas, skill list,
CLAUDE.md, memory) that every call of the job re-reads.

| Agent type | Jobs (1-10 calls) | First-call context | Effective tokens per call |
|---|---|---|---|
| `general-purpose` | 73 | about 67k | about 29k |
| `reviewer` | 21 | about 20k | about 17k |
| `implementer` | 7 | about 21k | about 12k |

Medians. For jobs of 11-30 calls: `general-purpose` about 21k per call (n = 86),
`implementer` about 12k (n = 7). The start-context difference (about 46k tokens) is a
direct effect of the smaller tool set; the per-call difference also depends on the jobs,
which were not matched, and the new-agent samples are small (three sessions). Re-measure
once more jobs exist before quoting a session-level saving.
