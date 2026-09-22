#!/usr/bin/env python3
"""Cases for `derive_ci_rows.py` and `ci_row.py` — one per derivation ADR-087 §C.14 governs.

§C.14's rule is *no row, never a convenient value*, and a rule like that is only as good as the
cases that watch it refuse. Every case here builds a repository on disk — `schemas/`, an empty
`inbox/`, a git clone of the streams repository at a named commit — serves the REST bodies from a
fake that records what it was asked for, runs the deriver as a function, and then reads what
reached the inbox rather than what the deriver said about it.

Two constants are pinned by hand rather than recomputed: the rendered prompt of the golden case and
the hash over it. A test that recomputes a hash the way the code computes it agrees with the code
and with nothing else; the derivation of both is written out in the comment above them, and a
change to the canonicalisation is supposed to break the case, because that is the change
`instrument.fence` exists to mark.

WHAT THIS SUITE ASSUMES OF THE IMPLEMENTATION, stated because it is written beside it rather than
after it:

  * `tools.derive_ci_rows.main(argv, fetcher=None) -> int` — `argv` without the program name,
    `fetcher` the injected REST reader, a real `gh api` one when absent. The log goes to stdout.
  * The fetcher is `get(path) -> object`: `path` is a `gh api` REST path with no leading slash,
    the return is the parsed JSON body, and an absent resource is `{"status": "404"}` rather than
    an exception. Nothing else is called — `gh` porcelain is not a fetcher.
  * Per stream, one JSON line on stdout. A stream that yielded no row names the reason in it; the
    cases below assert the reason is about the right thing, not the exact wording.
  * `ci_row.render_prompt(template, subs)` takes the workflow file's whole text and a mapping whose
    keys are the substitution expressions as they are written in it, `${{ github.repository }}` and
    the other four.
  * `ci_row.refuse_publisher(row)` raises on a row that puts an organisation App's name in `agent.*`
    and returns on one that puts it in `execution.principal`.
  * The pre-write validator is reached through the module-level name `tools.derive_ci_rows.check`,
    which one case replaces so that a refusal can be watched stopping a row before it is written.
    That is a coupling to a name rather than to a behaviour, and it is stated here because a
    producer that called the validator some other way would pass the case without running it.
"""

import base64
import hashlib
import importlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
FIXTURES = os.path.join(HERE, "fixtures", "ci")
SCHEMAS = os.path.join(ROOT, "schemas")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

CASES: list[tuple[str, object]] = []


def case(name):
    def register(fn):
        CASES.append((name, fn))
        return fn
    return register


# ---------------------------------------------------------------------------------------------
# What the fixtures say. Every one of these is a value written into `tools/fixtures/ci/`, repeated
# here so a case can name it; none is derived.

REPO = "exeris-systems/exeris-ai-execution"
ORG = "exeris-systems/.github"
PR = 7
RUN = 41000000001
ARTIFACT = 20000000001
HEAD = "a1b2c3d4" * 5
REF_SHA = "e5f60718" * 5
RUN_ID = f"ci-{RUN}-{ARTIFACT}"
ROW_PATH = os.path.join("inbox", "2026-09-17", "runs", f"{RUN_ID}.json")
# The fence date every case passes. A fence id carries the day the fence was written, and a
# producer that took it from the clock would write a different id on a re-run of the same input —
# so the deriver takes it as an argument and the cases name one, which is what makes the golden
# case's id an equality rather than a pattern.
FENCE_DATE = "2026-09-19"
MODEL = "claude-sonnet-5"
SECOND_MODEL = "claude-haiku-4-5-20251001"
CLIENT_VERSION = "2.1.274"

DOCS_REVIEW = "guardrails-org/.github/workflows/docs-review.yml"
ROUTINE = "guardrails-org/docs-guardrails-review.md"
AGENTS_MD = "repo/AGENTS.md"
MANIFEST = "repo/.agents/manifest.yaml"
SETTINGS = "repo/.claude/settings.json"
CALLER = "repo/.github/workflows/guardrails.yml"

# The five expressions the produce job's prompt carries, and what the fixture substitutes into
# each. The first two come from the event, the third from the L1 job conclusions through the
# caller's `l1-results` expression (`docs` success, `commits` success, `pr-body` failure, mapped
# `success→pass`, `failure→fail`, anything else `not-run`, emitted the way `jq -c` emits it), and
# the last two from the caller's own inputs.
SUBS = {
    "${{ github.event.pull_request.number }}": "7",
    "${{ github.repository }}": REPO,
    "${{ steps.gates.outputs.checks_run }}":
        '[{"check":"docs-lint","result":"pass"},{"check":"commit-lint","result":"pass"},'
        '{"check":"pr-body-check","result":"fail"}]',
    "${{ inputs.repo-routine != '' && inputs.repo-routine || '(none)' }}":
        "docs/repo-review-rules.md",
    "${{ inputs.repo-checks != '' && 'repo-checks.out' || '(none)' }}": "repo-checks.out",
}

# The golden text: the fixture workflow's `prompt: |` block, dedented by the indent of its own
# first line, with those five expressions replaced and exactly one trailing newline. Written out
# rather than rendered, so that a renderer which drops a blank line, keeps the block's indentation
# or reorders a substitution disagrees with something.
GOLDEN_PROMPT = (
    "Review pull request 7 in\n"
    "exeris-systems/exeris-ai-execution against the routine in\n"
    "`.guardrails/docs-guardrails-review.md`.\n"
    "\n"
    "L1 GATE RESULTS, already in the shape `checks_run` takes:\n"
    "\n"
    '[{"check":"docs-lint","result":"pass"},{"check":"commit-lint","result":"pass"},'
    '{"check":"pr-body-check","result":"fail"}]\n'
    "\n"
    "REPOSITORY EXTENSION: docs/repo-review-rules.md\n"
    "REPOSITORY CHECK OUTPUT: repo-checks.out\n"
)

