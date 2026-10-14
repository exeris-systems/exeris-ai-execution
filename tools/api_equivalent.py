#!/usr/bin/env python3
"""The api-equivalent figure of one run — derived at analysis time, never written anywhere.

What a run would have been billed under the vendor's API price list, computed from the requests its
event stream records and the list in force at the row's `started_at`. It is an imputed figure, which
is exactly what ADR-086 §C.14 keeps off a row, so this tool reads a row and a stream and prints to
stdout; it writes into neither, nor into the inbox. The rules it follows are in
`price-lists/README.md`, and these are the ones that shape the code:

- **Per request, not per row.** A tiered model prices a request by that request's prompt size —
  its input, cache-read and cache-write tokens together — so a figure from a row's totals is wrong
  whenever some requests sit above a tier bound and some below. A request is one `message.id`: the
  client writes one request as several `assistant` lines, one per content block, each repeating the
  same usage, and it is counted once.
- **Two cache-write lifetimes, two prices.** The five-minute and one-hour writes are priced apart,
  so a request that wrote cache without saying which lifetime cannot be priced, and is refused
  rather than guessed at. So is a request whose model the list does not price, and a request id
  repeated with a different usage.
- **The list names the figure.** The output carries `price_list_id` and the list's calibration
  status, and the figure is quotable only where that status is `pass`. No key says `cost`: this is
  not a cost anybody paid, and it never sums with `provider_reported_cost`, which this tool does not
  read.

`--stream` accepts the inbox's published stream or the client's own session log, which carry the
same events; `stream_sha256_matches_row` says whether the bytes read are the ones the row hashed.

Usage: api_equivalent.py --row ROW.json --stream STREAM.jsonl [--price-lists DIR]
"""

import argparse
import datetime
import hashlib
import json
import os
import sys
from decimal import Decimal, InvalidOperation

HERE = os.path.dirname(os.path.abspath(__file__))
MILLION = Decimal(1_000_000)
CLASSES = ("input", "output", "cache_write_5m", "cache_write_1h", "cache_read")
SLUG_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-")
# The client writes this as the model of an assistant line no model produced; it carries no usage.
SYNTHETIC = "<synthetic>"


class Refusal(Exception):
    """A run this tool will not price, and why — reported, never replaced by a guess."""


def existing_file(value: str) -> str:
    """An `argparse` `type=` admitting only a file already on disk, as its real path."""
    resolved = os.path.realpath(value)
    if not os.path.isfile(resolved):
        raise argparse.ArgumentTypeError(f"{value!r} is not a file")
    return resolved


def existing_directory(value: str) -> str:
    """An `argparse` `type=` admitting only a directory already on disk, as its real path."""
    resolved = os.path.realpath(value)
    if not os.path.isdir(resolved):
        raise argparse.ArgumentTypeError(f"{value!r} is not a directory")
    return resolved


def within(path: str, root: str) -> str | None:
    """`path`'s real path where it lies strictly inside `root`'s, else None."""
    real, top = os.path.realpath(path), os.path.realpath(root)
    if os.path.commonprefix((real, top)) != top or not real.startswith(top + os.sep):
        return None
    return real


def listed_child(directory: str, name: str) -> str | None:
    """`name` under `directory`, only once a listing of `directory` has named it."""
    if name not in set(os.listdir(directory)):
        return None
    return within(os.path.join(directory, name), directory)


def is_slug(value: object) -> bool:
    """Whether `value` is spelled as `agent.provider` and a list's `vendor` are."""
    return (isinstance(value, str) and 0 < len(value) <= 64 and set(value) <= SLUG_CHARS
            and not value.startswith("-") and not value.endswith("-") and "--" not in value)


def as_date(stem: str) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(stem)
    except ValueError:
        return None


def usd(value: Decimal) -> str:
    """An exact decimal string, never in exponent form."""
    return format(value.normalize(), "f")


# ---- the row -------------------------------------------------------------------------------------

