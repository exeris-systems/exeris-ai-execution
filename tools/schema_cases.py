#!/usr/bin/env python3
"""Cases for the run-record SCHEMA itself — the half `inbox_validate_suite.py` cannot reach.

That suite runs the cross-file rules, which import nothing, so it never sees a `pattern`. The
conformance job validates the records the inbox HOLDS, and the inbox holds none yet, so the patterns
in `run-record.schema.json` have been shipped untested: a narrowing edit would have been caught by
nobody until the first row it refused.

Two fields here are one rule written twice. `agent.model_id` is the vendor's identifier as the
vendor writes it, and `agent.model_snapshot` carries `unresolved:` + that same value where the
runtime exposes no snapshot. If the snapshot's character set is narrower than the id's, some ids
have no valid marked form at all — the row can neither give the snapshot it does not have nor say
that it does not have one. Each pattern is perfectly sensible read alone, which is why this reads
them against each other.

The fields the contract gains under ADR-086 §C.14a, §C.14b, §F.31 and the two local-executor RFCs
are all optional, and an optional field is the easiest kind to ship wrong: nothing writes it, so
nothing fails when its pattern admits a spelling that means a second thing or refuses a value a
producer must be able to record. Each is read here in both directions — one value that has to be
admitted, one entry per way the shape can be wrong — and the fixture carries one example of every
one of them, so a field that disappears from the schema turns the fixture red instead of passing
unnoticed.

Usage: schema_cases.py   (needs `jsonschema`)
"""
from __future__ import annotations

import copy
import json
import os
import sys

from jsonschema import Draft202012Validator

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMAS = os.path.join(os.path.dirname(HERE), "schemas")
VERSION_FILE = os.path.join(SCHEMAS, "VERSION")


