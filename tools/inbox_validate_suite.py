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
        "agent": {"provider": "anthropic", "model_id": "m", "model_snapshot": "m-20260917",
                  "harness": {"client": "c", "version": "1"}},
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


# Not one of the five cross-file rules: a within-record rule that lives here because JSON Schema
# compares a value against a constant and never against its sibling.
@case("an alias repeated as a snapshot is refused, and the marked form is not")
def _(root):
    bare = run_record()
    bare["agent"]["model_snapshot"] = bare["agent"]["model_id"]
    build(root, runs=[bare])
    code, out = validate(root)
    assert code == 1, out
    assert "an alias is not a snapshot" in out, out
    assert "unresolved:m" in out, out


@case("the marked form passes, because saying so is the whole point of marking it")
def _(root):
    marked = run_record()
    marked["agent"]["model_snapshot"] = "unresolved:" + marked["agent"]["model_id"]
    build(root, runs=[marked])
    code, out = validate(root)
    assert code == 0, out


# Vendors write `/`, `@` and `:` into model identifiers and this repository does not get to
# legislate that. The snapshot field's pattern was narrower than the id it marks, so a HuggingFace-
# shaped id had no valid marked form at all: the row could neither carry the snapshot it does not
# have nor say that it does not have one. Caught by the review reading the two patterns against each
# other, which is the only way to see it — each is valid on its own.
@case("a vendor id carrying a slash can still be marked unresolved")
def _(root):
    marked = run_record()
    marked["agent"]["model_id"] = "meta-llama/Llama-3.1-70B-Instruct"
    marked["agent"]["model_snapshot"] = "unresolved:meta-llama/Llama-3.1-70B-Instruct"
    build(root, runs=[marked])
    code, out = validate(root)
    assert code == 0, out


@case("and one carrying an @")
def _(root):
    marked = run_record()
    marked["agent"]["model_id"] = "gpt-4o@2026-05-13"
    marked["agent"]["model_snapshot"] = "unresolved:gpt-4o@2026-05-13"
    build(root, runs=[marked])
    code, out = validate(root)
    assert code == 0, out


# The other half of the mark. `unresolved:` says "this row's alias, and no snapshot behind it", so a
# mark naming a DIFFERENT alias marks nothing — it reads as a snapshot that happens to begin with a
# word. Nothing asked before this.
@case("a mark naming another alias marks nothing")
def _(root):
    wrong = run_record()
    wrong["agent"]["model_snapshot"] = "unresolved:some-other-model"
    build(root, runs=[wrong])
    code, out = validate(root)
    assert code == 1, out
    assert "this row's own alias" in out, out


# A local runtime names the weights it ran, and a digest is an alias of nothing: the rule above
# reads a snapshot against this row's own `model_id`, and the `sha256:` form is not in that
# comparison at all. The two live one `elif` apart, which is the distance a widening edit has to
# cover without taking the alias rule with it.
@case("weights named by digest are not an alias repeated")
def _(root):
    local = run_record()
    local["agent"]["model_id"] = "llama3:70b"
    local["agent"]["model_snapshot"] = "sha256:" + "a" * 64
    local["accounting"]["mode"] = "local"
    build(root, runs=[local])
    code, out = validate(root)
    assert code == 0, out


@case("rule 5 — a judgement resolves to its run or it is about nothing")
def _(root):
    build(root, runs=[run_record("R-1")], judgements=[judgement("J-1", run_id="R-404")])
    code, text = validate(root)
    assert code == 1, text
    assert "is not a run record in this inbox" in text, text


# Rule 6, on §F.31's third fingerprint class. `adhoc:` says the run was never planned, and a group
# is a plan: a row carrying both claims a preregistration that does not exist, and the claim is the
# one thing a paired comparison cannot survive. The schema refuses the pair within one record; this
# is the same rule where the inbox can see it, which is where a producer's rows are read.
@case("rule 6 — an adhoc run was never planned, so it joins no group")
def _(root):
    doc = run_record(group="G-1", arm="exeris")
    doc["workload"]["fingerprint"] = "adhoc:01JBQ8Z9K3W7YV4X2M5N6P7R8T"
    build(root, runs=[doc])
    code, text = validate(root)
    assert code == 1, text
    assert "never planned" in text, text


@case("an unplanned run that joins nothing is what the class is for")
def _(root):
    doc = run_record()
    doc["workload"]["fingerprint"] = "adhoc:01JBQ8Z9K3W7YV4X2M5N6P7R8T"
    build(root, runs=[doc])
    code, text = validate(root)
    assert code == 0, text


@case("a planned run and a CI-observed one each keep their group")
def _(root):
    planned = run_record("R-1", group="G-1", arm="exeris")
    planned["workload"]["fingerprint"] = "reg:task-0001"
    observed = run_record("R-2", group="G-2", arm="exeris")
    observed["workload"]["fingerprint"] = "ci:abc"
    build(root, runs=[planned, observed])
    code, text = validate(root)
    assert code == 0, text


# The publisher's name is not a model's, a provider's or a harness client's (ADR-087 §A.4). Three
# Apps write under the organisation's own names, and a row that puts one of them in `agent.*` says
# the publisher took the turns — the one thing none of the three does. The refusal is on the field,
# not on the string: `execution.principal` is where a platform identity is the answer, so those
# names are correct there and the rule has to leave that field alone by construction.
@case("a publisher's name is not a model's")
def _(root):
    doc = run_record()
    doc["agent"]["model_id"] = "exeris-bot"
    build(root, runs=[doc])
    code, text = validate(root)
    assert code == 1, text
    assert "exeris-bot" in text, text


@case("nor a harness client's, however the login is spelled")
def _(root):
    doc = run_record()
    doc["agent"]["harness"]["client"] = "exeris-inbox[bot]"
    build(root, runs=[doc])
    code, text = validate(root)
    assert code == 1, text
    assert "exeris-inbox" in text, text


@case("nor a provider's, however it is capitalised")
def _(root):
    doc = run_record()
    doc["agent"]["provider"] = "Exeris-Agent"
    build(root, runs=[doc])
    code, text = validate(root)
    assert code == 1, text
    assert "Exeris-Agent" in text, text


@case("and the principal is the field where such a name is the answer")
def _(root):
    doc = run_record()
    doc["execution"]["principal"] = {"kind": "app", "login": "exeris-agent[bot]"}
    build(root, runs=[doc])
    code, text = validate(root)
    assert code == 0, text


@case("a replicate in its own slot is not a collision")
def _(root):
    a = run_record("R-1", group="G-1", arm="exeris-1")
    b = run_record("R-2", group="G-1", arm="exeris-2")
    build(root, runs=[a, b])
    code, text = validate(root)
    assert code == 0, text


# A row of the wrong SHAPE is a producer defect like any other, and this file's whole job is to
# report one back to the producer. A field the row writes as a string where the contract writes an
# object is the case that reaches the validator's own dereferences, so the rule it violates has to
# still be printed — and so does every other row in the batch, which a traceback would take with it.
@case("a field of the wrong shape is reported, not crashed on")
def _(root):
    doc = run_record(visibility="enterprise-private")
    doc["agent"]["harness"] = "claude-code"
    build(root, visibility="public", runs=[doc])
    code, text = validate(root)
    assert code == 1, text
    assert "belongs in the sibling inbox" in text, text


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
