"""The `docs-guardrails` oracle: the L1 guardrail suite run over one checkout, read as a judgement.

ADR-086 §D.15 names this oracle and its scope in one clause, and the scope is the part that must
survive every reading of the code below: it answers **does this corpus satisfy a stated set of
machine-verifiable structural properties**, and never whether the documentation is any good.
`TRUE_DONE` from this oracle means the gates below passed. It does not mean the work was good, and
a reader who takes it for that has every risk the record lists at once.

The gates are the organisation's own checks, invoked the way the shared `docs-lint` workflow
invokes them and never reimplemented here:

  * `frontmatter_check.py --mode strict --no-section-check` — frontmatter, filenames, private
    links. The taxonomy rule that `public-staged` is no longer a visibility value is **this gate's**
    and is not duplicated: the checker's visibility vocabulary is two-valued, so a deprecated value
    is already an error, and a grep beside it would be a second owner of one rule — the shape that
    drifts in one of its copies.
  * `registry_check.py` — every record has a registry row, and every row's link resolves.
  * `agents_file_check.py`, `agents_render.py --check`, `agents_bundle.py verify` — the agent
    layer: schema, adapter drift and the pinned bundle's digest.

Two gates read what no structural checker can, and are this layer's own (see their modules):

  * `content_preserved` (`preservation.py`) — the files the task named as kept keep their body
    below the frontmatter, compared with the commit the run started from. A corpus with prose
    deleted is as well-formed as one without the deletion; only the task can say which it asked for.
  * `adr_links_resolve` (`adr_links.py`) — every ADR link stub names the record the registry holds
    by that record's title and links to its owning repository, read through the Exeris MCP server.

The root is the checkout. That is the shared path model: the whole repository is documentation and
what is not documentation is named in the taxonomy, so a root list here would be an opt-in list —
the arrangement that fails by omission, silently, in the direction of a green result. The corpus
is therefore whatever `lint_globs.py` admits under the checkout, narrowed by what that checkout's
own workflow excludes, and both are read rather than recomputed — so this oracle and the CI that
gates the same repository cannot disagree about what documentation is.

The mode is the one thing deliberately not taken from the caller. This oracle is always strict,
because a row is about the corpus as it stands when the run ended, and the ramp mode a repository
may run answers a different question: whether the change made things worse.

**An empty or absent corpus never reaches a gate.** Every check above is silent on a corpus it
cannot find — the registry check prints "nothing to check" and exits 0 — so a checkout holding
nothing would otherwise be judged clean. That is the one verdict an instrument must never return
about nothing, and it is the eighth mutant of the calibration suite.

Usage: `python3 -m oracles.docs_guardrails <checkout> [--index <adr-index.md>] [--base <commit>]
[--preserve <glob>]... [--bridge <dist/server.js>]`, printing the Judgement as JSON. The oracle reports and never gates: its exit status says whether it could run,
not what it found, because a non-zero exit on `FALSE_DONE` would make a row's verdict somebody's
red build and turn the observer into a gate.
"""
from __future__ import annotations

import argparse
import glob as globlib
import os
import re
import subprocess
import sys

from . import FAIL, NOT_RUN, PASS, Gate, Judgement
from . import adr_links, preservation
from .paths import PATH_GRAMMAR, existing_directory, existing_file, listed_path

ORACLE_ID = "docs-guardrails"
# The value written where a checkout pins no bundle. A word rather than a number, because rows
# either side of it are not one population and an invented version would hide that.
UNPINNED = "unpinned"
BUNDLE = "exeris-agents"

#: Why `corpus` found nothing where the checkout is the reason. Named, because it is the one empty
#: result that is a fact about the tree rather than about the instrument, and composition treats
#: the two differently.
EMPTY_CORPUS = "the checkout holds no documentation the shared taxonomy admits"

# Named once, in the order a reader of a row meets them, and reused for the not-run case so that a
# judgement always carries the same gates whether or not any of them could run. A gate that
# disappears when it cannot run reads as a gate that did not apply. The first five are the
# organisation's checkers; the last two read what the task kept and what the registry says.
CHECKER_GATES = ("frontmatter_check", "registry_check", "agents_file_check",
                 "agents_render_check", "agents_bundle_verify")
