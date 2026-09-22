---
title: "Fences in force for exeris-ai-execution"
type: reference
visibility: public
owning-repo: exeris-ai-execution
status: active
last-verified: 2026-09-19
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

## The grammar of a fence id

`<date>-<producer>-cc-<harness version, its dots written as dashes>`

- `<date>` — the day the fence was written, `YYYY-MM-DD`, which the contract's own pattern requires
  first.
- `<producer>` — what wrote the rows the fence marks, named by what it is rather than by what ran
  it: `ci-backfill`, `ci-live`, `harness-claude`.
- `cc-<version>` — the client and the version of it the rows were produced under, `2.1.274` written
  `2-1-274`.

The dots are written as dashes because `instrument.fence`'s pattern is a date followed by lower-case
alphanumeric segments separated by hyphens, and it admits no dot anywhere. A version spelled the way
its vendor spells it would be refused by the contract, so the id says the same thing in the alphabet
the contract admits, and says it the same way every time — a fence id that has two spellings joins
to neither half of the rows it marks.

One client version is one fence: a producer that ran under three client versions writes three ids
and three entries, because the client is part of the model reference (`agent.harness`) and a change
to it is a change to the run's conditions.

The first entry above carries no `cc-` segment, and that is not a shortening: no client ran. A fence
written for the contract itself names the contract and the version it moved to, because there is no
producer and no client version to name, and inventing one would make the id resolve to a run that
never happened.
