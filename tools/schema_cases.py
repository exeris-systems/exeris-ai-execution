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

# THE FIRST CONFORMING RECORD THIS REPOSITORY HAS. `inbox_validate_suite.py` builds a record too,
# but for the cross-file rules, which read four keys and ignore the rest — it does not validate, and
# it does not conform. The inbox holds no rows yet either, so until this file nothing had ever been
# checked against the schema, patterns included. Keep it conforming: every case below is this with
# one field changed, so a fixture that drifts out of conformance turns every case into a false red.
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
                      "event_stream": {"ref": "none", "sha256": "0" * 64, "event_count": 1}},
        "accounting": {"mode": "subscription"},
        "oracle": {"id": "ci-gates", "version": "1",
                   "calibration": {"suite": "inbox-validate", "status": "pass",
                                   "result": "16 cases, 0 failures", "run_at": "2026-09-17T12:00:00Z"}},
        "outcome": "TRUE_DONE",
        "instrument": {"capture_version": "0.1.0", "fence": "2026-09-17"},
    }


CASES: list[tuple[str, dict, bool]] = []


def case(name: str, *, valid: bool, **agent) -> None:
    rec = copy.deepcopy(conforming())
    rec["agent"].update(agent)
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

# The patterns still have to refuse something, or they are decoration.
case("a snapshot with a space", valid=False,
     model_id="m", model_snapshot="not a snapshot")
case("an id with a space", valid=False, model_id="a model", model_snapshot="m-1")
case("an id that is only punctuation", valid=False, model_id="--", model_snapshot="m-1")
case("a snapshot ending in punctuation", valid=False, model_id="m", model_snapshot="m-1-")


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
