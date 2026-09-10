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

Retention and the privacy boundary for event payloads are an open question in both RFC-2026-09-08
and RFC-2026-09-09, and both record it as a policy question rather than a schema one. It is not
answered here.

Until that boundary is written down, this directory operates under an explicit assumption: **only
rows whose `repository_state.repository` is a public repository land here.** It is an assumption,
not a rule derived from a decision — when the policy is written, it supersedes this paragraph.