# SHA-256 over the three components in §C.14's order, each stripped of one trailing newline and
# given exactly one: GOLDEN_PROMPT, then `guardrails-org/docs-guardrails-review.md`, then
# `repo/AGENTS.md`. Computed once from the fixture bytes; a fixture edit moves it, which is the
# point of pinning it.
SYSTEM_PROMPT_SHA256 = "318bdb6a65f08253784342a025fba7c27b399cb106f4d5eb1b454c92a981fc3d"

# The same hash with the third component empty. An absent `AGENTS.md` is a documented empty
# component — one newline, not a skipped concatenation — so a repository that carries none still
# has a hash, and it is this one for as long as the other two components hold.
SYSTEM_PROMPT_SHA256_NO_AGENTS = "961b9a5268a6d3c2f7909f473d53112e5fe79260fd52cd4e0c8908e2318dfcf9"

# SHA-256 over `json.dumps({"v":1,"allow":A,"checkout_permissions":P}, sort_keys=True,
# separators=(",",":"), ensure_ascii=True) + "\n"`, where A is the `--allowedTools` string split on
# commas at bracket depth zero, stripped, deduplicated and sorted —
#   ["Bash(git diff:*)","Bash(git log:*)","Bash(git show:*)","Bash(git status:*)","Glob","Grep","Read"]
# — and P is `{"allow": ["Read(//home/runner/work/**)"], "deny": ["Bash(rm:*)"]}` from the fixture's
# `.claude/settings.json`.
TOOL_SURFACE = "fdb5246cbad76771a888b04504b84ddb75b41e90430b020f3b56b63fe5aa2652"

# The same canonicalisation with `A` recorded as `null`. A workflow that passes no allow-list is a
# run whose powers are known and unrestricted, which is a state, not an absence — so the field is
# present and the digest differs.
TOOL_SURFACE_NO_ALLOW = "f72eeff2f44753af758e66f6d93a2cee3f52ea4af4e4b6906f8fc719726b11b6"

# `"ci:" + sha256(f"{repo}\n{pr}\n{head_sha}\n")`. The derivation carries no key: a fingerprint two
# producers have to agree on cannot depend on a secret only one of them holds.
FINGERPRINT = "ci:9675a802e85fcf87bdb5858a746d18e4ade46b5c3acb8fc6854b6dae575b8f7d"


# ---------------------------------------------------------------------------------------------
# The fake host.

def fixture(name: str) -> str:
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as fh:
        return fh.read()


class FakeFetcher:
    """The canned REST bodies, and a record of what was asked for.

    Keyed the way a cache of `gh api` responses is keyed — by the path — with one accommodation:
    a file read through the contents API is looked up by (repository, path, ref) rather than by the
    exact spelling of the query, because the ref is the part of that URL a derivation depends on
    and the rest is the caller's punctuation. A lookup at a ref nothing was registered at is a 404,
    recorded, and not quietly served from another ref.
    """

    def __init__(self) -> None:
        self.bodies: dict[str, object] = dict(json.loads(fixture("rest.json")))
        self.files: dict[tuple[str, str, str], object] = {}
        self.asked: list[str] = []
        self.missed: list[str] = []
        # `rest.json` holds every canned body, contents responses included; the contents ones move
        # into the by-(repo, path, ref) map so that one lookup serves them however the URL is spelt.
        for key in [k for k in self.bodies if "/contents/" in k]:
            bare, params = self._split(key)
            m = re.match(r"repos/([^/]+/[^/]+)/contents/(.+)$", bare)
            self.files[(m.group(1), m.group(2), params.get("ref", ""))] = self.bodies.pop(key)

    def file(self, repo: str, path: str, ref: str, text: str) -> None:
        self.files[(repo, path, ref)] = text

    def drop_file(self, repo: str, path: str, ref: str) -> None:
        self.files.pop((repo, path, ref), None)

    def drop(self, key: str) -> None:
        bare, params = self._split(key)
        m = re.match(r"repos/([^/]+/[^/]+)/contents/(.+)$", bare)
        if m:
            self.drop_file(m.group(1), m.group(2), params.get("ref", ""))
            return
        self.bodies.pop(bare, None)

    @staticmethod
    def _split(url: str) -> tuple[str, dict[str, str]]:
        bare, _, query = url.lstrip("/").partition("?")
        params = {}
        for piece in query.split("&"):
            if "=" in piece:
                k, v = piece.split("=", 1)
                params[k] = v
        return bare, params

    def get(self, url: str, *args, **kwargs) -> object:
        self.asked.append(url)
        bare, params = self._split(url)
        m = re.match(r"repos/([^/]+/[^/]+)/contents/(.+)$", bare)
        if m:
            repo, path = m.group(1), m.group(2)
            text = self.files.get((repo, path, params.get("ref", "")))
            if text is None:
                self.missed.append(url)
                return {"status": "404"}
            if isinstance(text, list):                      # a directory listing, served as one
                return text
            return {"type": "file", "name": os.path.basename(path), "path": path,
                    "encoding": "base64",
                    "content": base64.b64encode(text.encode("utf-8")).decode("ascii")}
        if bare in self.bodies:
            return self.bodies[bare]
        self.missed.append(url)
        return {"status": "404"}

    # One canned body, two access shapes: a deriver that asks for a file's text rather than for the
    # contents object gets the same bytes, so the fixture does not decide which of the two the
    # implementation uses.
    def get_text(self, url: str, *args, **kwargs) -> str | None:
        body = self.get(url)
        if isinstance(body, dict) and body.get("encoding") == "base64":
            return base64.b64decode(body["content"]).decode("utf-8")
        return None


