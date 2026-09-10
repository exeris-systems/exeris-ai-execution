# inbox/

The landing zone for run records produced elsewhere, before this repository has a store to put
them in. Rows arrive here; nothing here reads them.

It exists this early because the alternative is losing rows. Envelopes uploaded by CI are workflow
artefacts, and ninety days is GitHub's default retention — rows written before there is somewhere
durable to keep them are gone before V0 can collect them. RFC-2026-09-09 §Risks names creating this
inbox early, "even if it holds nothing else", as the answer.

## Convention

- **One file per run, `inbox/<YYYY-MM-DD>/<run_id>.json`, dated by `started_at` in UTC.** One run
  per file so a row can be added, corrected or quarantined without rewriting its neighbours; UTC so
  the directory a row lands in does not depend on where it was produced.
- **Public rows only.** Every row here carries `repository_state.visibility: public`; an
  `enterprise-private` row belongs in the private inbox of the sibling repository
  `exeris-ai-execution-enterprise`, which does not exist yet — ADR-018's public spec beside a private
  decoder, `exeris-benchmarks` beside `exeris-benchmarks-enterprise`. The schema and the tooling stay
  public because a contract is public; it is the rows that visibility protects. The rule is
  machine-checkable but **not yet machine-checked**: nothing in this repository validates an inbox,
  and a JSON Schema cannot, because a schema does not know which repository it is being validated in.
  An inbox validator is therefore the first tooling this repository needs.
- **Every file validates against `../schemas/run-record.schema.json`.** A file that does not is not
  a row, it is a defect in the producer — it is reported back to the producer, never repaired here,
  because a repaired row records what the fixer believed rather than what the run did.
- **Metadata only.** Prompts, file content and tool arguments do not enter this directory in any
  form, because `execution.event_stream` references the stream rather than carrying it, and that
  material may be customer or private-repository content. The artefact `event_stream.ref` points at
  is stored outside this directory for exactly that reason: the row carries its digest and its event
  count, the content stays elsewhere.
- **Rows are marked, never deleted.** A correction is a new row and a dated fence; rows either side
  of a fence are never summarised in one figure. Deleting a row destroys the evidence that the
  instrument was once wrong.
- **Nothing here is interpreted.** No aggregation, no comparison across rows, no sentence about
  which model did better. That is V1 and later, and doing it here would break the V0 rule.

## What is not settled

Retention and the privacy boundary for event payloads. How long the artefact `event_stream.ref`
points at is kept, and where exactly the line between metadata and content falls, are open in both
RFC-2026-09-08 and RFC-2026-09-09, and both record them as a policy question rather than a schema
one. Neither is answered here, and the conventions above do not depend on the answer.
