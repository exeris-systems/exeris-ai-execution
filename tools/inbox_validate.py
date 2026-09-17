#!/usr/bin/env python3
"""The inbox validator — ADR-086 §G.33, enforcing the cross-file rules of §G.34.

`inbox/README.md` promises that a non-conforming row is "reported back to the producer". Until
something reads the inbox that promise is false from the first row, which is why this is the layer's
first tooling and why it exists before any row lands.

Standard library only, on purpose: it runs in CI, in a producer's own workflow before it opens an
inbox pull request (ADR-087 §C.15), and on a maintainer's machine, and none of those should need an
install step to tell a good row from a bad one.

What it does NOT do: full JSON Schema validation. The schemas are Draft 2020-12 compositions and
checking one properly needs a validator this file may not import. So schema conformance is
**checkable, not checked** here — `.github/workflows/inbox.yml` runs it with `jsonschema` installed,
and this file reports the structural subset it can see without one: the required top-level keys the
schema names, and the identity between a record's filename and the id inside it.
"""

import argparse
import json
import os
import sys

RUNS, JUDGEMENTS = "runs", "judgements"
SCHEMA_OF = {RUNS: "run-record.schema.json", JUDGEMENTS: "judgement-record.schema.json"}
ID_OF = {RUNS: "run_id", JUDGEMENTS: "judgement_id"}


class Report:
    """Errors as GitHub annotations plus a summary, the shape every script in the org prints."""

    def __init__(self) -> None:
        self.bad: list[tuple[str, str]] = []
        self.files = 0

    def error(self, where: str, message: str) -> None:
        self.bad.append((where, message))

    def emit(self, notes: list[str]) -> int:
        for where, message in self.bad:
            print(f"::error file={where}::{message}")
        print(f"\n## inbox_validate\n\nChecked **{self.files}** record(s) — "
              f"**{len(self.bad)} error(s)**.")
        for note in notes:
            print(f"\n{note}")
        if self.bad:
            print("\n| File | Problem |\n|:--|:--|")
            for where, message in self.bad:
                print(f"| `{where}` | {message} |")
        return 1 if self.bad else 0


def records(inbox: str) -> list[tuple[str, str, dict | None, str | None]]:
    """Every record under the inbox as (relative path, kind, parsed body, parse error)."""
    out = []
    for here, _dirs, names in os.walk(inbox):
        kind = os.path.basename(here)
        if kind not in SCHEMA_OF:
            continue
        for name in sorted(names):
            if not name.endswith(".json"):
                continue
            path = os.path.join(here, name)
            try:
                with open(path, encoding="utf-8") as fh:
                    out.append((path, kind, json.load(fh), None))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                out.append((path, kind, None, str(exc)))
    return out


def required_keys(schemas: str, kind: str) -> list[str]:
    """The top-level keys the schema names as required — read, not validated against."""
    try:
        with open(os.path.join(schemas, SCHEMA_OF[kind]), encoding="utf-8") as fh:
            return list(json.load(fh).get("required") or [])
    except OSError:
        return []