class World:
    """A tempdir holding the out root, a git clone of the streams repository, and the fake host."""

    def __init__(self, tmp: str) -> None:
        self.tmp = tmp
        self.out = os.path.join(tmp, "out")
        self.streams = os.path.join(tmp, "streams")
        self.cache = os.path.join(tmp, "cache")
        self.private = os.path.join(tmp, "private")
        self.index = json.loads(fixture("index.json"))
        self.events = json.loads(fixture("stream.json"))
        self.rest = FakeFetcher()
        self.visibility = "public"
        self.private_visibility = "enterprise-private"
        self.commit = ""
        self.rest.file(REPO, "AGENTS.md", HEAD, fixture(AGENTS_MD))
        self.rest.file(REPO, ".agents/manifest.yaml", HEAD, fixture(MANIFEST))
        self.rest.file(REPO, ".claude/settings.json", HEAD, fixture(SETTINGS))
        self.rest.file(REPO, ".github/workflows/guardrails.yml", HEAD, fixture(CALLER))
        self.rest.file(ORG, ".github/workflows/docs-review.yml", REF_SHA, fixture(DOCS_REVIEW))
        self.rest.file(ORG, "docs-guardrails-review.md", REF_SHA, fixture(ROUTINE))

    # -- building ------------------------------------------------------------------------------

    def pr_body(self, scope: str = "docs-only") -> None:
        body = self.rest.bodies[f"repos/{REPO}/pulls/{PR}"]["body"]
        # The line is written the way the template writes it and the way `pr_body_check.py` reads
        # it — at the start of a line, unadorned. A substitution that matched nothing would leave
        # every case that calls this asserting about the fixture's own scope class instead of the
        # one it asked for, so the count is checked rather than assumed.
        body, count = re.subn(r"(?m)^Scope class: .*$", f"Scope class: {scope}", body)
        assert count == 1, f"the fixture body carries no `Scope class:` line to rewrite"
        self.rest.bodies[f"repos/{REPO}/pulls/{PR}"] = dict(
            self.rest.bodies[f"repos/{REPO}/pulls/{PR}"], body=body)

    def build(self) -> "World":
        os.makedirs(os.path.join(self.out, "inbox"), exist_ok=True)
        os.makedirs(self.cache, exist_ok=True)
        shutil.copytree(SCHEMAS, os.path.join(self.out, "schemas"), dirs_exist_ok=True)
        with open(os.path.join(self.out, "inbox", "inbox.json"), "w", encoding="utf-8") as fh:
            json.dump({"visibility": self.visibility}, fh)

        # The private root is a repository too — the enterprise sibling — and it says what it holds
        # the same way the public one does. A destination that declares nothing is a destination
        # the producer cannot run its own pre-write check for.
        os.makedirs(os.path.join(self.private, "inbox"), exist_ok=True)
        with open(os.path.join(self.private, "inbox", "inbox.json"), "w",
                  encoding="utf-8") as fh:
            json.dump({"visibility": self.private_visibility}, fh)

        os.makedirs(self.streams, exist_ok=True)
        # One file per index entry, every one of them these events. An index naming two artefacts of
        # one run is how a case says two streams cover one commit, and each entry's own file is
        # written so that the deriver reads what the entry names rather than what the suite guessed.
        for entry in self.index:
            stream_path = os.path.join(self.streams, entry["path"])
            os.makedirs(os.path.dirname(stream_path), exist_ok=True)
            with open(stream_path, "w", encoding="utf-8") as fh:
                json.dump(self.events, fh, indent=1, ensure_ascii=False)
                fh.write("\n")
            raw = open(stream_path, "rb").read()
            if entry.get("sha256") == "recompute":
                entry["sha256"] = hashlib.sha256(raw).hexdigest()
                entry["event_count"] = len(self.events)
                entry["size_bytes"] = len(raw)
        with open(os.path.join(self.streams, "index.json"), "w", encoding="utf-8") as fh:
            json.dump(self.index, fh, indent=1, ensure_ascii=False)
            fh.write("\n")

        git = ["git", "-c", "user.email=x@x", "-c", "user.name=x",
               "-c", "commit.gpgsign=false", "-C", self.streams]
        subprocess.run(["git", "-c", "init.defaultBranch=main", "init", "-q", self.streams],
                       check=True, capture_output=True)
        subprocess.run(git + ["add", "-A"], check=True, capture_output=True)
        subprocess.run(git + ["commit", "-qm", "x"], check=True, capture_output=True)
        self.commit = subprocess.run(git + ["rev-parse", "HEAD"], check=True, capture_output=True,
                                     text=True).stdout.strip()
        return self

    # -- running -------------------------------------------------------------------------------

    def run(self, *extra: str) -> tuple[int, str]:
        deriver = importlib.import_module("tools.derive_ci_rows")
        argv = ["--streams", self.streams, "--streams-commit", self.commit,
                "--out", self.out, "--cache-dir", self.cache,
                "--fence-date", FENCE_DATE, *extra]
        held, sys.stdout = sys.stdout, io.StringIO()
        try:
            code = deriver.main(argv, fetcher=self.rest)
            text = sys.stdout.getvalue()
        finally:
            sys.stdout = held
        return code, text

    # -- reading back --------------------------------------------------------------------------

    def rows(self, where: str | None = None) -> dict[str, dict]:
        base = where or self.out
        found = {}
        for here, _dirs, names in os.walk(base):
            if os.path.basename(here) != "runs":
                continue
            for name in sorted(names):
                if name.endswith(".json"):
                    path = os.path.join(here, name)
                    with open(path, encoding="utf-8") as fh:
                        found[os.path.relpath(path, base)] = json.load(fh)
        return found

    def row(self) -> dict:
        found = self.rows()
        assert len(found) == 1, f"expected one row, found {sorted(found)}"
        return next(iter(found.values()))


