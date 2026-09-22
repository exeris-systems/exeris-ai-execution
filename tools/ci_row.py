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
# Two PAIRS of them state one fact in two workflow generations, and both members are supplied so
# that the generation a run belongs to is the template's to decide rather than this module's.
# `inputs.l1-results` is the caller's own text handed to the prompt whole, and
# `steps.gates.outputs.checks_run` is that text after a translating step was put between them.
# `inputs.repo-routine` names the extension at the caller's own path, and `repo-routine.base.md`
# names the copy the job takes from the base commit — a pull request does not get to write the
# rules it is judged by, so the prompt stopped naming the file the pull request can edit.
PROMPT_SUBSTITUTIONS = (
    "${{ github.event.pull_request.number }}",
    "${{ github.repository }}",
    "${{ steps.gates.outputs.checks_run }}",
    "${{ inputs.l1-results }}",
    "${{ inputs.repo-routine != '' && inputs.repo-routine || '(none)' }}",
    "${{ inputs.repo-routine != '' && 'repo-routine.base.md' || '(none)' }}",
    "${{ inputs.repo-checks != '' && 'repo-checks.out' || '(none)' }}",
)

EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}", re.S)
# An expression naming a workflow variable, which is the one class of expression that can be
# resolved from the workflow text alone: `env:` is written in the file, where `inputs` and `needs`
# are the run's.
#
# ASCII, here and wherever a shorthand class reads a name or a number out of one of these files: a
# variable's name, a here-document's delimiter and a version number are drawn from ASCII, and a
# class that also admitted the letters and digits of another script would match a name no host
# declares and a version no comparison could order.
ENV_EXPRESSION = re.compile(r"\$\{\{\s*env\.([A-Za-z_]\w*)\s*\}\}", re.ASCII)
# The action's `prompt:` where it names a step's output instead of carrying the text. A prompt is
# hashed as rendered, so the job renders it once into a file and hands the action the same string
# through a step output; the text then stands in that step's `run:` and no longer in the `with:`.
PROMPT_OUTPUT = re.compile(
    r"^[ \t]*prompt:[ \t]*\$\{\{\s*steps\.([A-Za-z0-9_-]+)\.outputs\.[A-Za-z0-9_-]+\s*\}\}[ \t]*$",
    re.M)
# A here-document's opening redirection, with its delimiter. The quoting is read rather than
# ignored: an unquoted delimiter lets the shell expand the body, so the file written is not the
# text standing in the workflow and no reading of the workflow is the prompt.
HEREDOC = re.compile(r"<<(-?)(['\"]?)([A-Za-z_]\w*)\2", re.ASCII)
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

# The fenced `json` block a runner's own text carries, as `publish_verdict.py` finds it: an opener
# standing at the end of its line, and a terminator opening one.
FENCE_OPENER = "```json"
FENCE_TERMINATOR = "\n```"
# The whitespace a fence's opening line may carry after the opener — every space but the line
# break that ends it, which is what makes the two disjoint.
FENCE_LINE_SPACE = " \t\r\f\v"

# A YAML list item's marker, with whatever indentation stands before it. A list item's column is
# what bounds it: the next marker at that column or further left is the next item.
LIST_MARKER = re.compile(r"^(\s*)-\s")
# The openers of the two blocks this module reads by indentation, and the key of an entry inside
# one: an `env:` mapping, the `with:` mapping a call hands a reusable workflow, and a plain key.
ENV_BLOCK = re.compile(r"^(\s*)env:\s*$")
WITH_BLOCK = re.compile(r"^\s*with:\s*$")
ENTRY_KEY = re.compile(r"[A-Za-z0-9_.-]+")
# A value that is not a value at all but the announcement of a block scalar, with its chomping
# indicator: the lines it opens are where the string is.
BLOCK_OPENER = re.compile(r"[|>][-+]?")