def read_row(path: str) -> dict:
    """The three things a row contributes: its id, its vendor, and the UTC day it started."""
    with open(path, encoding="utf-8") as fh:
        row = json.load(fh)
    if not isinstance(row, dict):
        raise Refusal("the row is not a JSON object, so it is not a run record")
    agent = row.get("agent") if isinstance(row.get("agent"), dict) else {}
    vendor = agent.get("provider")
    if not is_slug(vendor):
        raise Refusal(f"the row's `agent.provider` is {vendor!r}, which names no price-list vendor")
    try:
        started = datetime.datetime.fromisoformat(str(row.get("started_at")))
    except ValueError as exc:
        raise Refusal(f"the row's `started_at` is not a timestamp: {exc}") from exc
    if started.tzinfo is None:
        raise Refusal("the row's `started_at` carries no offset, so its day is unknown")
    stream = (row.get("execution") or {}).get("event_stream") or {}
    return {"run_id": row.get("run_id"), "vendor": vendor,
            "day": started.astimezone(datetime.timezone.utc).date(),
            "stream_sha256": stream.get("sha256")}


# ---- the list ------------------------------------------------------------------------------------

def list_in_force(lists: str, vendor: str, day: datetime.date) -> dict:
    """The vendor's list with the latest `effective_date` not after `day`."""
    vendor_dir = listed_child(lists, vendor)
    if vendor_dir is None or not os.path.isdir(vendor_dir):
        raise Refusal(f"there is no price list for vendor `{vendor}`")
    dated = {as_date(name[:-len(".json")]): name for name in os.listdir(vendor_dir)
             if name.endswith(".json")}
    eligible = [d for d in dated if d is not None and d <= day]
    if not eligible:
        raise Refusal(f"no `{vendor}` price list is in force on {day.isoformat()} — the run "
                      f"predates every list, and an older run is not priced by a newer list")
    path = listed_child(vendor_dir, dated[max(eligible)])
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def tier_label(tiers: list, index: int) -> str:
    bound = tiers[index]["max_prompt_tokens"]
    if bound is not None:
        return f"prompt<={bound}"
    return f"prompt>{tiers[index - 1]['max_prompt_tokens']}" if index else "all"


def pick_tier(tiers: list, prompt: int) -> int:
    """The first tier whose inclusive bound the prompt does not exceed."""
    for index, tier in enumerate(tiers):
        if tier["max_prompt_tokens"] is None or prompt <= tier["max_prompt_tokens"]:
            return index
    raise Refusal(f"no tier prices a prompt of {prompt} tokens — the list's last tier is bounded")


# ---- the stream ----------------------------------------------------------------------------------

def stream_lines(path: str):
    """Each event of an NDJSON stream, with its line number."""
    with open(path, encoding="utf-8") as fh:
        for number, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                yield number, json.loads(line)
            except json.JSONDecodeError as exc:
                raise Refusal(f"line {number} of the stream is not JSON: {exc}") from exc


def requests(path: str) -> dict[str, tuple[str, dict]]:
    """Every request in the stream, once each, as id → (model, usage)."""
    seen: dict[str, tuple[str, dict]] = {}
    for number, event in stream_lines(path):
        message = event.get("message") if event.get("type") == "assistant" else None
        if not isinstance(message, dict) or not isinstance(message.get("usage"), dict):
            continue
        rid, model, usage = message.get("id"), message.get("model"), message["usage"]
        if not isinstance(rid, str) or not rid:
            raise Refusal(f"line {number} carries usage under no `message.id`, so it cannot be "
                          f"told apart from the request it may repeat")
        if rid in seen and seen[rid] != (model, usage):
            raise Refusal(f"request `{rid}` appears twice with different usage — which one was "
                          f"billed is not something this tool can know")
        seen[rid] = (model, usage)
    return seen


def count(usage: dict, key: str) -> int:
    value = usage.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise Refusal(f"`usage.{key}` is {value!r}, not a token count")
    return value