def ci_row():
    return importlib.import_module("tools.ci_row")


def assert_no_row(world: World, out: str, *tokens: str) -> None:
    """No row reached the inbox, and the log says why in words about the right thing."""
    found = world.rows()
    assert not found, f"a row was written where §C.14 admits none: {sorted(found)}\n{out}"
    low = out.lower()
    assert any(t.lower() in low for t in tokens), \
        f"no reason naming any of {tokens} in the log:\n{out}"


def log_line(out: str, run_id: str) -> dict:
    """One stream's JSON line of the run log, read as the log rather than as prose.

    The log is one JSON line per stream followed by a Markdown summary, and a case asserting about
    a value in it parses that line. A substring looked for anywhere in the output is satisfied by a
    key whose value is `null`, and `null` is the state most of these keys are in on a run where
    nothing disagreed — so the word would be found on every run and the case would pin nothing.
    """
    found = []
    for line in out.splitlines():
        if not line.startswith("{"):
            continue
        try:
            found.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    mine = [line for line in found if line.get("run_id") == run_id]
    assert len(mine) == 1, f"expected one log line for `{run_id}`, found {len(mine)}:\n{out}"
    return mine[0]


def fenced_verdicts(facts: object) -> list[dict]:
    """The fenced verdicts in whatever `stream_facts` returns, found by their shape.

    The suite pins the value and not the field it arrives under: a verdict is a mapping carrying
    `decision`, which is the same test `execution_verdicts()` applies to a fenced block.
    """
    bag = facts if isinstance(facts, dict) else vars(facts)
    for value in bag.values():
        if isinstance(value, list) and value and all(
                isinstance(v, dict) and "decision" in v for v in value):
            return value
    raise AssertionError(f"no fenced verdict in the facts: {sorted(bag)}")


def validate(root: str) -> list[tuple[str, str]]:
    """The inbox validator, imported rather than shelled — the gate, not a subprocess of it."""
    mod = importlib.import_module("tools.inbox_validate")
    rep = mod.Report()
    mod.check(root, rep)
    return rep.bad


# ---------------------------------------------------------------------------------------------
# 1 — the ledger is not the row.

@case("1 — a priced stream yields a row with no price in it, and no second model")
def _(root):
    w = World(root).build()
    code, out = w.run()
    assert code == 0, out
    row = w.row()
    text = json.dumps(row, sort_keys=True)

    # `total_cost_usd` is a subscription runtime's printed figure and §C.14 keeps it off the row;
    # `modelUsage` names every model BILLED, and the row names the one that took the turns. The
    # assertion is over the serialised row rather than over named fields: a producer that invents a
    # key for either is caught by the same line that catches it filling the one the schema has.
    assert "provider_reported_cost" not in row["accounting"], text
    assert "cost" not in text.lower(), text
    assert "1.5" not in text, text
    assert SECOND_MODEL not in text, text
    assert row["agent"]["model_id"] == MODEL, text
    assert row["accounting"]["mode"] == "subscription", text


# 2 — the pull request a row is filed against.

@case("2 — a name that carries no pull request, and a head that pull request does not hold, "
      "each yield no row")
def _(root):
    w = World(root)
    w.index[0]["artifact_name"] = "l2-execution-x"
    w.build()
    _code, out = w.run()
    assert_no_row(w, out, "pr-unresolved")

    # A name that resolves is not yet a pull request that holds this run. The fingerprint is over
    # (repository, pull request, head), so a head belonging to some other pull request would file
    # this run against a task it never was — and every later comparison would group it there. The
    # pull request's own commit list is what says which, and it is checked rather than assumed.
    elsewhere = os.path.join(root, "elsewhere")
    os.makedirs(elsewhere)
    w2 = World(elsewhere)
    w2.rest.bodies[f"repos/{REPO}/pulls/{PR}/commits"] = [
        {"sha": "d" * 40, "commit": {"message": "x"}}]
    w2.build()
    _code, out = w2.run()
    assert_no_row(w2, out, "head-not-in-pr", "head")


# 3 — the run itself is gone.

@case("3 — a run the host no longer has yields no row, and nothing but REST was asked")
def _(root):
    w = World(root)
    w.build()
    w.rest.drop(f"repos/{REPO}/actions/runs/{RUN}")
    _code, out = w.run()
    assert_no_row(w, out, "404", "run-absent", "run-404")
    assert all(u.lstrip("/").startswith("repos/") for u in w.rest.asked), w.rest.asked


# 4 — the publisher never took a turn.

@case("4 — a stream naming an organisation App as the model yields no row")
def _(root):
    w = World(root)
    for ev in w.events:
        if ev.get("subtype") == "init":
            ev["model"] = "exeris-bot"
        if ev.get("type") == "assistant":
            ev["message"]["model"] = "exeris-bot"
    w.index[0]["sha256"] = "recompute"
    w.build()
    _code, out = w.run()
    assert_no_row(w, out, "publisher", "exeris-bot")


# 5 — the refusal is on the field, not on the string.

