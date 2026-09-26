"""`adr_links_resolve` — every link stub in the checkout names a record the registry holds, as it is.

`registry_check` asks whether a stub's number has a row. It cannot ask whether the stub describes
that row: a stub that names ADR-086 by some other record's title, or points at a repository that
does not own the decision, is well-formed and passes it. This gate reads the registry through the
Exeris MCP server (`exeris-ai-bridge`), which ADR-086 §A.3 admits to this layer as a context
adapter — the oracle consumes the bridge's tools and adds nothing to the bridge.

For each `docs/adr/ADR-NNN.link.md` and `adr/ADR-NNN.link.md` in the checkout:

  * the registry holds record NNN (`docs-list_adrs`);
  * the stub names the record by its title — in its frontmatter `title` or its first `# ` heading.
    The title is the record's own heading where `docs-get_adr` can read the record, and the
    registry row's title in any case; either counts. Titles are compared as sequences of lower-case
    words, so case, whitespace, punctuation and markup do not matter, and the spellings `&`, `/` and
    `and` of the same conjunction are one spelling. A trailing `(link stub)` is outside the title;
  * the stub links to the record's owning repository: the registry's owning-repo name is one
    segment of a link target in the stub.

Applicability, stated where the gate is made:

  * no stubs in the checkout — nothing to judge; `not-run`, available;
  * stubs present and no bridge, a bridge that does not start, a call that does not answer, or no
    registry for the bridge to read — the stubs are unread, which is not the same as correct;
    `not-run`, **not** available.

A tool's own error about one record (`docs-get_adr` refusing a cross-repo record it cannot resolve
on disk) is not a protocol failure and does not make the gate unavailable: the registry row still
answers for that record.
"""
from __future__ import annotations

import json
import os
import re
import subprocess

from . import FAIL, NOT_RUN, PASS, Gate
from .mcp_client import McpClient, McpError, launcher_for
from .paths import PATH_GRAMMAR, listed_path

CHECK = "adr_links_resolve"
STUB = re.compile(r"ADR-(\d{3})\.link\.md")
STUB_DIRS = (("docs", "adr"), ("adr",))
LINK = re.compile(r"\]\(\s*<?([^)\s>]+)")
WORD = re.compile(r"[a-z0-9]+")
#: One conjunction, three spellings; the registry and the records it indexes use all of them.
CONJUNCTIONS = frozenset({"and"})
LINK_STUB_SUFFIX = re.compile(r"\(\s*link stub\s*\)\s*$", re.I)
RECORD_PREFIX = re.compile(r"^\s*ADR-\d+\s*[:—–-]\s*")
LIST_TOOL = "docs-list_adrs"
GET_TOOL = "docs-get_adr"
#: The key a registry row carries its number under, and the argument `docs-get_adr` takes it as.
NUMBER = "number"


# --------------------------------------------------------------------------------------------
# Reading a stub
# --------------------------------------------------------------------------------------------


def stubs(checkout: str) -> list[tuple[str, int, str]]:
    """`(relative path, number, real path)` for every link stub in the checkout's records dirs."""
    found = []
    for parts in STUB_DIRS:
        directory = listed_path(checkout, *parts)
        if directory is None or not os.path.isdir(directory):
            continue
        for name in sorted(os.listdir(directory)):
            match = STUB.fullmatch(name)
            real = listed_path(directory, name) if match else None
            if match and real and os.path.isfile(real):
                found.append(("/".join((*parts, name)), int(match.group(1)), real))
    return found


def _split_frontmatter(text: str) -> tuple[list[str], list[str]]:
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return lines[1:i], lines[i + 1:]
    return [], lines


def names(text: str) -> list[str]:
    """The names a stub gives its record: its frontmatter `title` and its first `# ` heading."""
    front, rest = _split_frontmatter(text)
    found = []
    for line in front:
        key, sep, value = line.partition(":")
        if sep and key.strip() == "title":
            found.append(value.strip().strip("'\""))
            break
    heading = next((line[2:] for line in rest if line.startswith("# ")), None)
    if heading is not None:
        found.append(heading)
    return [LINK_STUB_SUFFIX.sub("", n).strip() for n in found]


def link_targets(text: str) -> list[str]:
    return LINK.findall(text)


# --------------------------------------------------------------------------------------------
# Comparing
# --------------------------------------------------------------------------------------------


def words(text: str) -> list[str]:
    return [w for w in WORD.findall(text.lower()) if w not in CONJUNCTIONS]


def names_title(name: str, title: str) -> bool:
    """Whether `title`'s words appear, contiguously and in order, among `name`'s."""
    want, have = words(title), words(name)
    if not want:
        return False
    return any(have[i:i + len(want)] == want for i in range(len(have) - len(want) + 1))


def record_title(markdown: str) -> str | None:
    """The record's own title: its first `# ` heading, without the `ADR-NNN:` in front of it."""
    _, rest = _split_frontmatter(markdown)
    heading = next((line[2:] for line in rest if line.startswith("# ")), None)
    return RECORD_PREFIX.sub("", heading).strip() if heading else None


def links_repository(targets: list[str], repository: str) -> bool:
    return any(repository in target.split("/") for target in targets)


