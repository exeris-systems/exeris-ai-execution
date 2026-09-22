"""`docs-mutation-v1` — the calibration suite of the `docs-guardrails` oracle.

An oracle whose `PASS` has never been contradicted by a known-broken input is not validated. This
suite is what contradicts it: eight deliberately broken repository states, each one a defect at
least one gate must catch, run as a suite with a published result — the condition ADR-086 §G.35
puts before any row may name this oracle with `calibration.status: pass`, and the eight mutants of
RFC-2026-09-08 §Testing.

The eight, with the gate each is aimed at:

  1. an ADR file whose number has no registry row                    — `registry_check`
  2. a registry row whose link does not resolve                      — `registry_check`
  3. a hand-edited rendered adapter                                  — `agents_render_check`
  4. a hand-edited vendored bundle policy                            — `agents_bundle_verify`
  5. a deprecated `public-staged` visibility value                   — `frontmatter_check`
  6. a narrative document whose frontmatter lacks `last-verified`    — `frontmatter_check`
  7. an agent profile whose output schema does not exist             — `agents_file_check`
  8. an empty or absent corpus reported as clean                     — every gate, by refusing to run

The eighth catches the instrument rather than the target, and it is the only one that expects
`UNKNOWN` instead of `FALSE_DONE`: a corpus with nothing in it has nothing to be wrong about, so
the honest verdict is that nothing was judged. It is run in both of its forms — a checkout whose
documentation has been removed, and a path that is not there — because the two reach the oracle by
different routes and only one of them would be noticed if the other were assumed.

**The unmutated copy is part of the suite.** A suite that has never seen a pass proves only that
the oracle can say no, which a gate that fails on everything also does. `status: pass` therefore
needs 8/8 **and** a clean copy judged `TRUE_DONE`; either half missing leaves `status: fail`, and
under fail-closed accounting every documentation row stays `UNKNOWN` until both hold.

The corpus is copied from a checkout at a named ref and the copy is what is mutated; the checkout
itself is read and never written. `--check` recomputes the suite and compares it with the published
file instead of rewriting it, which is what CI needs: a published calibration that is no longer
true is the failure mode the file exists to prevent.

Usage:
    python3 -m oracles.docs_mutation_v1 --corpus <checkout> --out <oracle-selftest.json> [--check]
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Callable

from . import FALSE_DONE, TRUE_DONE, UNKNOWN
from .docs_guardrails import corpus as documentation_corpus
from .docs_guardrails import default_agents_tools, default_guardrails, judge

SUITE = "docs-mutation-v1"
# A record states a decision as of its own date and cannot drift, so the shared taxonomy asks
# `last-verified` of narrative documents only. Mutant 6 must therefore break a narrative one: the
# same deletion in a record is not a defect and would calibrate the oracle against a rule nobody
# holds.
RECORD_TYPES = frozenset({"adr", "adr-link", "rfc", "research"})
LINK = re.compile(r"\]\(([^)]+)\)")


class MutationError(Exception):
    """The corpus does not offer what a mutant needs to break.

    Reported, never skipped: a mutant that quietly did not mutate is a suite that scores itself on
    a checkout it left alone.
    """


# ---------------------------------------------------------------------------------------------
# The mutations. Each takes the root of a copy, breaks exactly one thing, and says what it broke.
# ---------------------------------------------------------------------------------------------

def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _documents(root: str) -> list[str]:
    """Markdown under the root, skipping trees and files that are not an ordinary page.

    The provider entry files and the registry are left alone although they are documentation: each
    is also read by another gate, and a mutant must break one gate's rule so that the suite
    measures the gate it names rather than whichever one happened to notice.
    """
    entry_files = {"AGENTS.md", "CLAUDE.md", "GEMINI.md", "adr-index.md"}
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith((".", "_")))
        for name in sorted(filenames):
            if name.endswith(".md") and name not in entry_files:
                found.append(os.path.join(dirpath, name))
    return sorted(found)


def _frontmatter(text: str) -> list[str]:
    """The lines of the leading `---` block, or an empty list where there is none."""
    if not text.startswith("---\n"):
        return []
    end = text.find("\n---", 4)
    return text[4:end].splitlines() if end > 0 else []


def _records_dir(root: str) -> str:
    for candidate in ("adr", os.path.join("docs", "adr")):
        if os.path.isdir(os.path.join(root, candidate)):
            return candidate
    raise MutationError("the corpus has no records directory")


def _index_path(root: str) -> str:
    path = os.path.join(root, "adr-index.md")
    if not os.path.isfile(path):
        raise MutationError("the corpus holds no registry")
    return path


def _index_rows(text: str) -> list[tuple[int, str]]:
    """The numbered rows of the registry's own index section, with their line numbers.

    Scoped to that section because the tables after it carry cross-repo stubs, which the registry
    check reads under a different rule — a mutation there would be aimed at the wrong gate.
    """
    rows, inside = [], False
    for i, line in enumerate(text.splitlines()):
        if line.startswith("## Index"):
            inside = True
            continue
        if inside and line.startswith("## "):
            break
        if inside and re.match(r"^\|\s*\d{3}\s*\|", line):
            rows.append((i, line))
    return rows


def _no_registry_row(root: str) -> str:
    records = _records_dir(root)
    index = _read(_index_path(root))
    taken = {re.match(r"^\|\s*(\d{3})", line).group(1) for _, line in _index_rows(index)}
    number = next((f"{n:03d}" for n in range(999, 99, -1) if f"{n:03d}" not in taken), None)
    if number is None:
        raise MutationError("every three-digit number already has a row")
    source = next((f for f in sorted(os.listdir(os.path.join(root, records)))
                   if re.match(r"^ADR-\d{3}-.*\.md$", f)), None)
    if source is None:
        raise MutationError("the records directory holds no ADR to copy")
    target = f"ADR-{number}-a-number-with-no-registry-row.md"
    shutil.copyfile(os.path.join(root, records, source), os.path.join(root, records, target))
    return f"{records}/{target} has no row in adr-index.md"


def _unresolvable_link(root: str) -> str:
    path = _index_path(root)
    text = _read(path)
    lines = text.splitlines(keepends=True)
    for i, line in _index_rows(text):
        # A row marked as pending its branch is allowed an unresolved link, so it would be caught
        # as a warning and not as a finding. The mutant has to land on a row the gate is strict
        # about, which is an accepted one.
        if "accepted" not in line.lower() or "pending merge" in line.lower():
            continue
        for target in LINK.findall(line):
            if target.startswith("http"):
                continue
            local = os.path.join(root, target.split("#")[0])
            if os.path.exists(local):
                broken = target[:-3] + "-not-on-disk.md" if target.endswith(".md") \
                    else target + "-not-on-disk"
                lines[i] = lines[i].replace(f"]({target})", f"]({broken})")
                _write(path, "".join(lines))
                return f"a registry row links to {broken}, which is not on disk"
    raise MutationError("no accepted registry row links to a file in this corpus")


def _hand_edited_adapter(root: str) -> str:
    marker = re.compile(r"do[- ]not[- ]edit|generated from|@generated", re.I)
    for dirpath, dirnames, filenames in os.walk(os.path.join(root, ".claude")):
        dirnames[:] = sorted(dirnames)
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            if name.endswith(".md") and marker.search(_read(path)):
                _write(path, _read(path) + "\nA line nobody rendered.\n")
                return f"{os.path.relpath(path, root)} differs from what the renderer emits"
    raise MutationError("the corpus holds no rendered adapter")


def _hand_edited_vendored_policy(root: str) -> str:
    vendor = os.path.join(root, ".agents", "vendor")
    for dirpath, dirnames, filenames in os.walk(vendor):
        dirnames[:] = sorted(dirnames)
        if os.path.basename(dirpath) != "policies":
            continue
        for name in sorted(filenames):
            if name.endswith(".md"):
                path = os.path.join(dirpath, name)
                _write(path, _read(path) + "\nA clause nobody vendored.\n")
                return f"{os.path.relpath(path, root)} no longer matches the pinned digest"
    raise MutationError("the corpus vendors no bundle policy")


def _deprecated_visibility(root: str) -> str:
    for path in _documents(root):
        text = _read(path)
        for line in _frontmatter(text):
            if line.strip() == "visibility: public":
                _write(path, text.replace("\nvisibility: public\n",
                                          "\nvisibility: public-staged\n", 1))
                return f"{os.path.relpath(path, root)} carries the deprecated visibility value"
    raise MutationError("no document declares a visibility this mutant can deprecate")


def _missing_last_verified(root: str) -> str:
    for path in _documents(root):
        text = _read(path)
        front = _frontmatter(text)
        kind = next((l.split(":", 1)[1].strip() for l in front if l.startswith("type:")), "")
        if kind in RECORD_TYPES or not kind:
            continue
        for line in front:
            if line.startswith("last-verified:"):
                _write(path, text.replace(f"\n{line}\n", "\n", 1))
                return f"{os.path.relpath(path, root)} is narrative and declares no last-verified"
    raise MutationError("no narrative document declares last-verified")


def _missing_output_schema(root: str) -> str:
    profiles = os.path.join(root, ".agents", "agents")
    for name in sorted(os.listdir(profiles)) if os.path.isdir(profiles) else []:
        path = os.path.join(profiles, name, "AGENT.md")
        if not os.path.isfile(path):
            continue
        text = _read(path)
        for line in _frontmatter(text):
            if line.startswith("output:"):
                absent = "schemas/a-schema-that-is-not-on-disk.schema.json"
                if os.path.exists(os.path.join(root, ".agents", absent)):
                    raise MutationError("the absent schema this mutant names is on disk")
                _write(path, text.replace(f"\n{line}\n", f"\noutput: {absent}\n", 1))
                return f"the {name} profile declares an output schema that is not on disk"
    raise MutationError("no agent profile declares an output schema")


@dataclass(frozen=True)
class Mutant:
    id: int
    gate: str
    expected: str
    apply: Callable[[str], str] | None


# The table is the suite. An expectation is weakened by editing this list, which is a change a
# reviewer sees, rather than by a runner deciding a mutant was unreasonable.
MUTANTS: tuple[Mutant, ...] = (
    Mutant(1, "registry_check", FALSE_DONE, _no_registry_row),
    Mutant(2, "registry_check", FALSE_DONE, _unresolvable_link),
    Mutant(3, "agents_render_check", FALSE_DONE, _hand_edited_adapter),
    Mutant(4, "agents_bundle_verify", FALSE_DONE, _hand_edited_vendored_policy),
    Mutant(5, "frontmatter_check", FALSE_DONE, _deprecated_visibility),
    Mutant(6, "frontmatter_check", FALSE_DONE, _missing_last_verified),
    Mutant(7, "agents_file_check", FALSE_DONE, _missing_output_schema),
    # No mutation function: the eighth is built by removing the corpus, and is judged twice.
    Mutant(8, "every gate", UNKNOWN, None),
)


# ---------------------------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------------------------

def entry(mutant: Mutant, judgement, detail: str = "") -> dict:
    """One row of the published table: what was expected of the oracle, and what it did.

    A mutant is caught when the outcome is the expected one **and**, where a defect was planted,
    the gate aimed at it is among the gates that failed. Without the second half a mutant passes on
    any failure at all, which scores the suite on the oracle noticing something else.
    """
    failed = judgement.failed()
    ok = judgement.outcome == mutant.expected
    if mutant.expected == FALSE_DONE:
        ok = ok and mutant.gate in failed
    observed = judgement.outcome + (f" ({'+'.join(failed)})" if failed else "")
    row = {"id": mutant.id, "gate": mutant.gate, "expected": mutant.expected,
           "observed": observed, "ok": ok}
    if detail:
        row["detail"] = detail
    return row


def tally(clean_ok: bool, mutants: list[dict]) -> tuple[str, str]:
    """The suite's status and score.

    `pass` needs every mutant caught and the unmutated copy judged `TRUE_DONE`. Neither half is
    negotiable: without the first the oracle has an uncontradicted `PASS`, and without the second
    it has never been seen to return one.
    """
    caught = sum(1 for m in mutants if m["ok"])
    status = "pass" if clean_ok and caught == len(MUTANTS) else "fail"
    return status, f"{caught}/{len(MUTANTS)}"


# ---------------------------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------------------------

def _git(path: str, *args: str) -> str:
    """One git command's stdout, run inside `path` rather than told where `path` is.

    `path` becomes the process's own working directory instead of a `-C` argument, so a directory
    this process did not choose reaches git as where it runs and never as one more thing on its
    command line.
    """
    proc = subprocess.run(["git", *args], cwd=path, capture_output=True, text=True)
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _repository(path: str) -> str:
    """`owner/name` for the corpus, from its remote where it has one."""
    url = _git(path, "config", "--get", "remote.origin.url")
    if not url:
        return os.path.basename(os.path.abspath(path))
    url = url.removesuffix(".git")
    parts = url.replace(":", "/").split("/")
    return "/".join(parts[-2:]) if len(parts) >= 2 else url


def _export(corpus_path: str, ref: str, into: str) -> str:
    """A clean copy of the corpus at `ref`, and the commit it resolved to.

    An export of a ref rather than a copy of the working tree: the suite's result names a commit,
    and a result that names a commit it did not read is not reproducible. The ref falls back to
    `HEAD` for a checkout that has no remote-tracking branch — a shallow CI checkout is one — and
    the commit recorded is whichever was actually exported.
    """
    commit = ""
    for candidate in (ref, "HEAD"):
        commit = _git(corpus_path, "rev-parse", "--verify", f"{candidate}^{{commit}}")
        if commit:
            ref = candidate
            break
    if not commit:
        raise MutationError(f"{corpus_path} is not a checkout whose {ref} resolves")
    os.makedirs(into, exist_ok=True)
    archive = os.path.join(os.path.dirname(into), "corpus.tar")
    subprocess.run(["git", "archive", "--format=tar", "-o", archive, ref],
                   cwd=corpus_path, check=True)
    shutil.unpack_archive(archive, into, format="tar")
    os.remove(archive)
    return commit


def run(corpus_path: str, *, guardrails: str, agents_tools: str, index: str | None = None,
        ref: str = "origin/main") -> dict:
    """Build the eight mutants from a clean copy, judge each, and assemble the published result.

    The three directories are resolved to real paths once, here, at the boundary where they arrive
    from the caller, so that every git command below runs inside one of them rather than being told
    where it is on a command line.
    """
    corpus_path = os.path.realpath(corpus_path)
    guardrails = os.path.realpath(guardrails)
    agents_tools = os.path.realpath(agents_tools)
    now = datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    result = {
        "suite": SUITE,
        "run_at": now,
        "status": "fail",
        "result": f"0/{len(MUTANTS)}",
        "corpus": {"repository": _repository(corpus_path), "commit": ""},
        "guardrails_commit": _git(guardrails, "rev-parse", "HEAD") or "unknown",
        "agents_tools_commit": _git(os.path.dirname(agents_tools.rstrip(os.sep)),
                                    "rev-parse", "HEAD") or "unknown",
        "clean_corpus": {"expected": TRUE_DONE, "observed": "", "ok": False},
        "mutants": [],
    }
    with tempfile.TemporaryDirectory(prefix="docs-mutation-v1-") as work:
        clean = os.path.join(work, "clean")
        result["corpus"]["commit"] = _export(corpus_path, ref, clean)

        def verdict(path: str):
            return judge(path, guardrails=guardrails, agents_tools=agents_tools, index=index)

        clean_judgement = verdict(clean)
        result["clean_corpus"] = {
            "expected": TRUE_DONE,
            "observed": clean_judgement.outcome + (
                f" ({'+'.join(clean_judgement.failed())})" if clean_judgement.failed() else ""),
            "ok": clean_judgement.outcome == TRUE_DONE,
        }
        for mutant in MUTANTS:
            if mutant.apply is None:
                result["mutants"].append(_eighth(work, clean, guardrails, verdict))
                continue
            root = os.path.join(work, f"m{mutant.id}")
            shutil.copytree(clean, root, symlinks=True)
            try:
                detail = mutant.apply(root)
            except MutationError as exc:
                result["mutants"].append({"id": mutant.id, "gate": mutant.gate,
                                          "expected": mutant.expected,
                                          "observed": f"not built: {exc}", "ok": False})
                continue
            result["mutants"].append(entry(mutant, verdict(root), detail))
    result["status"], result["result"] = tally(result["clean_corpus"]["ok"], result["mutants"])
    return result


def _eighth(work: str, clean: str, guardrails: str, verdict) -> dict:
    """The mutant that catches the instrument: an empty corpus and an absent one.

    Both must be `UNKNOWN`. Emptying the corpus removes the documentation the shared taxonomy
    admits and leaves everything else in place, because that is the state a checker walks into and
    reports nothing about — a copy stripped to nothing would be caught by its own absence instead.
    """
    mutant = MUTANTS[-1]
    empty = os.path.join(work, "m8-empty")
    shutil.copytree(clean, empty, symlinks=True)
    files, why = documentation_corpus(empty, guardrails)
    if not files:
        return {"id": mutant.id, "gate": mutant.gate, "expected": mutant.expected,
                "observed": f"not built: {why}", "ok": False}
    for path in files:
        os.remove(os.path.join(empty, path))
    absent = os.path.join(work, "m8-absent")
    # Scored through the same function as every other mutant, once per probe: this mutant is
    # caught when *both* probes are, and a rule spelled a second time here would be the place the
    # two could differ.
    probes = [entry(mutant, verdict(empty)), entry(mutant, verdict(absent))]
    return {"id": mutant.id, "gate": mutant.gate, "expected": mutant.expected,
            "observed": "+".join(dict.fromkeys(p["observed"] for p in probes)),
            "ok": all(p["ok"] for p in probes),
            "detail": "an emptied corpus and a path that is not there"}


def _validated_out(out: str, corpus_path: str) -> str | None:
    """`--out`'s real path, or None where it lands outside the trees this run is allowed to touch.

    `--out` is read back under `--check` and written otherwise, and the one CI job that runs this
    suite writes it at a path relative to the checkout it runs in — under the working directory.
    Requiring the real path to land there, or under the corpus this run judged, is exactly what
    that use holds to, while refusing a path built to resolve anywhere else this process was not
    told to read or write.
    """
    real = os.path.realpath(out)
    for root in (os.path.realpath(os.getcwd()), os.path.realpath(corpus_path)):
        if real == root or real.startswith(root + os.sep):
            return real
    return None


def _table(result: dict) -> str:
    lines = ["| mutant | gate | expected | observed | ok |", "|--:|:--|:--|:--|:--|"]
    for row in result["mutants"]:
        lines.append(f"| {row['id']} | {row['gate']} | {row['expected']} | {row['observed']} "
                     f"| {'yes' if row['ok'] else 'NO'} |")
    clean = result["clean_corpus"]
    lines.append(f"| clean | every gate | {clean['expected']} | {clean['observed']} "
                 f"| {'yes' if clean['ok'] else 'NO'} |")
    lines.append("")
    lines.append(f"{SUITE}: {result['status']} — {result['result']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corpus", required=True, help="a checkout of the corpus, read and not written")
    ap.add_argument("--out", required=True, help="where the published result is written")
    ap.add_argument("--ref", default="origin/main", help="the ref the clean copy is exported from")
    ap.add_argument("--guardrails", default=None)
    ap.add_argument("--agents-tools", default=None)
    ap.add_argument("--index", default=None)
    ap.add_argument("--check", action="store_true",
                    help="compare with the published file instead of writing it")
    a = ap.parse_args(argv)
    out = _validated_out(a.out, a.corpus)
    if out is None:
        print(f"::error title={SUITE}::--out ({a.out}) resolves outside the working directory "
              f"and outside --corpus ({a.corpus}); refusing to read or write it")
        return 2
    try:
        result = run(a.corpus, guardrails=a.guardrails or default_guardrails(),
                     agents_tools=a.agents_tools or default_agents_tools(),
                     index=a.index, ref=a.ref)
    except (MutationError, subprocess.CalledProcessError) as exc:
        print(f"::error title={SUITE}::the suite could not be built: {exc}")
        return 2
    print(_table(result))
    if a.check:
        try:
            with open(out, encoding="utf-8") as fh:
                published = json.load(fh)
        except (OSError, ValueError) as exc:
            print(f"::error title={SUITE}::no published result to check against: {exc}")
            return 1
        for key in ("status", "result"):
            if published.get(key) != result[key]:
                print(f"::error title={SUITE}::the published {key} is "
                      f"{published.get(key)!r}; this run reports {result[key]!r}")
                return 1
    else:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