@case("5 — refuse_publisher reads agent.*, and leaves execution.principal alone")
def _(root):
    w = World(root).build()
    code, out = w.run()
    assert code == 0, out
    row = w.row()
    refuse = ci_row().refuse_publisher

    bad = json.loads(json.dumps(row))
    bad["agent"]["harness"]["client"] = "exeris-inbox[bot]"
    try:
        refuse(bad)
    except Exception:                                      # noqa: BLE001 - the refusal is the point
        pass
    else:
        raise AssertionError("`harness.client: exeris-inbox[bot]` was not refused")

    # `execution.principal` is where a platform identity is the answer (ADR-087 §A.4), so the rule
    # has to leave that field alone by construction rather than by the spelling of the login.
    fine = json.loads(json.dumps(row))
    fine["execution"]["principal"] = {"kind": "app", "login": "exeris-agent[bot]"}
    refuse(fine)


# 6 — the gate between the deriver and the inbox.

@case("6 — the written row clears inbox_validate, and an alias repeated does not")
def _(root):
    w = World(root).build()
    code, out = w.run()
    assert code == 0, out
    assert validate(w.out) == [], validate(w.out)

    # The deriver writes `unresolved:<model_id>` because no CI runtime exposes a dated snapshot, so
    # the collapsed form cannot arise from the fixture — it is written here to show what the
    # pre-write check would refuse if it ever did. A producer that skips the check ships the row
    # this case describes.
    row = w.row()
    row["agent"]["model_snapshot"] = row["agent"]["model_id"]
    with open(os.path.join(w.out, ROW_PATH), "w", encoding="utf-8") as fh:
        json.dump(row, fh)
    bad = validate(w.out)
    assert any("an alias is not a snapshot" in m for _p, m in bad), bad

    # And the gate runs BEFORE the write, which is the whole of what §C.15 asks of a producer: with
    # the validator refusing every candidate, nothing reaches `inbox/` at all and the log says the
    # validator is why. A producer that validated after writing, or not at all, would leave the row
    # in the dataset for the inbox to quarantine — which is the state the pre-write check exists to
    # make impossible.
    refusing = os.path.join(root, "refusing")
    os.makedirs(refusing)
    w2 = World(refusing).build()
    deriver = importlib.import_module("tools.derive_ci_rows")
    held = deriver.check

    def refuse_every(where, report):
        for here, _dirs, names in os.walk(os.path.join(where, "inbox")):
            if os.path.basename(here) != "runs":
                continue
            for name in sorted(names):
                if name.endswith(".json"):
                    report.error(os.path.join(here, name), "x-refused-before-the-write")
        return []

    deriver.check = refuse_every
    try:
        _code, out = w2.run()
    finally:
        deriver.check = held
    assert_no_row(w2, out, "validator-refused")


# 7 — two models took turns.

@case("7 — two models in the assistant events yield no row")
def _(root):
    w = World(root)
    turns = [e for e in w.events if e.get("type") == "assistant"]
    turns[3]["message"]["model"] = "claude-opus-4-1"
    w.index[0]["sha256"] = "recompute"
    w.build()
    _code, out = w.run()
    assert_no_row(w, out, "multi-model", "two models", "models")


# 8 — the reference has to be verifiable.

@case("8 — a digest or a count off by one yields no row")
def _(root):
    # Recomputed from the bytes and compared with the index, both halves: the digest keeps the
    # reference verifiable once retention has taken the artefact, and the count keeps the row
    # summarisable once it is gone. A row whose reference disagrees with the file it names is a row
    # about a stream nobody can produce again.
    w = World(root)
    w.index[0]["event_count"] = w.index[0]["event_count"] + 1
    w.build()
    _code, out = w.run()
    assert_no_row(w, out, "event_count", "event count", "digest", "mismatch")

    digest = os.path.join(root, "digest")
    os.makedirs(digest)
    w2 = World(digest)
    w2.index[0]["sha256"] = "0" * 64
    w2.build()
    _code, out = w2.run()
    assert_no_row(w2, out, "sha256", "digest", "mismatch")


# 9 — a row about a repository nobody outside can read.

@case("9 — a private repository's row lands outside inbox/, and a refused read is fail-closed")
def _(root):
    w = World(root)
    w.rest.bodies[f"repos/{REPO}"] = {"full_name": REPO, "private": True, "visibility": "private"}
    w.build()
    code, out = w.run("--private-out", w.private)
    assert code == 0, out
    assert not w.rows(), f"a private row reached the public inbox: {sorted(w.rows())}"
    private = w.rows(w.private)
    assert len(private) == 1, f"{sorted(private)}\n{out}"
    assert next(iter(private.values()))["repository_state"]["visibility"] == "enterprise-private"

    # A read the host refuses is not a read that said `public`. §C.17 maps only `PUBLIC` to
    # `public`, and a producer that cannot see the value at all is further from `public` than one
    # that saw `INTERNAL`.
    refused = os.path.join(root, "refused")
    os.makedirs(refused)
    w2 = World(refused)
    w2.rest.drop(f"repos/{REPO}")                           # a 403 reaches the deriver as absent
    w2.build()
    _code, out = w2.run("--private-out", w2.private)
    assert not w2.rows(), f"an unreadable visibility was written public: {sorted(w2.rows())}\n{out}"


# 10 — the scope class comes from the body or the row does not exist.

