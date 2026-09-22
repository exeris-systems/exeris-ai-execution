#!/usr/bin/env python3
"""One run record per rescued execution stream — the capture producer's first form.

ADR-086 Engineering Protocol 2 makes this the first producer in the organisation: not a workflow and
not a harness, but one script run by hand over the streams repository at a named commit, whose run
log settles Engineering Protocol 4. Rows above zero and item 4 stands as written; no row that
validates and the registry becomes a precondition of the first row instead of a consequence of it.
That is why every stream that yields no row is counted with a reason, and why the counts are the
output rather than a side effect of it: the decision is made from the log, not from expectation.

`tools/ci_row.py` holds the derivations. This file holds everything that reaches outside it — the
streams clone, `gh api`, the inbox tree — and the division is deliberate: the derivations can
then be
stated as equalities by a suite, and the same functions fill a row from a step inside the reviewing
workflow later without carrying any of this.

Two invariants the reconstruction rests on, both about text the run no longer names directly.

`referenced_workflows[].sha` names the commit of the routine repository this run resolved its
reusable workflow at, and the produce job's own checkout of that repository is unpinned — so the two
are one commit, and the routine file at that SHA is the routine the runner read. A produce job that
pins that checkout independently of the workflow reference makes the two different commits, and a
row derived after such a change belongs on the far side of a fence from one derived before it.

`AGENTS.md` is read at the reviewed commit, which is where `repository_state.commit` points. The
runner reads it on the merge ref — the reviewed commit merged into its base — so the two agree
except where the base moved under the run. The hash and the repository state describe one tree, and
that is the property being held: a hash over an agent file from a tree the row does not name would
describe a run against a repository state nobody could reconstruct.

Both are stated in `docs/fences.md` beside the fence these rows carry, because a fence is retired by
a disagreement with the first live run that exports these components — and a disagreement is only
legible where what was assumed is written down.
"""

from __future__ import annotations

import argparse
import base64
import dataclasses
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools import ci_row  # noqa: E402
from tools.inbox_validate import PUBLISHERS, Report, check  # noqa: E402

if set(ci_row.PUBLISHERS) != set(PUBLISHERS):
    raise RuntimeError(
        "the producer and the inbox validator hold different lists of the organisation's writing "
        "identities — a name one refuses and the other admits is a row refused at the inbox after "
        "the producer believed it had already refused it")

# The repository the streams live in, named here because `execution.event_stream.ref` is a reference
# and not a path: a row read in ten years resolves it without knowing where the clone was.
STREAMS_REPO = "exeris-systems/exeris-ai-execution-streams"

# What the reviewing workflow is called, what its step is called, and the routine it hands the
# runner. All three are read from the run's own record of which workflow it resolved, so these are
# the names to match against rather than locations to read from.
REVIEW_WORKFLOW = "docs-review.yml"
REVIEW_STEP = "Docs and hygiene review"
ROUTINE_PATH = "docs-guardrails-review.md"

# Where the call to the review workflow is, when the run names no workflow file of its own. It is
# the entry workflow of `caller-example/`, the file every repository in the organisation copies.
CALLER_PATH = ".github/workflows/guardrails.yml"

# The files in the reviewed checkout the row's own components come from.
AGENTS_FILE = "AGENTS.md"
MANIFEST_PATH = ".agents/manifest.yaml"
VENDOR_PATH = ".agents/vendor"
SETTINGS_PATH = ".claude/settings.json"

# What this producer is, in a fence id. The grammar is `docs/fences.md`'s.
PRODUCER = "ci-backfill"

# Fixed for every row this producer writes, and each for a stated reason. `anthropic` is the only
# provider whose model ids this producer recognises, and a model id it does not recognise costs the
# row rather than being filed under a provider nobody established. `claude-code` is the client the
# reviewing action runs, and the version beside it is the stream's. `full` is what this producer can
# see: every count on a row comes from the run's own event record. `UNKNOWN` is ADR-086 §E.19 — the
# oracle judges after the pull request closes, so a row written at review time has not been judged,
# and fail-closed says exactly that rather than a pass.
PROVIDER = "anthropic"
MODEL_PREFIX = "claude-"
HARNESS_CLIENT = "claude-code"
CAPTURE_LEVEL = "full"
OUTCOME = "UNKNOWN"

class FetchError(Exception):
    """A REST call did not answer — not a 404, which is an answer, but no answer at all.

    Kept separate from a 404 so that an expired token, a rate limit or an unplugged network never
    reads as "the repository has no `AGENTS.md`". The first is a run to repeat; the second is a
    fact about the repository, and a row derived from the two confused would carry a hash over a
    file that exists.
    """


