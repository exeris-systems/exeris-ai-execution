# price-lists/

A vendor's published unit prices, one file per vendor per date:
`price-lists/<vendor>/<effective_date>.json`, shaped by `../schemas/price-list.schema.json`.

## A price list is instrument state

A price list is not data about any run. It is part of the instrument a figure is read through, in
the sense ADR-086 §E.20 gives instrument state: two figures derived from two different lists measure
under different conditions, and the list's id travels with every figure derived from it so that
nobody has to guess which conditions those were.

A run row stays USD-free (ADR-086 §C.14). Under a subscription there is no per-run price; a figure
computed from a list is imputed, not reported, and no row carries one. What a list makes possible is
a figure derived **at analysis time** and kept out of every row.

## The api-equivalent figure

What a run would have been billed under the vendor's API prices. `tools/api_equivalent.py` derives
it, and these are its rules:

- **The list in force.** A row is priced by its vendor's list with the latest `effective_date` not
  after the UTC day of the row's `started_at`. A row older than every list is not priced: a newer
  list does not reach back.
- **From the event stream, per request.** The stream records each request's usage, and a request is
  one `message.id` however many lines the client wrote it as. A tiered model prices a request by
  that request's prompt size — input, cache-read and cache-write tokens together — so the figure is
  never computed from a row's totals.
- **Each price class at its own price.** Input, output, cache read, and the two cache-write
  lifetimes — five minutes and one hour — which the vendor prices apart. A request that wrote cache
  without saying for how long is refused, and so is a request on a model the list does not price.
  Nothing is guessed.
- **Labelled, and never called a cost.** The figure carries the `price_list_id` it was derived from
  and that list's calibration status. No name it travels under says `cost`, and it is never summed
  with `provider_reported_cost`: one is a vendor's bill, the other a computation, and a total of the
  two is neither.
- **Quotable only from a calibrated list.** A list's `calibration` records whether its figures have
  been shown to agree with what the vendor bills — paired arms on one task, one under API billing
  and one under a subscription. Until a list's status is `pass`, a figure derived from it may be
  computed and inspected, and is not quoted as what a run would have cost.

## Adding a list

A new list is a new file, written when the vendor's prices change or when a list for a new vendor is
needed: `price-lists/<vendor>/<effective_date>.json`, `price_list_id` set to
`<vendor>-<effective_date>`, `source.url` the page the prices were read from and
`source.retrieved_at` the day they were read.

**A published list is never edited.** A figure already derived names the list it came from, and an
edited list would make that name point at different prices. A correction is therefore a new list
with its own `effective_date` — the day the correction takes effect for derivation — and the list it
corrects stays where it is. A new list changes the conditions figures are read under in the way a
fence does, so figures either side of it are not compared as if they were one measurement.

Prices are decimal strings, not JSON numbers: a price is exact, and a number is read by most parsers
as a binary float in which `0.10` is not one tenth. Read them with `decimal.Decimal`.

`tools/price_list_validate.py` checks what the schema cannot — the file's place against its
`vendor` and `effective_date`, the id against both, tier order, prices and model ids — and
`tools/price_list_validate_suite.py` is its case suite.
