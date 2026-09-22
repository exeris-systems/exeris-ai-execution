# inbox/

The landing zone for records produced elsewhere, before this repository has a store to put
them in. Records arrive here; nothing here reads them.

It exists this early because the alternative is losing rows. Envelopes uploaded by CI are workflow
artefacts, and ninety days is GitHub's default retention — rows written before there is somewhere
durable to keep them are gone before V0 can collect them. Creating this inbox early, even if it
holds nothing else, is the answer.

## Convention

- **One file per record: a run at `inbox/<YYYY-MM-DD>/runs/<run_id>.json`, a judgement at
  `inbox/<YYYY-MM-DD>/judgements/<judgement_id>.json`, each dated in UTC by its own timestamp —
  `started_at` for a run, `judged_at` for a judgement.** One record per file so it can be added
  or corrected without rewriting its neighbours — corrected by filing a new record, never by editing
  the file in place, as the rule below requires; UTC so the directory a record lands in does not
  depend on where it was produced. The `runs/` and `judgements/` segments are what tell the
  validator which schema to read a file against; a discriminator field inside the file would have
  to be guessed at before the file could be validated.
- **One visibility per inbox.** A row that does not match belongs in the sibling repository's inbox,
  `exeris-ai-execution-enterprise`, which does not exist yet — ADR-018's public spec beside a private
  decoder, `exeris-benchmarks` beside `exeris-benchmarks-enterprise`. The rule itself is rule 1 of
  `## Rules a schema cannot see`.
- **Every file validates against its segment's schema — `../schemas/run-record.schema.json` or
  `../schemas/judgement-record.schema.json`.** A file that does not is not a record, it is a defect
  in the producer — it is reported back to the producer, never repaired here, for the reason the
  rule below gives for never rewriting a record.
- **Metadata only.** Prompts, file content and tool arguments do not enter this directory in any
  form, because `execution.event_stream` references the stream rather than carrying it, and that
  material may be customer or private-repository content. The artefact `event_stream.ref` points at
  is stored outside this directory for exactly that reason: the row carries its digest and its event
  count, the content stays elsewhere.
- **Records are appended, marked, never rewritten or deleted.** A correction is a new record and a
  dated fence; rows either side of a fence are never summarised in one figure. Deleting a record
  destroys the evidence that the instrument was once wrong, and editing one destroys it too — an
  edited record, repaired or rewritten, says what the editor later believed rather than what the
  run did.
- **Nothing here is interpreted.** No aggregation, no comparison across rows, no sentence about
  which model did better. That is V1 and later, and doing it here would break the V0 rule.

## Rules a schema cannot see

These five rules are what `tools/inbox_validate.py` enforces. They live here because JSON Schema can
express none of them — each is a rule across files, and a schema sees one document at a time — which
is the argument for having built the validator before anything analytical.

Which inbox this is, the validator reads from `inbox.json` — JSON, so the first piece of tooling
that has to read it needs nothing beyond the standard library. Not from the directory name, and not
from the git remote.

1. **Visibility matches the inbox.** Every row's `repository_state.visibility` equals the
   `visibility` in `inbox.json` — a row filed under the wrong visibility is published by the act
   of filing it, which is the one mistake here that cannot be corrected afterwards.
2. **`repository_state` is identical within a `group_id`.** The arms of a paired run share a
   repository state by definition — that is what makes them a comparison — so a group that spans
   two states, and therefore possibly two inboxes, is a group each validator would pass on its own
   half while the whole is uninterpretable.
3. **`human_baseline` is byte-for-byte identical within a `group_id`.** Divergence is not one row
   being wrong; it is a group that cannot be interpreted.
4. **`arm` is unique within a `group_id`.** Otherwise `arms_planned` sees a complete group while a
   condition is missing replicates.
5. **A judgement's `run_id` resolves to a run record in this inbox.** A judgement of a run nobody
   holds is not a judgement, only an assertion about one.

### The one exception to "records are appended, marked, never rewritten or deleted"

A row that lands here without matching the visibility `inbox.json` declares puts the two
conventions in collision; where that declared visibility is `public`, it is published already, by
the same commit that filed it. The visibility rule wins, and the remedy is not a tidy deletion:
remove the row, rewrite the history that carried it, and then treat the content as disclosed and
handle it as a disclosure — a force-push does not un-publish anything. Record the removal as a
dated fence and a marker row carrying the run's identifiers and no content, so the dataset still
shows that something was removed and why. What "appended, marked, never rewritten or deleted"
protects is the evidence that the instrument was once wrong, and that evidence survives in the
marker.

## What is not settled

The privacy boundary for event payloads: where exactly the line between metadata and content falls
is open, and ADR-086 §H.37 records it as a policy question rather than a schema one. It is not
answered here, and the conventions above do not depend on the answer.
