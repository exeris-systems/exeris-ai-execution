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

## Rules a schema cannot see

These four rules are the specification of the inbox validator, which does not exist. They live
here because until it does they have no other home — and they are the argument for building it
before anything analytical: JSON Schema can express none of them, because each is a rule across
files and a schema sees one document at a time.

Which inbox this is, the validator reads from `inbox.yaml`. Not from the directory name, and not
from the git remote.

1. **Visibility matches the inbox.** Every row's `repository_state.visibility` equals the
   `visibility` in `inbox.yaml` — a row filed under the wrong visibility is published by the act
   of filing it, which is the one mistake here that cannot be corrected afterwards.
2. **`repository_state` is identical within a `group_id`.** The arms of a paired run share a
   repository state by definition — that is what makes them a comparison — so a group that spans
   two states, and therefore possibly two inboxes, is a group each validator would pass on its own
   half while the whole is uninterpretable.
3. **`human_baseline` is byte-for-byte identical within a `group_id`.** Divergence is not one row
   being wrong; it is a group that cannot be interpreted.
4. **`arm` is unique within a `group_id`.** Otherwise `arms_planned` sees a complete group while a
   condition is missing replicates.

### The one exception to "rows are marked, never deleted"

A row that lands here marked `enterprise-private` is public from the moment it is committed, and
the two conventions collide. The visibility rule wins, and the remedy is not a tidy deletion:
remove the row, rewrite the history that carried it, and then treat the content as disclosed and
handle it as a disclosure — a force-push does not un-publish anything. Record the removal as a
dated fence and a marker row carrying the run's identifiers and no content, so the dataset still
shows that something was removed and why. What "marked, never deleted" protects is the evidence
that the instrument was once wrong, and that evidence survives in the marker.

## What is not settled

Retention and the privacy boundary for event payloads. How long the artefact `event_stream.ref`
points at is kept, and where exactly the line between metadata and content falls, are open in both
RFC-2026-09-08 and RFC-2026-09-09, and both record them as a policy question rather than a schema
one. Neither is answered here, and the conventions above do not depend on the answer.
