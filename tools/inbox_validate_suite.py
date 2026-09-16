#!/usr/bin/env python3
"""Cases for `inbox_validate.py` — one per rule of ADR-086 §G.34, each shown to go red.

A rule nobody has watched fail is a rule nobody knows is wired up. Every case here builds an inbox
on disk, runs the validator over it, and asserts both that a conforming inbox is silent and that the
specific violation is the thing reported.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
VALIDATOR = os.path.join(HERE, "inbox_validate.py")
SCHEMAS = os.path.join(os.path.dirname(HERE), "schemas")

CASES: list[tuple[str, object]] = []


def case(name):
    def register(fn):
        CASES.append((name, fn))
        return fn
    return register


def run_record(run_id="R-1", visibility="public", group=None, arm=None, baseline=None) -> dict:
    doc = {
        "run_id": run_id,
        "started_at": "2026-09-17T10:00:00Z",
        "workload": {"domain": "docs-guardrails", "scope": "docs-only", "fingerprint": "ci:abc"},
        "agent": {"provider": "anthropic", "model_id": "m", "harness": {"client": "c",
                                                                        "version": "1"}},
        "repository_state": {"repository": "exeris-systems/exeris-docs", "visibility": visibility,
                             "commit": "a" * 40, "bundle_version": "2.0.0", "dirty": False},
        "execution": {"turns": 1, "tool_calls": 1, "wall_time_ms": 1,
                      "event_stream": {"ref": "x", "sha256": "b" * 64, "event_count": 1}},
        "accounting": {"mode": "subscription"},
        "oracle": {"name": "review-disposition", "calibration": {"status": "not-run"}},
        "outcome": "UNKNOWN",
        "instrument": {"capture_version": "0.1.0"},
    }
    if group is not None:
        doc["pairing"] = {"group_id": group, "arm": arm or "a", "arms_planned": 2,
                          "baseline": "none"}
    if baseline is not None:
        doc["human_baseline"] = baseline
    return doc


def judgement(judgement_id="J-1", run_id="R-1") -> dict:
    return {"judgement_id": judgement_id, "run_id": run_id,
            "oracle": {"name": "review-disposition", "calibration": {"status": "not-run"}},
            "judged_at": "2026-09-17T11:00:00Z", "outcome": "UNKNOWN",
            "instrument": {"capture_version": "0.1.0"}}


def build(root: str, visibility="public", runs=(), judgements=()) -> None:
    os.makedirs(os.path.join(root, "inbox", "2026-09-17", "runs"), exist_ok=True)
    os.makedirs(os.path.join(root, "inbox", "2026-09-17", "judgements"), exist_ok=True)
    shutil.copytree(SCHEMAS, os.path.join(root, "schemas"), dirs_exist_ok=True)
    with open(os.path.join(root, "inbox", "inbox.json"), "w", encoding="utf-8") as fh:
        json.dump({"visibility": visibility}, fh)
    for doc in runs:
        p = os.path.join(root, "inbox", "2026-09-17", "runs", f"{doc['run_id']}.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
    for doc in judgements:
        p = os.path.join(root, "inbox", "2026-09-17", "judgements",
                         f"{doc['judgement_id']}.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)


def validate(root: str) -> tuple[int, str]:
    out = subprocess.run([sys.executable, VALIDATOR, "--root", root],
                         capture_output=True, text=True)
    return out.returncode, out.stdout + out.stderr


@case("a conforming inbox is silent")
def _(root):
    build(root, runs=[run_record()], judgements=[judgement()])
    code, text = validate(root)
    assert code == 0, text
    assert "0 error(s)" in text, text


@case("rule 1 — a row whose visibility is not the inbox's belongs in the sibling")
def _(root):
    build(root, visibility="public", runs=[run_record(visibility="enterprise-private")])
    code, text = validate(root)
    assert code == 1, text
    assert "belongs in the sibling inbox" in text, text


@case("rule 2 — repository_state cannot differ inside one group")
def _(root):
    a = run_record("R-1", group="G-1", arm="exeris")
    b = run_record("R-2", group="G-1", arm="spring")
    b["repository_state"]["commit"] = "c" * 40
    build(root, runs=[a, b])
    code, text = validate(root)
    assert code == 1, text
    assert "`repository_state` differs inside `group_id: G-1`" in text, text


@case("rule 3 — human_baseline cannot differ inside one group")
def _(root):
    a = run_record("R-1", group="G-1", arm="exeris", baseline={"wall_time_ms": 10,
                                                               "outcome": "TRUE_DONE"})
    b = run_record("R-2", group="G-1", arm="spring", baseline={"wall_time_ms": 99,
                                                               "outcome": "TRUE_DONE"})
    build(root, runs=[a, b])
    code, text = validate(root)
    assert code == 1, text
    assert "`human_baseline` differs inside `group_id: G-1`" in text, text


@case("rule 4 — one arm is one slot, and a second row in it is a collision")
def _(root):
    a = run_record("R-1", group="G-1", arm="exeris")
    b = run_record("R-2", group="G-1", arm="exeris")
    build(root, runs=[a, b])
    code, text = validate(root)
    assert code == 1, text
    assert "appears 2 times in `group_id: G-1`" in text, text


@case("rule 5 — a judgement resolves to its run or it is about nothing")
def _(root):
    build(root, runs=[run_record("R-1")], judgements=[judgement("J-1", run_id="R-404")])
    code, text = validate(root)
    assert code == 1, text
    assert "is not a run record in this inbox" in text, text


@case("a replicate in its own slot is not a collision")
def _(root):
    a = run_record("R-1", group="G-1", arm="exeris-1")
    b = run_record("R-2", group="G-1", arm="exeris-2")
    build(root, runs=[a, b])
    code, text = validate(root)
    assert code == 0, text


@case("a record missing a key the schema requires is a producer defect")
def _(root):
    doc = run_record()
    del doc["accounting"]
    build(root, runs=[doc])
    code, text = validate(root)
    assert code == 1, text
    assert "`accounting`" in text and "absent here" in text, text


@case("the filename is the id")
def _(root):
    build(root, runs=[run_record("R-1")])
    os.rename(os.path.join(root, "inbox", "2026-09-17", "runs", "R-1.json"),
              os.path.join(root, "inbox", "2026-09-17", "runs", "R-9.json"))
    code, text = validate(root)
    assert code == 1, text
    assert "the name is the id" in text, text


@case("a file that is not JSON is not a record")
def _(root):
    build(root, runs=[run_record()])
    with open(os.path.join(root, "inbox", "2026-09-17", "runs", "broken.json"), "w") as fh:
        fh.write("{not json")
    code, text = validate(root)
    assert code == 1, text
    assert "not JSON, so it is not a record" in text, text


@case("an inbox with no identity cannot check rule 1 and says so")
def _(root):
    build(root, runs=[run_record()])
    os.remove(os.path.join(root, "inbox", "inbox.json"))
    code, text = validate(root)
    assert code == 1, text
    assert "the inbox declares no identity" in text, text


def main() -> int:
    failures = 0
    for name, fn in CASES:
        with tempfile.TemporaryDirectory() as root:
            try:
                fn(root)
            except AssertionError as exc:
                failures += 1
                print(f"::error title=inbox_validate_suite::{name}: {exc}")
            except Exception as exc:                       # noqa: BLE001 - reported, not hidden
                failures += 1
                print(f"::error title=inbox_validate_suite::{name}: {type(exc).__name__}: {exc}")
    print(f"inbox_validate_suite: ran {len(CASES)} cases, {failures} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
