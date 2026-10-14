#!/usr/bin/env python3
"""Cases for `api_equivalent.py` — every rule of the derivation, each shown to hold or to refuse.

The inputs are synthetic and live in `tools/fixtures/price/`: two lists for an invented vendor
`acme` at round prices, so that every expected figure can be checked by hand, a row, and a stream.
No real stream is a fixture — a stream is a run's private record, and a case needs only the shape.
Each case writes its own row or stream beside a copy of those, runs the tool, and reads its output.

The `acme` prices per million tokens, in the order input / output / 5m write / 1h write / read:
`acme-tiered` is 1/2/3/4/5 for prompts up to 100,000 tokens and 10/20/30/40/50 above;
`acme-flat` is 1 for everything. The list effective `2026-06-01` doubles every price.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(HERE, "api_equivalent.py")
FIXTURES = os.path.join(HERE, "fixtures", "price")

CASES: list[tuple[str, object]] = []


#: The fixture files every case lays out.
ROW = "row.json"
STREAM = "stream.jsonl"

def case(name):
    def register(fn):
        CASES.append((name, fn))
        return fn
    return register


def request(rid="msg_1", model="acme-tiered", *, inp=0, out=0, read=0, w5=0, w1=0,
            split=True) -> dict:
    usage = {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": read,
             "cache_creation_input_tokens": w5 + w1}
    if split:
        usage["cache_creation"] = {"ephemeral_5m_input_tokens": w5, "ephemeral_1h_input_tokens": w1}
    return {"type": "assistant", "message": {"id": rid, "model": model, "usage": usage}}


def derive(root: str, events: list | None = None,
           started_at: str | None = None) -> tuple[int, dict | str]:
    """Run the tool over the fixtures, with this case's stream and start time where given."""
    lists = os.path.join(root, "price-lists")
    shutil.copytree(os.path.join(FIXTURES, "price-lists"), lists)
    with open(os.path.join(FIXTURES, ROW), encoding="utf-8") as fh:
        row = json.load(fh)
    if started_at is not None:
        row["started_at"] = started_at
    row_path, stream_path = os.path.join(root, ROW), os.path.join(root, STREAM)
    with open(row_path, "w", encoding="utf-8") as fh:
        json.dump(row, fh)
    if events is None:
        shutil.copy(os.path.join(FIXTURES, STREAM), stream_path)
    else:
        with open(stream_path, "w", encoding="utf-8") as fh:
            fh.writelines(json.dumps(e) + "\n" for e in events)
    out = subprocess.run([sys.executable, TOOL, "--base", root, "--row", row_path,
                          "--stream", stream_path, "--price-lists", lists],
                         capture_output=True, text=True)
    if out.returncode:
        return out.returncode, out.stdout + out.stderr
    return 0, json.loads(out.stdout)


def figure(root: str, events: list | None, started_at: str | None = None) -> dict:
    code, out = derive(root, events, started_at)
    assert code == 0, out
    return out


def refusal(root: str, events: list | None, words: str, started_at: str | None = None) -> None:
    code, out = derive(root, events, started_at)
    assert code == 1, out
    assert words in out, out


def keys(node: object) -> list[str]:
    """Every key anywhere in a JSON value."""
    if isinstance(node, dict):
        return [k for k, v in node.items() for k in [k, *keys(v)]]
    if isinstance(node, list):
        return [k for v in node for k in keys(v)]
    return []


@case("the fixture stream prices to the figure worked by hand")
def _(root):
    # msg_a, written as two lines: 10×1 + 100×2 + 1000×5 + 2000×4 = 13,210; msg_b: 4×1.
    out = figure(root, None)
    assert out["api_equivalent_usd"] == "0.013214", out
    assert out["requests"] == 2, out
    assert out["price_list_id"] == "acme-2026-01-01", out
    assert out["calibration_status"] == "not-run" and out["quotable"] is False, out


@case("a prompt of exactly 100,000 tokens is priced by the lower tier")
def _(root):
    out = figure(root, [request(inp=100_000)])
    assert out["api_equivalent_usd"] == "0.1", out
    assert list(out["by_tier"]["acme-tiered"]) == ["prompt<=100000"], out


@case("a prompt of 100,001 tokens is priced by the upper tier")
def _(root):
    out = figure(root, [request(inp=100_001)])
    assert out["api_equivalent_usd"] == "1.00001", out
    assert list(out["by_tier"]["acme-tiered"]) == ["prompt>100000"], out


@case("the prompt is input, cache read and cache write together")
def _(root):
    # 1 + 50,000 + 50,000 = 100,001: every class at the upper tier's price.
    out = figure(root, [request(inp=1, read=50_000, w1=50_000)])
    assert list(out["by_tier"]["acme-tiered"]) == ["prompt>100000"], out
    assert out["api_equivalent_usd"] == "4.50001", out


@case("each request is tiered on its own, never on the run's total")
def _(root):
    out = figure(root, [request("msg_1", inp=60_000), request("msg_2", inp=60_000)])
    assert out["by_tier"]["acme-tiered"] == {
        "prompt<=100000": {"requests": 2, "api_equivalent_usd": "0.12"}}, out