def declared_version() -> str | None:
    """The contract version this repository declares, or None when nothing declares it."""
    try:
        with open(VERSION_FILE, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return None


# The version these cases are written for: the fields below are the MINOR it names. It is a
# literal and not a read of `schemas/VERSION`, because a case that reads the value under test and
# compares it with itself agrees for every value that file could hold — the version it declares
# today, and the one the MINOR was supposed to replace. The last case is where the two meet.
CONTRACT_VERSION = "0.2.0"

# THE FIRST CONFORMING RECORD THIS REPOSITORY HAS. `inbox_validate_suite.py` builds a record too,
# but for the cross-file rules, which read four keys and ignore the rest — it does not validate, and
# it does not conform. The inbox holds no rows yet either, so until this file nothing had ever been
# checked against the schema, patterns included. Keep it conforming: every case below is this with
# one field changed, so a fixture that drifts out of conformance turns every case into a false red.
# `execution` carries one example of each optional field for a second reason: with
# `additionalProperties: false` there, a field dropped from the schema makes the fixture itself
# refused, and every case is a case about the fixture.
def conforming() -> dict:
    return {
        "run_id": "R-1",
        "started_at": "2026-09-17T12:00:00Z",
        "workload": {"fingerprint": "ci:exeris-ai-execution-3-485e0c2", "domain": "schema",
                     "scope": "public"},
        "agent": {"provider": "anthropic", "model_id": "claude-sonnet-5",
                  "model_snapshot": "unresolved:claude-sonnet-5",
                  "harness": {"client": "claude-code", "version": "2.1.274"},
                  "system_prompt_sha256": "0" * 64},
        "repository_state": {"repository": "exeris-systems/exeris-ai-execution",
                             "visibility": "public", "commit": "4" * 40,
                             "bundle_version": "1.0.0", "dirty": False},
        "execution": {"turns": 1, "tool_calls": 0, "wall_time_ms": 1000,
                      "event_stream": {"ref": "none", "sha256": "0" * 64, "event_count": 1},
                      "tool_surface": "1" * 64, "verdict_route": "execution-log",
                      "permission_denials": 0, "scope_denials": 0, "capture_level": "full",
                      "principal": {"kind": "app", "login": "exeris-agent[bot]"},
                      "human_prompts": 0, "result_commits": []},
        "accounting": {"mode": "subscription"},
        "oracle": {"id": "ci-gates", "version": "1",
                   "calibration": {"suite": "inbox-validate", "status": "pass",
                                   "result": "16 cases, 0 failures", "run_at": "2026-09-17T12:00:00Z"}},
        "outcome": "TRUE_DONE",
        "instrument": {"capture_version": CONTRACT_VERSION, "fence": "2026-09-17"},
    }


CASES: list[tuple[str, dict, bool]] = []

DELETE = object()          # `case_at(..., DELETE, ...)` removes the key instead of setting it


def case(name: str, *, valid: bool, **agent) -> None:
    rec = copy.deepcopy(conforming())
    rec["agent"].update(agent)
    CASES.append((name, rec, valid))


def _at(rec: dict, path: str, value: object) -> dict:
    """Set — or, for DELETE, remove — one dotted path inside `rec`, and hand `rec` back."""
    keys = path.split(".")
    node = rec
    for key in keys[:-1]:
        node = node.setdefault(key, {})
    if value is DELETE:
        node.pop(keys[-1], None)
    else:
        node[keys[-1]] = value
    return rec


def case_at(name: str, path: str, value: object, *, valid: bool) -> None:
    """One case: the fixture with one dotted path set, or removed when the value is DELETE."""
    CASES.append((name, _at(copy.deepcopy(conforming()), path, value), valid))


def case_with(name: str, changes: list[tuple[str, object]], *, valid: bool) -> None:
    """A case needing two paths at once — the root `allOf` rules each read a pair of fields."""
    rec = copy.deepcopy(conforming())
    for path, value in changes:
        _at(rec, path, value)
    CASES.append((name, rec, valid))


CASES.append(("the conforming record conforms", conforming(), True))


# Vendor shapes this repository does not get to legislate. HuggingFace writes a slash, Azure
# deployments write an `@`, ollama writes a colon.
case("a slash in the id, marked", valid=True,
     model_id="meta-llama/Llama-3.1-70B-Instruct",
     model_snapshot="unresolved:meta-llama/Llama-3.1-70B-Instruct")
case("an @ in the id, marked", valid=True,
     model_id="gpt-4o@2026-05-13", model_snapshot="unresolved:gpt-4o@2026-05-13")
case("a colon in the id, marked", valid=True,
     model_id="llama3:70b", model_snapshot="unresolved:llama3:70b")
case("a slash in the id, resolved", valid=True,
     model_id="meta-llama/Llama-3.1-70B-Instruct",
     model_snapshot="meta-llama/Llama-3.1-70B-Instruct-20260501")
case("an ordinary dated snapshot", valid=True,
     model_id="claude-sonnet-5", model_snapshot="claude-sonnet-5-20260514")

# THE SAME PARITY, IN LENGTH. The character sets were made to match and the length caps were not,
# so an id of 118 characters or more had no valid marked form: identical defect, one axis over,
# under a commit whose title said the defect was closed. Nothing here went near the boundary, which
# is why nothing said so. `model_snapshot` now carries `model_id`'s cap plus `unresolved:`.
LONG_ID = "a" + "b0-c._d:e/f@g+" * 9 + "a"                     # 128, every admitted character
case("an id at its own length limit, marked", valid=True,
     model_id=LONG_ID, model_snapshot="unresolved:" + LONG_ID)
case("and resolved at that length", valid=True,
     model_id=LONG_ID, model_snapshot=LONG_ID)
case("an id one character past its limit", valid=False,
     model_id=LONG_ID + "z", model_snapshot="m-1")
case("a marked form past the id's limit plus the prefix", valid=False,
     model_id=LONG_ID, model_snapshot="unresolved:" + LONG_ID + "z")


# The patterns still have to refuse something, or they are decoration.
case("a snapshot with a space", valid=False,
     model_id="m", model_snapshot="not a snapshot")
case("an id with a space", valid=False, model_id="a model", model_snapshot="m-1")
case("an id that is only punctuation", valid=False, model_id="--", model_snapshot="m-1")
case("a snapshot ending in punctuation", valid=False, model_id="m", model_snapshot="m-1-")


# `execution.tool_surface` (§C.14a) — the identity of the allow-list a run held. An identity is
# useful only while one surface has one spelling, so the digest is lowercase hex of a fixed length
# and every other spelling of the same bytes is refused rather than admitted as a second surface.
case_at("a tool surface is sixty-four hex digits", "execution.tool_surface", "a" * 64, valid=True)
case_at("a digest one digit short identifies nothing", "execution.tool_surface", "a" * 63,
        valid=False)
case_at("a surface has one spelling, and it is lower case", "execution.tool_surface", "A" * 64,
        valid=False)


# `execution.verdict_route` (§C.14a) — which transport carried the verdict, in the vocabulary the
# publisher already distinguishes. The publisher prints its own wording with a space; the field is a
# value in a column, so the value is hyphenated and fixed.
case_at("a verdict written to a file", "execution.verdict_route", "file", valid=True)
case_at("one read out of the execution log", "execution.verdict_route", "execution-log", valid=True)
case_at("one read out of a fenced block", "execution.verdict_route", "fenced-block", valid=True)
case_at("and a run that carried no verdict at all", "execution.verdict_route", "none", valid=True)
case_at("the publisher's prose spelling is not the value", "execution.verdict_route",
        "execution log", valid=False)
case_at("nor is the value shouted", "execution.verdict_route", "FILE", valid=False)


# The counters, which are one shape: how many times a thing happened. Zero is a measurement — the
# run was watched and nothing was refused — which is exactly why a count the producer cannot
# establish is an ABSENT field and never a zero. Negative, fractional and stringly-typed counts are
# none of the three.
def counter_cases(field: str) -> None:
    """One counter read four ways: the zero that measures, and three values that are not counts."""
    case_at(f"`{field}` counted nothing, and says so", f"execution.{field}", 0, valid=True)
    case_at(f"`{field}` cannot count backwards", f"execution.{field}", -1, valid=False)
    case_at(f"half a `{field}` is not a count", f"execution.{field}", 1.5, valid=False)
    case_at(f"a `{field}` written as text is not a count", f"execution.{field}", "0", valid=False)


# `execution.permission_denials` (§C.14a) — the clause names it beside `tool_calls`, because the
# two count different things under two tool surfaces.
counter_cases("permission_denials")

# `execution.scope_denials` (§C.14b) and `execution.human_prompts` (§C.14b, RFC-2026-09-17) — what
# a scope-bounding harness refused, and how often a person steered the run after starting it. Both
# are observed by a producer that has such a harness, and absent from a row produced where none is.
counter_cases("scope_denials")
counter_cases("human_prompts")


# `execution.capture_level` (RFC-2026-09-17, RFC-2026-09-18) — where the counters on this row
# came from. The three values are the three states a producer can honestly be in; a fourth word
# invented at capture time is a reader's problem for as long as the column exists.
case_at("everything the runtime recorded", "execution.capture_level", "full", valid=True)
case_at("counts, with no per-event record behind them", "execution.capture_level", "counts-only",
        valid=True)
case_at("identity and wall time, and nothing more", "execution.capture_level", "identity-only",
        valid=True)
case_at("a level nobody defined", "execution.capture_level", "partial", valid=False)


# `execution.principal` (§C.14b, RFC-2026-09-17) — the platform identity the producer acted under.
# An App login ends in `[bot]` and a person's does not, so `kind` and `login` have to agree: a pair
# that disagrees is a row claiming one kind of actor while naming the other.
case_at("the hands act under their own App", "execution.principal",
        {"kind": "app", "login": "exeris-agent[bot]"}, valid=True)
case_at("a person acts under their own account", "execution.principal",
        {"kind": "user", "login": "arkstack-dev"}, valid=True)
case_at("an App says so in its login", "execution.principal",
        {"kind": "app", "login": "exeris-agent"}, valid=False)
case_at("and a person's login does not", "execution.principal",
        {"kind": "user", "login": "someone[bot]"}, valid=False)
case_at("there is no third kind of principal", "execution.principal",
        {"kind": "bot", "login": "exeris-agent[bot]"}, valid=False)
case_at("a principal with no login names nobody", "execution.principal", {"kind": "app"},
        valid=False)
case_at("and the pair carries nothing else", "execution.principal",
        {"kind": "app", "login": "exeris-agent[bot]", "id": 4}, valid=False)
case_at("a login is the host's spelling, and a space is not in it", "execution.principal",
        {"kind": "user", "login": "ark stack"}, valid=False)
case_at("nor is a login longer than the host lets one be", "execution.principal",
        {"kind": "user", "login": "a" * 65}, valid=False)
# The rule the whole set exists for: a producer that cannot establish the identity leaves the field
# out. Absence is the honest value, so absence has to validate.
case_at("a producer that establishes no principal leaves it out", "execution.principal", DELETE,
        valid=True)


# `execution.result_commits` (RFC-2026-09-17) — what the run produced, oldest first. An empty array is a
# measurement (the run committed nothing), a repeat is one commit written twice, and a commit has
# one spelling for the same reason a digest does.
case_at("a run that produced no commits measured that", "execution.result_commits", [], valid=True)
case_at("a commit is forty hex digits", "execution.result_commits", ["a" * 40], valid=True)
case_at("thirty-nine of them is not a commit", "execution.result_commits", ["a" * 39], valid=False)
case_at("the same commit twice is still one commit", "execution.result_commits",
        ["a" * 40, "a" * 40], valid=False)
case_at("and a commit has one spelling", "execution.result_commits", ["A" * 40], valid=False)


# `workload.fingerprint` gains a third class (§F.31). `adhoc:` says the run was never planned, so
# there is no registry entry it can be joined to — and the value is a run's own identifier, which
# is why it is as long as the other classes' values and not a word.
ADHOC = "adhoc:01JBQ8Z9K3W7YV4X2M5N6P7R8T"
PAIRING = {"group_id": "G-1", "arm": "exeris", "arms_planned": 2, "baseline": "none"}
case_at("an unplanned run carries the class that says so", "workload.fingerprint", ADHOC,
        valid=True)
case_at("a fingerprint too short to identify a run", "workload.fingerprint", "adhoc:abc",
        valid=False)
case_at("there are three classes, and `local:` is not one", "workload.fingerprint",
        "local:01JBQ8Z9K3W7YV4X2M5N6P7R8T", valid=False)
case_with("a run nobody planned belongs to no group",
          [("workload.fingerprint", ADHOC), ("pairing", PAIRING)], valid=False)
case_with("a run CI observed can still be an arm of one",
          [("workload.fingerprint", "ci:exeris-ai-execution-3-485e0c2"), ("pairing", PAIRING)],
          valid=True)


# `agent.model_snapshot` gains the local form (RFC-2026-09-18): weights identified by digest, and
# optionally the adapter over them. Weights that can be named by digest are weights on a machine the
# organisation runs, which is what `accounting.mode: local` says — the pair is one fact stated
# twice, and the root `allOf` is what keeps the two halves together.
WEIGHTS = "sha256:" + "a" * 64
ADAPTER = "+sha256:" + "b" * 64
case_with("a local run names the weights it ran",
          [("agent.model_snapshot", WEIGHTS), ("accounting.mode", "local")], valid=True)
case_with("and the adapter it ran over them",
          [("agent.model_snapshot", WEIGHTS + ADAPTER), ("accounting.mode", "local")], valid=True)
case_with("a digest one digit short is not a snapshot",
          [("agent.model_snapshot", "sha256:" + "a" * 63), ("accounting.mode", "local")],
          valid=False)
case_with("an adapter with no weights under it",
          [("agent.model_snapshot", ADAPTER), ("accounting.mode", "local")], valid=False)
case_with("weights nobody hosts cannot be billed to a subscription",
          [("agent.model_snapshot", WEIGHTS), ("accounting.mode", "subscription")], valid=False)
# The word is half the guard and the digest is the other half. A shouted prefix that the vendor
# branch admitted would be a local run wearing a vendor string, and the root constraint — keyed on
# the same prefix — would never ask which ledger it belonged to. The second of these reads the
# vendor branch on its own: under `local` the root constraint is already satisfied, so what refuses
# the row is the branch refusing to read a shouted word as a vendor string.
case_with("a digest has one spelling, and the word is part of it",
          [("agent.model_snapshot", "SHA256:" + "a" * 64), ("accounting.mode", "subscription")],
          valid=False)
case_with("which the vendor form refuses to read as a name",
          [("agent.model_snapshot", "SHA256:" + "a" * 64), ("accounting.mode", "local")],
          valid=False)
case_with("and its digits are lower case like every other digest here",
          [("agent.model_snapshot", "sha256:" + "A" * 64), ("accounting.mode", "local")],
          valid=False)


# Closure. `execution` enumerates its fields and admits nothing else, so a producer that wants to
# record a thing this contract has no field for amends the contract. A token count is the standing
# example: it belongs to `accounting.usage`, and a second home for it would be a second answer.
case_at("`execution` names its fields, and a token count is not one", "execution.tokens", 1,
        valid=False)
case_at("what `execution` cannot leave out is what it is required to carry",
        "execution.event_stream", DELETE, valid=False)


def version_case() -> tuple[str, dict, bool]:
    """What the contract declares, against the version every case above is written for.

    `schemas/VERSION` is the declaration and `CONTRACT_VERSION` is the assumption; a MINOR that
    moves one and not the other leaves them disagreeing, and this is the case that can say so. The
    disagreement is written into the record, which the schema then refuses, so the case goes red
    naming both values rather than passing on a comparison of the declaration with itself.
    """
    rec = conforming()
    declared = declared_version()
    if declared is None:
        rec["instrument"]["capture_version"] = "schemas/VERSION declares nothing"
    elif declared != CONTRACT_VERSION:
        rec["instrument"]["capture_version"] = (
            f"{declared} declared, {CONTRACT_VERSION} written")
    return ("these cases are written against the version the contract declares", rec, True)


CASES.append(version_case())


def main() -> int:
    with open(os.path.join(SCHEMAS, "run-record.schema.json"), encoding="utf-8") as fh:
        validator = Draft202012Validator(json.load(fh))
    failures = 0
    for name, record, want_valid in CASES:
        errors = sorted(validator.iter_errors(record), key=lambda e: list(e.path))
        got_valid = not errors
        if got_valid != want_valid:
            failures += 1
            said = "; ".join(f"{'/'.join(str(p) for p in e.path)}: {e.message}"
                             for e in errors[:2]) or "no error at all"
            print(f"::error title=schema_cases::{name}: expected "
                  f"{'valid' if want_valid else 'refused'}, got {said}")
    print(f"schema_cases: ran {len(CASES)} cases, {failures} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
