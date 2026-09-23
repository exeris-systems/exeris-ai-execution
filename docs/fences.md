---
title: "Fences in force for exeris-ai-execution"
type: reference
visibility: public
owning-repo: exeris-ai-execution
status: active
last-verified: 2026-09-22
---

# Fences in force

`instrument.capture_version` is the version of the row contract — the value in `schemas/VERSION`,
moved by the SemVer ADR-086 §C.9 fixes over that contract, and one value for every producer writing
against it. What a producer itself was when it wrote a row is the other half, and that is what
`instrument.fence` carries. This file is the register those ids resolve in.

What a fence means is ADR-086 §E.20's and `inbox/README.md`'s, in the same words as both: rows
either side of one are never summarised in one figure, and rows are appended and marked, never
rewritten or deleted. Neither rule is restated here. This is the half they need and cannot supply —
an id nobody wrote down is a fence nobody can say they are on the far side of.

## The register

| Fence id | Date | Producer | What it marks | Entry |
|:--|:--|:--|:--|:--|
| `2026-09-19-contract-0-2-0` | 2026-09-19 | none — the contract, not a producer | The row contract moving from `0.1.0` to `0.2.0`: eight optional `execution` fields, a third `workload.fingerprint` class, and the local form of `agent.model_snapshot`. | A change to the capture version writes a dated fence under §E.20 whatever SemVer step it is; §C.9's "MAJOR writes a fence" says what makes a step MAJOR, not what makes a fence, and this step is a MINOR. So this is a fence that fences nothing: the inbox holds no rows, so there is no figure on either side of it and nothing that could be summarised across it. It is entered because the register is what makes an id resolvable, and the first id is the one most likely to be assumed rather than looked up. Every field the version adds is optional, so no row written against `0.1.0` would have become invalid had one existed. |
| `2026-09-19-ci-backfill-cc-2-1-272` | 2026-09-19 | `ci-backfill` | Rows derived by `tools/derive_ci_rows.py` from the rescued execution streams of runs under client `2.1.272`. On every one of them `agent.system_prompt_sha256` is **reconstructed**, not captured. | See *The backfill fences* below. |
| `2026-09-19-ci-backfill-cc-2-1-273` | 2026-09-19 | `ci-backfill` | Rows derived by `tools/derive_ci_rows.py` from the rescued execution streams of runs under client `2.1.273`. On every one of them `agent.system_prompt_sha256` is **reconstructed**, not captured. | See *The backfill fences* below. |
| `2026-09-19-ci-backfill-cc-2-1-274` | 2026-09-19 | `ci-backfill` | Rows derived by `tools/derive_ci_rows.py` from the rescued execution streams of runs under client `2.1.274`. On every one of them `agent.system_prompt_sha256` is **reconstructed**, not captured. | See *The backfill fences* below. |
| `2026-09-19-ci-backfill-cc-2-1-278` | 2026-09-19 | `ci-backfill` | Rows derived by `tools/derive_ci_rows.py` from the rescued execution streams of runs under client `2.1.278`. On every one of them `agent.system_prompt_sha256` is **reconstructed**, not captured. | See *The backfill fences* below. |
| `2026-09-23-harness-claude-cc-2-1-280` | 2026-09-23 | `harness-claude` | Rows written by `exeris-agent close-run` for an arm that runs Claude Code `2.1.280` against a vendor's model, headless, under the execution identity. | See *The harness fences* below. |
| `2026-09-23-harness-claude-cc-2-1-280-w-4c856523d61d` | 2026-09-23 | `harness-claude` | The same producer and client, for an arm whose model is served on this machine from weights whose digest begins `4c856523d61d` (`gemma4:26b-a4b-it-qat` under Ollama, 128k context). | See *The harness fences* below. |
| `2026-09-23-harness-antigravity-cc-1-2-8` | 2026-09-23 | `harness-antigravity` | Rows written by `exeris-agent close-run` for an arm that runs Antigravity `1.2.8` in its print mode, under the execution identity. | See *The harness fences* below. |
| `2026-09-23-harness-claude-oracle3-cc-2-1-280` | 2026-09-23 | `harness-claude-oracle3` | Rows written for an arm driven by `exeris-agent drive` with up to three oracle feedback rounds, Claude Code `2.1.280` against a vendor's model. | See *The harness fences* below. |
| `2026-09-23-harness-claude-oracle3-cc-2-1-280-w-4c856523d61d` | 2026-09-23 | `harness-claude-oracle3` | The same, for the arm serving the local weights whose digest begins `4c856523d61d`. | See *The harness fences* below. |
| `2026-09-23-harness-antigravity-oracle3-cc-1-2-8` | 2026-09-23 | `harness-antigravity-oracle3` | The same loop for an arm running Antigravity `1.2.8`, resumed through its conversation id. | See *The harness fences* below. |

## The backfill fences

The four `ci-backfill` ids above share one entry, because they mark one derivation run under four
client versions and the argument is the same argument four times.

**What is reconstructed.** A stream carries what the runner said, never the prompt it was given,
and the reviewing workflow exported no hash of that prompt at the time these runs happened. So
`agent.system_prompt_sha256` on these rows is not a measurement of text the producer read from the
run; it is recomputed from what GitHub still holds — the reviewing workflow's `prompt:` block at
the SHA the run's own `referenced_workflows` names, rendered with the run's inputs, then the
routine file at that same SHA, then `AGENTS.md` at the reviewed commit. ADR-087 §C.14 admits this
for a backfill and only as a stated derivation: the fence is the statement, and without it the
column would claim to be the same kind of fact as a captured hash.