# The paths whose answer arrives a page at a time — a pull request's commits, an issue's comments
# and a run's jobs. None of the three is bounded by one page, and a first page taken for the whole
# answer turns a head commit that IS in the pull request into one that is not, and a gate that DID
# conclude into one the run never reports.
PAGINATED = ("/commits", "/comments", "/jobs")

# What a fetcher answers where the host says there is nothing there. A 404 is an ANSWER — this
# repository has no `AGENTS.md`, that pull request is gone — and it is deliberately not an
# exception, which is kept for a call that produced no answer at all.
ABSENT = {"status": "404"}


def absent(body: object) -> bool:
    """Whether a fetcher's answer is the host saying there is nothing there."""
    return isinstance(body, dict) and str(body.get("status")) == "404"


def decode_stream(text: str) -> list:
    """Every JSON document in `gh api`'s output, which is one per page under `--paginate`."""
    decoder = json.JSONDecoder()
    out, position = [], 0
    while position < len(text):
        while position < len(text) and text[position] in " \t\r\n":
            position += 1
        if position >= len(text):
            break
        document, position = decoder.raw_decode(text, position)
        out.append(document)
    return out


def merge_documents(text: str) -> object:
    """One document from a paginated response's several.

    Arrays concatenate. Objects merge their list-valued keys and keep the first page's scalars,
    which is the shape `runs/{id}/jobs` returns: a count and a list, where the list is the answer.
    """
    documents = decode_stream(text)
    if not documents:
        return None
    if len(documents) == 1:
        return documents[0]
    if all(isinstance(d, list) for d in documents):
        return [item for document in documents for item in document]
    if all(isinstance(d, dict) for d in documents):
        merged = dict(documents[0])
        for key, value in list(merged.items()):
            if isinstance(value, list):
                merged[key] = [item for d in documents for item in (d.get(key) or [])]
        return merged
    return documents


class Fetcher:
    """Every REST fact this derivation reads, through `gh api` and through nothing else.

    The seam is one method — `get(path)`, where `path` is a REST path with no leading slash and the
    answer is the parsed body or `ABSENT`. A fake answering that one method substitutes completely,
    which is what lets a suite state a derivation without a network. REST and never porcelain: a
    REST path is an interface the host versions, and `gh pr view` is a rendering that changes under
    its own releases.

    One cache entry per path, so a second run of this producer makes no call and derives the same
    rows from the same bytes. That is what makes the derivation checkable by someone who was not
    there: the cache IS the input, in the format a fixture takes. Only an answer is cached — a
    transport failure is not a fact about the repository, and caching one would turn an expired
    token into a permanent 404.
    """

    def __init__(self, cache_dir: str) -> None:
        self.cache_dir = cache_dir
        self.calls = 0
        os.makedirs(cache_dir, exist_ok=True)

    def paginated(self, path: str) -> bool:
        """Whether this path's answer has to be read to its end rather than to its first page."""
        return path.split("?")[0].endswith(PAGINATED)

    def key(self, path: str) -> str:
        """A file name that is safe, readable and a function of the whole path.

        The readable half is truncated and the digest is over the untruncated path, so two long
        paths that share a prefix are two entries rather than one silently reused.
        """
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", path)[:80]
        return f"{safe}-{ci_row.stream_digest(path.encode('utf-8'))[:16]}.json"

    def fetch(self, path: str, paginate: bool) -> dict:
        """The one call that leaves this process."""
        self.calls += 1
        command = ["gh", "api", "-H", "Accept: application/vnd.github+json"]
        if paginate:
            command.append("--paginate")
        command.append(path)
        try:
            done = subprocess.run(command, capture_output=True, text=True, check=False)
        except OSError as exc:
            return {"status": 0, "body": None, "error": str(exc)}
        if done.returncode != 0:
            error = done.stderr.strip()
            status = 404 if "HTTP 404" in error or "Not Found" in error else 0
            return {"status": status, "body": None, "error": error[:500]}
        return {"status": 200, "body": merge_documents(done.stdout), "error": ""}

    def get(self, path: str) -> object:
        """The body for `path`, `ABSENT` where the host has nothing there."""
        where = os.path.join(self.cache_dir, self.key(path))
        answer = None
        if os.path.exists(where):
            try:
                with open(where, encoding="utf-8") as handle:
                    answer = json.load(handle)
            except (OSError, json.JSONDecodeError):
                answer = None
        if answer is None:
            answer = self.fetch(path, self.paginated(path))
            if answer.get("status") in (200, 404):
                with open(where, "w", encoding="utf-8") as handle:
                    json.dump({"url": path, **answer}, handle,
                              indent=2, sort_keys=True, ensure_ascii=False)
        if answer.get("status") == 404:
            return dict(ABSENT)
        if answer.get("status") != 200:
            raise FetchError(f"{path}: {answer.get('error') or 'no answer'}")
        return answer.get("body")


