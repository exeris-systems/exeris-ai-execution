#!/usr/bin/env python3
"""The price-list validator — the rules `schemas/price-list.schema.json` cannot state.

A price list is instrument state (`price-lists/README.md`): every api-equivalent figure names the
list it came from, and a list is found by where it sits. So the rules that matter most are the ones
a schema reading one file in isolation cannot see — that the file's place agrees with what it says
about itself, that its tiers can be selected by a prompt size without ambiguity, and that its model
ids are spelled the way the rows and streams it will be joined to spell them.

Standard library only, for the reason `inbox_validate.py` gives: it runs where nothing is installed.
Schema conformance is checkable, not checked, here; the `schema` job of
`.github/workflows/inbox.yml` validates every list against the schema with `jsonschema` installed.

The rules:

1. A list lives at `price-lists/<vendor>/<effective_date>.json`, one directory deep.
2. The file is named for its own `effective_date`, and sits under its own `vendor`.
3. `price_list_id` is `<vendor>-<effective_date>`, so the label a derived figure carries resolves to
   exactly one file.
4. Each model's tiers have strictly ascending bounds and exactly one unbounded (`null`) tier, which
   is the last: otherwise some prompt size selects no tier, or two.
5. Every price is a non-negative decimal written as a string, which `decimal.Decimal` reads exactly.
6. Every model id satisfies the row contract's rule for `agent.model_id`, since a list is joined to
   rows and streams by that value and an id no row can carry prices nothing.
"""

import argparse
import json
import os
import sys
from decimal import Decimal, InvalidOperation

LISTS = "price-lists"
PRICE_CLASSES = ("input", "output", "cache_write_5m", "cache_write_1h", "cache_read")

# `agent.model_id` in `schemas/run-record.schema.json`: up to 128 characters from this set, the
# first and last alphanumeric. Read here character by character rather than by that pattern, so the
# check costs one pass over the value whatever the value is.
MODEL_ID_MAX = 128
ALNUM = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789")
MODEL_ID_CHARS = ALNUM | frozenset("._:/@+-")


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


def obj(node: object, key: str) -> dict:
    """The mapping under `key`, or an empty one: a malformed list is reported, not a traceback."""
    value = node.get(key) if isinstance(node, dict) else None
    return value if isinstance(value, dict) else {}


def is_model_id(value: object) -> bool:
    """Whether `value` is an id the row contract's `agent.model_id` admits."""
    if not isinstance(value, str) or not 0 < len(value) <= MODEL_ID_MAX:
        return False
    return value[0] in ALNUM and value[-1] in ALNUM and set(value) <= MODEL_ID_CHARS


def as_price(value: object) -> Decimal | None:
    """A price as an exact non-negative decimal, or None where the value is not one.

    Digits with at most one inner point and nothing else: no sign, no exponent, no `NaN`, and no
    JSON number, whose float reading is what a string exists to avoid.
    """
    if not isinstance(value, str) or not value.isascii():
        return None
    if value.startswith(".") or value.endswith(".") or not value.replace(".", "", 1).isdigit():
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


class Report:
    """Errors as GitHub annotations plus a summary, the shape every script in the org prints."""

    def __init__(self) -> None:
        self.bad: list[tuple[str, str]] = []
        self.files = 0

    def error(self, where: str, message: str) -> None:
        self.bad.append((where, message))

    def emit(self) -> int:
        for where, message in self.bad:
            print(f"::error file={where}::{message}")
        print(f"\n## price_list_validate\n\nChecked **{self.files}** list(s) — "
              f"**{len(self.bad)} error(s)**.")
        if self.bad:
            print("\n| File | Problem |\n|:--|:--|")
            for where, message in self.bad:
                print(f"| `{where}` | {message} |")
        return 1 if self.bad else 0