**The two assumptions.** Neither is recoverable from the run, and both are conditions the rows
depend on:

1. `referenced_workflows[].sha` names the commit of the routine repository the run resolved its
   reusable workflow at, and the produce job's own checkout of that repository — which is
   unpinned — read that same commit. A produce job that pins its checkout independently of the
   workflow reference makes the two different commits, and rows derived after such a change belong
   on the far side of a fence from rows derived before it.
2. `AGENTS.md` is taken at the reviewed commit, which is the commit `repository_state.commit`
   names. The runner read it on the merge ref — the reviewed commit merged into its base — so the
   two agree except where the base moved under the run.

**What retires them.** The first live run that exports these components as text lets the
reconstruction be checked against them component by component. Agreement leaves the fences standing
as a record of how the rows were made. A disagreement retires them by a dated entry in this
register, and the rows already written stay marked and are neither re-derived nor removed — §E.20's
rule, applied to the producer that wrote them.

**Why four.** One client version is one fence, by the rule stated below, and the rescued streams
were produced under `2.1.272`, `2.1.273`, `2.1.274` and `2.1.278`. A single id spanning all four
would join rows whose harness differed, which is the join `instrument.fence` exists to prevent.

## The harness fences

The three `harness-*` ids above are the local producer's first, entered before its first row, as
the register requires of a new client version.

**What they mark.** A row the harness writes is derived from the session log the client left, never
from the model's account of itself: turns, tool calls and usage are counted from that log, and the
outcome is the docs oracle's under the calibration it published. The client is part of the model
reference, so each client version is its own id; the local arm's weights are part of it too, so the
arm that serves `gemma4:26b-a4b-it-qat` here carries the weights' digest and stands apart from the
arm that reaches a vendor through the same client at the same version.

**The conditions they share.** Every arm is launched headless with one task text, with the
organisation's documentation standards readable beside the worktree and nothing else outside it,
and with no person in the loop after the first prompt. The Claude Code arms run under one fixed
tool surface; the Antigravity arm runs under that client's own accept-edits mode, which is a
different surface and is why its rows sit under a different producer. The local model server's
context window is part of the local arm's conditions: a window smaller than the client's own
system prompt plus the task's reading truncates the conversation, and rows either side of a change
to it belong on different fences.

**The oracle loop.** An `-oracle<N>` producer drives the arm to an outcome rather than taking its
first answer: after each pass the calibrated docs oracle judges the tree, and while the outcome is
`FALSE_DONE` and fewer than `N` feedback rounds have been sent, the same session is resumed with a
prompt made of nothing but the failing gates' names and details, verbatim. It stops at `TRUE_DONE`,
at an outcome the oracle could not reach, or when the rounds run out. The row's cost is the whole
session's, so a row under such a fence reads as cost to the outcome the loop ended on — ADR-086
§E.21's primary quantity — and never sums with a single-pass row, whose cost is one attempt's.
The oracle's prompts are the instrument speaking, not a person: they are excluded from
`execution.human_prompts`, and the rounds actually used are kept beside the row in the run's
staging, because the row has no field for them.

## The grammar of a fence id

`<date>-<producer>-cc-<harness version, its dots written as dashes>[-w-<weights digest>]`

- `<date>` — the day the fence was written, `YYYY-MM-DD`, which the contract's own pattern requires
  first.
- `<producer>` — what wrote the rows the fence marks, named by what it is rather than by what ran
  it: `ci-backfill`, `ci-live`, `harness-claude`.
- `cc-<version>` — the client and the version of it the rows were produced under, `2.1.274` written
  `2-1-274`.
- `w-<digest>` — the first twelve hex of `agent.model_snapshot`, and present only on a fence for
  rows whose weights are the producer's own machine's. A model snapshot is instrument state under
  §E.20, and an arm serving local weights reaches the same client at the same version as the arm
  that reaches a vendor: without this segment the two resolve one id, and rows whose model
  reference differs would sit on one fence. A row whose snapshot reads `unresolved:<alias>` names
  no weights and carries no such segment — there is nothing to name, and the alias is the state the
  marking exists to expose rather than a value to fence on.

The dots are written as dashes because `instrument.fence`'s pattern is a date followed by lower-case
alphanumeric segments separated by hyphens, and it admits no dot anywhere. A version spelled the way
its vendor spells it would be refused by the contract, so the id says the same thing in the alphabet
the contract admits, and says it the same way every time — a fence id that has two spellings joins
to neither half of the rows it marks.

One client version is one fence: a producer that ran under three client versions writes three ids
and three entries, because the client is part of the model reference (`agent.harness`) and a change
to it is a change to the run's conditions. One set of weights is one fence for the same reason: a
local arm whose `.gguf` is replaced writes another id and another entry, and the rows before the
swap are neither re-derived nor summarised with the rows after it.

The first entry above carries no `cc-` segment, and that is not a shortening: no client ran. A fence
written for the contract itself names the contract and the version it moved to, because there is no
producer and no client version to name, and inventing one would make the id resolve to a run that
never happened.
