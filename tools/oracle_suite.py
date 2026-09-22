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
    `TRUE_DONE`, never one without the other.

Fixtures only: gates are built by hand and the two judgements that need no checker at all — an
absent checkout, and checkers that are not on disk — are the only ones that touch the filesystem.
A case here never needs a real corpus, which is what lets this run in every job while the mutation
suite runs where the corpus is.
"""

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from oracles import (FAIL, FALSE_DONE, NOT_RUN, PASS, TRUE_DONE, UNKNOWN,  # noqa: E402
                     Gate, Judgement, outcome_of)
from oracles import docs_guardrails, docs_mutation_v1  # noqa: E402

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
        # that did not apply.
        assert all(not g.available for g in j.gates), j.as_dict()


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


def main() -> int:
    failures = 0
    for name, fn in CASES:
        try:
            fn()
        except AssertionError as exc:
            failures += 1
            print(f"::error title=oracle_suite::{name}: {exc}")
        except Exception as exc:                       # noqa: BLE001 - reported, not hidden
            failures += 1
            print(f"::error title=oracle_suite::{name}: {type(exc).__name__}: {exc}")
    print(f"oracle_suite: ran {len(CASES)} cases, {failures} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
