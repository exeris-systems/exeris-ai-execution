---
title: "Oracles a producer may name in exeris-ai-execution"
type: reference
visibility: public
owning-repo: exeris-ai-execution
status: active
last-verified: 2026-09-26
---

# Oracles a producer may name

What each oracle answers, which domain it judges and what its verdict is admissible as are
ADR-086 §D.15's, and are cited here rather than restated: a table kept in two places drifts in one
of them, and this is not the copy the contract would be amended against.

This file answers the other question, which §D.15 does not: which `oracle.id` and `oracle.version` a
producer may write **today**, and what `oracle.calibration` a row naming one carries when it is
written.

## What a producer may write today

| `oracle.id` | `oracle.version` | `oracle.calibration` on a row written today | Written by |
|:--|:--|:--|:--|
| `review-disposition` | `rest-v1` | `{suite: none, status: not-run, result: none}` | the CI producer, on a `docs-review-live` row |
| `docs-guardrails` | the agent-bundle version the judged checkout pinned when the gates ran, or `unpinned`; it equals `repository_state.bundle_version` except in a run that edited the manifest — that field is read at `base_sha`, this one at the head the run left | whatever `oracles/docs-guardrails/oracle-selftest-v2.json` publishes — `{suite: docs-mutation-v2, status: pass, result: 11/11}` as it stands | a harness, on a documentation row |
| `scb` | `1.3` | `{suite: oracle-selftest, status: not-run, result: not-run}` | a harness, on a construction row |

An id that is not in this table is not writable: the row would name a state no reader can look up.
Adding one is a change to this file, made in the same pull request as the producer that needs it.

§D.15's fourth oracle, `review-planted` on the `docs-review-calibration` domain, is absent for a
reason the rule above does not cover: there is no row to write it on. It judges the seeded corpus
of §D.18, and no seeded pull request exists, so no run of that domain has happened. The id enters
this table in the pull request that creates the corpus, carrying `review-planted-selftest-v1` and
the `not-run` status §D.17 leaves it at until that self-test passes as a suite.

## Where the `docs-guardrails` calibration comes from

`oracles/docs-guardrails/oracle-selftest-v2.json` is the home of that row's `status` and `result`,
and the table above quotes it rather than owning it. The file is what `docs-mutation-v2` writes
when it runs: mutants built from a clean checkout of the documentation corpus, each judged by the
oracle, plus the unmutated copy — because a suite that has never seen a `TRUE_DONE` has shown only
that the oracle can say no.

**What v2 adds.** The oracle's first five gates are the organisation's structural checkers, and
`docs-mutation-v1`'s eight mutants — RFC-2026-09-08 §Testing — calibrate them. A run can leave a
corpus well-formed and still wrong, and v1 has no mutant of that shape. v2 adds two gates and the
three mutants that contradict them:

| Gate | What it judges | Its inputs | Mutants |
|:--|:--|:--|:--|
| `content_preserved` | every file present at the run's starting commit and matching a pattern the task named keeps its body below the frontmatter byte for byte; a deleted one fails | `--base <commit>`, `--preserve <glob>` (repeatable) | 9 — a preserved file keeps valid frontmatter and loses body lines → `FALSE_DONE` |
| `adr_links_resolve` | every `docs/adr/ADR-NNN.link.md` (or `adr/…`) names a record the registry holds, by that record's title, and links the record's owning repository | `--bridge <dist/server.js>` — the registry is read through the Exeris MCP server, which ADR-086 §A.3 admits as a context adapter | 10 — a stub naming another record's title → `FALSE_DONE`; 11 — a correct stub with no bridge on disk → `UNKNOWN` |

Each gate states its own applicability, and fail-closed composition is unchanged. A task that names
nothing to preserve, and a checkout holding no stubs, leave the gate `not-run` and out of the way.
A task that names files to preserve against a base the checkout cannot read, stubs with no bridge
that answers for them, and a stub whose record has no title anywhere — none in its registry row and
none the bridge could read from the record — leave it `not-run` **and unavailable**, so the row is
`UNKNOWN`: nothing looked at what the gate exists to see.

The two gates are the second generation's question, and a call asks it by naming a bridge or
something to preserve. A call naming neither — the form a producer calibrated against v1 uses — is
judged as v1 judges: the five structural gates, without these two.

v2 is v1's eight plus these three, so `status: pass` needs 11/11 **and** the clean copy judged
`TRUE_DONE` with both new gates among those that passed — the clean copy is the corpus committed
as a base, with a correct stub added, judged with that base, a `**/*.md` pattern and the bridge.
The published file names the bridge's version and commit beside the corpus commit, because two of
the gates judged there are the bridge's reading of the registry.

`oracles/docs-guardrails/oracle-selftest.json`, v1's result, stays where it is and is still
checked: it is the calibration of the five structural gates on their own, and v2 imports its
mutants rather than restating them.

**The harness reads the calibration from the v2 file when it closes a row**, and copies the two
values onto the row as they stood at that moment. It does not decide them, and it does not carry a
remembered pair: a row is interpretable only against the calibration in force when it was written,
and a value typed into a producer is a second owner of a number that has a home.

The suite has run as a suite and its result is published, which is the condition ADR-086 §G.35 puts
before any row may name this oracle with `calibration.status: pass`. A run of the suite that scores
below 11/11, or whose clean copy is not `TRUE_DONE`, publishes `status: fail`, and under §E.19
every documentation row written while that stands is `UNKNOWN` — the same mechanism, running in the
direction it was built to run.

The `oracle calibration` job re-checks v1 on every pull request, and `oracle-v2.yml` re-checks v2:
it builds `exeris-ai-bridge` at the commit the published v2 file names — so the pin has one home —
and runs `python3 -m oracles.docs_mutation_v2 --corpus <exeris-docs> --bridge <dist/server.js>
--out oracles/docs-guardrails/oracle-selftest-v2.json --check`. Neither job is a required check.

`oracle.version` is the agent-bundle version pinned by the checkout that was judged, because the
gates are that bundle's rules. A checkout pinning none is judged all the same and the version
written is `unpinned`: rows either side of that word are not one population, and a number invented
for them would hide it.

It is read at the head the run left, which is what makes it a different reading from
`repository_state.bundle_version` — that one is read at `base_sha`. The two agree on every run but
one: a run that edited `.agents/manifest.yaml` was subject to the pin it found and was judged by
the pin it wrote, and the row says both. A reader who takes them for one field would read such a
row as a contradiction.

## Why the other two statuses are `not-run`

Neither `oracle-selftest` nor the review domain's suite has been run as a suite. `not-run` is not a
pass (ADR-086 §E.19), the schema admits no `TRUE_DONE` or `FALSE_DONE` on a row whose
`oracle.calibration.status` is `fail` or `not-run`, and so a row naming either of those two carries
`UNKNOWN`, or `UNREACHABLE` where a run never got far enough to be judged. Naming a suite that has
not run is how a row says which pass it is waiting for, and `result: not-run` repeats the status
because the field holds a score as it was published and no score was.

## Why `review-disposition` carries a version at all

`rest-v1` names what the oracle could read. §D.15 defines a finding as *addressed* when the file its
`location` names changed after the review **and its thread was resolved**; a disposition derived
through the host's REST API sees the first half and not the second, because the API does not expose
thread resolution and the publication it reads is one comment carrying no threads. That clause is
therefore empty under this version — the oracle applies what it can read and the version is what
says which definition was in force. A later implementation that can read resolution is a different
version, and rows either side of the change are not one population.