class Unreadable:
    """The host answered about a file, and the answer carried no text.

    A file past the contents API's inline limit comes back with no content at all, a directory
    comes back as a listing, and a file that is not UTF-8 decodes to nothing. None of the three is
    the host saying there is no such file, and the difference decides a row: an absent `AGENTS.md`
    is a documented empty component of `agent.system_prompt_sha256`, so a component that could not
    be read, folded into the same answer, would hash as the convenient value where §C.14 requires
    the component to be recovered exactly or the row to be given up.
    """

    def __repr__(self) -> str:                             # so a log line reads
        return "<unreadable>"


UNREADABLE = Unreadable()


def text_at(fetcher, repo: str, path: str, ref: str):
    """One file of a repository at one commit.

    Three answers: the text, `None` where the host says there is no such file, and `UNREADABLE`
    where the host answered with something that is not inline text.

    A function and not a method, so that a fake answering `get` alone is a whole fetcher.
    """
    body = fetcher.get(f"repos/{repo}/contents/{urllib.parse.quote(path)}?ref={ref}")
    if absent(body):
        return None
    if not isinstance(body, dict) or body.get("encoding") != "base64":
        return UNREADABLE
    try:
        return base64.b64decode(body.get("content") or "").decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return UNREADABLE


def listing_at(fetcher, repo: str, path: str, ref: str) -> list[str] | None:
    """The entry names of one directory at one commit, or `None` where it is not there."""
    body = fetcher.get(f"repos/{repo}/contents/{urllib.parse.quote(path)}?ref={ref}")
    if not isinstance(body, list):
        return None
    return [str(entry.get("name")) for entry in body if isinstance(entry, dict)]


# --------------------------------------------------------------------------------------------
# One stream
# --------------------------------------------------------------------------------------------


@dataclasses.dataclass
class Outcome:
    """What one stream produced: a row, or a counted reason there is none."""

    run_id: str
    repo: str
    artifact_id: object
    workflow_run_id: object
    pull_request: int | None = None
    row: dict | None = None
    reason: str | None = None
    detail: str = ""
    visibility: str | None = None
    fence: str | None = None
    permission_mode: str | None = None
    started_at_coarse: bool = False
    denial_mismatch: dict | None = None
    surface_writes_nothing: bool | None = None
    head_rebased_away: bool | None = None
    path: str | None = None
    state: str | None = None

    def no_row(self, reason: str, detail: str = "") -> "Outcome":
        self.row, self.reason, self.detail = None, reason, detail
        return self


def head_of(entry: dict, fetcher: Fetcher) -> tuple[str, int, str] | None:
    """The `(repository, pull request, head sha)` one stream belongs to, or None where none.

    Resolved for every entry of the index and not only for the selected ones, because
    `execution.verdict_route` is filled only where one stream covers a commit: the publisher edits
    one comment per pull request, so two runs on one head leave one comment that names neither.
    """
    if not entry.get("workflow_run_id"):
        return None
    pull = ci_row.pr_number(entry.get("artifact_name"))
    if pull is None:
        return None
    try:
        run = fetcher.get(f"repos/{entry['repo']}/actions/runs/{entry['workflow_run_id']}")
    except FetchError:
        return None
    if absent(run) or not isinstance(run, dict) or not run.get("head_sha"):
        return None
    return str(entry["repo"]), pull, str(run["head_sha"])