# The pull request's declaration, read the way `pr_body_check.py` reads it: the line's own text,
# settled in code, so that the class named on that line is the class this maps.
SCOPE_LINE = re.compile(r"^Scope class:(.*)$", re.M)
# The `.agents` manifest's `exeris-agents` entry and the pin standing inside it, and the name a
# vendored tree carries when there is no manifest to state one.
BUNDLE_ENTRY = re.compile(r"^\s*-\s*bundle:\s*exeris-agents\s*$")
VERSION_PIN = re.compile(r"^\s*version:\s*\"?(\d+\.\d+\.\d+)\"?\s*$", re.M | re.ASCII)
VENDOR_DIRECTORY = re.compile(r"exeris-agents-(\d+\.\d+\.\d+)", re.ASCII)


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


def event_message(event: dict) -> dict:
    """One event's message, or an empty mapping where it carries none of this shape."""
    message = event.get("message")
    return message if isinstance(message, dict) else {}


def content_blocks(content: object) -> list[dict]:
    """The content blocks of a message that are mappings, and none where the content is not a list.

    A block this cannot read is left out rather than guessed at: every count taken from the blocks
    is a count of what the stream stated, and a block of another shape states nothing about tools,
    text or turns.
    """
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def is_typed_prompt(content: object, blocks: list[dict]) -> bool:
    """Whether a `user` event is a prompt a person submitted, and not a tool result fed back.

    The stream spells a tool result as a `user` event too, so the test is the block kinds rather
    than the event kind: text and no tool result is someone typing, which is what steering is. A
    message whose content is not blocks at all is text, because that spelling carries nothing else.
    """
    if blocks:
        kinds = {b.get("type") for b in blocks}
    elif content:
        kinds = {"text"}
    else:
        kinds = set()
    return "text" in kinds and "tool_result" not in kinds


def event_texts(event: dict, blocks: list[dict]) -> list[str]:
    """What the runner itself said in one event: its text blocks, and a result's own text.

    Both, because a run states its verdict twice — once in the turn that reaches it and again in
    the `result` event that repeats it — and the publisher reads whichever it is handed.
    """
    said = [block["text"] for block in blocks
            if block.get("type") == "text" and block.get("text")]
    if event.get("type") == "result" and isinstance(event.get("result"), str):
        said.append(event["result"])
    return said


@dataclasses.dataclass
class StreamPass:
    """What one pass over a stream's events has seen, before `StreamFacts` states it.

    A pass and not a parser: the stream is a log of what happened in the order it happened, and
    every field here is either the last announcement of its kind or a running count of events that
    have already gone by.
    """

    model: str | None = None
    harness_version: str | None = None
    permission_mode: str | None = None
    api_key_source: str | None = None
    acted: list = dataclasses.field(default_factory=list)
    tool_calls: int = 0
    human_prompts: int = 0
    permission_denied_events: int = 0
    has_result: bool = False
    result: dict = dataclasses.field(default_factory=dict)
    texts: list = dataclasses.field(default_factory=list)

    def read(self, event: dict) -> None:
        """One event, added to what the pass has seen so far."""
        kind, subtype = event.get("type"), event.get("subtype")
        content = event_message(event).get("content")
        blocks = content_blocks(content)
        if kind == "system" and subtype == "init":
            self.announced(event)
        elif kind == "system" and subtype == "permission_denied":
            self.permission_denied_events += 1
        elif kind == "assistant":
            self.took_a_turn(event_message(event).get("model"), blocks)
        elif kind == "user" and is_typed_prompt(content, blocks):
            self.human_prompts += 1
        elif kind == "result":
            self.has_result, self.result = True, event
        self.texts += event_texts(event, blocks)

    def announced(self, event: dict) -> None:
        """What the client said of itself at startup: the model, and what it opened under."""
        self.model = event.get("model") or None
        self.harness_version = event.get("claude_code_version") or None
        self.permission_mode = event.get("permissionMode") or None
        self.api_key_source = event.get("apiKeySource")

    def took_a_turn(self, spoke: object, blocks: list[dict]) -> None:
        """Who took this turn, in the order turns were taken, and the tools the turn called."""
        if spoke and spoke not in self.acted:
            self.acted.append(spoke)
        self.tool_calls += sum(1 for block in blocks if block.get("type") == "tool_use")


