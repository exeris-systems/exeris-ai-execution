#!/usr/bin/env python3
"""Cases for `price_list_validate.py` — one per rule, each shown to go red.

Every case builds a `price-lists/` tree on disk from one conforming list with one thing changed,
runs the validator over it, and asserts the specific refusal. A rule whose case still passes with
the rule deleted is not covered, so each case asserts the rule's own words, not merely a non-zero
exit some other rule could produce.
"""

import copy
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
VALIDATOR = os.path.join(HERE, "price_list_validate.py")
REPO = os.path.dirname(HERE)

CASES: list[tuple[str, object]] = []


def case(name):
    def register(fn):
        CASES.append((name, fn))
        return fn
    return register


def tier(bound, price="1") -> dict:
    return {"max_prompt_tokens": bound, "input": price, "output": price,
            "cache_write_5m": price, "cache_write_1h": price, "cache_read": price}


def price_list() -> dict:
    return {
        "price_list_id": "acme-2026-01-01", "vendor": "acme", "effective_date": "2026-01-01",
        "source": {"url": "https://example.invalid/pricing", "retrieved_at": "2026-01-01"},
        "unit": "USD per 1,000,000 tokens",
        "calibration": {"suite": "none", "status": "not-run", "result": "none"},
        "models": {"m-1": {"tiers": [tier(1000, "0.10"), tier(None, "0.50")]}},
    }


def write(root: str, body: object, *parts: str) -> None:
    path = os.path.join(root, "price-lists", *parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body if isinstance(body, str) else json.dumps(body))


def validate(root: str) -> tuple[int, str]:
    out = subprocess.run([sys.executable, VALIDATOR, "--root", root],
                         capture_output=True, text=True)
    return out.returncode, out.stdout + out.stderr


def refused(root: str, body: dict, words: str, *parts: str) -> None:
    """Write `body` (at the conforming place unless `parts` says otherwise) and expect `words`."""
    write(root, body, *(parts or ("acme", "2026-01-01.json")))
    code, text = validate(root)
    assert code == 1, text
    assert words in text, text


def with_tiers(tiers: list) -> dict:
    body = price_list()
    body["models"]["m-1"]["tiers"] = tiers
    return body


@case("a conforming list is silent")
def _(root):
    write(root, price_list(), "acme", "2026-01-01.json")
    code, text = validate(root)
    assert code == 0, text
    assert "Checked **1** list(s) — **0 error(s)**" in text, text


@case("the lists this repository publishes conform")
def _(_root):
    code, text = validate(REPO)
    assert code == 0, text
    assert "0 error(s)" in text, text


@case("the synthetic lists the api-equivalent suite prices by conform too")
def _(_root):
    code, text = validate(os.path.join(HERE, "fixtures", "price"))
    assert code == 0, text
    assert "Checked **2** list(s) — **0 error(s)**" in text, text


@case("rule 1 — a list outside a vendor directory is selected by nothing")
def _(root):
    refused(root, price_list(), "one directory deep", "2026-01-01.json")


@case("rule 1 — nor is one two directories deep")
def _(root):
    refused(root, price_list(), "one directory deep", "acme", "old", "2026-01-01.json")


@case("rule 2 — the name is the date")
def _(root):
    refused(root, price_list(), "so the name is the date", "acme", "2026-01-02.json")


@case("rule 2 — the directory is the vendor")
def _(root):
    refused(root, price_list(), "so the directory is the vendor", "other", "2026-01-01.json")


@case("rule 3 — the id is the vendor and the date joined")
def _(root):
    body = price_list()
    body["price_list_id"] = "acme-2026-01-02"
    refused(root, body, "`price_list_id` is `acme-2026-01-02`, not `acme-2026-01-01`")


@case("rule 4 — bounds that descend select two tiers for one prompt")
def _(root):
    refused(root, with_tiers([tier(2000), tier(1000), tier(None)]), "ascend strictly")


@case("rule 4 — an equal bound is not an ascent")
def _(root):
    refused(root, with_tiers([tier(1000), tier(1000), tier(None)]), "ascend strictly")


@case("rule 4 — a last tier with a bound leaves larger prompts unpriced")
def _(root):
    refused(root, with_tiers([tier(1000), tier(2000)]), "ascend strictly")


@case("rule 4 — an unbounded tier that is not last hides the ones after it")
def _(root):
    refused(root, with_tiers([tier(None), tier(None)]), "ascend strictly")


@case("rule 4 — a model with no tiers has no price")
def _(root):
    refused(root, with_tiers([]), "ascend strictly")


@case("rule 5 — a price written as a JSON number is read as a float")
def _(root):
    body = with_tiers([tier(None)])
    body["models"]["m-1"]["tiers"][0]["input"] = 0.1
    refused(root, body, "tier 0 `input` is 0.1")


@case("rule 5 — a negative price is not a price")
def _(root):
    refused(root, with_tiers([tier(None, "-1")]), "a price is a non-negative decimal")


@case("rule 5 — nor is an exponent, or a word Decimal would read")
def _(root):
    body = with_tiers([tier(None)])
    body["models"]["m-1"]["tiers"][0]["output"] = "1e3"
    body["models"]["m-1"]["tiers"][0]["cache_read"] = "NaN"
    refused(root, body, "tier 0 `cache_read` is 'NaN'")
    _code, text = validate(root)
    assert "tier 0 `output` is '1e3'" in text, text


@case("rule 6 — a model id no row can carry")
def _(root):
    body = copy.deepcopy(price_list())
    body["models"]["a model"] = body["models"].pop("m-1")
    refused(root, body, "`a model` is not an id `agent.model_id` admits")


@case("a file that is not JSON is not a list")
def _(root):
    refused(root, "{", "not JSON, so it is not a list")


def main() -> int:
    failures = 0
    for name, fn in CASES:
        with tempfile.TemporaryDirectory() as root:
            try:
                fn(root)
            except AssertionError as exc:
                failures += 1
                print(f"::error title=price_list_validate_suite::{name}: {exc}")
            # Anything else a case raises is a failure of that case, reported and counted.
            except Exception as exc:                       # noqa: BLE001
                failures += 1
                print(f"::error title=price_list_validate_suite::{name}: "
                      f"{type(exc).__name__}: {exc}")
    print(f"price_list_validate_suite: ran {len(CASES)} cases, {failures} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
