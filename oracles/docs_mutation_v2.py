"""`docs-mutation-v2` — the calibration suite of the `docs-guardrails` oracle with its two semantic gates.

`docs-mutation-v1` calibrates the five structural gates, and every one of its mutants is a corpus
that is malformed. A run can also leave a corpus perfectly well-formed and still wrong: prose the
task said to keep deleted under frontmatter that was added correctly, or a link stub that names a
record by some other record's title. v1 has no mutant of either shape, so a PASS from the oracle on
them had never been contradicted. This suite is v1's eight mutants, unchanged and imported rather
than restated, plus three that contradict it:

  9. a preserved file keeps valid frontmatter and loses body lines     — `content_preserved`
 10. an ADR link stub whose title and heading name a different record — `adr_links_resolve`
 11. a correct stub, with the bridge path pointing at nothing          — `adr_links_resolve`, by
     refusing to run

The eleventh expects `UNKNOWN`, as v1's eighth does, and for the same reason: it catches the
instrument. Stubs the registry was never asked about are not stubs that passed, and the suite
requires both the outcome and the gate's own statement that it could not run.

**The clean copy exercises every gate.** It is the export committed as the base of a git repository,
with a correct link stub for a record the index holds added on top, judged with that base, a
`**/*.md` preserve pattern and the bridge — so `content_preserved` and `adr_links_resolve` both pass
on it rather than being carried by gates that did not apply. v1's eight are judged with v1's inputs
plus the bridge, which has nothing to read in a corpus holding no stubs; mutant 8 in particular is
judged without a preserve pattern, because an emptied corpus with one is a deletion the task
forbade and `FALSE_DONE` by design — the instrument question it asks is the one v1 asks.

`status: pass` needs 11/11 and the clean copy `TRUE_DONE`. The bridge's version and commit are
published beside the result, because two of the gates judged here are the bridge's reading of the
registry, and a calibration is only interpretable against the instrument that produced it.

Usage:
    python3 -m oracles.docs_mutation_v2 --corpus <checkout> --out <oracle-selftest-v2.json>
        --bridge <dist/server.js> [--check]
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Callable

from . import FALSE_DONE, PASS, TRUE_DONE, UNKNOWN, adr_links, preservation
from . import docs_mutation_v1 as v1
from .docs_guardrails import default_agents_tools, default_guardrails, judge
from .docs_guardrails import existing_directory, existing_file
from .paths import listed_path

SUITE = "docs-mutation-v2"
PRESERVE = ("**/*.md",)
#: How many body lines mutant 9 removes from the file it breaks, and how long a body must be for
#: the file to be chosen: a body that loses everything is caught by other rules as well.
LOST_LINES = 5
MIN_BODY_LINES = 3 * LOST_LINES
#: Where the stubs go: the consumer layout, which is where the registry expects a stub to be.
STUB_DIR = ("docs", "adr")
OWNER = "exeris-docs"
#: A fixed identity and date for the base commit, so a mutant tree's history is the same bytes on
#: every run and nothing about the machine running the suite enters it.
BASE_ENV = {f"GIT_{role}_{field}": value for role in ("AUTHOR", "COMMITTER")
            for field, value in (("NAME", SUITE), ("EMAIL", "suite@invalid"),
                                 ("DATE", "2000-01-01T00:00:00Z"))}

MUTANTS: tuple[v1.Mutant, ...] = (
    *v1.MUTANTS,
    v1.Mutant(9, preservation.CHECK, FALSE_DONE, None),
    v1.Mutant(10, adr_links.CHECK, FALSE_DONE, None),
    v1.Mutant(11, adr_links.CHECK, UNKNOWN, None),
)


# ---------------------------------------------------------------------------------------------
# What the three new mutants break
# ---------------------------------------------------------------------------------------------

def records(root: str) -> list[tuple[int, str, str]]:
    """`(number, title, file)` for each accepted index row the corpus itself owns and holds.

    The title is the record's own heading, which is what the bridge reads for a record it can
    resolve; a record whose heading is missing is not offered.
    """
    index = v1._read(v1._index_path(root))
    found = []
    for _, line in v1._index_rows(index):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        target = next((t for t in v1.LINK.findall(line) if t.startswith("adr/")), None)
        if len(cells) < 3 or cells[2] != OWNER or "accepted" not in line.lower() or not target:
            continue
        # Walked one listing at a time: the target is text from the index, not a path to join.
        path = listed_path(root, *target.split("/"))
        title = adr_links.record_title(v1._read(path)) if path and os.path.isfile(path) else None
        if title:
            found.append((int(cells[0]), title, target))
    if len(found) < 2:
        raise v1.MutationError("the index offers fewer than two records this corpus owns")
    return found


def stub_text(number: int, title: str, target: str) -> str:
    return (f'---\ntitle: "ADR-{number:03d}: {title} (link stub)"\ntype: adr-link\n'
            f"visibility: public\nowning-repo: {OWNER}\nstatus: active\n---\n\n"
            f"# ADR-{number:03d}: {title} (link stub)\n\n"
            f"**Authoritative copy:** [`{OWNER}/{target}`]"
            f"(https://github.com/exeris-systems/{OWNER}/blob/main/{target})\n")


def _write_stub(root: str, number: int, title: str, target: str) -> str:
    directory = os.path.join(root, *STUB_DIR)
    os.makedirs(directory, exist_ok=True)
    rel = "/".join((*STUB_DIR, f"ADR-{number:03d}.link.md"))
    v1._write(os.path.join(root, rel), stub_text(number, title, target))
    return rel


def correct_stub(root: str) -> str:
    number, title, target = records(root)[0]
    return f"{_write_stub(root, number, title, target)} names ADR-{number:03d} as the record does"


def stub_with_wrong_title(root: str) -> str:
    (number, _, target), (other, title, _) = records(root)[:2]
    rel = _write_stub(root, number, title, target)
    return f"{rel} names ADR-{number:03d} by the title of ADR-{other:03d}"


def body_lines_lost(root: str) -> str:
    """Remove the last body lines of the first document with frontmatter long enough to lose them."""
    for path in v1._documents(root):
        with open(path, "rb") as fh:
            data = fh.read()
        if not v1._frontmatter(data.decode("utf-8", "replace")):
            continue
        lines = preservation.body(data).splitlines(keepends=True)
        if len(lines) < MIN_BODY_LINES:
            continue
        keep = len(data) - len(b"".join(lines[-LOST_LINES:]))
        with open(path, "wb") as fh:
            fh.write(data[:keep])
        return (f"{os.path.relpath(path, root)} keeps its frontmatter and loses its last "
                f"{LOST_LINES} body lines")
    raise v1.MutationError("no document with frontmatter has a body long enough to shorten")


# ---------------------------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------------------------

def _commit_base(repo: str) -> str:
    """Make `repo` a git repository whose one commit is its current tree, and return that commit."""
    env = {**os.environ, **BASE_ENV}
    for args in (("init", "-q"), ("add", "-A"),
                 ("-c", "commit.gpgsign=false", "commit", "-q", "-m", "base")):
        subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True)
    commit = _head(repo)
    if not preservation.COMMIT.fullmatch(commit):
        raise v1.MutationError("the base commit did not resolve")
    return commit


def _head(path: str) -> str:
    return v1._git(path, "rev-parse", "HEAD")


def _not_built(mutant: v1.Mutant, why) -> dict:
    return {"id": mutant.id, "gate": mutant.gate, "expected": mutant.expected,
            "observed": f"not built: {why}", "ok": False}


def _observed(judgement) -> str:
    failed = judgement.failed()
    return judgement.outcome + (f" ({'+'.join(failed)})" if failed else "")


def clean_ok(judgement) -> bool:
    """`TRUE_DONE` with both semantic gates among those that passed.

    A clean copy on which either gate did not apply would be a `TRUE_DONE` carried by the structural
    gates alone, which says nothing about whether the new gates can pass at all.
    """
    passed = {g.check for g in judgement.gates if g.result == PASS}
    return judgement.outcome == TRUE_DONE and {preservation.CHECK, adr_links.CHECK} <= passed


def _instrument_unavailable(row: dict, judgement, check: str) -> dict:
    """`row`, not caught unless the gate it names says it could not run."""
    gate = next((g for g in judgement.gates if g.check == check), None)
    if gate is None or gate.available:
        row = {**row, "ok": False}
    return row


def _built(work: str, source: str, mutant: v1.Mutant, apply: Callable[[str], str]):
    root = os.path.join(work, f"m{mutant.id}")
    shutil.copytree(source, root, symlinks=True)
    try:
        return root, apply(root)
    except v1.MutationError as exc:
        return None, _not_built(mutant, exc)


def _v1_rows(work: str, clean: str, guardrails: str, verdict) -> list[dict]:
    rows = []
    for mutant in v1.MUTANTS:
        if mutant.apply is None:
            rows.append(v1._eighth(work, clean, guardrails, verdict))
            continue
        root, detail = _built(work, clean, mutant, mutant.apply)
        rows.append(detail if root is None else v1.entry(mutant, verdict(root), detail))
    return rows


def _v2_rows(work: str, clean: str, base_repo: str, base: str, judge_with) -> list[dict]:
    m9, m10, m11 = MUTANTS[8:11]
    rows = []
    root, detail = _built(work, base_repo, m9, body_lines_lost)
    rows.append(detail if root is None else
                v1.entry(m9, judge_with(root, base=base, preserve=PRESERVE), detail))
    root, detail = _built(work, clean, m10, stub_with_wrong_title)
    rows.append(detail if root is None else v1.entry(m10, judge_with(root), detail))
    root, detail = _built(work, clean, m11, correct_stub)
    if root is None:
        rows.append(detail)
    else:
        nowhere = os.path.join(work, "no-bridge", "dist", "server.js")
        judgement = judge_with(root, bridge=nowhere)
        rows.append(_instrument_unavailable(v1.entry(m11, judgement, detail), judgement, m11.gate))
    return rows


def _clean(work: str, base_repo: str, base: str, judge_with) -> dict:
    """The unmutated copy: the base plus a correct stub, judged with every input the gates take."""
    with_stub = os.path.join(work, "clean-with-stub")
    shutil.copytree(base_repo, with_stub, symlinks=True)
    row = {"expected": TRUE_DONE}
    try:
        row["detail"] = correct_stub(with_stub)
    except v1.MutationError as exc:
        return {**row, "observed": f"not built: {exc}", "ok": False}
    judgement = judge_with(with_stub, base=base, preserve=PRESERVE)
    return {**row, "observed": _observed(judgement), "ok": clean_ok(judgement)}


def run(corpus_path: str, *, guardrails: str, agents_tools: str, bridge: str,
        index: str | None = None, ref: str = "origin/main") -> dict:
    """Build the eleven mutants and the clean copy, judge each, and assemble the published result."""
    corpus_path = os.path.realpath(corpus_path)
    guardrails, agents_tools = os.path.realpath(guardrails), os.path.realpath(agents_tools)
    now = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

    def judge_with(path: str, **inputs):
        inputs.setdefault("bridge", bridge)
        return judge(path, guardrails=guardrails, agents_tools=agents_tools, index=index, **inputs)

    with tempfile.TemporaryDirectory(prefix=f"{SUITE}-") as work:
        clean = os.path.join(work, "clean")
        exported = v1._export(corpus_path, ref, clean)
        base_repo = os.path.join(work, "base")
        shutil.copytree(clean, base_repo, symlinks=True)
        base = _commit_base(base_repo)
        clean_row = _clean(work, base_repo, base, judge_with)
        rows = _v1_rows(work, clean, guardrails, judge_with) + \
            _v2_rows(work, clean, base_repo, base, judge_with)
    status, score = v1.tally(clean_row["ok"], rows, len(MUTANTS))
    return {
        "suite": SUITE, "run_at": now, "status": status, "result": score,
        "corpus": {"repository": v1._repository(corpus_path), "commit": exported},
        "guardrails_commit": _head(guardrails) or "unknown",
        "agents_tools_commit": _head(os.path.dirname(agents_tools.rstrip(os.sep))) or "unknown",
        "bridge": adr_links.instrument(bridge),
        "clean_corpus": clean_row,
        "mutants": rows,
    }


def _compare(out: str, result: dict) -> int:
    try:
        published = json.loads(v1._read(out))
    except (OSError, ValueError) as exc:
        print(f"::error title={SUITE}::no published result to check against: {exc}")
        return 1
    for key in ("status", "result"):
        if published.get(key) != result[key]:
            print(f"::error title={SUITE}::the published {key} is "
                  f"{published.get(key)!r}; this run reports {result[key]!r}")
            return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corpus", required=True, type=existing_directory,
                    help="a checkout of the corpus, read and not written")
    ap.add_argument("--out", required=True, help="where the published result is written")
    ap.add_argument("--ref", default="origin/main", help="the ref the clean copy is exported from")
    ap.add_argument("--bridge", required=True, type=existing_file,
                    help="the Exeris MCP server (dist/server.js) the registry is read through")
    ap.add_argument("--guardrails", type=existing_directory, default=None)
    ap.add_argument("--agents-tools", type=existing_directory, default=None)
    ap.add_argument("--index", type=existing_file, default=None)
    ap.add_argument("--check", action="store_true",
                    help="compare with the published file instead of writing it")
    a = ap.parse_args(argv)
    out = v1._validated_out(a.out, a.corpus)
    if out is None:
        print(f"::error title={SUITE}::--out ({a.out}) resolves outside the working directory "
              f"and outside --corpus ({a.corpus}); refusing to read or write it")
        return 2
    try:
        result = run(a.corpus, guardrails=a.guardrails or default_guardrails(),
                     agents_tools=a.agents_tools or default_agents_tools(), bridge=a.bridge,
                     index=a.index, ref=a.ref)
    except (v1.MutationError, subprocess.CalledProcessError) as exc:
        print(f"::error title={SUITE}::the suite could not be built: {exc}")
        return 2
    print(v1._table(result, SUITE))
    if a.check:
        if _compare(out, result):
            return 1
    else:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        v1._write(out, json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