GATES = (*CHECKER_GATES, preservation.CHECK, adr_links.CHECK)

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SIBLINGS = os.path.dirname(_REPO)


def _sibling(name: str, env: str) -> str:
    """Where a sibling checkout is, in the places it is ever on disk.

    The environment variable is what a runner sets; the workspace copy is what CI checks out beside
    this repository; an ancestor holding it is what a working copy of the ecosystem has. The search
    climbs rather than taking the parent directly, because a worktree parked inside its own
    repository is not beside the siblings its repository is beside.

    A path spelled into the source would be one machine's, and none of these is a fallback for a
    checker that is missing: an absent checker leaves its gate `not-run`, which is not a pass.
    """
    from_env = os.environ.get(env)
    if from_env:
        return from_env
    in_workspace = os.path.join(os.getcwd(), name)
    if os.path.isdir(in_workspace):
        return in_workspace
    here = _REPO
    while True:
        parent = os.path.dirname(here)
        if parent == here:
            return os.path.join(_SIBLINGS, name)
        candidate = os.path.join(parent, name)
        if os.path.isdir(candidate):
            return candidate
        here = parent


def default_guardrails() -> str:
    return _sibling(".guardrails", "EXERIS_GUARDRAILS")


def default_agents_tools() -> str:
    return os.environ.get("EXERIS_AGENTS_TOOLS") or os.path.join(
        _sibling("exeris-agents", "EXERIS_AGENTS"), "tools")


def _manifest_import_items(text: str):
    """The manifest's `imports:` list, one line's worth of update at a time.

    Block style only and scoped to `imports:`, which is how the manifest is written: a `version:`
    under any other key belongs to that key, and the manifest's own schema version is one of them.
    A line outside that block, a comment or a blank line changes nothing; a line opening a new list
    entry starts a fresh item before its own key is read. What this yields after each line is the
    item as it stands at that point in the file, so a caller sees a match exactly where the line
    that completes it appears, not only once an entry has been read in full.
    """
    inside = False
    item: dict[str, str] = {}
    for line in text.splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        if not line[0].isspace():
            inside = entry.split(":", 1)[0].strip() == "imports"
            item = {}
            continue
        if not inside:
            continue
        if entry.startswith("-"):
            item = {}
            entry = entry[1:].strip()
        key, sep, value = entry.partition(":")
        if sep and key.strip() in ("bundle", "version") and value.split():
            item[key.strip()] = value.split()[0].strip("'\"")
        yield item


def _pinned_version(item: dict[str, str], bundle: str) -> str | None:
    """The version `item` pins for `bundle`, or None where the pair is not yet complete."""
    if item.get("bundle") == bundle and item.get("version"):
        return item["version"]
    return None