@case("10 — an unparseable scope class yields no row, and the four classes reach the enum")
def _(root):
    w = World(root)
    w.pr_body("<one of: runtime hot path | runtime non-hot | test-tooling | docs-only>")
    w.build()
    _code, out = w.run()
    assert_no_row(w, out, "scope")

    # Nor is a class the table does not carry. §C.14 makes the mapping a FIXED TABLE and not a
    # convention, so a name outside it costs the row instead of being spelt into one: a producer
    # that hyphenated whatever it was given would file a run under a scope nobody declared.
    outside = os.path.join(root, "outside")
    os.makedirs(outside)
    w0 = World(outside)
    w0.pr_body("infra")
    w0.build()
    _code, out = w0.run()
    assert_no_row(w0, out, "scope")

    # And each of the four classes the body may declare reaches the row in the enum's spelling of
    # it: `workload.scope` admits lower-case hyphenated segments and the body writes spaces.
    want = {"runtime hot path": "runtime-hot-path", "runtime non-hot": "runtime-non-hot",
            "test-tooling": "test-tooling", "docs-only": "docs-only"}
    for i, (written, expected) in enumerate(want.items()):
        here = os.path.join(root, f"c{i}")
        os.makedirs(here)
        w = World(here)
        w.pr_body(written)
        w.build()
        code, out = w.run()
        assert code == 0, out
        assert w.row()["workload"]["scope"] == expected, f"{written}: {w.row()['workload']}"


# 11 — a run under no allow-list is a run whose powers are known.

@case("11 — a produce job passing no --allowedTools records allow:null, and a different digest")
def _(root):
    w = World(root).build()
    code, out = w.run()
    assert code == 0, out
    assert w.row()["execution"]["tool_surface"] == TOOL_SURFACE, w.row()["execution"]

    bare = os.path.join(root, "bare")
    os.makedirs(bare)
    w2 = World(bare)
    template = fixture(DOCS_REVIEW)
    template = "\n".join(l for l in template.splitlines() if "--allowedTools" not in l) + "\n"
    w2.rest.file(ORG, ".github/workflows/docs-review.yml", REF_SHA, template)
    w2.build()
    code, out = w2.run()
    assert code == 0, out
    surface = w2.row()["execution"]["tool_surface"]
    assert surface == TOOL_SURFACE_NO_ALLOW, surface
    assert surface != TOOL_SURFACE, surface

    # And the same run states no commits at all. `result_commits: []` is a claim about what the run
    # produced, and the only evidence a stream-derived row has for it is an allow-list carrying no
    # tool that could write; under the client's own defaults there is no such evidence, so the
    # field is left out and the log carries the state it was left out for.
    assert "result_commits" not in w2.row()["execution"], w2.row()["execution"]
    assert log_line(out, RUN_ID)["surface_writes_nothing"] is None, out
    assert w.row()["execution"]["result_commits"] == [], w.row()["execution"]


# 12 — a repository with no AGENTS.md still has a hash.

@case("12 — an absent AGENTS.md is an empty component, and the hash is this constant")
def _(root):
    w = World(root)
    w.rest.drop_file(REPO, "AGENTS.md", HEAD)
    w.build()
    code, out = w.run()
    assert code == 0, out
    got = w.row()["agent"]["system_prompt_sha256"]
    assert got == SYSTEM_PROMPT_SHA256_NO_AGENTS, got
    assert got != SYSTEM_PROMPT_SHA256, got


# 13 — a template the reconstruction cannot render exactly.

@case("13 — a sixth expression in the prompt block yields no row")
def _(root):
    w = World(root)
    template = fixture(DOCS_REVIEW).replace(
        "            REPOSITORY CHECK OUTPUT:",
        "            RUN ATTEMPT: ${{ github.run_attempt }}\n            REPOSITORY CHECK OUTPUT:")
    w.rest.file(ORG, ".github/workflows/docs-review.yml", REF_SHA, template)
    w.build()
    _code, out = w.run()
    assert_no_row(w, out, "template", "unsupported", "${{")


# 14 — the golden.

