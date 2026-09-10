# exeris-ai-execution

The provider-neutral AI execution / workload-intelligence layer. Today it captures runs: one
workload, attempted by one agent, against one repository state, judged by one oracle — one row.

**The V0 rule: this repository observes and does not route — it emits no routing decision and no
model-selection recommendation, not even "model X appears appropriate for this workload".**

The rule is about output, not about automation: capture may be fully automatic, and still nothing
here may say which model to use. Everything else in this repository follows from it.

## What is here

- `schemas/run-record.schema.json` — the record shape, transcribed field for field from the table
  "What a run record must carry" in RFC-2026-09-08. Every component of that table is required,
  because the table's third column is a list of things that cannot be added later.
- `inbox/` — the landing zone for rows produced elsewhere, before there is a store to put them in.
  See `inbox/README.md` for its convention.

That is all there is today, and deliberately so: no store, no analysis, no oracle implementation,
no router. A capture shape that has never met a real row is the thing most likely to be wrong, so
the shape ships first and alone.

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
