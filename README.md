# exeris-ai-execution

The provider-neutral AI execution / workload-intelligence layer. Today it captures runs: one
workload, attempted by one agent, against one repository state, judged by one oracle — one row.

**The V0 rule: this repository observes and does not route — it emits no routing decision and no
model-selection recommendation, not even "model X appears appropriate for this workload".**

The rule is about output, not about automation: capture may be fully automatic, and still nothing
here may say which model to use. Everything else in this repository follows from it.

## What is here

- `schemas/run-record.schema.json` — the record shape, transcribed from the table "What a run record
  must carry" in RFC-2026-09-08, every component of which is required because that table's third
  column lists what cannot be added later. Plus a small, marked set of additions — `run_id`,
  `started_at`, `repository_state.repository`, `repository_state.visibility`, `pairing` — each
  carrying a "Not from the RFC table" note in its own description, and each a condition either of
  one of the table's own disciplines being executable, or of a row being placeable in the right
  inbox without leaking what it was.
- `inbox/` — the landing zone for rows produced elsewhere, before there is a store to put them in.
  See `inbox/README.md` for its convention.

That is all there is today, and deliberately so: no store, no analysis, no oracle implementation,
no router. A capture shape that has never met a real row is the thing most likely to be wrong, so
the shape ships first and alone.

## The paired-run protocol

Paired runs — the same task across N models — are the primary collection mode, because they answer
the one useful question at a fraction of the n that observational rows across heterogeneous tasks
would need. A group is declared before any of its arms runs, under a `pairing.group_id` assigned in
advance; it is never recovered afterwards by grouping rows on `workload.fingerprint`. A group
assigned in advance is an artefact of the design, and a group recovered by a query is a query.

**The human arm runs first**, for two reasons. The baseline is then already known when each model
row is written, so no row has to be rewritten later — which `inbox/` forbids anyway. And the human
has not seen a model's output, so the baseline is not contaminated by it. A group with no human arm
declares `pairing.baseline: none`; it is a legitimate group, and it never carries an economic claim.

## For producers of rows

- **`agent.system_prompt_sha256` hashes the instructions the repository controls** — the workflow's
  prompt text, the routine file it points at, and the agent files at that commit. Not the client's
  own system prompt, which a producer usually cannot read and whose changes `harness.version`
  proxies imperfectly rather than replaces — a stated hole in the model reference, not a covered
  case.
- **The artefact `execution.event_stream.ref` points at is stored outside `inbox/`.** It may contain
  prompts, file content and tool arguments; the row carries only its digest and its event count.
- **A runner may report a USD figure under a subscription** by applying a price list to the token
  counts. That figure is imputed rather than reported, the schema rejects it on a non-`api` row, and
  the producer is expected to drop it deliberately rather than never to have looked for it.
- **`repository_state.visibility` is ADR-020's taxonomy, not the host's.** The producer maps
  `gh repo view --json visibility` onto it fail-closed: anything it cannot establish as `PUBLIC`
  becomes `enterprise-private`.
- **A producer with access to the task registry writes `reg:` fingerprints; the CI bot, which has
  none, writes `ci:` keyed digests over `(repository, pull request, head sha)`.** The prefix is
  part of the value, so the two never join silently.

## Where the decisions are

- `exeris-docs/rfc/RFC-2026-09-08-ai-execution-layer.md` — accepted 2026-09-09. It settles the
  oracle V0 observes against (the existing documentation and agent-layer guardrail suite, behind an
  oracle interface the System Construction Benchmark implements later as a second provider), the
  first domain, and the preregistration discipline the dataset carries from its first row.
- `exeris-docs/adr-index.md` — **ADR-086** is reserved there, content pending. It will fix this
  layer's boundary against ADR-025 (`exeris-ai-bridge` is a context adapter, not a host) and
  against the agent bundle (a telemetry sink contract in the hook dispatcher, not a second place
  rules live).
- `exeris-docs/rfc/RFC-2026-09-09-exeris-bot-review-publication-and-run-capture.md` — draft. It is
  the producer of the rows that land in `inbox/`.

Referenced by path rather than by URL: these are sibling repositories in one workspace, and a path
resolves in a clone with no network.

## The direction of the seam

```text
exeris-agents  ──normalized events──▶  Telemetry Sink Contract  ──▶  exeris-ai-execution
```

The agent layer owns event semantics; this layer owns their interpretation — and it is not
permitted to interpret yet.

## Status

V0, before the first row.

The docs oracle's mutation suite has never been run as a suite. Seven of its eight mutants have
been exercised individually in the course of the agent-layer work; the eighth — an empty or absent
corpus reported as clean, the one mutant that catches the instrument rather than the target — has
never been run at all. Under fail-closed accounting an oracle whose `PASS` has never been
contradicted by a known-broken input is unvalidated, so no outcome this repository can honestly
record today is a pass: every row is `UNKNOWN`, or `UNREACHABLE` where a run never got far enough
to be judged. The schema enforces exactly that — a row whose `oracle.calibration.status` is `fail`
or `not-run` cannot carry `TRUE_DONE` or `FALSE_DONE`.

Private rows do not land in this repository's `inbox/`: `exeris-ai-execution-enterprise` will hold
them, and it does not exist yet — which is fine while V0's domain is documentation in public
repositories, and it is to be created early for the same reason `inbox/` was.

`fingerprint` values of the `reg:` class come from a private task registry that does not exist
yet. It is the first thing `exeris-ai-execution-enterprise` must hold, and it is what makes that
repository needed before the first *planned pair* rather than before the first enterprise row,
because a group is declared before its arms run — including a group whose repository is public.

## Open questions

- **The oracle for the review domain.** The L1 gates judge a pull request, not the reviewer that
  reviewed it, so `docs-guardrails` on a review row does not mean what it means on a sweep row.
  `workload.domain` keeps the two apart from the first row; which oracle judges a review is
  undecided. It sits beside the sink question, not behind it.
- **The telemetry sink contract.** Its payload, its versioning and which bundle version ships it are
  open in RFC-2026-09-08.
- **Whether a CI runner emits an execution log usable as `execution.event_stream`.** Unverified. It
  needs one real run — like the question in RFC-2026-09-09 about whether a hosted action honours
  `.claude/settings.json` hooks.
- **Retention and the privacy boundary for event payloads.** How long the artefact
  `execution.event_stream.ref` points at is kept, and where the line between metadata and content
  falls, are the open part, and a policy question in both RFCs. What this repository runs under
  meanwhile is no longer an assumption: `inbox/README.md` carries it as a rule, and its `## Rules a
  schema cannot see` specifies the inbox validator that will check it.
