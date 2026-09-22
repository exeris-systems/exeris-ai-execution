"""The oracle interface: what a judgement is, and how an outcome follows from the gates behind it.

An oracle answers one question about one workload — does this checkout satisfy a stated set of
machine-verifiable properties — and it answers with gates, never with a verdict composed by hand.
Composition lives here rather than in each oracle so that `outcome` means the same thing on every
row whatever judged it (ADR-086 §D.15), and so that a second oracle behind this interface inherits
the rule instead of restating it.

Fail-closed (ADR-086 §E.19) is the whole of that rule:

  * one failed gate makes the run `FALSE_DONE`. The gates that did not run cannot argue with it.
  * `TRUE_DONE` needs every applicable gate to have passed **and** at least one of them to have
    run. A judgement with nothing behind it is the false green an oracle exists to refuse.
  * no gate ran — the checkers are absent, the corpus is empty, the checkout is not there — is
    `UNKNOWN`. `UNKNOWN` is not a pass, which is why the contract admits it as an outcome.

`Judgement.outcome` is derived on every read and never stored: an outcome carried beside its gates
is an outcome that can disagree with them, and a reader has no way to tell which half is true.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Sequence

PASS = "pass"
FAIL = "fail"
NOT_RUN = "not-run"
# Three-valued like `checks_run` in the verdict schema the organisation's review publishes: a check
# that has not run is its own state and is never folded into either of the other two.
RESULTS = frozenset({PASS, FAIL, NOT_RUN})

TRUE_DONE = "TRUE_DONE"
FALSE_DONE = "FALSE_DONE"
UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Gate:
    """One check, its result and what it said.

    `detail` is what the check reported, quoted rather than summarised: a gate whose detail is this
    module's paraphrase of a checker's output is a second implementation of that checker's opinion.
    """

    check: str
    result: str
    detail: str = ""

    def __post_init__(self) -> None:
        # A result outside the three cannot be composed into an outcome, and the fail-closed rule
        # has no reading under which an unrecognised value is a pass. It is refused at the door
        # rather than defaulted, because defaulting decides the question silently.
        if self.result not in RESULTS:
            raise ValueError(f"gate {self.check!r}: result {self.result!r} is not one of "
                             f"{sorted(RESULTS)}")

    def as_dict(self) -> dict:
        return {"check": self.check, "result": self.result, "detail": self.detail}


def outcome_of(gates: Sequence[Gate]) -> str:
    """`TRUE_DONE`, `FALSE_DONE` or `UNKNOWN` for a set of gates, per ADR-086 §E.19."""
    if any(g.result == FAIL for g in gates):
        return FALSE_DONE
    if any(g.result == PASS for g in gates):
        return TRUE_DONE
    return UNKNOWN


@dataclass(frozen=True)
class Judgement:
    """What an oracle returns about one checkout.

    `oracle_id` and `oracle_version` are the two halves a row carries beside the outcome, because a
    verdict is only interpretable against the version of the rules that produced it.
    """

    oracle_id: str
    oracle_version: str
    gates: tuple[Gate, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "gates", tuple(self.gates))

    @property
    def outcome(self) -> str:
        return outcome_of(self.gates)

    def failed(self) -> tuple[str, ...]:
        return tuple(g.check for g in self.gates if g.result == FAIL)

    def ran(self) -> tuple[str, ...]:
        return tuple(g.check for g in self.gates if g.result != NOT_RUN)

    def as_dict(self) -> dict:
        return {"oracle_id": self.oracle_id, "oracle_version": self.oracle_version,
                "gates": [g.as_dict() for g in self.gates], "outcome": self.outcome}

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.as_dict(), indent=indent, ensure_ascii=False)