@case("14 — the template and the inputs render this exact text, and hash to this exact digest")
def _(root):
    w = World(root).build()
    code, out = w.run()
    assert code == 0, out

    rendered = ci_row().render_prompt(fixture(DOCS_REVIEW), SUBS)
    assert rendered == GOLDEN_PROMPT, repr(rendered)

    row = w.row()
    assert row["agent"]["system_prompt_sha256"] == SYSTEM_PROMPT_SHA256, row["agent"]
    assert row["run_id"] == RUN_ID, row["run_id"]
    assert row["started_at"] == "2026-09-17T10:00:00Z", row["started_at"]
    assert row["workload"]["fingerprint"] == FINGERPRINT, row["workload"]
    assert row["repository_state"]["commit"] == HEAD, row["repository_state"]
    assert row["repository_state"]["bundle_version"] == "2.1.0", row["repository_state"]
    assert row["agent"]["harness"] == {"client": "claude-code", "version": CLIENT_VERSION}
    assert row["agent"]["model_snapshot"] == f"unresolved:{MODEL}", row["agent"]
    assert row["execution"]["turns"] == 19, row["execution"]
    assert row["execution"]["tool_calls"] == 18, row["execution"]
    assert row["execution"]["wall_time_ms"] == 408318, row["execution"]
    assert row["execution"]["capture_level"] == "full", row["execution"]
    assert row["execution"]["result_commits"] == [], row["execution"]
    assert "principal" not in row["execution"], row["execution"]
    assert "scope_denials" not in row["execution"], row["execution"]
    assert row["execution"]["event_stream"]["ref"] == \
        f"exeris-systems/exeris-ai-execution-streams/{w.index[0]['path']}@{w.commit}"
    assert row["oracle"] == {"id": "review-disposition", "version": "rest-v1",
                             "calibration": {"suite": "none", "status": "not-run",
                                             "result": "none"}}, row["oracle"]
    assert row["outcome"] == "UNKNOWN", row["outcome"]
    assert row["instrument"]["capture_version"] == open(
        os.path.join(SCHEMAS, "VERSION"), encoding="utf-8").read().strip()
    assert row["instrument"]["fence"] == f"{FENCE_DATE}-ci-backfill-cc-2-1-274", row["instrument"]
    assert all(u.lstrip("/").startswith("repos/") for u in w.rest.asked), w.rest.asked

    # The third substitution is bridged, and the bridge is READ rather than assumed: the caller's
    # own `l1-results` says which check name each of its jobs reports under, and the prompt prints
    # those names against those jobs' conclusions. A producer that took `needs.<id>` for the check
    # name would render another text — and hash it — for every caller that names the two apart,
    # which is the shape the organisation's own callers already have.
    renamed = os.path.join(root, "renamed")
    os.makedirs(renamed)
    w2 = World(renamed)
    caller = fixture(CALLER).replace('"docs-lint"', '"docs-gate"')
    w2.rest.file(REPO, ".github/workflows/guardrails.yml", HEAD, caller)
    w2.build()
    code, out = w2.run()
    assert code == 0, out
    mapping = ci_row().l1_mapping(ci_row().caller_inputs(caller).get("l1-results"))
    assert mapping == {"docs-gate": "docs", "commit-lint": "commits",
                       "pr-body-check": "pr-body"}, mapping
    conclusions = {"docs": "success", "commits": "success", "pr-body": "failure"}
    checks_run = ci_row().checks_run_json({c: conclusions[job] for c, job in mapping.items()})
    assert checks_run == ('[{"check":"docs-gate","result":"pass"},'
                          '{"check":"commit-lint","result":"pass"},'
                          '{"check":"pr-body-check","result":"fail"}]'), checks_run
    moved = w2.row()["agent"]["system_prompt_sha256"]
    assert moved != SYSTEM_PROMPT_SHA256, moved


# 15 — the ledger's names are not the contract's.

@case("15 — the four token classes reach accounting.usage under the contract's names")
def _(root):
    w = World(root).build()
    code, out = w.run()
    assert code == 0, out
    assert w.row()["accounting"]["usage"] == {
        "input_tokens": 112, "output_tokens": 3788,
        "cache_read_tokens": 388702, "cache_write_tokens": 10176}, w.row()["accounting"]

    # A class the runtime did not report is absent from the row, never zero. Zero tokens and no
    # measurement are the one pair this column cannot tell apart, so the key is left out and the
    # other three stand unchanged beside it.
    partial = os.path.join(root, "partial")
    os.makedirs(partial)
    w2 = World(partial)
    result = next(e for e in w2.events if e.get("type") == "result")
    del result["usage"]["cache_creation_input_tokens"]
    w2.index[0]["sha256"] = "recompute"
    w2.build()
    code, out = w2.run()
    assert code == 0, out
    usage = w2.row()["accounting"]["usage"]
    assert "cache_write_tokens" not in usage, usage
    assert usage == {"input_tokens": 112, "output_tokens": 3788,
                     "cache_read_tokens": 388702}, usage


# 16 — the count is the list's length.

@case("16 — permission_denials is the length of the result's list, and a disagreement is logged")
def _(root):
    w = World(root).build()
    code, out = w.run()
    assert code == 0, out
    assert w.row()["execution"]["permission_denials"] == 2, w.row()["execution"]
    assert log_line(out, RUN_ID)["permission_denial_mismatch"] is None, out

    # The stream carries the refusals twice — as events and as a list on `result` — and the row
    # takes the list. Where the two disagree the row still says two and the log says by how much,
    # because a producer that silently prefers one of two disagreeing sources has made a
    # measurement out of a choice nobody can see. The two counts are read from the log rather than
    # a word looked for in it: the key is on every line, so a line that carried `null` would
    # satisfy any test that only asked whether refusals were mentioned.
    odd = os.path.join(root, "odd")
    os.makedirs(odd)
    w2 = World(odd)
    denial = next(e for e in w2.events if e.get("subtype") == "permission_denied")
    w2.events.insert(6, json.loads(json.dumps(denial)))
    w2.index[0]["sha256"] = "recompute"
    w2.build()
    code, out = w2.run()
    assert code == 0, out
    assert w2.row()["execution"]["permission_denials"] == 2, w2.row()["execution"]
    assert log_line(out, RUN_ID)["permission_denial_mismatch"] == {"events": 3, "result": 2}, out


# 17 — a second pass writes the same bytes or refuses.

@case("17 — the same input twice is identical bytes and a skip, and an edited row is refused")
def _(root):
    w = World(root).build()
    code, out = w.run()
    assert code == 0, out
    first = open(os.path.join(w.out, ROW_PATH), "rb").read()

    code, out = w.run()
    assert code == 0, out
    assert open(os.path.join(w.out, ROW_PATH), "rb").read() == first
    assert "skip" in out.lower(), out

    row = json.loads(first)
    row["outcome"] = "TRUE_DONE"
    with open(os.path.join(w.out, ROW_PATH), "w", encoding="utf-8") as fh:
        json.dump(row, fh, indent=2, sort_keys=True, ensure_ascii=False)
        fh.write("\n")
    code, out = w.run()
    assert "row-exists-differs" in out, out
    assert json.loads(open(os.path.join(w.out, ROW_PATH), encoding="utf-8").read())["outcome"] \
        == "TRUE_DONE", "the deriver rewrote a row that differed instead of refusing"


# 18 — a prompt nobody typed is not a prompt.