def stream_facts(events: list) -> StreamFacts:
    """The facts a run record takes from a Claude Code event stream.

    The model that acted is read from the `assistant` events in the order they took their turns,
    which is the only place the stream says who did the work — the copy is named in the commit that
    introduces it rather than here, because a comment naming where code came from decays the moment
    the source moves. The counts are counted rather than reported: `tool_calls` is the number of
    `tool_use` blocks, not a figure the runtime prints.

    `total_cost_usd` and `modelUsage` are not read. See the module docstring.
    """
    seen = StreamPass()
    for event in events:
        if isinstance(event, dict):
            seen.read(event)

    result = seen.result
    denials = result.get("permission_denials")
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else None
    return StreamFacts(
        event_count=len(events),
        model=seen.model,
        acted_models=seen.acted,
        harness_version=seen.harness_version,
        permission_mode=seen.permission_mode,
        api_key_source=seen.api_key_source,
        has_result=seen.has_result,
        result_subtype=result.get("subtype"),
        turns=result.get("num_turns") if isinstance(result.get("num_turns"), int) else None,
        wall_time_ms=result.get("duration_ms") if isinstance(result.get("duration_ms"), int)
        else None,
        tool_calls=seen.tool_calls,
        usage=usage_counts(usage),
        permission_denials=len(denials) if isinstance(denials, list) else None,
        permission_denied_events=seen.permission_denied_events,
        human_prompts=seen.human_prompts,
        verdicts=fenced_verdicts(seen.texts),
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


def fenced_blocks(text: str) -> list[str]:
    """The body of every fenced `json` block in `text`, in the order they stand.

    Scanned rather than matched by one pattern, because a pattern whose body and whose terminator
    can both stand for the same characters costs time in the square of the text's length whenever a
    block never closes — and this text is a runner's own output, as long as the run made it.

    A block opens where the opener is followed by nothing but spaces to the end of its line, and it
    closes at the first line beginning with three backticks. One that never closes carries no body,
    and the scan goes on to the next opener after it.
    """
    found: list[str] = []
    position = 0
    while True:
        opened = text.find(FENCE_OPENER, position)
        if opened < 0:
            return found
        start = opened + len(FENCE_OPENER)
        while start < len(text) and text[start] in FENCE_LINE_SPACE:
            start += 1
        position = start
        if start >= len(text) or text[start] != "\n":
            continue
        closed = text.find(FENCE_TERMINATOR, start + 1)
        if closed < 0:
            continue
        found.append(text[start + 1:closed])
        position = closed + len(FENCE_TERMINATOR)


def fenced_verdicts(texts: list[str]) -> list[dict]:
    """The fenced `json` verdicts in what the runner itself said, oldest first."""
    found = []
    for block in fenced_blocks("\n".join(texts)):
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


def _block_lines(lines: list[str], index: int, column: int) -> list[str]:
    """The lines of the block opened at `index`, as written.

    A block's extent is its opener's column: every line after it that is more indented, up to the
    first that is not. A blank line is inside the block whatever its own indentation, because a
    blank line states nothing about where the block ends.
    """
    body: list[str] = []
    for candidate in lines[index + 1:]:
        if candidate.strip() and len(candidate) - len(candidate.lstrip()) <= column:
            break
        body.append(candidate)
    return body


def _dedented(body: list[str]) -> list[str]:
    """A block's lines with the block's own indentation taken off, blanks as empty lines.

    The indentation removed is the least any content line carries, which is the block's: taking off
    more would eat a continuation the author aligned, and taking off less would leave the block's
    own layout inside the value.
    """
    indent = min(len(b) - len(b.lstrip()) for b in body if b.strip())
    return [b[indent:] if b.strip() else "" for b in body]


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
        return _block_lines(lines, index, len(found.group(1))), found.group(3)
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
    return "\n".join(_dedented(body)) + "\n"


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
        return _empty_fold(chomp, trailing)
    value = _fold(_segments(_dedented(body)))
    if chomp == "-":
        return value
    if chomp == "+":
        return value + "\n" + "\n" * trailing
    return value + "\n"


def _empty_fold(chomp: str, trailing: int) -> str:
    """A folded block of nothing but blank lines, under each chomping indicator.

    `-` strips every trailing break and `+` keeps them all, so such a block is empty under the
    first and those lines under the second. The default keeps exactly one break of a value's own,
    and a value with no content line has none to keep.
    """
    if chomp == "+":
        return "\n" * trailing
    return ""


def _segments(lines: list[str]) -> list[tuple[int, str]]:
    """Each content line with the number of blank lines standing before it.

    Folding is a rule about what separates two lines and not about either line on its own, so the
    separation is what the reading is carried out over.
    """
    out: list[tuple[int, str]] = []
    blanks = 0
    for line in lines:
        if line == "":
            blanks += 1
            continue
        out.append((blanks, line))
        blanks = 0
    return out


def _break(previous: str | None, before: int, line: str) -> str:
    """What YAML's folding puts between the line before and this one.

    Nothing stands before the first line but the blank lines that opened the block. After that:
    a break survives beside a MORE-indented line, one space replaces the break between two lines at
    the block's own indentation, and a run of blank lines loses the one break folding would have
    eaten and keeps the rest.
    """
    if previous is None:
        return "\n" * before
    if previous.startswith((" ", "\t")) or line.startswith((" ", "\t")):
        return "\n" * (before + 1)
    if before == 0:
        return " "
    return "\n" * before


def _fold(segments: list[tuple[int, str]]) -> str:
    """The segments of a folded block, joined by what folding puts between them."""
    out: list[str] = []
    previous = None
    for before, line in segments:
        out.append(_break(previous, before, line))
        out.append(line)
        previous = line
    return "".join(out)


def plain_scalar(value: str) -> str | None:
    """A single-line YAML scalar's value, or None where the text is not one.

    Quotes are stripped where they surround the whole value, because they are the spelling and not
    the string. Anything opening a block, an anchor, an alias or a collection is None: those have
    values this does not compute, and returning the text as though it were the value would state a
    string the host never built.
    """
    value = value.strip()
    if not value or value[0] in "|>&*{[":
        return None
    if value[0] in "'\"" and len(value) > 1 and value[-1] == value[0]:
        return value[1:-1]
    return value


def workflow_env(text: str, name: str) -> str | None:
    """The value a workflow's `env:` blocks give NAME, or None where that is not established.

    None where no block names it, AND none where two do. `env:` is scoped — a workflow, a job and a
    step may each declare one — so which value a given step ran under is a fact about the hierarchy
    that this reading of the file does not have. One value written once is the case this answers,
    and it is the shape the review workflow's allow-list is written in for the same reason it is
    read here: two consumers spend one string, so there is one string to spell.
    """
    found: set[str] = set()
    entry = re.compile(rf"^(\s*){re.escape(name)}:\s*(.*)$")
    lines = text.splitlines()
    for index, line in enumerate(lines):
        opener = ENV_BLOCK.match(line)
        if not opener:
            continue
        parent = len(opener.group(1))
        found |= _named_values(_block_lines(lines, index, parent), entry, parent)
    return found.pop() if len(found) == 1 else None


def _named_values(body: list[str], entry: re.Pattern, column: int) -> set[str]:
    """Every plain value the entries of one block give the name `entry` matches.

    A set, because the question this answers is how many DIFFERENT values a file gives one name:
    one name written twice with one value is one value, and two values are why no value is
    established.
    """
    found: set[str] = set()
    for candidate in body:
        hit = entry.match(candidate)
        if hit and len(hit.group(1)) > column:
            value = plain_scalar(hit.group(2))
            if value is not None:
                found.add(value)
    return found


def step_block(text: str, step_id: str) -> str | None:
    """The lines of the step carrying `id: <step_id>`, as written, or None where no step does.

    A step is a list item, so its extent is its marker's column: every line more indented than the
    `- ` that opens it, up to the first that is not. It is found by id rather than by position
    because a step's place in a job is not something the workflow promises and its id is — the same
    id the expression that spends its output names.
    """
    lines = text.splitlines()
    wanted = re.compile(rf"^(\s*)id:\s*['\"]?{re.escape(step_id)}['\"]?\s*$")
    for index, line in enumerate(lines):
        found = wanted.match(line)
        if not found:
            continue
        start = _item_start(lines, index, len(found.group(1)))
        if start is None:
            continue
        column = len(LIST_MARKER.match(lines[start]).group(1))
        return "\n".join([lines[start]] + _block_lines(lines, start, column))
    return None


def _item_start(lines: list[str], index: int, column: int) -> int | None:
    """Where the list item holding the line at `index` opens, or None where no item holds it.

    The nearest marker above it that stands further left: a marker at the line's own column or
    further right opens an item of some list inside this one, and an item of an inner list is not
    the item the line belongs to.
    """
    for back in range(index, -1, -1):
        opener = LIST_MARKER.match(lines[back])
        if opener and len(opener.group(1)) < column:
            return back
    return None


def heredoc_body(script: str) -> str:
    """The text a shell script's first here-document writes, as the shell would write it.

    Two things are refused rather than worked around. An UNQUOTED delimiter lets the shell expand
    the body, so what lands in the file is not what stands in the workflow and no reading of the
    workflow is the prompt. A body whose delimiter never arrives is a script that would not run, so
    there is no text it wrote.

    The terminator is the line that IS the delimiter, which is the shell's own rule: a line
    carrying anything else, leading space included, is body. Every body line ends in a newline,
    because that is what the redirection writes.
    """
    lines = script.splitlines()
    for index, line in enumerate(lines):
        found = HEREDOC.search(line)
        if found is None:
            continue
        if found.group(1) or not found.group(2):
            raise UnsupportedTemplate(
                "the step that renders the prompt opens an unquoted here-document, whose body the "
                "shell expands before it is written")
        delimiter = found.group(3)
        body: list[str] = []
        for candidate in lines[index + 1:]:
            if candidate == delimiter:
                return "".join(part + "\n" for part in body)
            body.append(candidate)
        raise UnsupportedTemplate(
            f"the here-document `{delimiter}` the prompt is written from is never closed")
    raise UnsupportedTemplate("the step that renders the prompt writes no here-document")


def prompt_template(text: str) -> str:
    """The prompt as the workflow spells it, before substitution.

    Two spellings, because the workflow has had two. The text may stand in the action's own
    `prompt:` block scalar; or the job may render it once into a file and hand the action the same
    string through a step output, in which case `prompt:` names that step and the text stands in
    the here-document that step's `run:` writes.

    The second spelling is what lets the job that rendered the prompt export a hash over it, so a
    reader that knew only the first would answer "this workflow carries no prompt" for every run of
    the generation whose hash is checkable — and a reconstruction that yields no row for those runs
    is a reconstruction that cannot be compared with anything.
    """
    body = block_scalar(text, "prompt")
    if body is not None:
        return body
    found = PROMPT_OUTPUT.search(text)
    if found is None:
        raise UnsupportedTemplate("the workflow carries no `prompt:` block scalar")
    step = step_block(text, found.group(1))
    if step is None:
        raise UnsupportedTemplate(f"the workflow hands the runner `steps.{found.group(1)}.outputs` "
                                  f"and no step carries that id")
    script = block_scalar(step, "run")
    if script is None:
        raise UnsupportedTemplate(f"the step `{found.group(1)}` that renders the prompt carries no "
                                  f"`run:` block scalar")
    return heredoc_body(script)


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
    body = prompt_template(template)
    supplied = {expression_key(key): value for key, value in subs.items()}
    supported = {expression_key(one) for one in PROMPT_SUBSTITUTIONS}

    def one(match: re.Match) -> str:
        key = expression_key(match.group(0))
        if key not in supported:
            raise UnsupportedTemplate(f"unsupported template expression `{key}`")
        return str(supplied[key])

    return EXPRESSION.sub(one, body)


def allow_list_tokens(claude_args: str | None, workflow: str | None = None) -> list[str] | None:
    """The tools the client was launched with, or None where the launch passed no allow-list.

    Split at bracket depth zero, because a token may carry a comma inside its own parentheses and a
    naive split would report one permission as two — `Bash(git log --format=a,b:*)` is one rule, and
    two halves of it are two rules the run never had.

    None and `[]` are different states and stay different: no `--allowedTools` is a run under the
    client's own defaults, and an empty one is a run permitted nothing. `tool_surface` records
    which, because a hash that read them alike would compare two runs whose powers were not the
    same.

    THE LIST MAY BE SPENT THROUGH A VARIABLE. Two consumers read it — the launch and the digest
    that says which powers a run was compared under — so the workflow writes it once as `env:` and
    names it in both. Given the workflow text, an `${{ env.NAME }}` in the value is resolved
    against that file; without it, or where the file does not establish one value, the expression
    stands.

    AN UNRENDERED EXPRESSION COSTS THE ROW. A token still carrying `${{` names no tool, and the
    surface hashed over it would be sixty-four hexadecimal digits over an allow-list no run ever
    had — indistinguishable from a digest over one that did, which is the single outcome the
    contract forbids. The prompt half of this reconstruction already refuses on the same ground,
    and a half that returned a plausible value where the other refuses is the half nobody checks.
    """
    if claude_args is None:
        return None
    value = _allowed_tools_value(claude_args)
    if value is None:
        return None
    if workflow is not None:
        value = _resolved_against(value, workflow)
    allow = [part.strip() for part in _split_outside_brackets(value) if part.strip()]
    unrendered = [part for part in allow if "${{" in part]
    if unrendered:
        raise UnsupportedTemplate(f"the allow-list carries the unrendered expression "
                                  f"`{unrendered[0]}`, so the tools the run was permitted are not "
                                  f"established")
    return allow


def _allowed_tools_value(claude_args: str) -> str | None:
    """What a launch line gives `--allowedTools`, in either spelling, or None where it gives none.

    The LAST one wins, which is the client's own rule: a line that names the flag twice was launched
    under the second, and a reading that took the first would record a surface the run did not have.
    A line that is not a shell line at all establishes nothing, so it names no tools either.
    """
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
    return value


def _resolved_against(value: str, workflow: str) -> str:
    """`value` with every `${{ env.NAME }}` the workflow establishes replaced by what it says.

    An expression the file does not settle is left standing rather than dropped: what a caller does
    with an unresolved expression is the caller's, and a value quietly emptied of one would read as
    a value the file gave.
    """
    def resolve(match: re.Match) -> str:
        named = workflow_env(workflow, match.group(1))
        return match.group(0) if named is None else named
    return ENV_EXPRESSION.sub(resolve, value)


def _split_outside_brackets(value: str) -> list[str]:
    """`value` split at the commas standing at bracket depth zero.

    A token may carry a comma inside its own parentheses and a naive split would report one
    permission as two — `Bash(git log --format=a,b:*)` is one rule, and two halves of it are two
    rules the run never had.
    """
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
    return out


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
    return not any(_token_writes(token) for token in allow)


def _token_writes(token: str) -> bool:
    """Whether one allow-list token names a power that could write to the checkout.

    A token outside the read-only table writes, because a name neither list carries is a tool this
    reading cannot vouch for and the cost of vouching wrongly is a measurement nobody made standing
    on a row. `Bash` is the one name whose argument is read, because it is the one whose argument
    is a command.
    """
    name, bracket, argument = token.partition("(")
    name = name.strip()
    if not bracket or name != "Bash":
        return name not in READ_ONLY_TOOLS
    command = argument.rstrip().rstrip(")").strip()
    if command.endswith(":*"):
        command = command[:-2].strip()
    return command not in READ_ONLY_BASH


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
    return dict(NEEDS_RESULT.findall(l1_results))


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
        start = _with_block(lines, index, len(found.group(1)))
        if start is None:
            continue
        return _mapping_at(lines[start:])
    return {} if calls else None


def _skipped_line(line: str) -> bool:
    """Whether a line is one this reading goes past: blank, or a comment standing on its own.

    Neither states anything about indentation, so neither ends a block or opens an entry.
    """
    return not line.strip() or line.lstrip().startswith("#")


def _with_block(lines: list[str], index: int, column: int) -> int | None:
    """Where the `with:` of the call opened at `index` begins, or None where the call carries none.

    The call's own column decides which `with:` is the call's: one standing at that column belongs
    to it, one further left belongs to something the call is inside, and a line further left than
    the call has left the call altogether.
    """
    for offset in range(index + 1, len(lines)):
        candidate = lines[offset]
        if _skipped_line(candidate):
            continue
        indent = len(candidate) - len(candidate.lstrip())
        if indent < column:
            return None
        if indent == column and WITH_BLOCK.match(candidate):
            return offset + 1
    return None


def _mapping_at(body: list[str]) -> dict:
    """The one-level mapping standing at the head of `body`, values as their raw text.

    The first entry's column is the mapping's: a line further left has left it, and a line further
    in belongs to the entry above rather than being an entry of its own.
    """
    out: dict = {}
    entry_indent = None
    for offset, candidate in enumerate(body):
        if _skipped_line(candidate):
            continue
        indent = len(candidate) - len(candidate.lstrip())
        if entry_indent is None:
            entry_indent = indent
        if indent < entry_indent:
            break
        if indent > entry_indent:
            continue
        pair = _key_value(candidate)
        if pair is None:
            continue
        out[pair[0]] = _entry_value(body, offset, *pair)
    return out


def _key_value(line: str) -> tuple[str, str] | None:
    """One `key: value` entry as the two of them, or None where the line is not an entry.

    The key runs to the first colon and the value is the rest, stripped: a colon inside a value is
    the value's, and a line whose key is not a name is not an entry of a mapping at all.
    """
    key, colon, value = line.strip().partition(":")
    if not colon or not ENTRY_KEY.fullmatch(key):
        return None
    return key, value.strip()


def _entry_value(body: list[str], offset: int, key: str, value: str) -> str:
    """One entry's value: the text beside the key, or the block scalar the key opens.

    A block's value is its style's: a `|` block is its lines, a `>` block is those lines folded. An
    input read under the wrong one is a different string from the one the called workflow received,
    and this mapping is where the prompt's inputs come from.
    """
    if not BLOCK_OPENER.fullmatch(value):
        return value.strip("\"'")
    held = "\n".join(body[offset:])
    read = folded_scalar(held, key) if value.startswith(">") else block_scalar(held, key)
    return read or ""


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
    found = SCOPE_LINE.search(text)
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
    entry = _bundle_entry(manifest or "")
    if entry is not None:
        pin = VERSION_PIN.search(entry)
        if pin:
            return pin.group(1)
    for name in sorted(vendor_dir or []):
        found = VENDOR_DIRECTORY.fullmatch(name)
        if found:
            return found.group(1)
    return None


def _bundle_entry(manifest: str) -> str | None:
    """The lines of the manifest entry naming the `exeris-agents` bundle, or None where none does.

    An entry's extent is the next entry: a bundle list is a list, so what stands between one marker
    and the next belongs to the first, and a pin under a later entry pins a different bundle.
    """
    lines = manifest.splitlines()
    for index, line in enumerate(lines):
        if not BUNDLE_ENTRY.match(line):
            continue
        body: list[str] = []
        for candidate in lines[index + 1:]:
            if LIST_MARKER.match(candidate):
                break
            body.append(candidate)
        return "\n".join(body)
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


# Every field a row must be given, and every field it may be given. The two are lists rather than
# parameters so that one call shape — `assemble(**fields)` — serves each producer, and they are
# explicit rather than inferred from the contract so that a field this producer stopped deriving is
# refused by name here instead of arriving at the inbox as a key that is not there.
ASSEMBLE_FIELDS = (
    "run_id", "started_at", "fingerprint", "domain", "scope", "provider", "model_id",
    "harness_client", "harness_version", "system_prompt_sha256", "repository", "visibility",
    "commit", "bundle_version", "turns", "tool_calls", "wall_time_ms", "event_stream_ref",
    "event_stream_sha256", "event_count", "accounting_mode", "oracle", "outcome",
    "capture_version", "fence",
)
OPTIONAL_FIELDS = (
    "tool_surface", "verdict_route", "permission_denials", "capture_level", "human_prompts",
    "result_commits", "usage",
)


def assemble(**fields) -> dict:
    """One run record, from values already derived, refused if it names a publisher as the agent.

    Taken by name and flat. `ASSEMBLE_FIELDS` is what a caller must give: `run_id`, `started_at`,
    `fingerprint`, `domain`, `scope`, `provider`, `model_id`, `harness_client`, `harness_version`,
    `system_prompt_sha256`, `repository`, `visibility`, `commit`, `bundle_version`, `turns`,
    `tool_calls`, `wall_time_ms`, `event_stream_ref`, `event_stream_sha256`, `event_count`,
    `accounting_mode`, `oracle`, `outcome`, `capture_version` and `fence`. `OPTIONAL_FIELDS` is
    what it may give beside them: `tool_surface`, `verdict_route`, `permission_denials`,
    `capture_level`, `human_prompts`, `result_commits` and `usage`. A name outside the two, or a
    required one left out, raises `TypeError` naming it — so a field this producer stopped
    deriving is refused here rather than missing from a row the inbox discovers later.

    `agent.model_snapshot` is derived rather than passed, as `unresolved:` over this row's own
    `model_id`. No execution log of this runner exposes a dated snapshot for the model that takes
    the turns; the marked form says so, and the bare alias is refused because one alias may name
    different weights at different times.

    Optional fields are written only where the caller established them — the default is absence,
    which is how this record distinguishes a value nobody measured from a value that was zero.
    There is no cost field: under a subscription no per-run price exists, and a figure computed
    from a price list is imputed rather than reported.
    """
    unknown = [name for name in fields if name not in ASSEMBLE_FIELDS + OPTIONAL_FIELDS]
    if unknown:
        raise TypeError(f"`assemble` has no field `{unknown[0]}` to put a value in")
    missing = [name for name in ASSEMBLE_FIELDS if name not in fields]
    if missing:
        raise TypeError(f"`assemble` was given no `{missing[0]}`, which every row states")
    model_id = fields["model_id"]
    row = {
        "run_id": fields["run_id"],
        "started_at": fields["started_at"],
        "workload": {"fingerprint": fields["fingerprint"], "domain": fields["domain"],
                     "scope": fields["scope"]},
        "agent": {
            "provider": fields["provider"],
            "model_id": model_id,
            "model_snapshot": f"unresolved:{model_id}",
            "harness": {"client": fields["harness_client"],
                        "version": fields["harness_version"]},
            "system_prompt_sha256": fields["system_prompt_sha256"],
        },
        "repository_state": {
            "repository": fields["repository"],
            "visibility": fields["visibility"],
            "commit": fields["commit"],
            "bundle_version": fields["bundle_version"],
            "dirty": False,
        },
        "execution": {
            "turns": fields["turns"],
            "tool_calls": fields["tool_calls"],
            "wall_time_ms": fields["wall_time_ms"],
            "event_stream": {"ref": fields["event_stream_ref"],
                             "sha256": fields["event_stream_sha256"],
                             "event_count": fields["event_count"]},
        },
        "accounting": {"mode": fields["accounting_mode"]},
        "oracle": fields["oracle"],
        "outcome": fields["outcome"],
        "instrument": {"capture_version": fields["capture_version"], "fence": fields["fence"]},
    }
    execution = row["execution"]
    for key in ("tool_surface", "verdict_route", "permission_denials", "capture_level",
                "human_prompts", "result_commits"):
        if fields.get(key) is not None:
            execution[key] = fields[key]
    if fields.get("usage"):
        row["accounting"]["usage"] = fields["usage"]
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
