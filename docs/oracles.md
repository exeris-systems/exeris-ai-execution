---
title: "Oracles a producer may name in exeris-ai-execution"
type: reference
visibility: public
owning-repo: exeris-ai-execution
status: active
last-verified: 2026-09-19
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
| `docs-guardrails` | the bundle version in force, the same value as `repository_state.bundle_version` | `{suite: docs-mutation-v1, status: not-run, result: not-run}` | a harness, on a documentation row |
| `scb` | `1.3` | `{suite: oracle-selftest, status: not-run, result: not-run}` | a harness, on a construction row |

An id that is not in this table is not writable: the row would name a state no reader can look up.
Adding one is a change to this file, made in the same pull request as the producer that needs it.

§D.15's fourth oracle, `review-planted` on the `docs-review-calibration` domain, is absent for a
reason the rule above does not cover: there is no row to write it on. It judges the seeded corpus
of §D.18, and no seeded pull request exists, so no run of that domain has happened. The id enters
this table in the pull request that creates the corpus, carrying `review-planted-selftest-v1` and
the `not-run` status §D.17 leaves it at until that self-test passes as a suite.

## Why every status is `not-run`

None of the three suites has been run as a suite. `not-run` is not a pass (ADR-086 §E.19), the
schema admits no `TRUE_DONE` or `FALSE_DONE` on a row whose `oracle.calibration.status` is `fail` or
`not-run`, and so every row this repository can honestly hold today carries `UNKNOWN`, or
`UNREACHABLE` where a run never got far enough to be judged. `docs-mutation-v1` and
`oracle-selftest` are named in the calibration of rows that will one day carry their results; naming
a suite that has not run is how a row says which pass it is waiting for, and `result: not-run`
repeats the status because the field holds a score as it was published and no score was.

## Why `review-disposition` carries a version at all

`rest-v1` names what the oracle could read. §D.15 defines a finding as *addressed* when the file its
`location` names changed after the review **and its thread was resolved**; a disposition derived
through the host's REST API sees the first half and not the second, because the API does not expose
thread resolution and the publication it reads is one comment carrying no threads. That clause is
therefore empty under this version — the oracle applies what it can read and the version is what
says which definition was in force. A later implementation that can read resolution is a different
version, and rows either side of the change are not one population.