def list_files(top: str) -> list[tuple[str, tuple[str, ...]]]:
    """Every `.json` file under `top`, as (real path, its directory parts below `top`)."""
    out = []
    for here, _dirs, names in os.walk(top):
        parts = tuple(os.path.relpath(here, top).split(os.sep)) if here != top else ()
        for name in sorted(names):
            real = within(os.path.join(here, name), top)
            if name.endswith(".json") and real is not None:
                out.append((real, parts))
    return out


def check_place(path: str, parts: tuple[str, ...], body: dict, rep: Report) -> None:
    """Rules 1–3: where the file sits, what it is named, and the id it calls itself."""
    if len(parts) != 1:
        rep.error(path, "a list lives at `price-lists/<vendor>/<effective_date>.json`, one "
                        "directory deep — a list found anywhere else is selected by nothing")
        return
    vendor, date = body.get("vendor"), body.get("effective_date")
    stem = os.path.splitext(os.path.basename(path))[0]
    if stem != date:
        rep.error(path, f"the file is named `{stem}` and declares `effective_date: {date}` — "
                        f"a list is found by its date, so the name is the date")
    if parts[0] != vendor:
        rep.error(path, f"the file sits under `{parts[0]}/` and declares `vendor: {vendor}` — "
                        f"a list is found by its vendor, so the directory is the vendor")
    expected = f"{vendor}-{date}"
    if body.get("price_list_id") != expected:
        rep.error(path, f"`price_list_id` is `{body.get('price_list_id')}`, not `{expected}` — "
                        f"the label a derived figure carries must resolve to this one file")


def check_bounds(path: str, model: str, tiers: list, rep: Report) -> None:
    """Rule 4: bounds strictly ascending, one unbounded tier, and it last."""
    bounds = [t.get("max_prompt_tokens") if isinstance(t, dict) else None for t in tiers]
    finite = bounds[:-1]
    ascending = all(isinstance(b, int) and not isinstance(b, bool) for b in finite) and all(
        a < b for a, b in zip(finite, finite[1:]))
    if not tiers or bounds[-1] is not None or not ascending:
        rep.error(path, f"`{model}` has tier bounds {bounds} — they ascend strictly and only the "
                        f"last is `null`, or some prompt size selects no tier, or two")


def check_prices(path: str, model: str, tiers: list, rep: Report) -> None:
    """Rule 5: every price an exact non-negative decimal string."""
    for index, tier in enumerate(tiers):
        for name in PRICE_CLASSES:
            value = tier.get(name) if isinstance(tier, dict) else None
            if as_price(value) is None:
                rep.error(path, f"`{model}` tier {index} `{name}` is {value!r} — a price is a "
                                f"non-negative decimal written as a string, read exactly")


def check_models(path: str, body: dict, rep: Report) -> None:
    """Rules 4–6, model by model."""
    for model, entry in obj(body, "models").items():
        if not is_model_id(model):
            rep.error(path, f"`{model}` is not an id `agent.model_id` admits — a list is joined "
                            f"to rows by that value, and an id no row can carry prices nothing")
        tiers = entry.get("tiers") if isinstance(entry, dict) else None
        tiers = tiers if isinstance(tiers, list) else []
        check_bounds(path, model, tiers, rep)
        check_prices(path, model, tiers, rep)


def check(root: str, rep: Report) -> None:
    top = os.path.join(root, LISTS)
    if not os.path.isdir(top):
        rep.error(top, "no `price-lists/` directory — there is nothing a figure could name")
        return
    found = list_files(top)
    rep.files = len(found)
    for path, parts in found:
        try:
            with open(path, encoding="utf-8") as fh:
                body = json.load(fh)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            rep.error(path, f"not JSON, so it is not a list: {exc}")
            continue
        if not isinstance(body, dict):
            rep.error(path, "not a JSON object, so it is not a list")
            continue
        check_place(path, parts, body, rep)
        check_models(path, body, rep)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--root", type=existing_directory, default=".",
                   help="the repository holding `price-lists/`")
    args = p.parse_args()
    rep = Report()
    check(args.root, rep)
    return rep.emit()


if __name__ == "__main__":
    sys.exit(main())
