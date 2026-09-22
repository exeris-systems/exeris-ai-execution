#!/usr/bin/env python3
"""The derivations one CI run record is made of — ADR-087 §C.14, as pure functions.

Every function here is a function of its arguments and of nothing else: no network, no clock, no
filesystem, no environment. That is what lets one derivation fill a row from a rescued stream today
and from a step inside the reviewing workflow later, and it is what lets a suite state a derivation
as an equality instead of as a run. Everything that reaches outside — `gh api`, the stream files,
the inbox tree — is `tools/derive_ci_rows.py`'s.

The rule the derivations are written to is §C.14's: a value the inputs do not establish yields NO
ROW, never a convenient one. So a function here returns `None` where it cannot tell, and the caller
turns that into a counted no-row. None of them invents a zero for a count nobody measured, an empty
list for a list nobody read, or an absent digest for a surface nobody could see.

Two fields of a stream are never read. `total_cost_usd` is a price-list computation over token
counts, which under a subscription is imputed rather than reported and which the contract refuses
(ADR-086 §C.14); `modelUsage` is the ledger of models BILLED, and a run's model reference built from
it names a model that took no turn as the model that did the work. `stream_facts` reads neither, and
`assemble` has no parameter through which a cost could reach a row at all.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import shlex

# The organisation's own writing identities, as `tools/inbox_validate.py` holds them. The two lists
# are one list stated twice and `tools/derive_ci_rows.py` compares them at import: this module is
# shared with a producer that will run where the inbox validator is not installed, so it carries the
# names rather than importing them, and the comparison is what stops the two spellings from drifting
# into a row refused at the inbox by a name the producer thought it had already refused.
PUBLISHERS = ("exeris-bot", "exeris-inbox", "exeris-agent")

# The oracle a `docs-review-live` row names, and the calibration state it carries while no suite has
# been run as a suite — `docs/oracles.md` is the register these two values resolve in, and ADR-086
# §E.19 is why a row naming an uncalibrated oracle is `UNKNOWN` and never a pass.
REVIEW_DISPOSITION_ORACLE = {
    "id": "review-disposition",
    "version": "rest-v1",
    "calibration": {"suite": "none", "status": "not-run", "result": "none"},
}

# The domain of a review the runner performed on a real pull request in CI (ADR-086 §D.15). A sweep
# and a review are judged by different oracles, so they are different domains however alike the two
# runs look, and one name over both would average what must never be averaged.
REVIEW_LIVE_DOMAIN = "docs-review-live"

# ADR-087 §C.14's fixed table, and the reason it is a table: a scope class mapped by convention is a
# scope class each producer maps its own way.
SCOPE_CLASSES = {
    "runtime hot path": "runtime-hot-path",
    "runtime non-hot": "runtime-non-hot",
    "test-tooling": "test-tooling",
    "docs-only": "docs-only",
}

# The expressions the review workflow's prompt template substitutes, written as the template writes
# them. The set is closed on purpose: a template that grew one more substitutes something this
# reconstruction cannot supply, and a hash over a prompt with an unrendered expression left in it is
# a hash of a prompt no runner ever read.
#
# A substitution is keyed by its own expression rather than by a nickname for it, so that what
# a caller supplies and what the template asks for are one string and cannot be paired wrongly.
#
# Two of them state the same fact in two workflow generations: `inputs.l1-results` is the caller's
# own text handed to the prompt whole, and `steps.gates.outputs.checks_run` is that text after a
# translating step was put between them. A template carries one or the other, and the reconstruction
# supplies both so that the generation a run belongs to is the template's to decide.
PROMPT_SUBSTITUTIONS = (
    "${{ github.event.pull_request.number }}",
    "${{ github.repository }}",
    "${{ steps.gates.outputs.checks_run }}",
    "${{ inputs.l1-results }}",
    "${{ inputs.repo-routine != '' && inputs.repo-routine || '(none)' }}",
    "${{ inputs.repo-checks != '' && 'repo-checks.out' || '(none)' }}",
)

EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}", re.S)
ARTIFACT_NAME = re.compile(r"^l2-execution-(\d+)$")
NEEDS_RESULT = re.compile(
    r'"([^"]+)"\s*:\s*"\$\{\{\s*needs\.([A-Za-z0-9_.-]+)\.result\s*\}\}"')
# The same expression read for the job alone, without the check name a caller writes beside it. The
# two patterns are not one: a check name is a claim about what a gate is called and only the pairs
# carry it, while a value substituted into the prompt is owed for every `needs.<id>.result` in the
# text, including one no pair names.
NEEDS_EXPRESSION = re.compile(r"\$\{\{\s*needs\.([A-Za-z0-9_.-]+)\.result\s*\}\}")

# The publication's own header and its own sentence, read exactly as `publish_verdict.py` writes
# them. The marker carries the commit the verdict covers, which is the whole of why it can be
# matched to a run at all: a verdict whose marker names another commit describes another tree.
VERDICT_MARKER = re.compile(
    r"<!-- exeris-bot: l2-verdict agent=([^\s]+) decision=([A-Z]+)(?: sha=([0-9a-f]{7,40}))? -->")
VERDICT_SOURCE = re.compile(r"Verdict read from the (file|execution log|fenced block|none)\.")

# The publishing step's vocabulary, with its spaces written as hyphens. That substitution is the
# whole of the mapping, and it is a table rather than a `replace` so that a fifth transport added
# upstream fails here instead of arriving as an enum value the contract does not admit.
VERDICT_ROUTES = {
    "file": "file",
    "execution log": "execution-log",
    "fenced block": "fenced-block",
    "none": "none",
}

# `result.usage` in the runtime's spelling, mapped to the contract's. The two cache counts are
# renamed rather than copied: `cache_creation_input_tokens` is what was WRITTEN into the cache, and
# a column named for creation beside one named for reading invites the two to be added together.
USAGE_FIELDS = (
    ("input_tokens", "input_tokens"),
    ("output_tokens", "output_tokens"),
    ("cache_read_input_tokens", "cache_read_tokens"),
    ("cache_creation_input_tokens", "cache_write_tokens"),
)

# The fenced `json` block a runner's own text carries, as `publish_verdict.py` finds it.
FENCE = re.compile(r"```json\s*\n(.*?)\n```", re.S)


class UnsupportedTemplate(Exception):
    """The prompt template holds something this reconstruction cannot render.

    Raised rather than worked around, because every way around it produces a hash of a prompt that
    was never sent: dropping an unknown expression, rendering it as its own text, or guessing its
    value all yield sixty-four hexadecimal digits that look exactly like a measurement.
    """


class PublisherAsAgent(Exception):
    """A row names one of the organisation's writing identities in `agent.*`.

    `agent.*` is the model reference — who was asked. An App the organisation installs is who acted,
    and `execution.principal` is where that belongs. A row that confuses the two puts an identity
    into the column a comparison across models groups by, and the comparison then reads as a
    model's result (ADR-087 §A.4).
    """


def names_a_publisher(value: object, publishers: tuple[str, ...] = PUBLISHERS) -> bool:
    """Whether a value names one of the organisation's writing identities.

    Compared case-insensitively and with the `[bot]` suffix the host appends taken off first, so
    that one name in three spellings is one name rather than one refusal and two ways past it.
    """
    if not isinstance(value, str):
        return False
    name = value.strip().lower()
    if name.endswith("[bot]"):
        name = name[:-len("[bot]")]
    return name in publishers


def refuse_publisher(row: dict, publishers: tuple[str, ...] = PUBLISHERS) -> None:
    """Raise `PublisherAsAgent` where `agent.*` names a publisher; return None otherwise.

    It reads `agent.provider`, `agent.model_id` and `agent.harness.client` and deliberately not
    `execution.principal.login`: the principal is the one field where such a name is the correct
    answer, so a check that refused it there would refuse the row for saying the true thing.
    """
    agent = row.get("agent") if isinstance(row.get("agent"), dict) else {}
    harness = agent.get("harness") if isinstance(agent.get("harness"), dict) else {}
    for field, value in (("provider", agent.get("provider")),
                         ("model_id", agent.get("model_id")),
                         ("harness.client", harness.get("client"))):
        if names_a_publisher(value, publishers):
            raise PublisherAsAgent(
                f"`agent.{field}` is `{value}` — the bot is the pen, never the agent; the identity "
                f"a run acted under belongs in `execution.principal`")


# --------------------------------------------------------------------------------------------
# The stream
# --------------------------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class StreamFacts:
    """Everything one row takes from the run's own event record, and nothing else.

    `model` is what the client announced at startup and `acted_models` is who actually took a turn.
    They are two questions, and a row that answers the first with the second names a model the
    harness consulted for its own purposes as the model that reviewed the pull request.
    """

    event_count: int
    model: str | None
    acted_models: list[str]
    harness_version: str | None
    permission_mode: str | None
    api_key_source: str | None
    has_result: bool
    result_subtype: str | None
    turns: int | None
    wall_time_ms: int | None
    tool_calls: int
    usage: dict | None
    permission_denials: int | None
    permission_denied_events: int
    human_prompts: int
    verdicts: list[dict]


def stream_facts(events: list) -> StreamFacts:
    """The facts a run record takes from a Claude Code event stream.

    The model that acted is read from the `assistant` events in the order they took their turns,
    which is the only place the stream says who did the work — the copy is named in the commit that
    introduces it rather than here, because a comment naming where code came from decays the moment
    the source moves. The counts are counted rather than reported: `tool_calls` is the number of
    `tool_use` blocks, not a figure the runtime prints.

    `total_cost_usd` and `modelUsage` are not read. See the module docstring.
    """
    model = harness_version = permission_mode = api_key_source = None
    acted: list[str] = []
    tool_calls = 0
    human_prompts = 0
    permission_denied_events = 0
    result: dict = {}
    has_result = False
    texts: list[str] = []

    for event in events:
        if not isinstance(event, dict):
            continue
        kind, subtype = event.get("type"), event.get("subtype")
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        content = message.get("content")
        blocks = [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []
        if kind == "system" and subtype == "init":
            model = event.get("model") or None
            harness_version = event.get("claude_code_version") or None
            permission_mode = event.get("permissionMode") or None
            api_key_source = event.get("apiKeySource")
        elif kind == "system" and subtype == "permission_denied":
            permission_denied_events += 1
        elif kind == "assistant":
            spoke = message.get("model")
            if spoke and spoke not in acted:
                acted.append(spoke)
            tool_calls += sum(1 for b in blocks if b.get("type") == "tool_use")
        elif kind == "user":
            # A prompt a person submitted, and not a tool result the client fed back. The stream
            # spells a tool result as a `user` event too, so the test is the block kinds rather than
            # the event kind: text and no tool result is someone typing, which is what steering is.
            kinds = {b.get("type") for b in blocks} if blocks else {"text"} if content else set()
            if "text" in kinds and "tool_result" not in kinds:
                human_prompts += 1
        elif kind == "result":
            has_result = True
            result = event
        for block in blocks:
            if block.get("type") == "text" and block.get("text"):
                texts.append(block["text"])
        if kind == "result" and isinstance(event.get("result"), str):
            texts.append(event["result"])

    denials = result.get("permission_denials")
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else None
    return StreamFacts(
        event_count=len(events),
        model=model,
        acted_models=acted,
        harness_version=harness_version,
        permission_mode=permission_mode,
        api_key_source=api_key_source,
        has_result=has_result,
        result_subtype=result.get("subtype"),
        turns=result.get("num_turns") if isinstance(result.get("num_turns"), int) else None,
        wall_time_ms=result.get("duration_ms") if isinstance(result.get("duration_ms"), int)
        else None,
        tool_calls=tool_calls,
        usage=usage_counts(usage),
        permission_denials=len(denials) if isinstance(denials, list) else None,
        permission_denied_events=permission_denied_events,
        human_prompts=human_prompts,
        verdicts=fenced_verdicts(texts),
    )


def usage_counts(usage: dict | None) -> dict | None:
    """`accounting.usage` from the runtime's own counts, and nothing beside them.

    Only the four the contract admits, and each only where the runtime reported an integer: a count
    the runtime did not report is absent rather than zero, because zero tokens and no measurement
    are the same value in a column that cannot tell them apart.
    """
    if not isinstance(usage, dict):
        return None
    out = {}
    for theirs, ours in USAGE_FIELDS:
        value = usage.get(theirs)
        if isinstance(value, int) and not isinstance(value, bool):
            out[ours] = value
    return out or None


def fenced_verdicts(texts: list[str]) -> list[dict]:
    """The fenced `json` verdicts in what the runner itself said, oldest first."""
    found = []
    for block in FENCE.findall("\n".join(texts)):
        try:
            doc = json.loads(block)
        except json.JSONDecodeError:
            continue
        if isinstance(doc, dict) and "agent" in doc and "decision" in doc:
            found.append(doc)
    return found


def stream_digest(data: bytes) -> str:
    """SHA-256 over a stream file's bytes, recomputed rather than trusted.

    The index states a digest; this recomputes it. `execution.event_stream.sha256` is what keeps the
    reference verifiable once the artefact is gone, so a row that copies a digest instead of
    computing one verifies the index against itself.
    """
    return hashlib.sha256(data).hexdigest()


def accounting_mode(api_key_source: object) -> str | None:
    """`subscription`, or `None` where the credential class is not established.

    The produce job hands the runner an OAuth token, so `none` — the runtime's word for "no API key
    was used" — is the subscription case. Any other value names a key, which is the `api` ledger and
    a per-run price this producer cannot see; an absent value names nothing at all. Both yield no
    row, because a run on the wrong ledger corrupts every later cost conclusion undetectably and a
    guess between two ledgers is exactly the convenient value §C.14 forbids.
    """
    return "subscription" if api_key_source == "none" else None


# --------------------------------------------------------------------------------------------
# The workflow text
# --------------------------------------------------------------------------------------------


def _block_body(text: str, key: str, styles: str) -> tuple[list[str], str] | None:
    """`<key>:`'s block-scalar lines as written, with its chomping indicator, or None where none.

    The body is taken by indentation, which is what a block scalar's extent is: every line more
    indented than the key, up to the first that is not. `styles` is the opener this accepts, so that
    a reader asking for a folded block is not handed a literal one — the two have different values
    for the same lines, and a reader that could not tell would return one under the other's name.
    """
    lines = text.splitlines()
    opener = re.compile(rf"^(\s*){re.escape(key)}:\s*([{styles}])([-+]?)\s*$")
    for index, line in enumerate(lines):
        found = opener.match(line)
        if not found:
            continue
        parent = len(found.group(1))
        body: list[str] = []
        for candidate in lines[index + 1:]:
            if candidate.strip() and len(candidate) - len(candidate.lstrip()) <= parent:
                break
            body.append(candidate)
        return body, found.group(3)
    return None


def block_scalar(text: str, key: str) -> str | None:
    """The raw body of `<key>:`'s block scalar, dedented to column zero, or None where none.

    It does not fold, and it is read by the two places whose value is its lines as they stand: the
    `prompt: |` block the runner was handed, and the `claude_args: |` block the allow-list is split
    out of. A `>` block's value is not its lines — `folded_scalar` is what states that one — and the
    two are kept apart because a folded value read literally is a different string from the one the
    host built, and every hash over it is a hash of a prompt nobody sent.

    Trailing blank lines are dropped and the body ends in exactly one newline, which is what both
    styles do under the default clip chomping these workflows use.
    """
    found = _block_body(text, key, "|>")
    if found is None:
        return None
    body = list(found[0])
    while body and not body[-1].strip():
        body.pop()
    if not body:
        return ""
    indent = min(len(b) - len(b.lstrip()) for b in body if b.strip())
    return "\n".join(b[indent:] if b.strip() else "" for b in body) + "\n"


def folded_scalar(text: str, key: str) -> str | None:
    """The VALUE of `<key>:`'s folded block scalar — `>`, `>-` or `>+` — or None where none.

    Folding is not a convenience of layout: it decides the string, so a reconstruction that skipped
    it would restate a caller's input as something the host never built. YAML's rule has three parts
    and all three are load-bearing here:

      * a line break between two lines at the block's own indentation becomes one space;
      * a line break beside a MORE-indented line stays a line break, so a continuation the author
        aligned under an opening brace keeps the newline the author put there;
      * a run of blank lines loses the one break folding would have eaten and keeps the rest.

    The chomping indicator says what becomes of the end: `-` strips every trailing line break, `+`
    keeps them all, and the default keeps exactly one.

    None where the key carries no folded block at all — a literal `|` block or a plain scalar is
    somebody else's reading, and returning one of those from here would say the host folded
    something it did not.
    """
    found = _block_body(text, key, ">")
    if found is None:
        return None
    body, chomp = found
    trailing = 0
    while body and not body[-1].strip():
        body.pop()
        trailing += 1
    if not body:
        return "" if chomp == "-" else "\n" * (trailing if chomp == "+" else 0)
    indent = min(len(b) - len(b.lstrip()) for b in body if b.strip())
    lines = [b[indent:] if b.strip() else "" for b in body]

    # Each content line with the number of blank lines standing before it, because folding is a
    # rule about what separates two lines and not about either line on its own.
    segments: list[tuple[int, str]] = []
    blanks = 0
    for line in lines:
        if line == "":
            blanks += 1
            continue
        segments.append((blanks, line))
        blanks = 0

    out: list[str] = []
    for position, (before, line) in enumerate(segments):
        if position == 0:
            out.append("\n" * before)
        else:
            previous = segments[position - 1][1]
            if previous.startswith((" ", "\t")) or line.startswith((" ", "\t")):
                out.append("\n" * (before + 1))
            elif before == 0:
                out.append(" ")
            else:
                out.append("\n" * before)
        out.append(line)
    value = "".join(out)
    if chomp == "-":
        return value
    if chomp == "+":
        return value + "\n" + "\n" * trailing
    return value + "\n"


def expression_key(text: str) -> str:
    """One `${{ … }}` expression in the one spelling this module compares by.

    Whitespace inside an expression is GitHub's to ignore, so it is ignored here too: a template
    that puts its expression on two lines asks for the same substitution as one that does not, and
    a lookup that could not tell would refuse a template over its own line breaks.
    """
    inner = text.strip()
    if inner.startswith("${{") and inner.endswith("}}"):
        inner = inner[3:-2]
    return "${{ " + " ".join(inner.split()) + " }}"


def render_prompt(template: str, subs: dict) -> str:
    """The prompt text the workflow handed the runner, rendered from the template and the inputs.

    The block is taken by indentation and each expression is replaced by the value `subs` gives for
    it, keyed by the expression itself. An expression outside the closed set raises
    `UnsupportedTemplate` and costs the row: a template that substitutes something this cannot
    supply produced a prompt this cannot restate, and a hash over a restatement that is not the
    prompt is worse than no row, because it is indistinguishable from one that is.

    A supported expression the caller supplied no value for raises `KeyError`, which is a defect in
    the caller and not a fact about the template — the two are kept apart so that one does not
    arrive counted as the other.
    """
    body = block_scalar(template, "prompt")
    if body is None:
        raise UnsupportedTemplate("the workflow carries no `prompt:` block scalar")
    supplied = {expression_key(key): value for key, value in subs.items()}
    supported = {expression_key(one) for one in PROMPT_SUBSTITUTIONS}

    def one(match: re.Match) -> str:
        key = expression_key(match.group(0))
        if key not in supported:
            raise UnsupportedTemplate(f"unsupported template expression `{key}`")
        return str(supplied[key])

    return EXPRESSION.sub(one, body)


def allow_list_tokens(claude_args: str | None) -> list[str] | None:
    """The tools the client was launched with, or None where the launch passed no allow-list.

    Split at bracket depth zero, because a token may carry a comma inside its own parentheses and a
    naive split would report one permission as two — `Bash(git log --format=a,b:*)` is one rule, and
    two halves of it are two rules the run never had.

    None and `[]` are different states and stay different: no `--allowedTools` is a run under the
    client's own defaults, and an empty one is a run permitted nothing. `tool_surface` records
    which, because a hash that read them alike would compare two runs whose powers were not the
    same.
    """
    if claude_args is None:
        return None
    try:
        tokens = shlex.split(claude_args)
    except ValueError:
        return None
    value = None
    for index, token in enumerate(tokens):
        if token == "--allowedTools" and index + 1 < len(tokens):
            value = tokens[index + 1]
        elif token.startswith("--allowedTools="):
            value = token[len("--allowedTools="):]
    if value is None:
        return None
    out, depth, current = [], 0, ""
    for character in value:
        if character in "([":
            depth += 1
        elif character in ")]":
            depth = max(0, depth - 1)
        if character == "," and depth == 0:
            out.append(current)
            current = ""
        else:
            current += character
    out.append(current)
    return [part.strip() for part in out if part.strip()]


# The tools that read and nothing else, and the shell commands that do. Neither list is a
# convenience: `execution.result_commits` is a claim about what a run produced, and the only
# evidence a stream-derived row has for the empty list is that the run held no tool that could
# have written anything. A name absent from both lists is treated as a tool that can write,
# because the cost of guessing wrong is a measurement nobody made standing on a row.
READ_ONLY_TOOLS = frozenset({"Read", "Grep", "Glob", "LS", "NotebookRead", "WebFetch",
                             "WebSearch"})
READ_ONLY_BASH = frozenset({"git diff", "git log", "git show", "git status", "git blame",
                            "git rev-parse", "git ls-files", "git cat-file", "cat", "ls",
                            "head", "tail", "wc", "grep", "find"})


def writes_nothing(allow: list[str] | None) -> bool | None:
    """Whether the permitted surface carries no tool that could write to the checkout.

    `None` where the launch passed no allow-list. A run under the client's own defaults has a
    surface this table cannot read, and a row that stated "no commits" from it would be stating a
    measurement nobody took — which is the shape ADR-087 §C.14 refuses. `True` is the one state
    that licenses an empty `execution.result_commits`.

    A `Bash` token with no command, or with a command outside the read-only list, is a token that
    may commit whatever it in fact ran: what the row records is what the run was PERMITTED.
    """
    if allow is None:
        return None
    for token in allow:
        name, bracket, argument = token.partition("(")
        name = name.strip()
        if not bracket:
            if name not in READ_ONLY_TOOLS:
                return False
            continue
        command = argument.rstrip().rstrip(")").strip()
        if name != "Bash":
            if name not in READ_ONLY_TOOLS:
                return False
            continue
        if command.endswith(":*"):
            command = command[:-2].strip()
        if command not in READ_ONLY_BASH:
            return False
    return True


def tool_surface(allow: list[str] | None, perms: dict | None) -> str:
    """`execution.tool_surface` — canonicalisation v1, documented here because it is the producer's.

    The contract leaves the canonicalisation to the producer and requires the producer to state it,
    so that the hash compares only across producers that canonicalise alike. Version 1 is SHA-256
    over this text, UTF-8:

        json.dumps({"v": 1, "allow": A, "checkout_permissions": P},
                   sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"

    `A` is the allow-list the client was launched with — stripped, deduplicated and sorted — or
    `null` where the launch passed none. `P` is the permission rules the checkout declares, as the
    client's own settings spell them, or `null` where it declares none.

    Three properties it is built for. An absent allow-list and an absent permission rule set are
    RECORDED STATES inside the text — `null`, written — never an absent field: a run under no
    allow-list is a run whose powers are known, and no digest would have meant "unrestricted". The
    allow-list is sorted because the surface is what the run was permitted and not the order
    somebody typed it, so two spellings of one surface hash alike. And the client's own tool
    manifest is outside the hash entirely: it says what the client offers, which is not what this
    run was permitted.

    One distinction it deliberately does not make. A checkout with no settings file and a checkout
    whose settings declare no permission rules are both `null` here, because the run was permitted
    the same things under either, and this hash is over what the run was permitted rather than over
    what the repository happens to contain.

    `"v": 1` is inside the hashed text so that a change to this canonicalisation is legible in the
    hash's own input and not only in the fence that must accompany it.
    """
    tokens = None if allow is None else sorted(set(part for part in allow if part))
    rules = perms.get("permissions") if isinstance(perms, dict) else None
    payload = {"v": 1, "allow": tokens, "checkout_permissions": rules}
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def system_prompt_sha256(prompt: str, routine: str, agents_md: str) -> str:
    """`agent.system_prompt_sha256` — the instructions the repository controlled, in §C.14's order.

    Three components, each with one trailing newline stripped and exactly one appended, concatenated
    as prompt, routine, agent file. The normalisation is what makes the hash a function of the text
    rather than of how a host happened to terminate it, and the order is fixed because a hash over a
    set would be the same hash for two different arrangements of the same instructions.

    An empty third component is the documented state of a repository with no `AGENTS.md`: it
    contributes exactly one newline, the same value every time, so two such repositories agree and
    neither is confused with a repository whose agent file is an empty file.
    """
    parts = [(part[:-1] if part.endswith("\n") else part) + "\n"
             for part in (prompt, routine, agents_md)]
    return hashlib.sha256("".join(parts).encode("utf-8")).hexdigest()


def checks_run_json(l1: dict | None) -> str:
    """The L1 gate results in the verdict's vocabulary, as the workflow's `jq -c` writes them.

    The mapping is three cases and CI's to know: `success` is a pass, `failure` is a fail, and
    everything else — cancelled, skipped, an absent result — means the gate reported nothing, which
    is what `not-run` is for. The output is compact and in the caller's own key order, because this
    text goes into the prompt whose hash a row carries and a difference of one space is a different
    hash.
    """
    if not l1:
        return "[]"
    entries = [{"check": name,
                "result": "pass" if value == "success"
                else "fail" if value == "failure" else "not-run"}
               for name, value in l1.items()]
    return json.dumps(entries, separators=(",", ":"), ensure_ascii=False)


def l1_mapping(l1_results: str | None) -> dict:
    """The caller's `l1-results` expression, as check name → the job its result comes from.

    The caller states which of its own jobs stands behind each check name, so the reconstruction
    reads that statement rather than assuming a naming convention. A caller that passes no
    `l1-results` yields an empty mapping, which is the state the workflow's own default describes
    and which renders as `[]` — not a missing value.
    """
    if not l1_results:
        return {}
    return {check: job for check, job in NEEDS_RESULT.findall(l1_results)}


def caller_inputs(caller_yaml: str, workflow: str = "docs-review.yml") -> dict | None:
    """The `with:` mapping the caller hands the review workflow, values as their raw text.

    `None` where this file calls that workflow nowhere, and an empty mapping where it calls it and
    passes nothing. The two are different answers and the difference is load-bearing: the first says
    the file read is not the caller, which costs the row, and the second says the caller took every
    default, which renders a prompt like any other.

    Read by indentation rather than by a YAML parser, because this module imports nothing outside
    the standard library and the shape being read is two levels deep and fixed by the workflow it
    calls. A block scalar's value is its raw dedented body: the two inputs read from here are a
    plain filename and a command list whose emptiness is the only thing the prompt depends on.
    """
    lines = caller_yaml.splitlines()
    # A reusable workflow is referenced locally as `./…/docs-review.yml`, from another repository
    # as `owner/repo/…/docs-review.yml@<ref>`, and — where the reference is pinned to a commit —
    # with a trailing comment naming the branch that commit was on. All three are the same call,
    # so both the ref suffix and the comment are optional here: a pattern that admitted one
    # spelling would read a caller written in another as a file that calls nothing, and a file
    # that calls nothing costs the row.
    uses = re.compile(rf"^(\s*)uses:\s*\S*{re.escape(workflow)}(@\S+)?\s*(#.*)?$")
    calls = False
    for index, line in enumerate(lines):
        found = uses.match(line)
        if not found:
            continue
        calls = True
        depth = len(found.group(1))
        start = None
        for offset in range(index + 1, len(lines)):
            candidate = lines[offset]
            if not candidate.strip() or candidate.lstrip().startswith("#"):
                continue
            indent = len(candidate) - len(candidate.lstrip())
            if indent < depth:
                break
            if indent == depth and re.match(r"^\s*with:\s*$", candidate):
                start = offset + 1
                break
            if indent < depth:
                break
        if start is None:
            continue
        out: dict = {}
        body = lines[start:]
        entry_indent = None
        for offset, candidate in enumerate(body):
            if not candidate.strip() or candidate.lstrip().startswith("#"):
                continue
            indent = len(candidate) - len(candidate.lstrip())
            if entry_indent is None:
                entry_indent = indent
            if indent < entry_indent:
                break
            if indent > entry_indent:
                continue
            pair = re.match(r"^\s*([A-Za-z0-9_.-]+):\s*(.*?)\s*$", candidate)
            if not pair:
                continue
            key, value = pair.group(1), pair.group(2)
            if re.fullmatch(r"[|>][-+]?", value):
                # A block's value is its style's: a `|` block is its lines, a `>` block is those
                # lines folded. An input read under the wrong one is a different string from the
                # one the called workflow received, and this mapping is where the prompt's inputs
                # come from.
                held = "\n".join(body[offset:])
                out[key] = (folded_scalar(held, key) if value.startswith(">")
                            else block_scalar(held, key)) or ""
            else:
                out[key] = value.strip("\"'")
        return out
    return {} if calls else None


def l1_results_input(caller_yaml: str, conclusions: dict,
                     workflow: str = "docs-review.yml") -> str | None:
    """The value `inputs.l1-results` carried into the review, or None where it is not established.

    Two readings compose here, in the host's own order. YAML settles the scalar's text first — the
    caller writes its gate table as a folded block, and folding is what decides which of its line
    breaks survive — and only then does the expression language replace each
    `${{ needs.<id>.result }}` with what that job concluded. Folding after substituting would be a
    different string wherever a conclusion changed a line's length, and not folding at all is a
    different string always.

    A caller that passes the input nowhere gets the empty text the called workflow declares as its
    default, which is a value and not a gap. A caller that names a job the run reports no conclusion
    for gets None: the text is then unrecoverable, and §C.14's rule is that the row is given up
    rather than rendered around the hole.
    """
    inputs = caller_inputs(caller_yaml, workflow) or {}
    text = inputs.get("l1-results")
    if text is None:
        return ""
    missing: list[str] = []

    def one(match: re.Match) -> str:
        value = conclusions.get(match.group(1))
        if value is None:
            missing.append(match.group(1))
            return ""
        return str(value)

    rendered = NEEDS_EXPRESSION.sub(one, text)
    return None if missing else rendered


# --------------------------------------------------------------------------------------------
# The REST facts
# --------------------------------------------------------------------------------------------


def pr_number(artifact_name: str) -> int | None:
    """The pull request an execution artefact belongs to, from its name.

    The run's own `pull_requests[]` comes back empty for a run a reusable workflow started, so the
    artefact's name is where the number is. A name that does not carry one yields no row: a
    fingerprint over a pull request nobody resolved would join this run to a task it was not.
    """
    found = ARTIFACT_NAME.match(str(artifact_name or ""))
    return int(found.group(1)) if found else None


def run_id(workflow_run_id: object, artifact_id: object) -> str:
    """`ci-<workflow run>-<artefact>` — the run and the stream that records it, both named.

    The workflow run alone would not be unique: one run uploads one execution artefact today and
    nothing in the workflow prevents a second, and two rows named alike are one row lost.
    """
    return f"ci-{workflow_run_id}-{artifact_id}"


def fingerprint_ci(repo: str, pr: object, head_sha: str) -> str:
    """The `ci:` class of `workload.fingerprint` — a plain SHA-256 over the three inputs.

    No key. A MAC would guard what the row prints beside it — `repository_state` carries the
    repository and the commit in clear, and the pull request number is in the same row's reach —
    while its rotation would hand every task a new identity and write a fence for nothing. The
    argument rests on `repository_state.repository` staying required (ADR-087 §C.14); if that ever
    goes, the key comes back.
    """
    return "ci:" + hashlib.sha256(f"{repo}\n{pr}\n{head_sha}\n".encode("utf-8")).hexdigest()


def head_belongs_to(run_json: dict | None, pull_json: dict | None) -> bool:
    """Whether a run whose head the pull request does not list is that pull request's all the same.

    A branch rebased or force-pushed after a review leaves the reviewed commit off the pull
    request's commit list while the pull request goes on being the same pull request on the same
    branch. The run's `head_sha` is the identity of the tree that was reviewed and is what the
    fingerprint hashes, so the rewrite does not unmake the measurement — but the row is owed
    evidence that the run belongs where it is about to be filed, and the evidence is the branch.

    Both halves, or neither. A ref name is unique only inside one repository, so a fork that
    happens to carry the same branch name would otherwise file its runs against the upstream pull
    request, and every comparison would group them there.
    """
    if not isinstance(run_json, dict) or not isinstance(pull_json, dict):
        return False
    head = pull_json.get("head")
    if not isinstance(head, dict):
        return False
    branch = str(run_json.get("head_branch") or "")
    where = str((run_json.get("head_repository") or {}).get("full_name") or "")
    return bool(branch) and bool(where) \
        and branch == str(head.get("ref") or "") \
        and where == str((head.get("repo") or {}).get("full_name") or "")


def scope_from_body(body: str | None) -> str | None:
    """`workload.scope` from the pull request's *Scope class* line, by §C.14's fixed table.

    The line is read the way `pr_body_check.py` reads it, so that a body that gate accepts is a body
    this parses and the two never disagree about what a pull request declared. A placeholder still
    in angle brackets, a class outside the table and an absent line are all "not established", which
    costs the row — the gate has already made that state red on the pull request itself.
    """
    if not body:
        return None
    text = re.sub(r"<!--.*?-->", "", body, flags=re.S)
    found = re.search(r"^Scope class:\s*(.+?)\s*$", text, re.M)
    if not found:
        return None
    value = found.group(1).strip()
    if value.startswith("<") and value.endswith(">"):
        return None
    return SCOPE_CLASSES.get(value)


def bundle_version(manifest: str | None, vendor_dir: list[str] | None) -> str | None:
    """`repository_state.bundle_version` — the pinned `exeris-agents` version in the checkout.

    The manifest's pin is the answer where there is one: it is the version the repository DECLARES
    it is on. A vendored tree with no manifest still names its version in the directory it was
    unpacked into, and that is the second answer, not a preferred one — a directory name is what the
    tree was called, while a pin is what the repository committed to. Neither present is no row: the
    bundle carries the rules the run was subject to, so a row without it does not say what the run
    was subject to.
    """
    if manifest:
        block = re.search(r"^\s*-\s*bundle:\s*exeris-agents\s*$(.*?)(?=^\s*-\s|\Z)",
                          manifest, re.M | re.S)
        if block:
            pin = re.search(r"^\s*version:\s*\"?([0-9]+\.[0-9]+\.[0-9]+)\"?\s*$",
                            block.group(1), re.M)
            if pin:
                return pin.group(1)
    for name in sorted(vendor_dir or []):
        found = re.fullmatch(r"exeris-agents-([0-9]+\.[0-9]+\.[0-9]+)", name)
        if found:
            return found.group(1)
    return None


def visibility(repo_json: dict | None) -> str:
    """`repository_state.visibility` in ADR-020's taxonomy, fail-closed.

    Only a repository the host calls public and does not call private becomes `public`. Everything
    else — internal, private, and a visibility this producer could not establish at all — becomes
    `enterprise-private`, and the rule runs one way only: a row already written does not come back
    when a repository is opened afterwards.
    """
    if not isinstance(repo_json, dict):
        return "enterprise-private"
    if repo_json.get("private") is True:
        return "enterprise-private"
    return "public" if str(repo_json.get("visibility", "")).strip().lower() == "public" \
        else "enterprise-private"


def referenced_workflow(run_json: dict | None, basename: str) -> dict | None:
    """The reusable workflow this run resolved, as `{repository, path, sha}`.

    The run records the exact commit each referenced workflow was resolved at, which is the only
    surviving statement of which text the runner was handed — the produce job's own checkout of the
    routine repository is unpinned and leaves no record of its own.
    """
    for entry in (run_json or {}).get("referenced_workflows") or []:
        if not isinstance(entry, dict):
            continue
        location = str(entry.get("path") or "").split("@")[0]
        if not location.endswith("/" + basename):
            continue
        parts = location.split("/")
        if len(parts) < 3:
            continue
        return {"repository": "/".join(parts[:2]), "path": "/".join(parts[2:]),
                "sha": entry.get("sha"), "ref": entry.get("ref")}
    return None


def review_started_at(jobs_json: dict | None, run_json: dict | None,
                      step_name: str) -> tuple[str | None, bool]:
    """`started_at`, and whether it is the coarse one.

    The reviewing step's own start is what the row wants: it is when the run began, and a fence is
    dated, so a row dated by the workflow's start is dated by something else that happened that day.
    The run's `run_started_at` is the fallback and it is marked in the log, never in the row — a
    field that said "approximately" would need a reader to know which rows carry it, and the log is
    where that belongs.
    """
    for job in (jobs_json or {}).get("jobs") or []:
        if not isinstance(job, dict):
            continue
        for step in job.get("steps") or []:
            if isinstance(step, dict) and step.get("name") == step_name and step.get("started_at"):
                return str(step["started_at"]), False
    coarse = (run_json or {}).get("run_started_at")
    return (str(coarse), True) if coarse else (None, True)


def job_conclusions(jobs_json: dict | None, ids: list[str]) -> dict:
    """What each named job concluded, as the caller's `needs.<id>.result` saw it.

    A job that calls a reusable workflow appears in the run as one row per job of the called
    workflow, named `<id> / <inner job>`, and `needs` sees the aggregate. The aggregation is
    GitHub's: any failure makes the call a failure, any cancellation a cancellation, all-skipped a
    skip, and anything else a success. A job that concluded nothing, or that is not in the run at
    all, yields None — which costs the row, because the prompt's own text depends on it.
    """
    jobs = [j for j in (jobs_json or {}).get("jobs") or [] if isinstance(j, dict)]
    out: dict = {}
    for name in ids:
        mine = [j for j in jobs
                if str(j.get("name", "")) == name
                or str(j.get("name", "")).startswith(name + " / ")]
        results = [j.get("conclusion") for j in mine]
        if not mine or any(r is None for r in results):
            out[name] = None
        elif any(r in ("failure", "timed_out") for r in results):
            out[name] = "failure"
        elif any(r == "cancelled" for r in results):
            out[name] = "cancelled"
        elif all(r == "skipped" for r in results):
            out[name] = "skipped"
        else:
            out[name] = "success"
    return out


def verdict_route(comments: list | None, head_sha: str) -> str | None:
    """`execution.verdict_route` — which transport carried this run's verdict, or None.

    Read from the publication's own comment and only where its marker names THIS commit: the
    publisher edits one comment in place, so the comment on a pull request describes its latest
    head, and a verdict whose marker names another commit describes another tree. Absent is not
    `none`: `none` is the publisher saying it looked along every transport and found nothing, and
    this returns it only when the publisher said it.
    """
    latest = None
    for comment in sorted(comments or [], key=lambda c: str((c or {}).get("created_at", ""))):
        if not isinstance(comment, dict):
            continue
        body = str(comment.get("body") or "")
        found = VERDICT_MARKER.match(body)
        if not found or (found.group(3) or "") != head_sha:
            continue
        source = VERDICT_SOURCE.search(body)
        if source:
            latest = VERDICT_ROUTES.get(source.group(1))
    return latest


def event_stream_ref(streams_repo: str, path: str, commit: str) -> str:
    """`execution.event_stream.ref` — the stream, named at the commit that holds it.

    A reference to a branch would move under the row; a reference to a commit is what the digest
    beside it verifies.
    """
    return f"{streams_repo}/{path}@{commit}"


def fence_id(date: str, producer: str, client_version: str | None) -> str:
    """A fence id in `docs/fences.md`'s grammar: `<date>-<producer>-cc-<version, dots as dashes>`.

    The dots become dashes because the contract's pattern for this field admits none, and a version
    spelled the way its vendor spells it would be refused. One client version is one fence: the
    client is part of the model reference, so a change to it is a change to the run's conditions.
    """
    if not client_version:
        return f"{date}-{producer}"
    return f"{date}-{producer}-cc-{client_version.replace('.', '-')}"


# --------------------------------------------------------------------------------------------
# The row
# --------------------------------------------------------------------------------------------


def assemble(*, run_id: str, started_at: str, fingerprint: str, domain: str, scope: str,
             provider: str, model_id: str, harness_client: str, harness_version: str,
             system_prompt_sha256: str, repository: str, visibility: str, commit: str,
             bundle_version: str, turns: int, tool_calls: int, wall_time_ms: int,
             event_stream_ref: str, event_stream_sha256: str, event_count: int,
             accounting_mode: str, oracle: dict, outcome: str, capture_version: str, fence: str,
             tool_surface: str | None = None, verdict_route: str | None = None,
             permission_denials: int | None = None, capture_level: str | None = None,
             human_prompts: int | None = None, result_commits: list | None = None,
             usage: dict | None = None) -> dict:
    """One run record, from values already derived, refused if it names a publisher as the agent.

    Keyword-only and flat: every required field of the contract is a parameter a caller must name,
    so a field this producer stopped deriving becomes a `TypeError` here rather than a row missing a
    key the inbox discovers later.

    `agent.model_snapshot` is derived rather than passed, as `unresolved:` over this row's own
    `model_id`. No execution log of this runner exposes a dated snapshot for the model that takes
    the turns; the marked form says so, and the bare alias is refused because one alias may name
    different weights at different times.

    Optional fields are written only where the caller established them — the default is absence,
    which is how this record distinguishes a value nobody measured from a value that was zero.
    There is no cost parameter: under a subscription no per-run price exists, and a figure computed
    from a price list is imputed rather than reported.
    """
    row = {
        "run_id": run_id,
        "started_at": started_at,
        "workload": {"fingerprint": fingerprint, "domain": domain, "scope": scope},
        "agent": {
            "provider": provider,
            "model_id": model_id,
            "model_snapshot": f"unresolved:{model_id}",
            "harness": {"client": harness_client, "version": harness_version},
            "system_prompt_sha256": system_prompt_sha256,
        },
        "repository_state": {
            "repository": repository,
            "visibility": visibility,
            "commit": commit,
            "bundle_version": bundle_version,
            "dirty": False,
        },
        "execution": {
            "turns": turns,
            "tool_calls": tool_calls,
            "wall_time_ms": wall_time_ms,
            "event_stream": {"ref": event_stream_ref, "sha256": event_stream_sha256,
                             "event_count": event_count},
        },
        "accounting": {"mode": accounting_mode},
        "oracle": oracle,
        "outcome": outcome,
        "instrument": {"capture_version": capture_version, "fence": fence},
    }
    execution = row["execution"]
    for key, value in (("tool_surface", tool_surface), ("verdict_route", verdict_route),
                       ("permission_denials", permission_denials),
                       ("capture_level", capture_level), ("human_prompts", human_prompts),
                       ("result_commits", result_commits)):
        if value is not None:
            execution[key] = value
    if usage:
        row["accounting"]["usage"] = usage
    refuse_publisher(row)
    return row


def dump(row: dict) -> str:
    """A row as the bytes that go into the inbox.

    Two-space indent, keys sorted, no ASCII escaping, one trailing newline. Fixed here and nowhere
    else because `inbox/` appends and marks and never rewrites: a row re-derived byte-identically is
    a row that needs no second version, and a formatting choice made per producer would make every
    re-derivation look like a change.
    """
    return json.dumps(row, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