class Untitled(Exception):
    """Neither the registry row nor the record gives a title, so no stub can be checked against one.

    A state of the registry, not of the stub: the gate cannot judge, and says so rather than
    failing the run for what the registry left out.
    """


def judge_stub(text: str, number: int, row: dict | None, own_title: str | None) -> str | None:
    """Why the stub does not describe its record, or None where it does.

    Raises `Untitled` where the record has a row and neither it nor the record names a title.
    """
    if row is None:
        return f"ADR-{number:03d} is not in the registry"
    titles = [t for t in (own_title, row.get("title")) if t]
    if not titles:
        raise Untitled(f"ADR-{number:03d} has no title in its registry row and none the bridge "
                       f"could read from the record")
    stub_names = names(text)
    if not any(names_title(n, t) for n in stub_names for t in titles):
        shown = stub_names[-1] if stub_names else "no title or heading"
        return f"it names ADR-{number:03d} as {shown!r}, not by the record's title {titles[0]!r}"
    owner = row.get("owningRepo") or ""
    if not links_repository(link_targets(text), owner):
        return f"it does not link to {owner}, the repository that owns ADR-{number:03d}"
    return None


# --------------------------------------------------------------------------------------------
# Reading the registry through the bridge
# --------------------------------------------------------------------------------------------


def docs_root(checkout: str, index: str | None) -> str | None:
    """The registry checkout the bridge reads: the judged checkout if it is the registry, else the
    directory the central index is in."""
    if os.path.isfile(os.path.join(checkout, "adr-index.md")):
        return checkout
    if index and os.path.isfile(index):
        return os.path.dirname(index)
    return None


def _bridge_env(root: str) -> dict[str, str]:
    env = dict(os.environ)
    env["EXERIS_DOCS_ROOT"] = root
    env["EXERIS_BRIDGE_MODE"] = "contributor"
    return env


def _registry(client: McpClient) -> dict[int, dict]:
    is_error, rows = client.call_tool(LIST_TOOL, {})
    if is_error or not isinstance(rows, list):
        raise McpError(f"{LIST_TOOL} did not return the registry: {str(rows)[:200]}")
    return {r[NUMBER]: r for r in rows if isinstance(r, dict) and isinstance(r.get(NUMBER), int)}


def _own_title(client: McpClient, number: int) -> str | None:
    is_error, body = client.call_tool(GET_TOOL, {NUMBER: number})
    if is_error or not isinstance(body, str):
        return None
    return record_title(body)


def instrument(bridge: str) -> dict:
    """The bridge's version from its `package.json` and its commit where it is a git checkout."""
    package = os.path.dirname(os.path.dirname(bridge))
    version = ""
    manifest = listed_path(package, "package.json")
    if manifest:
        try:
            with open(manifest, encoding="utf-8") as fh:
                version = str(json.load(fh).get("version", ""))
        except (OSError, ValueError):
            version = ""
    top = subprocess.run(["git", "rev-parse", "--show-toplevel", "HEAD"], cwd=package,
                         capture_output=True, text=True)
    lines = top.stdout.split() if top.returncode == 0 else []
    commit = lines[1] if len(lines) == 2 and os.path.realpath(lines[0]) == package else None
    return {"version": version, "commit": commit}


def _offences(client: McpClient, found: list[tuple[str, int, str]]) -> list[str]:
    """What is wrong with each stub, read against the registry the bridge serves."""
    registry = _registry(client)
    offences = []
    for rel, number, path in found:
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except (OSError, UnicodeDecodeError) as exc:
            offences.append(f"{rel}: not readable as UTF-8 text ({exc})")
            continue
        row = registry.get(number)
        own = _own_title(client, number) if row else None
        why = judge_stub(text, number, row, own)
        if why:
            offences.append(f"{rel}: {why}")
    return offences


def _unavailable(detail: str) -> tuple[Gate, None]:
    return Gate(CHECK, NOT_RUN, detail, available=False), None


def gate(checkout: str, bridge: str | None, index: str | None) -> tuple[Gate, dict | None]:
    """The gate, and the bridge that answered it (None where no bridge was used)."""
    found = stubs(checkout)
    if not found:
        return Gate(CHECK, NOT_RUN, "the checkout holds no ADR link stubs"), None
    count = f"{len(found)} link stub{'s' if len(found) != 1 else ''}"
    real = os.path.realpath(bridge) if bridge else None
    if real is None or not os.path.isfile(real) or launcher_for(real) is None:
        return _unavailable(f"{count} and no bridge to read the registry through ({bridge})")
    if not PATH_GRAMMAR.fullmatch(real):
        return _unavailable(f"the bridge path is not one this oracle starts ({real})")
    root = docs_root(checkout, index)
    if root is None:
        return _unavailable(f"{count} and no registry checkout for the bridge to read")
    try:
        with McpClient(real, _bridge_env(root)) as client:
            offences = _offences(client, found)
    except McpError as exc:
        return _unavailable(f"{count} unread: the bridge did not answer ({exc})")
    except Untitled as exc:
        return _unavailable(f"{count} unjudged: {exc}")
    used = instrument(real)
    if offences:
        return Gate(CHECK, FAIL, f"{len(offences)} of {count} misdescribe their record; "
                    f"first: {offences[0]}"), used
    return Gate(CHECK, PASS, f"Checked {count} against the registry: each names its record and "
                "links its owning repository"), used