@case("a one-hour cache write is priced as one")
def _(root):
    out = figure(root, [request(w1=1_000_000)])
    assert out["api_equivalent_usd"] == "40", out          # above the bound: 1h at 40
    assert out["by_class"]["cache_write_1h"]["tokens"] == 1_000_000, out


@case("and a five-minute write as the other")
def _(root):
    out = figure(root, [request(w5=10_000), request("msg_2", w1=10_000)])
    assert out["by_class"]["cache_write_5m"]["api_equivalent_usd"] == "0.03", out
    assert out["by_class"]["cache_write_1h"]["api_equivalent_usd"] == "0.04", out


@case("a request written as several lines is counted once")
def _(root):
    line = request(inp=1_000)
    out = figure(root, [line, line, line])
    assert out["requests"] == 1 and out["api_equivalent_usd"] == "0.001", out


@case("one id carrying two different usages is refused")
def _(root):
    refusal(root, [request(inp=1), request(inp=2)], "appears twice with different usage")


@case("a cache write with no lifetime split is refused, not guessed")
def _(root):
    refusal(root, [request(w1=10, split=False)], "does not say for how long")


@case("a split that does not add up to the write is refused")
def _(root):
    line = request(w1=10)
    line["message"]["usage"]["cache_creation_input_tokens"] = 11
    refusal(root, [line], "which is not the 11 it wrote")


@case("no cache write needs no split")
def _(root):
    out = figure(root, [request(inp=1_000, split=False)])
    assert out["api_equivalent_usd"] == "0.001", out


@case("a model the list does not price is refused by name")
def _(root):
    refusal(root, [request(model="acme-unknown", inp=1)], "ran on `acme-unknown`")


@case("a row before the later list is priced by the earlier one")
def _(root):
    out = figure(root, [request(inp=1_000)], "2026-05-31T23:59:59Z")
    assert out["price_list_id"] == "acme-2026-01-01", out
    assert out["api_equivalent_usd"] == "0.001", out


@case("a row on or after the later list's date is priced by it")
def _(root):
    out = figure(root, [request(inp=1_000)], "2026-06-01T00:00:00Z")
    assert out["price_list_id"] == "acme-2026-06-01", out
    assert out["api_equivalent_usd"] == "0.002", out


@case("the day is the UTC day, whatever offset the row was written in")
def _(root):
    out = figure(root, [request(inp=1_000)], "2026-06-01T01:00:00+02:00")
    assert out["price_list_id"] == "acme-2026-01-01", out


@case("a row older than every list has no list in force")
def _(root):
    refusal(root, [request(inp=1)], "is in force on 2025-12-31", "2025-12-31T23:00:00Z")


@case("the output names no `cost` anywhere")
def _(root):
    code, out = derive(root)
    assert code == 0, out
    named = [k for k in keys(out) if "cost" in k.lower()]
    assert not named, named


@case("the tool writes nothing beside what it read")
def _(root):
    derive(root)
    with open(os.path.join(FIXTURES, ROW), encoding="utf-8") as fh:
        original = json.load(fh)
    with open(os.path.join(root, ROW), encoding="utf-8") as fh:
        assert json.load(fh) == original
    assert sorted(os.listdir(root)) == ["price-lists", ROW, STREAM], os.listdir(root)



@case("a list in force without the shape a list has is refused, not half-read")
def _(root):
    code, out = derive(root)
    assert code == 0, out
    lists = os.path.join(root, "price-lists", "acme")
    for name in os.listdir(lists):
        path = os.path.join(lists, name)
        with open(path, encoding="utf-8") as fh:
            body = json.load(fh)
        for entry in body["models"].values():
            for tier in entry["tiers"]:
                tier.pop("output", None)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(body, fh)
    out = subprocess.run([sys.executable, TOOL, "--base", root, "--row", os.path.join(root, ROW),
                          "--stream", os.path.join(root, STREAM), "--price-lists",
                          os.path.join(root, "price-lists")], capture_output=True, text=True)
    assert out.returncode == 1, (out.returncode, out.stderr)
    assert "is not a valid price list" in out.stderr, out.stderr
    assert "Traceback" not in out.stderr, out.stderr

@case("a row or a stream outside the base is refused before it is read")
def _(root):
    derive(root)
    base = os.path.join(root, "price-lists")
    out = subprocess.run([sys.executable, TOOL, "--base", base, "--row", os.path.join(root, ROW),
                          "--stream", os.path.join(root, STREAM), "--price-lists", base],
                         capture_output=True, text=True)
    assert out.returncode == 2, (out.returncode, out.stderr)
    assert "must lie inside" in out.stderr, out.stderr
    assert not out.stdout, out.stdout

def main() -> int:
    failures = 0
    for name, fn in CASES:
        with tempfile.TemporaryDirectory() as root:
            try:
                fn(root)
            except AssertionError as exc:
                failures += 1
                print(f"::error title=api_equivalent_suite::{name}: {exc}")
            # Anything else a case raises is a failure of that case, reported and counted.
            except Exception as exc:                       # noqa: BLE001
                failures += 1
                print(f"::error title=api_equivalent_suite::{name}: {type(exc).__name__}: {exc}")
    print(f"api_equivalent_suite: ran {len(CASES)} cases, {failures} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
