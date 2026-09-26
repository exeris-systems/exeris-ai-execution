#!/usr/bin/env python3
"""Cases for the oracle interface and for what its calibration suite may call a pass.

Two rules are worth more than the code that implements them, and both are here rather than only in
a docstring, because a rule nobody has watched fail is a rule nobody knows is wired up:

  * how gates compose into an outcome (ADR-086 §E.19) — one failure is `FALSE_DONE`, every
    applicable gate passing with at least one having run is `TRUE_DONE`, and nothing having run is
    `UNKNOWN`, which includes the empty corpus and the checkout that is not there. *Applicable* is
    the load-bearing word and has its own cases below: a gate whose checker was not on disk is not
    an inapplicable gate, and a pass carried over one would be the eighth mutant at the level of a
    single gate;
  * what `docs-mutation-v1` may publish as `status: pass` — 8/8 and a clean copy judged
    `TRUE_DONE`, never one without the other — and what `docs-mutation-v2` may, at 11/11;
  * when the two semantic gates apply: `content_preserved` only when the task named something to
    keep, and never as a pass when the base it compares against could not be read;
    `adr_links_resolve` only when the checkout holds stubs, and never as a pass when no bridge
    answered for them.

Fixtures only: gates are built by hand, the judgements that need no checker at all touch only a
temporary directory, and the registry is read through `fixtures/mcp/stub_bridge.py`, a stand-in
server this interpreter runs. A case here never needs a real corpus or a Node runtime, which is
what lets this run in every job while the mutation suites run where the corpus is.
"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from oracles import (FAIL, FALSE_DONE, NOT_RUN, PASS, TRUE_DONE, UNKNOWN,  # noqa: E402
                     Gate, Judgement, outcome_of)
from oracles import adr_links, docs_guardrails, docs_mutation_v1, docs_mutation_v2  # noqa: E402
from oracles import preservation  # noqa: E402

STUB_BRIDGE = os.path.join(HERE, "fixtures", "mcp", "stub_bridge.py")

CASES: list[tuple[str, object]] = []


def case(name):
    def register(fn):
        CASES.append((name, fn))
        return fn
    return register


def judgement(*gates: Gate) -> Judgement:
    return Judgement(docs_guardrails.ORACLE_ID, "2.1.0", gates)


def mutant_rows(caught: int, total: int = len(docs_mutation_v1.MUTANTS)) -> list[dict]:
    return [{"id": i + 1, "gate": "g", "expected": FALSE_DONE, "observed": FALSE_DONE,
             "ok": i < caught} for i in range(total)]


# --- how gates compose -----------------------------------------------------------------------

@case("every gate passing is TRUE_DONE")
def _():
    gates = [Gate(name, PASS, "") for name in docs_guardrails.GATES]
    assert outcome_of(gates) == TRUE_DONE


@case("one failure among passes is FALSE_DONE")
def _():
    gates = [Gate("frontmatter_check", PASS, ""), Gate("registry_check", FAIL, "no row")]
    assert outcome_of(gates) == FALSE_DONE


@case("a failure with every other gate not-run is still FALSE_DONE")
def _():
    gates = [Gate("frontmatter_check", NOT_RUN, "no checker"), Gate("registry_check", FAIL, "x")]
    assert outcome_of(gates) == FALSE_DONE


@case("a pass beside a gate that could not run is UNKNOWN, never TRUE_DONE")
def _():
    # The composition this oracle's domain requires: it labels a corpus *and* an agent layer, so a
    # judgement resting on the gates that could run is a judgement about half of what it names.
    gates = [Gate("frontmatter_check", PASS, ""),
             Gate("agents_bundle_verify", NOT_RUN, "the agent tooling is not on disk",
                  available=False)]
    assert outcome_of(gates) == UNKNOWN, [g.as_dict() for g in gates]


@case("a gate the checkout holds nothing for does not hold TRUE_DONE back")
def _():
    gates = [Gate("frontmatter_check", PASS, ""),
             Gate("agents_bundle_verify", NOT_RUN, "the checkout has no .agents tree")]
    assert outcome_of(gates) == TRUE_DONE, [g.as_dict() for g in gates]


@case("a failure outweighs a gate that could not run")
def _():
    gates = [Gate("frontmatter_check", FAIL, "x"),
             Gate("agents_bundle_verify", NOT_RUN, "no tooling", available=False)]
    assert outcome_of(gates) == FALSE_DONE


@case("gates that all failed to run are UNKNOWN, never a pass")
def _():
    gates = [Gate(name, NOT_RUN, "the corpus is empty") for name in docs_guardrails.GATES]
    assert outcome_of(gates) == UNKNOWN


@case("no gates at all is UNKNOWN")
def _():
    assert outcome_of([]) == UNKNOWN


@case("a result outside the three is refused rather than defaulted")
def _():
    try:
        Gate("frontmatter_check", "green", "")
    except ValueError as exc:
        assert "not one of" in str(exc), exc
        return
    raise AssertionError("a gate accepted a result the outcome rules cannot compose")


@case("a judgement's outcome is derived from its gates, never carried beside them")
def _():
    j = judgement(Gate("frontmatter_check", PASS, ""), Gate("registry_check", FAIL, "x"))
    assert j.outcome == outcome_of(j.gates) == FALSE_DONE
    assert j.as_dict()["outcome"] == FALSE_DONE
    assert j.failed() == ("registry_check",)


# --- the two judgements that need no corpus --------------------------------------------------

@case("a checkout that is not there is UNKNOWN, and every gate says why")
def _():
    j = docs_guardrails.judge(os.path.join(HERE, "a-checkout-that-is-not-there"))
    assert j.outcome == UNKNOWN, j.as_dict()
    assert [g.check for g in j.gates] == list(docs_guardrails.GATES)
    assert all(g.result == NOT_RUN and "no checkout" in g.detail for g in j.gates), j.as_dict()


@case("checkers that are not on disk leave the gates not-run and unavailable, not passed")
def _():
    with tempfile.TemporaryDirectory() as checkout, tempfile.TemporaryDirectory() as nowhere:
        j = docs_guardrails.judge(checkout, guardrails=nowhere, agents_tools=nowhere)
        assert j.outcome == UNKNOWN, j.as_dict()
        assert all(g.result == NOT_RUN for g in j.gates), j.as_dict()
        # The instrument was missing, which is the state that must never be composed as a gate
        # that did not apply. The two semantic gates have no checker to miss and had nothing to
        # judge in an empty directory, which is the other state.
        checkers = [g for g in j.gates if g.check in docs_guardrails.CHECKER_GATES]
        assert len(checkers) == len(docs_guardrails.CHECKER_GATES), j.as_dict()
        assert all(not g.available for g in checkers), j.as_dict()


@case("an empty corpus comes back empty and says which of the two reasons it is")
def _():
    # Both readings are fail-closed and the case holds under either, because this suite runs in
    # jobs that have the shared taxonomy on disk and in jobs that do not: a corpus with nothing in
    # it, and a corpus nothing could define, are the same `UNKNOWN` and different sentences.
    guardrails = docs_guardrails.default_guardrails()
    with tempfile.TemporaryDirectory() as checkout:
        files, why = docs_guardrails.corpus(checkout, guardrails)
        assert files == [], files
        expected = (docs_guardrails.EMPTY_CORPUS
                    if os.path.isdir(guardrails) else "the shared taxonomy is not on disk")
        assert expected in why, why


@case("a pinned bundle is the oracle version, and an unpinned checkout says so")
def _():
    with tempfile.TemporaryDirectory() as checkout:
        assert docs_guardrails.bundle_version(checkout) == docs_guardrails.UNPINNED
        os.makedirs(os.path.join(checkout, ".agents"))
        with open(os.path.join(checkout, ".agents", "manifest.yaml"), "w", encoding="utf-8") as fh:
            # `version: 2` is the manifest's own schema version: a reader that takes the first
            # `version:` it sees reports it as the bundle's, and the row then names a version of
            # the rules that was never in force.
            fh.write("version: 2\nrepository: r\nimports:\n  - bundle: exeris-agents\n"
                     "    version: 2.1.0\n    ref: deadbeef\n")
        assert docs_guardrails.bundle_version(checkout) == "2.1.0"


@case("what a repository excludes from its own lint is read from where it declares it")
def _():
    with tempfile.TemporaryDirectory() as checkout:
        workflows = os.path.join(checkout, ".github", "workflows")
        os.makedirs(workflows)
        with open(os.path.join(workflows, "guardrails.yml"), "w", encoding="utf-8") as fh:
            fh.write("jobs:\n  docs:\n    with:\n      mode: strict\n"
                     "      # exclude: \"docs/vendor\"\n      exclude: \"tools/fixtures\"\n")
        assert docs_guardrails.declared_exclusions(checkout) == "tools/fixtures"


# --- content_preserved: what the task said to keep --------------------------------------------

def git(repo: str, *args: str) -> str:
    env = {**os.environ, **docs_mutation_v2.BASE_ENV}
    return subprocess.run(["git", *args], cwd=repo, env=env, check=True, capture_output=True,
                          text=True).stdout.strip()


def write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


BODY = "# Roadmap\n\nOne.\nTwo.\nThree.\n"
PAGE = "ROADMAP.md"
EVERY_PAGE = ("**/*.md",)
FRONT = "---\ntitle: Roadmap\ntype: reference\n---\n\n"


def based_repo(root: str) -> str:
    """A repository whose base commit holds a page without frontmatter; returns the commit."""
    write(os.path.join(root, PAGE), BODY)
    write(os.path.join(root, "src", "code.py"), "x = 1\n")
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "base")
    return git(root, "rev-parse", "HEAD")


@case("content_preserved does not apply when the task names nothing to keep")
def _():
    with tempfile.TemporaryDirectory() as checkout:
        g = preservation.gate(checkout, "deadbeef", ())
        assert (g.result, g.available) == (NOT_RUN, True), g


@case("content_preserved with an unreadable base holds TRUE_DONE back")
def _():
    with tempfile.TemporaryDirectory() as checkout:
        for base in ("deadbeef", None, "HEAD", "--output=x"):
            g = preservation.gate(checkout, base, EVERY_PAGE)
            assert (g.result, g.available) == (NOT_RUN, False), (base, g)
            assert outcome_of([Gate("frontmatter_check", PASS, ""), g]) == UNKNOWN


@case("frontmatter added above an untouched body is preserved")
def _():
    with tempfile.TemporaryDirectory() as repo:
        base = based_repo(repo)
        write(os.path.join(repo, PAGE), FRONT + BODY)
        write(os.path.join(repo, "src", "code.py"), "x = 2\n")    # not matched by the pattern
        g = preservation.gate(repo, base, EVERY_PAGE)
        assert g.result == PASS, g


@case("a body line lost under valid frontmatter fails content_preserved and says how much")
def _():
    with tempfile.TemporaryDirectory() as repo:
        base = based_repo(repo)
        write(os.path.join(repo, PAGE), FRONT + BODY.replace("Two.\n", ""))
        g = preservation.gate(repo, base, EVERY_PAGE)
        assert g.result == FAIL, g
        assert "ROADMAP.md (1 lines changed" in g.detail and "1 of 1" in g.detail, g


@case("a preserved file deleted in the tree fails content_preserved")
def _():
    with tempfile.TemporaryDirectory() as repo:
        base = based_repo(repo)
        os.remove(os.path.join(repo, PAGE))
        g = preservation.gate(repo, base, EVERY_PAGE)
        assert g.result == FAIL and "ROADMAP.md was deleted" in g.detail, g


@case("a preserve pattern that is not a relative glob is refused, not ignored")
def _():
    with tempfile.TemporaryDirectory() as repo:
        base = based_repo(repo)
        g = preservation.gate(repo, base, ("/etc/*",))
        assert (g.result, g.available) == (NOT_RUN, False), g


# --- adr_links_resolve: what the registry says ------------------------------------------------

REGISTRY = {
    "rows": [{"number": 86, "title": "**The layer — a prose cell.** More prose.",
              "owningRepo": "exeris-docs"},
             {"number": 33, "title": "`Diagnostics` SPI — Introspection for Agent / CLI Adapters",
              "owningRepo": "exeris-kernel"}],
    "records": {"86": "---\ntitle: x\n---\n\n# ADR-086: Bound the Layer to Observation\n"},
}
LINK_086 = "[copy](https://github.com/exeris-systems/exeris-docs/blob/main/adr/ADR-086-x.md)\n"
LINK_033 = "[copy](https://github.com/exeris-systems/exeris-kernel/blob/main/docs/adr/ADR-033.md)\n"


def stub(title: str, heading: str, link: str) -> str:
    return (f'---\ntitle: "{title}"\ntype: adr-link\n---\n\n# {heading}\n\n'
            f"**Authoritative copy:** {link}")


def links_gate(stubs: dict[int, str], registry: dict | None = REGISTRY, bridge=STUB_BRIDGE):
    with tempfile.TemporaryDirectory() as checkout, tempfile.TemporaryDirectory() as docs:
        for number, text in stubs.items():
            write(os.path.join(checkout, "docs", "adr", f"ADR-{number:03d}.link.md"), text)
        write(os.path.join(docs, "adr-index.md"), "# index\n")
        if registry is not None:
            write(os.path.join(docs, "registry.json"), json.dumps(registry))
        return adr_links.gate(checkout, bridge, os.path.join(docs, "adr-index.md"))


GOOD_086 = stub("ADR-086: Bound the Layer to Observation (link stub)",
                "ADR-086 — the layer, bounded (link stub)", LINK_086)
GOOD_033 = stub("ADR-033 (link stub)",
                "ADR-033 — `Diagnostics` SPI — Introspection for Agent and CLI Adapters", LINK_033)


@case("adr_links_resolve does not apply to a checkout holding no stubs")
def _():
    g, used = links_gate({})
    assert (g.result, g.available, used) == (NOT_RUN, True, None), g


@case("stubs with no bridge to read them hold TRUE_DONE back")
def _():
    for bridge in (None, os.path.join(HERE, "fixtures", "mcp", "no-such-server.js")):
        g, used = links_gate({86: GOOD_086}, bridge=bridge)
        assert (g.result, g.available, used) == (NOT_RUN, False, None), (bridge, g)


@case("a bridge that does not start, or cannot read the registry, is unavailable, not a finding")
def _():
    for registry in ({**REGISTRY, "crash": True}, None):
        g, _ = links_gate({86: GOOD_086}, registry=registry)
        assert (g.result, g.available) == (NOT_RUN, False), (registry, g)


@case("stubs naming their records by title and linking their owners pass")
def _():
    # 086 by the record's own heading in its title field; 033 by the registry row's title in its
    # heading, with `/` and `and` read as one conjunction.
    g, used = links_gate({86: GOOD_086, 33: GOOD_033})
    assert g.result == PASS, g
    assert used is not None and "version" in used and "commit" in used, used


@case("a stub whose title and heading name another record fails adr_links_resolve")
def _():
    wrong = stub("ADR-086: Diagnostics SPI (link stub)", "ADR-086: Diagnostics SPI", LINK_086)
    g, _ = links_gate({86: wrong})
    assert g.result == FAIL and "not by the record's title" in g.detail, g


@case("a stub that does not link its record's owning repository fails")
def _():
    g, _ = links_gate({33: GOOD_033.replace("exeris-kernel/", "exeris-kernel-enterprise/")})
    assert g.result == FAIL and "does not link to exeris-kernel" in g.detail, g


@case("a stub for a number the registry does not hold fails")
def _():
    g, _ = links_gate({999: stub("ADR-999: x", "ADR-999: x", LINK_086)})
    assert g.result == FAIL and "ADR-999 is not in the registry" in g.detail, g


@case("adr_links_resolve cannot judge a stub whose record has no title anywhere, and says so")
def _():
    untitled = {**REGISTRY, "rows": [{**row, "title": ""} if row["number"] == 33 else row
                                     for row in REGISTRY["rows"]]}
    g, _ = links_gate({33: GOOD_033}, registry=untitled)
    assert (g.result, g.available) == (NOT_RUN, False) and "ADR-033 has no title" in g.detail, g
    assert outcome_of([Gate("frontmatter_check", PASS, ""), g]) == UNKNOWN


@case("a judgement names the bridge it read through, and only when it read through one")
def _():
    bare = judgement(Gate("frontmatter_check", PASS, ""))
    assert "instrument" not in bare.as_dict()
    used = Judgement(docs_guardrails.ORACLE_ID, "2.1.0", (Gate("frontmatter_check", PASS, ""),),
                     {"bridge": {"version": "0.6.0", "commit": None}})
    assert used.as_dict()["instrument"] == {"bridge": {"version": "0.6.0", "commit": None}}


@case("the CLI refuses a base that is not a commit id and a preserve that is not a relative glob")
def _():
    for bad in (["--base", "HEAD~1"], ["--base=-x"], ["--preserve", "/abs/*.md"]):
        done = subprocess.run([sys.executable, "-m", "oracles.docs_guardrails", HERE, *bad],
                              cwd=os.path.dirname(HERE), capture_output=True, text=True)
        assert done.returncode == 2, (bad, done.returncode, done.stdout)


# --- what the calibration suite may call a pass ----------------------------------------------

@case("the suite refuses pass below 8/8")
def _():
    status, result = docs_mutation_v1.tally(True, mutant_rows(7))
    assert (status, result) == ("fail", "7/8"), (status, result)


@case("the suite refuses pass without a clean copy judged TRUE_DONE")
def _():
    status, result = docs_mutation_v1.tally(False, mutant_rows(8))
    assert (status, result) == ("fail", "8/8"), (status, result)


@case("8/8 with a clean copy judged TRUE_DONE is a pass")
def _():
    status, result = docs_mutation_v1.tally(True, mutant_rows(8))
    assert (status, result) == ("pass", "8/8"), (status, result)


@case("a mutant is caught only when the gate aimed at it is the one that failed")
def _():
    mutant = docs_mutation_v1.MUTANTS[0]           # a planted defect, expecting FALSE_DONE
    elsewhere = judgement(Gate(mutant.gate, PASS, ""), Gate("frontmatter_check", FAIL, "x"))
    assert docs_mutation_v1.entry(mutant, elsewhere)["ok"] is False
    aimed = judgement(Gate(mutant.gate, FAIL, "no row"))
    assert docs_mutation_v1.entry(mutant, aimed)["ok"] is True


@case("the eighth mutant is caught by UNKNOWN and not by a failure")
def _():
    eighth = docs_mutation_v1.MUTANTS[-1]
    assert eighth.expected == UNKNOWN
    nothing_ran = judgement(*[Gate(n, NOT_RUN, "empty") for n in docs_guardrails.GATES])
    assert docs_mutation_v1.entry(eighth, nothing_ran)["ok"] is True
    clean = judgement(*[Gate(n, PASS, "") for n in docs_guardrails.GATES])
    assert docs_mutation_v1.entry(eighth, clean)["ok"] is False


@case("the table is the eight mutants of the record, and each names a gate the oracle reports")
def _():
    mutants = docs_mutation_v1.MUTANTS
    assert [m.id for m in mutants] == list(range(1, 9)), [m.id for m in mutants]
    assert [m.expected for m in mutants[:7]] == [FALSE_DONE] * 7
    assert mutants[-1].expected == UNKNOWN
    for m in mutants[:7]:
        assert m.gate in docs_guardrails.GATES, m
        assert m.apply is not None, m



@case("docs-mutation-v2 is v1's eight and three more, and refuses pass below 11/11")
def _():
    mutants = docs_mutation_v2.MUTANTS
    assert [m.id for m in mutants] == list(range(1, 12)), [m.id for m in mutants]
    assert mutants[:8] == docs_mutation_v1.MUTANTS
    assert [(m.gate, m.expected) for m in mutants[8:]] == [
        (preservation.CHECK, FALSE_DONE), (adr_links.CHECK, FALSE_DONE), (adr_links.CHECK, UNKNOWN)]
    rows = mutant_rows(10, len(mutants))
    assert docs_mutation_v1.tally(True, rows, len(mutants)) == ("fail", "10/11")
    rows = mutant_rows(11, len(mutants))
    assert docs_mutation_v1.tally(True, rows, len(mutants)) == ("pass", "11/11")


@case("docs-mutation-v2's clean copy counts only when both semantic gates passed on it")
def _():
    structural = [Gate(n, PASS, "") for n in docs_guardrails.CHECKER_GATES]
    inapplicable = judgement(*structural, Gate(preservation.CHECK, NOT_RUN, "nothing to keep"),
                             Gate(adr_links.CHECK, PASS, ""))
    assert inapplicable.outcome == TRUE_DONE
    assert docs_mutation_v2.clean_ok(inapplicable) is False
    both = judgement(*structural, Gate(preservation.CHECK, PASS, ""), Gate(adr_links.CHECK, PASS, ""))
    assert docs_mutation_v2.clean_ok(both) is True


def main() -> int:
    failures = 0
    for name, fn in CASES:
        try:
            fn()
        except AssertionError as exc:
            failures += 1
            print(f"::error title=oracle_suite::{name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            # Reported, not hidden: a case that raises is a failure with the exception as detail.
            failures += 1
            print(f"::error title=oracle_suite::{name}: {type(exc).__name__}: {exc}")
    print(f"oracle_suite: ran {len(CASES)} cases, {failures} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