def tokens_of(rid: str, usage: dict) -> dict[str, int]:
    """The request's tokens by price class, with the cache write split by its lifetime."""
    written = count(usage, "cache_creation_input_tokens")
    split = usage.get("cache_creation")
    if not isinstance(split, dict):
        if written:
            raise Refusal(f"request `{rid}` wrote {written} cache tokens and does not say for how "
                          f"long — five-minute and one-hour writes are priced apart")
        split = {}
    five = count(split, "ephemeral_5m_input_tokens")
    hour = count(split, "ephemeral_1h_input_tokens")
    if five + hour != written:
        raise Refusal(f"request `{rid}` splits its cache write as {five} + {hour}, which is "
                      f"not the {written} it wrote")
    return {"input": count(usage, "input_tokens"), "output": count(usage, "output_tokens"),
            "cache_write_5m": five, "cache_write_1h": hour,
            "cache_read": count(usage, "cache_read_input_tokens")}


# ---- the figure ----------------------------------------------------------------------------------

def price(value: object) -> Decimal:
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise Refusal(f"the list carries {value!r} where a price belongs") from exc


class Tally:
    """Running sums by tier and by price class, in exact decimals."""

    def __init__(self) -> None:
        self.total = Decimal(0)
        self.requests = 0
        self.by_tier: dict[str, dict[str, dict]] = {}
        self.by_class = {c: {"tokens": 0, "usd": Decimal(0)} for c in CLASSES}

    def add(self, model: str, label: str, tokens: dict[str, int], tier: dict) -> None:
        figure = Decimal(0)
        for cls in CLASSES:
            part = price(tier[cls]) * tokens[cls] / MILLION
            self.by_class[cls]["tokens"] += tokens[cls]
            self.by_class[cls]["usd"] += part
            figure += part
        slot = self.by_tier.setdefault(model, {}).setdefault(
            label, {"requests": 0, "usd": Decimal(0)})
        slot["requests"] += 1
        slot["usd"] += figure
        self.total += figure
        self.requests += 1


def price_requests(found: dict[str, tuple[str, dict]], models: dict) -> Tally:
    tally = Tally()
    for rid, (model, usage) in sorted(found.items()):
        tokens = tokens_of(rid, usage)
        if model == SYNTHETIC and not any(tokens.values()):
            continue
        entry = models.get(model) if isinstance(model, str) else None
        if not isinstance(entry, dict):
            raise Refusal(f"request `{rid}` ran on `{model}`, which the list does not price")
        tiers = entry["tiers"]
        prompt = tokens["input"] + tokens["cache_read"] + tokens["cache_write_5m"] + \
            tokens["cache_write_1h"]
        index = pick_tier(tiers, prompt)
        tally.add(model, tier_label(tiers, index), tokens, tiers[index])
    return tally


def sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 16), b""):
            digest.update(block)
    return digest.hexdigest()


def derive(row_path: str, stream_path: str, lists: str) -> dict:
    row = read_row(row_path)
    price_list = list_in_force(lists, row["vendor"], row["day"])
    tally = price_requests(requests(stream_path), price_list.get("models") or {})
    status = (price_list.get("calibration") or {}).get("status")
    return {
        "run_id": row["run_id"],
        "price_list_id": price_list.get("price_list_id"),
        "calibration_status": status,
        "quotable": status == "pass",
        "api_equivalent_usd": usd(tally.total),
        "requests": tally.requests,
        "by_tier": {m: {label: {"requests": s["requests"], "api_equivalent_usd": usd(s["usd"])}
                        for label, s in tiers.items()} for m, tiers in tally.by_tier.items()},
        "by_class": {c: {"tokens": v["tokens"], "api_equivalent_usd": usd(v["usd"])}
                     for c, v in tally.by_class.items()},
        "stream_sha256_matches_row": sha256_of(stream_path) == row["stream_sha256"],
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--row", type=existing_file, required=True, help="a run record")
    p.add_argument("--stream", type=existing_file, required=True,
                   help="the run's event stream, as published or as the client's session log")
    p.add_argument("--price-lists", type=existing_directory,
                   default=os.path.join(os.path.dirname(HERE), "price-lists"),
                   help="the root holding `<vendor>/<effective_date>.json`")
    args = p.parse_args()
    try:
        out = derive(args.row, args.stream, args.price_lists)
    except (Refusal, OSError, json.JSONDecodeError) as exc:
        print(f"::error title=api_equivalent::{exc}", file=sys.stderr)
        return 1
    print(json.dumps(out, indent=2, sort_keys=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
