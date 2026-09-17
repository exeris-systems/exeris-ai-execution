---
title: "Review rules for exeris-ai-execution"
type: reference
visibility: public
owning-repo: exeris-ai-execution
status: active
last-verified: 2026-09-17
---

# Review rules for `exeris-ai-execution`

The `repo-routine` extension of `exeris-systems/.github`'s `docs-guardrails-review.md`, applied
**after** its steps and under its severity tags, output format and verdict schema. It adds checks and
raises severities; it lowers nothing and skips nothing. One review, one verdict, one publisher — the
extension exists so that having rules of one's own is not a reason to keep a review of one's own.

## What this repository is answerable for

The dataset's contract. Three things live here and nothing else does: the schemas that say what a row
means, the validator that enforces what no single schema can express, and the inbox that holds rows.
A reviewer applying only the shared routine can judge this repository's prose and its pull request
bodies, and can say nothing about any of the three.

## Step R — rules of this repository

R1. **A field's description is the home for its meaning.** ADR-086's cross-references say so: where
    that record and these schemas disagree about what a field means, the schema's description wins
    and the ADR is amended. So a change to `schemas/*.json` that alters a description, type, pattern
    or required-ness carries the ADR clause or amendment it implements — name it in the PR body or
    in the diff → else `[CONTRACT]`. A description that **contradicts** a clause of ADR-086 or
    ADR-087 → `[HARD BLOCK]`.

R2. **A validator rule arrives with a case that can fail.** A rule added to or changed in
    `tools/inbox_validate.py` without a case in `tools/inbox_validate_suite.py` → `[HARD BLOCK]`.
    The suite is the whole of the difference between a rule and a comment: a rule nothing can fail
    on is not enforced, it is described.

R3. **The suite's count in the body matches the suite.** `REPOSITORY CHECK OUTPUT` carries what the
    suite actually reported. A *Verification* section whose case count disagrees with it →
    `[STYLE]`; one claiming cases that do not exist → `[HARD BLOCK]`.

R4. **A row is appended, marked, never rewritten or deleted** (ADR-086 §E.20, and `inbox/README.md`
    in the same words). A diff that modifies or removes a file under `inbox/` rather than adding one
    → `[HARD BLOCK]`, whatever the pull request body says the reason is. A correction is a new row
    and a fence.

R5. **`inbox.json` is the inbox's identity.** A change to its declared visibility routes every row
    in this inbox somewhere else → `[HARD BLOCK]` unless an accepted ADR clause in the same pull
    request says so.

R6. **Nothing here names a model as appropriate for a workload** (ADR-086 Engineering Protocol 8,
    binding until V3). Any artefact — schema description, rule comment, README line, pull request
    body — that says or implies one model suits a kind of task → `[HARD BLOCK]`. This layer observes
    before it routes, and the first sentence that forgets it is the one that matters.

## Where this does not apply, and what it costs

Not to the shared routine's own steps: PR body, records, commits and hygiene are judged by
`docs-guardrails-review.md` and are not restated here — a rule in two places drifts in one of them.
Not to `exeris-ai-execution-enterprise`, whose inbox is private and whose rules are its own.

The cost is that these rules are prose a reviewer applies, not a program: R1 and R6 in particular are
judgement, and a reviewer that reads them loosely enforces them loosely. R2, R3, R4 and R5 are
mechanical and belong in the validator or in CI the moment either can express them; until then this
file is the honest place for them, and "checkable, not checked" is the state to say out loud.