@case("18 — human_prompts counts a user turn carrying text, and a CI run has none")
def _(root):
    w = World(root).build()
    code, out = w.run()
    assert code == 0, out
    assert w.row()["execution"]["human_prompts"] == 0, w.row()["execution"]

    spoken = os.path.join(root, "spoken")
    os.makedirs(spoken)
    w2 = World(spoken)
    w2.events.insert(6, {"type": "user", "session_id": "x-session", "uuid": "x-human",
                         "timestamp": "2026-09-17T10:02:00Z",
                         "message": {"role": "user", "content": [{"type": "text", "text": "x"}]}})
    w2.index[0]["sha256"] = "recompute"
    w2.build()
    code, out = w2.run()
    assert code == 0, out
    assert w2.row()["execution"]["human_prompts"] == 1, w2.row()["execution"]


# 19 — the bundle pin.

@case("19 — no manifest and no vendor directory yields no row; a vendor directory alone names it")
def _(root):
    w = World(root)
    w.rest.drop_file(REPO, ".agents/manifest.yaml", HEAD)
    w.rest.drop(f"repos/{REPO}/contents/.agents/vendor?ref={HEAD}")
    w.build()
    _code, out = w.run()
    assert_no_row(w, out, "bundle")

    vendored = os.path.join(root, "vendored")
    os.makedirs(vendored)
    w2 = World(vendored)
    w2.rest.drop_file(REPO, ".agents/manifest.yaml", HEAD)
    w2.build()
    code, out = w2.run()
    assert code == 0, out
    assert w2.row()["repository_state"]["bundle_version"] == "2.1.0", w2.row()["repository_state"]


# 20 — which transport carried the verdict.

@case("20 — the publisher's marker at this head names the route; a marker at another does not")
def _(root):
    w = World(root).build()
    code, out = w.run()
    assert code == 0, out
    assert w.row()["execution"]["verdict_route"] == "execution-log", w.row()["execution"]

    # The route the comment names is the one the stream can be read for: the fenced verdict is in
    # the runner's own output, which is what `execution_verdicts()` reads and what the marker says
    # the publisher read. A run states its verdict twice — once in the final assistant turn and
    # again in the `result` event that repeats it — so the count is not the test; the reading is
    # the publisher's own, and the publisher takes the last fenced block.
    verdicts = fenced_verdicts(ci_row().stream_facts(w.events))
    assert verdicts and verdicts[-1]["decision"] == "PASS", verdicts

    stale = os.path.join(root, "stale")
    os.makedirs(stale)
    w2 = World(stale)
    comments = json.loads(json.dumps(w2.rest.bodies[f"repos/{REPO}/issues/{PR}/comments"]))
    comments[0]["body"] = comments[0]["body"].replace(f"sha={HEAD}", f"sha={'c' * 40}")
    w2.rest.bodies[f"repos/{REPO}/issues/{PR}/comments"] = comments
    w2.build()
    code, out = w2.run()
    assert code == 0, out
    assert "verdict_route" not in w2.row()["execution"], w2.row()["execution"]

    # And two streams covering one commit leave the field absent on both. The publisher edits one
    # comment per pull request, so a comment on a head that two runs reviewed names neither of
    # them; a route copied onto both rows would say of each that it was the run published.
    twice = os.path.join(root, "twice")
    os.makedirs(twice)
    w3 = World(twice)
    second = json.loads(json.dumps(w3.index[0]))
    second["artifact_id"] = ARTIFACT + 1
    second["path"] = second["path"].replace(str(ARTIFACT), str(ARTIFACT + 1))
    second["sha256"] = "recompute"
    w3.index.append(second)
    w3.build()
    code, out = w3.run()
    assert code == 0, out
    both = w3.rows()
    assert len(both) == 2, f"{sorted(both)}\n{out}"
    for name, row in both.items():
        assert "verdict_route" not in row["execution"], f"{name}: {row['execution']}"


# 21 — which ledger the run belongs to.

@case("21 — an API key named in the init event contradicts the produce job's token, so no row")
def _(root):
    w = World(root)
    for ev in w.events:
        if ev.get("subtype") == "init":
            ev["apiKeySource"] = "ANTHROPIC_API_KEY"
    w.index[0]["sha256"] = "recompute"
    w.build()
    _code, out = w.run()
    assert_no_row(w, out, "credential", "apikeysource", "api key", "accounting")


# 22 — the contract, read by a validator that is not this repository's.

@case("22 — every written row satisfies the schema under Draft202012Validator")
def _(root):
    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        print("skipped: jsonschema absent")
        return
    w = World(root).build()
    code, out = w.run()
    assert code == 0, out
    with open(os.path.join(SCHEMAS, "run-record.schema.json"), encoding="utf-8") as fh:
        schema = json.load(fh)
    validator = Draft202012Validator(schema)
    for name, row in w.rows().items():
        errors = sorted(validator.iter_errors(row), key=lambda e: list(e.path))
        assert not errors, f"{name}: " + "; ".join(
            f"{list(e.path)}: {e.message}" for e in errors)
    assert validate(w.out) == [], validate(w.out)


def main() -> int:
    failures = 0
    for name, fn in CASES:
        with tempfile.TemporaryDirectory() as root:
            try:
                fn(root)
            except AssertionError as exc:
                failures += 1
                print(f"::error title=derive_ci_rows_suite::{name}: {exc}")
            except Exception as exc:                       # noqa: BLE001 - reported, not hidden
                failures += 1
                print(f"::error title=derive_ci_rows_suite::{name}: {type(exc).__name__}: {exc}")
    print(f"derive_ci_rows_suite: ran {len(CASES)} cases, {failures} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