def check(root: str, rep: Report) -> list[str]:
    inbox = os.path.join(root, "inbox")
    schemas = os.path.join(root, "schemas")
    identity = os.path.join(inbox, "inbox.json")
    try:
        with open(identity, encoding="utf-8") as fh:
            declared = json.load(fh).get("visibility")
    except OSError:
        rep.error(identity, "the inbox declares no identity — `inbox.json` is the inbox's "
                            "visibility and rule 1 of §G.34 has nothing to compare a row against")
        return []

    found = records(inbox)
    rep.files = len(found)
    runs: dict[str, dict] = {}
    groups: dict[str, list[tuple[str, dict]]] = {}

    # Runs first, then judgements: rule 5 asks whether a judgement's run is in this inbox,
    # and `os.walk` reaches `judgements/` before `runs/`. Validating in directory order made
    # a conforming inbox report its own judgement as unresolved — found by the suite, which
    # is what a suite is for.
    for path, kind, body, parse_error in sorted(found, key=lambda r: r[1] != RUNS):
        if parse_error is not None:
            rep.error(path, f"not JSON, so it is not a record: {parse_error}")
            continue
        missing = [k for k in required_keys(schemas, kind) if k not in body]
        if missing:
            rep.error(path, f"the schema requires {', '.join('`%s`' % m for m in missing)}, "
                            f"absent here — a producer defect, repaired at the producer")
        stem = os.path.splitext(os.path.basename(path))[0]
        own_id = body.get(ID_OF[kind])
        if own_id is not None and own_id != stem:
            rep.error(path, f"the file is named `{stem}` and calls itself `{own_id}` — one record "
                            f"per file means the name is the id")
        if kind == RUNS:
            # Rule 1. One visibility per inbox, fail-closed: a row that does not match belongs in
            # the sibling repository's inbox, not repaired into this one.
            state = body.get("repository_state") or {}
            if state.get("visibility") != declared:
                rep.error(path, f"declares `repository_state.visibility: "
                                f"{state.get('visibility')!r}` in an inbox whose identity is "
                                f"{declared!r} — it belongs in the sibling inbox")
            # An alias is not a snapshot (ADR-086, amendment of 2026-09-17). Not a cross-file
            # rule, and here for the reason those are: JSON Schema compares a value against a
            # constant, never against its sibling, so this is the only place that can say it.
            # Measured over every execution log the review runner had produced by that date — none
            # exposes a dated snapshot for the model that takes the turns. Writing the alias into
            # the snapshot field would make every row claim a precision no row has.
            agent = body.get("agent") or {}
            model_id, snapshot = agent.get("model_id"), agent.get("model_snapshot")
            if isinstance(model_id, str) and isinstance(snapshot, str) and snapshot == model_id:
                rep.error(path, f"`agent.model_snapshot` repeats `agent.model_id` "
                                f"(`{model_id}`) — an alias is not a snapshot, and a runtime that "
                                f"exposes none is recorded as `unresolved:{model_id}`")
            if own_id is not None:
                runs[str(own_id)] = body
            group = (body.get("pairing") or {}).get("group_id")
            if group:
                groups.setdefault(str(group), []).append((path, body))
        else:
            # Rule 5. A judgement is about a run, and a judgement whose run is not here is either
            # about a row that never landed or a typo; both are the producer's to fix.
            target = body.get("run_id")
            if target is not None and str(target) not in runs:
                rep.error(path, f"judges `{target}`, which is not a run record in this inbox — "
                                f"a judgement resolves to its run or it is about nothing")

    for group, members in sorted(groups.items()):
        # Rules 2 and 3. A group is one task measured across arms; the tree it was measured on and
        # the human it is compared against are properties of the task, so they cannot differ by arm.
        for field, why in (("repository_state", "the tree a group was measured on is one tree"),
                           ("human_baseline", "a group is compared against one human run")):
            seen = {json.dumps(m.get(field), sort_keys=True, ensure_ascii=False)
                    for _p, m in members if field in m}
            if len(seen) > 1:
                for path, _m in members:
                    rep.error(path, f"`{field}` differs inside `group_id: {group}` — {why}, so "
                                    f"rows that disagree are not one group")
        # Rule 4. An arm is a slot in a plan; two rows in the same slot are a collision, not a
        # replicate, and a replicate is a distinct slot (§E.22).
        arms: dict[str, list[str]] = {}
        for path, member in members:
            arms.setdefault(str((member.get("pairing") or {}).get("arm")), []).append(path)
        for arm, paths in sorted(arms.items()):
            if len(paths) > 1:
                for path in paths:
                    rep.error(path, f"`arm: {arm}` appears {len(paths)} times in `group_id: "
                                    f"{group}` — an arm is a slot, and a replicate is its own slot")

    return [
        "Full JSON Schema conformance is **checkable, not checked** by this file: the schemas are "
        "Draft 2020-12 compositions and this validator imports nothing. What runs it against them "
        "is the `schema` job of `.github/workflows/inbox.yml`. What is checked here is the "
        "cross-file half of ADR-086 §G.34, which no single schema can express.",
    ]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--root", default=".", help="the repository holding `inbox/` and `schemas/`")
    args = p.parse_args()
    rep = Report()
    notes = check(args.root, rep)
    return rep.emit(notes)


if __name__ == "__main__":
    sys.exit(main())