def derive_one(entry: dict, fetcher: Fetcher, args, capture_version: str,
               heads: dict) -> Outcome:
    """One stream to one row, or to one counted reason there is none.

    The order of the tests is the order of what they cost: the stream is local and is read first, so
    that every stream contributes its `permissionMode` to the log whether or not it yields a row.
    After that the tests follow the derivation table, and each returns at the first value it cannot
    establish — §C.14's rule is that no row is written rather than a convenient value, so there is
    nothing to gather after the first failure.
    """
    out = Outcome(run_id=ci_row.run_id(entry.get("workflow_run_id"), entry.get("artifact_id")),
                  repo=str(entry.get("repo")), artifact_id=entry.get("artifact_id"),
                  workflow_run_id=entry.get("workflow_run_id"))

    where = os.path.join(args.streams, str(entry.get("path") or ""))
    try:
        with open(where, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        return out.no_row("stream-missing", str(exc))
    digest = ci_row.stream_digest(data)
    try:
        events = json.loads(data)
    except json.JSONDecodeError as exc:
        return out.no_row("stream-unreadable", str(exc))
    if not isinstance(events, list):
        return out.no_row("stream-unreadable", "the stream is not an array of events")
    facts = ci_row.stream_facts(events)
    out.permission_mode = facts.permission_mode
    if facts.permission_denials is not None \
            and facts.permission_denials != facts.permission_denied_events:
        out.denial_mismatch = {"result": facts.permission_denials,
                               "events": facts.permission_denied_events}

    if digest != entry.get("sha256") or facts.event_count != entry.get("event_count"):
        return out.no_row("digest-mismatch",
                          f"recomputed {digest} over {facts.event_count} event(s); the index "
                          f"states {entry.get('sha256')} over {entry.get('event_count')}")
    if not facts.has_result:
        return out.no_row("result-absent", "the stream carries no `result` event")
    if facts.turns is None or facts.wall_time_ms is None:
        return out.no_row("result-incomplete", "the `result` event names no turns or no duration")
    if ci_row.names_a_publisher(facts.model) or \
            any(ci_row.names_a_publisher(m) for m in facts.acted_models):
        return out.no_row("publisher-as-agent",
                          "the stream names one of the organisation's writing identities as the "
                          "model — the bot is the pen, never the agent")
    if not facts.acted_models:
        return out.no_row("model-absent", "no `assistant` event names a model")
    if len(facts.acted_models) > 1:
        return out.no_row("model-ambiguous",
                          f"{len(facts.acted_models)} models took turns: "
                          f"{', '.join(facts.acted_models)}")
    model_id = facts.acted_models[0]
    if not model_id.startswith(MODEL_PREFIX):
        return out.no_row("provider-unresolved", f"`{model_id}` is not a provider this producer "
                                                 f"can name")
    if not facts.harness_version:
        return out.no_row("harness-version-absent", "the `init` event names no client version")
    mode = ci_row.accounting_mode(facts.api_key_source)
    if mode is None:
        return out.no_row("credential-ambiguous",
                          f"`apiKeySource` is {facts.api_key_source!r} — the ledger a row belongs "
                          f"to is not established")
    out.fence = ci_row.fence_id(args.fence_date, PRODUCER, facts.harness_version)

    if not entry.get("workflow_run_id"):
        return out.no_row("run-unresolved", "the index entry names no workflow run")
    out.pull_request = ci_row.pr_number(entry.get("artifact_name"))
    if out.pull_request is None:
        return out.no_row("pr-unresolved",
                          f"`{entry.get('artifact_name')}` names no pull request")
    repo = out.repo
    pull = out.pull_request

    run = fetcher.get(f"repos/{repo}/actions/runs/{entry['workflow_run_id']}")
    if absent(run) or not isinstance(run, dict):
        return out.no_row("run-404", "the workflow run is gone")
    named = ((run.get("repository") or {}).get("full_name"))
    if named != repo:
        return out.no_row("repo-mismatch",
                          f"the run belongs to `{named}` and the index files it under `{repo}`")
    head_sha = str(run.get("head_sha") or "")
    if not re.fullmatch(r"[0-9a-f]{40}", head_sha):
        return out.no_row("head-unresolved", "the run names no head commit")

    pull_json = fetcher.get(f"repos/{repo}/pulls/{pull}")
    pull_json = None if absent(pull_json) or not isinstance(pull_json, dict) else pull_json

    commits = fetcher.get(f"repos/{repo}/pulls/{pull}/commits")
    if not isinstance(commits, list):
        return out.no_row("pr-404", f"pull request {pull} has no commit list")
    # The commit list is the ordinary evidence that this run reviewed this pull request, and a
    # branch rewritten after the review is the case where it is not there to give. The head SHA
    # stays the identity of the tree the run read and is what the fingerprint hashes, so the row
    # survives the rewrite on the branch's evidence instead — and the log records which of the two
    # answered, because a row whose head the pull request no longer lists is a row a reader will
    # come back to.
    if head_sha in {str(c.get("sha")) for c in commits if isinstance(c, dict)}:
        out.head_rebased_away = False
    elif ci_row.head_belongs_to(run, pull_json):
        out.head_rebased_away = True
    else:
        return out.no_row("head-not-in-pr",
                          f"`{head_sha}` is not a commit of pull request {pull}, and the run's "
                          f"branch is not that pull request's")

    jobs = fetcher.get(f"repos/{repo}/actions/runs/{entry['workflow_run_id']}/jobs?per_page=100")
    jobs = None if absent(jobs) else jobs
    started, coarse = ci_row.review_started_at(jobs, run, REVIEW_STEP)
    out.started_at_coarse = coarse
    if not started:
        return out.no_row("started-at-unresolved", "neither the review step nor the run is dated")

    reference = ci_row.referenced_workflow(run, REVIEW_WORKFLOW)
    if not reference or not reference.get("sha"):
        return out.no_row("review-workflow-unresolved",
                          f"the run references no `{REVIEW_WORKFLOW}` at a commit")
    template = text_at(fetcher, reference["repository"], reference["path"], reference["sha"])
    if template is UNREADABLE:
        return out.no_row("review-workflow-unreadable",
                          f"`{reference['path']}` at `{reference['sha']}` answered with no inline "
                          f"text")
    if template is None:
        return out.no_row("review-workflow-404",
                          f"`{reference['path']}` is not there at `{reference['sha']}`")
    routine = text_at(fetcher, reference["repository"], ROUTINE_PATH, reference["sha"])
    if routine is UNREADABLE:
        return out.no_row("routine-unreadable",
                          f"`{ROUTINE_PATH}` at `{reference['sha']}` answered with no inline text")
    if routine is None:
        return out.no_row("routine-404",
                          f"`{ROUTINE_PATH}` is not there at `{reference['sha']}`")
    # The caller is the workflow that handed the review its inputs, and the run names it where the
    # host reports one. Where it does not, the entry workflow every repository copies is where the
    # call is, and the file is accepted as the caller only once it is seen calling the review — a
    # file that calls it nowhere supplied none of the prompt's inputs, whatever it is named.
    caller_path = str(run.get("path") or CALLER_PATH)
    caller = text_at(fetcher, repo, caller_path, head_sha)
    if caller is UNREADABLE or caller is None:
        return out.no_row("caller-unreadable",
                          f"`{caller_path}` is not readable at `{head_sha}`")
    inputs = ci_row.caller_inputs(caller, REVIEW_WORKFLOW)
    if inputs is None:
        return out.no_row("caller-unreadable",
                          f"`{caller_path}` calls no `{REVIEW_WORKFLOW}`, so it is not the caller "
                          f"that handed the review its inputs")
    mapping = ci_row.l1_mapping(inputs.get("l1-results"))
    conclusions = ci_row.job_conclusions(jobs, list(mapping.values()))
    unresolved = sorted(name for name, value in conclusions.items() if value is None)
    if unresolved:
        return out.no_row("l1-unresolved",
                          f"the run reports no conclusion for {', '.join(unresolved)}")
    # The caller's own text, handed to the prompt whole. A template of the earlier generation
    # substitutes it directly and one of the later substitutes the translating step's output
    # instead, so both are supplied and the template decides which it asks for.
    l1_input = ci_row.l1_results_input(caller, conclusions, REVIEW_WORKFLOW)
    if l1_input is None:
        return out.no_row("l1-unresolved",
                          "the caller's `l1-results` names a job the run reports no conclusion "
                          "for, so the text the prompt carried is not established")
    substitutions = {
        "${{ github.event.pull_request.number }}": pull,
        "${{ github.repository }}": repo,
        "${{ steps.gates.outputs.checks_run }}": ci_row.checks_run_json(
            {check_name: conclusions[job] for check_name, job in mapping.items()}),
        "${{ inputs.l1-results }}": l1_input,
        "${{ inputs.repo-routine != '' && inputs.repo-routine || '(none)' }}":
            inputs.get("repo-routine") or "(none)",
        "${{ inputs.repo-routine != '' && 'repo-routine.base.md' || '(none)' }}":
            "repo-routine.base.md" if (inputs.get("repo-routine") or "").strip() else "(none)",
        "${{ inputs.repo-checks != '' && 'repo-checks.out' || '(none)' }}":
            "repo-checks.out" if (inputs.get("repo-checks") or "").strip() else "(none)",
    }
    try:
        prompt = ci_row.render_prompt(template, substitutions)
    except ci_row.UnsupportedTemplate as exc:
        return out.no_row("template-unsupported", str(exc))
    agents_md = text_at(fetcher, repo, AGENTS_FILE, head_sha)
    if agents_md is UNREADABLE:
        return out.no_row("agents-unreadable",
                          f"`{AGENTS_FILE}` at `{head_sha}` answered with no inline text, so "
                          f"whether the repository has one is not established")
    # An absent `AGENTS.md` is the documented empty component: the repository has none, and that
    # is a fact about the tree the hash describes rather than a gap in the reading of it.
    fingerprint_hash = ci_row.system_prompt_sha256(prompt, routine, agents_md or "")

    # An unreadable permission file leaves the surface absent, which is what the contract asks of a
    # producer that cannot read what a run was permitted — no digest is the one thing that does not
    # read as "unrestricted". An ABSENT file is a different answer and is inside the hash.
    #
    # The whole template goes in beside the launch line: the allow-list may be spent through a
    # workflow variable, and the file is where that variable's value is. An expression it cannot
    # resolve costs the row for the reason an unrenderable prompt does — a surface hashed over an
    # unrendered expression is a digest over an allow-list no run ever had.
    try:
        allow = ci_row.allow_list_tokens(ci_row.block_scalar(template, "claude_args"), template)
    except ci_row.UnsupportedTemplate as exc:
        return out.no_row("template-unsupported", str(exc))
    settings_raw = text_at(fetcher, repo, SETTINGS_PATH, head_sha)
    surface: str | None
    if settings_raw is UNREADABLE:
        surface = None
    elif settings_raw is None:
        surface = ci_row.tool_surface(allow, None)
    else:
        try:
            surface = ci_row.tool_surface(allow, json.loads(settings_raw))
        except json.JSONDecodeError:
            surface = None
    # `execution.result_commits` is written only where the allow-list establishes that no tool on
    # the run's surface could have written anything. Where the launch passed no allow-list the
    # field is left out: the run's powers were the client's defaults, and an empty list there would
    # say a review produced no commits on the strength of nothing.
    out.surface_writes_nothing = ci_row.writes_nothing(allow)

    repo_json = fetcher.get(f"repos/{repo}")
    out.visibility = ci_row.visibility(None if absent(repo_json) else repo_json)
    manifest = text_at(fetcher, repo, MANIFEST_PATH, head_sha)
    if manifest is UNREADABLE:
        return out.no_row("manifest-unreadable",
                          f"`{MANIFEST_PATH}` at `{head_sha}` answered with no inline text, so a "
                          f"pin it may carry would be missed and the vendored tree read instead")
    vendor = listing_at(fetcher, repo, VENDOR_PATH, head_sha)
    bundle = ci_row.bundle_version(manifest, vendor)
    if bundle is None:
        return out.no_row("bundle-unpinned",
                          "the checkout pins no `exeris-agents` bundle, so the rules the run was "
                          "subject to are not established")

    scope = ci_row.scope_from_body((pull_json or {}).get("body"))
    if scope is None:
        return out.no_row("scope-unparsed",
                          f"pull request {pull} declares no scope class this table admits")

    route = None
    if heads.get((repo, pull, head_sha)) == 1:
        comments = fetcher.get(f"repos/{repo}/issues/{pull}/comments")
        route = ci_row.verdict_route(comments if isinstance(comments, list) else [], head_sha)

    try:
        out.row = ci_row.assemble(
            run_id=out.run_id,
            started_at=started,
            fingerprint=ci_row.fingerprint_ci(repo, pull, head_sha),
            domain=ci_row.REVIEW_LIVE_DOMAIN,
            scope=scope,
            provider=PROVIDER,
            model_id=model_id,
            harness_client=HARNESS_CLIENT,
            harness_version=facts.harness_version,
            system_prompt_sha256=fingerprint_hash,
            repository=repo,
            visibility=out.visibility,
            commit=head_sha,
            bundle_version=bundle,
            turns=facts.turns,
            tool_calls=facts.tool_calls,
            wall_time_ms=facts.wall_time_ms,
            event_stream_ref=ci_row.event_stream_ref(
                STREAMS_REPO, str(entry.get("path")), args.streams_commit),
            event_stream_sha256=digest,
            event_count=facts.event_count,
            accounting_mode=mode,
            usage=facts.usage,
            oracle=ci_row.REVIEW_DISPOSITION_ORACLE,
            outcome=OUTCOME,
            capture_version=capture_version,
            fence=out.fence,
            tool_surface=surface,
            verdict_route=route,
            permission_denials=facts.permission_denials,
            capture_level=CAPTURE_LEVEL,
            human_prompts=facts.human_prompts,
            result_commits=[] if out.surface_writes_nothing else None,
        )
    except ci_row.PublisherAsAgent as exc:
        return out.no_row("publisher-as-agent", str(exc))
    return out


# --------------------------------------------------------------------------------------------
# The batch
# --------------------------------------------------------------------------------------------


def row_path(root: str, outcome: Outcome) -> str:
    """Where one row lands: `inbox/<UTC date it started>/runs/<run id>.json`.

    The private root mirrors the same layout under a directory of its own, so that a row bound for
    the enterprise sibling is moved rather than reshaped — and so that nothing bound for it ever
    sits inside a public `inbox/` on the way.
    """
    date = str(outcome.row["started_at"])[:10]
    return os.path.join(root, "inbox", date, "runs", outcome.run_id + ".json")


def validate_batch(outcomes: list[Outcome], schemas: str, declared: str) -> dict:
    """Every candidate row through the inbox validator, in a throwaway inbox of its own.

    The producer runs the same validator the inbox gates on, before it writes anything (ADR-087
    §C.15): a row that fails there never enters the batch, so an invalid row is repaired at the
    producer rather than quarantined in the dataset. The temporary root holds the real `schemas/`
    and an `inbox.json` declaring the visibility THIS batch is bound for, because rule 1 is fail
    closed on that identity and a public batch checked against a private inbox would pass a test it
    was never subject to.
    """
    if not outcomes:
        return {}
    where = tempfile.mkdtemp(prefix="derive-ci-rows-")
    try:
        shutil.copytree(schemas, os.path.join(where, "schemas"))
        os.makedirs(os.path.join(where, "inbox"), exist_ok=True)
        with open(os.path.join(where, "inbox", "inbox.json"), "w", encoding="utf-8") as handle:
            json.dump({"visibility": declared}, handle)
        by_path = {}
        for outcome in outcomes:
            target = row_path(where, outcome)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as handle:
                handle.write(ci_row.dump(outcome.row))
            by_path[os.path.realpath(target)] = outcome.run_id
        report = Report()
        check(where, report)
        refused: dict = {}
        for path, message in report.bad:
            name = by_path.get(os.path.realpath(path))
            if name:
                refused.setdefault(name, []).append(message)
        return {name: "; ".join(messages) for name, messages in refused.items()}
    finally:
        shutil.rmtree(where, ignore_errors=True)


def declared_identity(root: str) -> str | None:
    """What the inbox at `root` says it holds, or `None` where it says nothing readable.

    Rule 1 of the inbox validator is fail-closed on this value, so the producer runs its own
    pre-write check under the identity the DESTINATION declares rather than under the one the batch
    assumes. A batch checked against the wrong identity passes a test it was never subject to, and
    §C.15 has the producer run the validator precisely so that the inbox is not where that is
    discovered.
    """
    try:
        with open(os.path.join(root, "inbox", "inbox.json"), encoding="utf-8") as handle:
            declared = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(declared, dict):
        return None
    return str(declared.get("visibility") or "") or None


def write_row(root: str, outcome: Outcome) -> None:
    """Append a row, or refuse. `inbox/` is appended to and never rewritten.

    Identical bytes are a second derivation of one run and are a skip, which is what makes this
    producer re-runnable. Different bytes under one name are two answers about one run, and the
    repair is a new row and a fence — never an overwrite, which would take the first answer out of
    the record as though it had never been given.
    """
    target = row_path(root, outcome)
    text = ci_row.dump(outcome.row)
    outcome.path = os.path.relpath(target, root)
    if os.path.exists(target):
        with open(target, encoding="utf-8") as handle:
            if handle.read() == text:
                outcome.state = "skip"
                return
        outcome.no_row("row-exists-differs",
                       f"`{outcome.path}` already holds a different row for this run")
        return
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(text)
    outcome.state = "new"


# --------------------------------------------------------------------------------------------
# The log
# --------------------------------------------------------------------------------------------


def log_line(outcome: Outcome) -> dict:
    """One stream's line of the run log."""
    return {
        "run_id": outcome.run_id,
        "repository": outcome.repo,
        "pull_request": outcome.pull_request,
        "workflow_run_id": outcome.workflow_run_id,
        "artifact_id": outcome.artifact_id,
        "row": outcome.row is not None,
        "state": outcome.state,
        "path": outcome.path,
        "reason": outcome.reason,
        "detail": outcome.detail,
        "visibility": outcome.visibility,
        "fence": outcome.fence,
        "permission_mode": outcome.permission_mode,
        "started_at_coarse": outcome.started_at_coarse,
        "permission_denial_mismatch": outcome.denial_mismatch,
        "surface_writes_nothing": outcome.surface_writes_nothing,
        "head_rebased_away": outcome.head_rebased_away,
    }


def summary(outcomes: list[Outcome], args, capture_version: str) -> str:
    """The Markdown half of the run log — the counts Engineering Protocol 4 is decided from.

    Every no-row reason is a row of a table with a count beside it, because the protocol's question
    is not whether a derivation is possible but how many streams yield a row and what stops the
    rest. A summary that named only the successes would answer the easy half.
    """
    rows = [o for o in outcomes if o.row is not None]
    counted: dict = {}
    for outcome in outcomes:
        if outcome.reason:
            counted[outcome.reason] = counted.get(outcome.reason, 0) + 1
    fences: dict = {}
    for outcome in rows:
        fences[outcome.fence] = fences.get(outcome.fence, 0) + 1
    modes: dict = {}
    for outcome in outcomes:
        if outcome.permission_mode:
            modes[outcome.permission_mode] = modes.get(outcome.permission_mode, 0) + 1

    new = sum(1 for o in rows if o.state == "new")
    same = sum(1 for o in rows if o.state == "skip")
    out = [
        "## derive_ci_rows",
        "",
        f"Streams read **{len(outcomes)}** — rows **{len(rows)}** "
        f"(written **{new}**, already identical **{same}**), "
        f"no row **{len(outcomes) - len(rows)}**.",
        "",
        f"Contract `{capture_version}`, streams at `{args.streams_commit}`, "
        f"fence date `{args.fence_date}`.",
        "",
    ]
    if counted:
        out += ["| No-row reason | Streams |", "|:--|--:|"]
        out += [f"| `{reason}` | {count} |" for reason, count in sorted(counted.items())]
        out += [""]
    if fences:
        out += ["| Fence | Rows |", "|:--|--:|"]
        out += [f"| `{fence}` | {count} |" for fence, count in sorted(fences.items())]
        out += [""]
    if modes:
        out += ["| `permissionMode` seen | Streams |", "|:--|--:|"]
        out += [f"| `{mode}` | {count} |" for mode, count in sorted(modes.items())]
        out += [""]
    return "\n".join(out)


# --------------------------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------------------------


def streams_head(clone: str) -> str | None:
    """What commit the streams clone is on, or None where that cannot be established."""
    try:
        done = subprocess.run(["git", "-C", clone, "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=False)
    except OSError:
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def derive(index: list, fetcher: Fetcher, args, capture_version: str) -> list[Outcome]:
    """Every selected stream, derived — the pure-ish middle a suite drives with a fake fetcher."""
    heads: dict = {}
    for entry in index:
        resolved = head_of(entry, fetcher)
        if resolved:
            heads[resolved] = heads.get(resolved, 0) + 1
    chosen = [e for e in index
              if not args.only
              or ci_row.run_id(e.get("workflow_run_id"), e.get("artifact_id")) == args.only]
    return [derive_one(entry, fetcher, args, capture_version, heads) for entry in chosen]


def main(argv: list[str] | None = None, fetcher=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--streams", required=True,
                        help="a clone of the streams repository, holding `index.json`")
    parser.add_argument("--streams-commit", required=True,
                        help="the commit the clone must be on — every `event_stream.ref` names it")
    parser.add_argument("--out", required=True,
                        help="the repository holding `inbox/` and `schemas/`")
    parser.add_argument("--cache-dir", required=True,
                        help="where every `gh api` answer is kept, so a re-run makes no call")
    parser.add_argument("--private-out", default="",
                        help="where a row whose repository is not public goes; without it such a "
                             "row is counted and not written")
    parser.add_argument("--only", default="",
                        help="one run id, for re-deriving a single row")
    parser.add_argument("--fence-date", required=True,
                        help="the date the fence these rows carry was written, YYYY-MM-DD")
    args = parser.parse_args(argv)

    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", args.fence_date):
        print("::error::--fence-date is a date, `YYYY-MM-DD`, and the fence id is built from it")
        return 2
    head = streams_head(args.streams)
    if head != args.streams_commit:
        print(f"::error::the streams clone is on `{head}` and `--streams-commit` names "
              f"`{args.streams_commit}` — every row's `event_stream.ref` would name a commit that "
              f"does not hold the stream it was derived from")
        return 2
    try:
        with open(os.path.join(args.out, "schemas", "VERSION"), encoding="utf-8") as handle:
            capture_version = handle.read().strip()
        with open(os.path.join(args.streams, "index.json"), encoding="utf-8") as handle:
            index = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"::error::{exc}")
        return 2

    try:
        outcomes = derive(index, fetcher or Fetcher(args.cache_dir), args, capture_version)
    except FetchError as exc:
        print(f"::error::{exc}")
        return 2

    schemas = os.path.join(args.out, "schemas")
    batches = [("public", args.out), ("enterprise-private", args.private_out)]
    for declared, root in batches:
        candidates = [o for o in outcomes if o.row is not None and o.visibility == declared]
        if declared != "public" and not root:
            for outcome in candidates:
                outcome.no_row("visibility-not-public",
                               "the repository is not public and no `--private-out` was given, so "
                               "the row is counted and not written")
            continue
        identity = declared_identity(root)
        if identity != declared:
            print(f"::error::`{os.path.join(root, 'inbox', 'inbox.json')}` declares "
                  f"`{identity}` and the rows bound for it are `{declared}` — every one of them "
                  f"would pass this producer's own gate and be refused by the inbox's first rule")
            return 2
        refused = validate_batch(candidates, schemas, identity)
        for outcome in candidates:
            if outcome.run_id in refused:
                outcome.no_row("validator-refused", refused[outcome.run_id])
        for outcome in candidates:
            if outcome.row is not None:
                write_row(root, outcome)

    for outcome in outcomes:
        print(json.dumps(log_line(outcome), sort_keys=True, ensure_ascii=False))
    print()
    print(summary(outcomes, args, capture_version))
    return 1 if any(o.reason == "row-exists-differs" for o in outcomes) else 0


if __name__ == "__main__":
    sys.exit(main())