def bundle_version(checkout: str, bundle: str = BUNDLE) -> str:
    """The agent-bundle version the checkout pins — this oracle's version.

    The gates are the bundle's rules: its policies, its schemas and the agent-layer checks it
    ships. So the version in force is the version of the oracle, and it is read here from the tree
    the gates ran over. That is a different reading from the row's
    `repository_state.bundle_version`, which is taken at the commit the run started from: the two
    agree except in a run that edited the manifest, where one says what the work was subject to and
    the other what judged it. A checkout that pins nothing is `unpinned`, and the first `imports:`
    entry that names both a `bundle` and a `version` wins.
    """
    manifest = listed_path(checkout, ".agents", "manifest.yaml")
    if manifest is None:
        return UNPINNED
    try:
        with open(manifest, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return UNPINNED
    for item in _manifest_import_items(text):
        version = _pinned_version(item, bundle)
        if version:
            return version
    return UNPINNED


def _run(argv: list[str], cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=cwd, capture_output=True, text=True)


def _detail(proc: subprocess.CompletedProcess, limit: int = 300) -> str:
    """What the checker said, quoted from its own output.

    A finding comes back as the first annotation's message — the annotations are the checkers'
    worklist format and the text after the last `::` is the sentence a reader of the gate needs. A
    clean run comes back as the checker's own count, because "what was checked" is the half of a
    pass that makes it worth anything; the count line is preferred over the last line so that a
    summary table's final row is not mistaken for the verdict.
    """
    lines = (proc.stdout + proc.stderr).splitlines()
    for line in lines:
        if line.startswith("::error"):
            return line.split("::", 2)[-1][:limit]
    for line in lines:
        if line.startswith("Checked "):
            return line.strip()[:limit]
    for line in reversed(lines):
        if line.strip():
            return line.strip()[:limit]
    return ""


def _verdict(check: str, proc: subprocess.CompletedProcess) -> Gate:
    """A checker's exit code as a gate result.

    0 and 1 are the two verdicts these checkers reach — clean, and findings. Any other exit is the
    checker failing to reach a verdict at all, which is `not-run` and unavailable: a checker that
    crashed has said nothing about the corpus, reading its exit code as a finding would put a
    tooling failure on the corpus's record, and reading its silence as inapplicability would let
    the gates beside it carry a pass over the part it was meant to judge.
    """
    if proc.returncode == 0:
        return Gate(check, PASS, _detail(proc))
    if proc.returncode == 1:
        return Gate(check, FAIL, _detail(proc))
    return Gate(check, NOT_RUN, f"the checker exited {proc.returncode}: {_detail(proc)}",
                available=False)


# The bare form takes everything up to a comment and is trimmed afterwards, so that no two parts of
# the pattern can claim the same trailing blank.
_EXCLUDE = re.compile(r"^\s+exclude:\s*(?:\"([^\"]*)\"|'([^']*)'|([^#\s][^#]*))")


def declared_exclusions(checkout: str) -> str:
    """The paths the checkout's own CI takes out of the lint, read from where it declares them.

    The shared lint takes this as an input from the caller, so a corpus judged without it is not
    the corpus that repository's CI judges: a fixture tree exists to lack the frontmatter a record
    carries, and reading one as documentation makes a row say a repository is broken over files it
    deliberately keeps that way. It is read from the workflow rather than restated here, because
    the repository owns what it excludes and a list in this file would be a second owner.

    The file is the per-repo caller of the shared lint, which ADR-085 §C.11 places at
    `.github/workflows/guardrails.yml`; a repository that has none excludes nothing and is judged
    on everything the taxonomy admits, which errs towards checking too much. The first declaration
    wins: the lint is the caller's only input of this name, and a workflow that grows a second one
    is a workflow this gate would have to be told about anyway.
    """
    path = listed_path(checkout, ".github", "workflows", "guardrails.yml")
    if path is None:
        return ""
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return ""
    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            continue
        match = _EXCLUDE.match(line)
        if match:
            return next(g for g in match.groups() if g is not None).strip()
    return ""


def _patterns(specs: list[str]) -> list[re.Pattern]:
    return [re.compile(globlib.translate(s, recursive=True, include_hidden=True)) for s in specs]


def _taxonomy_patterns(stdout: str) -> tuple[list[re.Pattern], list[re.Pattern]]:
    """The keep and drop patterns `lint_globs.py` printed, one per line, `!` marking a drop."""
    keep_specs, drop_specs = [], []
    for line in stdout.splitlines():
        spec = line.strip()
        if not spec:
            continue
        (drop_specs if spec.startswith("!") else keep_specs).append(spec.lstrip("!"))
    return _patterns(keep_specs), _patterns(drop_specs)


def _prune_excluded(prefix: str, dirnames: list[str], drop: list[re.Pattern]) -> list[str]:
    """`dirnames`, sorted, with any directory the drop patterns exclude taken out.

    An excluded directory is pruned rather than walked. The probe is a name that cannot exist, so
    what it tests is the directory's own exclusion and not a real path.
    """
    return sorted(d for d in dirnames
                 if not any(rx.match(f"{prefix}{d}/.probe.md") for rx in drop))


def _admitted_files(prefix: str, filenames: list[str], keep: list[re.Pattern],
                    drop: list[re.Pattern]) -> list[str]:
    """The files in one directory the taxonomy admits, as paths relative to the checkout root."""
    found = []
    for name in sorted(filenames):
        path = f"{prefix}{name}"
        if any(rx.match(path) for rx in keep) and not any(rx.match(path) for rx in drop):
            found.append(path)
    return found


def _walk(checkout: str, keep: list[re.Pattern], drop: list[re.Pattern]) -> list[str]:
    """Every file under `checkout` the taxonomy admits, one directory at a time."""
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(checkout):
        rel = os.path.relpath(dirpath, checkout).replace(os.sep, "/")
        prefix = "" if rel == "." else rel + "/"
        dirnames[:] = _prune_excluded(prefix, dirnames, drop)
        found.extend(_admitted_files(prefix, filenames, keep, drop))
    return found


def corpus(checkout: str, guardrails: str, exclude: str = "") -> tuple[list[str], str]:
    """The documentation files this checkout holds, as the shared taxonomy admits them.

    Returns the files and, when there are none, why. The globs come from the organisation's own
    `lint_globs.py`: what counts as documentation is one definition, and a copy of it here would be
    a second owner of the rule that decides what an empty corpus is — the rule the eighth mutant
    exists to hold.
    """
    # Absolute, because every checker runs with the checkout as its working directory: a relative
    # path to a checker would be resolved against the tree being judged rather than against the
    # workspace the checker lives in.
    guardrails_abs = os.path.abspath(guardrails)
    script = listed_path(guardrails_abs, "scripts", "lint_globs.py")
    if script is None:
        shown = os.path.join(guardrails_abs, "scripts", "lint_globs.py")
        return [], f"the shared taxonomy is not on disk ({shown}), so the corpus is undefined"
    proc = _run([sys.executable, script, "--root", ".", "--exclude", exclude], cwd=checkout)
    if proc.returncode != 0:
        return [], f"the taxonomy did not resolve (exit {proc.returncode}): {_detail(proc)}"
    keep, drop = _taxonomy_patterns(proc.stdout)
    found = _walk(checkout, keep, drop)
    if not found:
        return [], EMPTY_CORPUS
    return found, ""


def _frontmatter_gate(checkout: str, guardrails: str, exclude: str) -> Gate:
    script = listed_path(guardrails, "scripts", "frontmatter_check.py")
    if script is None:
        shown = os.path.join(guardrails, "scripts", "frontmatter_check.py")
        return Gate("frontmatter_check", NOT_RUN, f"the checker is not on disk ({shown})",
                    available=False)
    # Strict, because the oracle judges the corpus as it stands rather than the diff that produced
    # it: ramp mode answers "did this change make things worse", which is a different question and
    # not one a row about a finished run can carry. Sections stay off, as the shared workflow has
    # them off, so this gate and that workflow are the same check.
    return _verdict("frontmatter_check", _run(
        [sys.executable, script, "--root", ".", "--mode", "strict", "--no-section-check",
         "--exclude", exclude], checkout))


def _registry_gate(checkout: str, guardrails: str, index: str | None) -> Gate:
    """The registry check, in whichever of its two modes the checkout calls for.

    A checkout holding `adr-index.md` **is** the registry and is checked against itself, with link
    resolution turned on. Resolution is relative to the index's own directory, so a registry judged
    as a copy is judged on where the copy stands — a row linking outside its own repository
    resolves against whatever is beside the copy.

    Any other checkout is a consumer and needs the central index. Without one on disk the gate is
    `not-run` and unavailable: the checker would otherwise fetch it, and a gate that depends on the
    network is not a gate — it reports the network. The index is one of this gate's inputs, so a
    checkout with records and no index is a checkout whose records nothing checked, which is not
    the same as a checkout that has no records.
    """
    script = listed_path(guardrails, "scripts", "registry_check.py")
    if script is None:
        shown = os.path.join(guardrails, "scripts", "registry_check.py")
        return Gate("registry_check", NOT_RUN, f"the checker is not on disk ({shown})",
                    available=False)
    if os.path.isfile(os.path.join(checkout, "adr-index.md")):
        # The checker runs in the checkout, so its siblings are one level up from where it runs:
        # a constant, and no value this process was handed reaches the command line.
        argv = [sys.executable, script, "--siblings-root", os.pardir]
        return _verdict("registry_check", _run(argv, checkout))
    adr_dir = next((d for d in ("docs/adr", "adr")
                    if os.path.isdir(os.path.join(checkout, d))), None)
    if adr_dir is None:
        return Gate("registry_check", NOT_RUN,
                    "the checkout holds neither a registry nor a records directory")
    # The index reaches the checker's command line, so what is passed is the grammar's own match
    # of it: one absolute path of plain segments, never an option.
    admitted = PATH_GRAMMAR.fullmatch(index) if index and os.path.isfile(index) else None
    if admitted is None:
        return Gate("registry_check", NOT_RUN,
                    "no central index on disk: the check would have to fetch one over the network",
                    available=False)
    return _verdict("registry_check", _run(
        [sys.executable, script, "--adr-dir", adr_dir, "--index", admitted.group(0)], checkout))


def _agent_gates(checkout: str, agents_tools: str) -> list[Gate]:
    """The three agent-layer checks, run from the bundle's own tooling.

    They are not-run where the checkout has no `.agents` tree — there is nothing for them to judge,
    and a pass on an absent tree is the eighth mutant's shape at the level of one gate. That is the
    only branch here that leaves the gate available: a checkout with an agent layer and a machine
    without the tooling that judges it is a run whose agent layer nothing read, and the gates that
    did run cover none of it.
    """
    invocations = {
        "agents_file_check": ["agents_file_check.py"],
        "agents_render_check": ["agents_render.py", "--root", ".", "--check"],
        "agents_bundle_verify": ["agents_bundle.py", "verify", "--root", "."],
    }
    if not os.path.isdir(os.path.join(checkout, ".agents")):
        return [Gate(name, NOT_RUN, "the checkout has no .agents tree") for name in invocations]
    if not os.path.isdir(agents_tools):
        return [Gate(name, NOT_RUN, f"the agent tooling is not on disk ({agents_tools})",
                     available=False) for name in invocations]
    gates = []
    for name, argv in invocations.items():
        script = listed_path(agents_tools, argv[0])
        if script is None:
            shown = os.path.join(agents_tools, argv[0])
            gates.append(Gate(name, NOT_RUN, f"the checker is not on disk ({shown})",
                              available=False))
            continue
        gates.append(_verdict(name, _run([sys.executable, script, *argv[1:]], checkout)))
    return gates


def _all_not_run(reason: str, *, available: bool = True, names=GATES) -> list[Gate]:
    return [Gate(name, NOT_RUN, reason, available=available) for name in names]


def index_within(index: str | None, checkout: str, guardrails: str) -> str | None:
    """The central index's real path, or None where it lies outside the trees this run knows.

    The index is handed to the registry checker on its command line, so it is admitted only from
    where an index is ever kept beside a judged checkout: inside the checkout, beside the shared
    guardrails clone (the ecosystem's working copy, or the CI workspace both are checked out into),
    or under the working directory. A path that resolves elsewhere is not an index this run reads.
    """
    if not index:
        return None
    real = os.path.realpath(index)
    if not os.path.isfile(real):
        return None
    if not PATH_GRAMMAR.fullmatch(real) or os.path.basename(real) != "adr-index.md":
        return None
    roots = (checkout, os.path.dirname(guardrails.rstrip(os.sep)), os.path.realpath(os.getcwd()))
    for root in roots:
        if os.path.commonprefix((real, root + os.sep)) == root + os.sep:
            return real
    return None


def judge(checkout: str, *, guardrails: str | None = None, agents_tools: str | None = None,
          index: str | None = None, base: str | None = None, preserve=(),
          bridge: str | None = None) -> Judgement:
    """Judge one checkout. The gates are run in the checkout; nothing in it is written.

    `checkout`, `guardrails` and `agents_tools` are resolved to real paths once, here, at the
    boundary where they arrive from the caller: every path a gate opens under one of them is then
    built by listing the same tree this resolution named, and every checker this run starts is run
    inside it rather than told where it is on a command line.

    `base` is the commit the run started from and `preserve` the task's patterns for files whose
    body the run must leave as it found it; `bridge` is the MCP server the registry is read
    through. Each is an input of one gate, and each gate says whether it could use it.

    **The first generation's form is kept.** A call naming neither a bridge nor anything to preserve
    is the question the first generation answers, and it gets that judgement: the five structural
    gates, without the two semantic ones. A caller still calibrated against the first generation
    passes neither — it is judged by what its suite measured — and a caller that names either is
    asking the second generation's question, where a stub no bridge could read holds `TRUE_DONE`
    back.
    """
    checkout = os.path.realpath(checkout)
    second = bool(bridge or preserve)
    names = GATES if second else CHECKER_GATES
    guardrails = os.path.realpath(guardrails or default_guardrails())
    agents_tools = os.path.realpath(agents_tools or default_agents_tools())
    version = bundle_version(checkout)
    if not os.path.isdir(checkout):
        return Judgement(ORACLE_ID, version,
                         _all_not_run(f"there is no checkout at {checkout}", available=False,
                                      names=names))
    index = index_within(index, checkout, guardrails)
    semantic, instrument = [], None
    if second:
        links, used = adr_links.gate(checkout, bridge, index)
        semantic = [preservation.gate(checkout, base, preserve), links]
        instrument = {"bridge": used} if used else None
    exclude = declared_exclusions(checkout)
    files, why = corpus(checkout, guardrails, exclude)
    if not files:
        # A checkout holding no documentation is a tree the gates reached and found nothing in; a
        # taxonomy that is absent or did not resolve is an instrument that never reached a tree.
        # Both are `UNKNOWN` for the checkers and they are not the same state. The two semantic
        # gates still run: a run that emptied the corpus has removed what the task said to keep.
        checkers = _all_not_run(why, available=(why == EMPTY_CORPUS), names=CHECKER_GATES)
        return Judgement(ORACLE_ID, version, [*checkers, *semantic], instrument)
    gates = [_frontmatter_gate(checkout, guardrails, exclude),
             _registry_gate(checkout, guardrails, index),
             *_agent_gates(checkout, agents_tools), *semantic]
    return Judgement(ORACLE_ID, version, gates, instrument)


def commit_id(value: str) -> str:
    """An `argparse` `type=` admitting only a commit id, never a ref name or an option."""
    if not preservation.COMMIT.fullmatch(value):
        raise argparse.ArgumentTypeError(f"{value!r} is not a commit id")
    return value


def preserve_pattern(value: str) -> str:
    """An `argparse` `type=` admitting only a relative glob."""
    if preservation.valid_preserve((value,)) is None:
        raise argparse.ArgumentTypeError(f"{value!r} is not a relative glob")
    return value


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("checkout", type=existing_directory, help="the checkout to judge")
    ap.add_argument("--guardrails", type=existing_directory, default=None,
                    help="the shared guardrail checkout")
    ap.add_argument("--agents-tools", type=existing_directory, default=None,
                    help="the agent bundle's tools/ directory")
    ap.add_argument("--index", type=existing_file, default=None,
                    help="the central ADR registry, for a checkout that is not the registry")
    ap.add_argument("--base", type=commit_id, default=None,
                    help="the commit the run started from, reachable in the checkout")
    ap.add_argument("--preserve", type=preserve_pattern, action="append", default=[],
                    help="a relative glob of files whose body the run must not change; repeatable")
    ap.add_argument("--bridge", type=existing_file, default=None,
                    help="the Exeris MCP server (dist/server.js) the registry is read through")
    a = ap.parse_args(argv)
    print(judge(a.checkout, guardrails=a.guardrails, agents_tools=a.agents_tools,
                index=a.index, base=a.base, preserve=tuple(a.preserve),
                bridge=a.bridge).to_json())
    return 0


if __name__ == "__main__":
    sys.exit(main())
