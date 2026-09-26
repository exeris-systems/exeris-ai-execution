"""`content_preserved` — the task's own statement of what a run must leave alone, checked.

Every other gate of the docs oracle is structural: it asks whether the corpus is well-formed, and a
corpus from which prose has been deleted is exactly as well-formed as one where it has not. A task
that asks for frontmatter to be added says, by implication, that the text under the frontmatter is
not the run's to change; this gate is where that implication becomes a check.

The rule: every file that exists at `base` and matches one of the `preserve` patterns keeps its body
byte-for-byte. The body is the text after the file's leading YAML frontmatter block — or the whole
text, where it has none — so that adding, rewriting or removing a frontmatter block is never read as
a change to the body, and nothing else is ever read as not one. Blank lines between the block and
the body belong to the block's layout, not to the body, and are set aside on both sides.

A preserved file the run deleted is a failure: its body is not there to be identical.

Applicability is stated where the gate is made, because composition cannot recover it:

  * no `preserve` patterns — the task asked for nothing to be kept, so there is nothing to judge
    and the gate is `not-run` and available;
  * patterns given and `base` unreadable in the checkout's repository — the gate never reached what
    it judges, so it is `not-run` and **not** available, and the judgement cannot be `TRUE_DONE`
    over it.

`base` is read through git, run inside the checkout: the tree it names is listed once and the blobs
it holds are read over `git cat-file --batch`'s standard input, so no path from that tree ever
reaches a command line.
"""
from __future__ import annotations

import difflib
import glob as globlib
import os
import re
import subprocess

from . import FAIL, NOT_RUN, PASS, Gate
from .paths import within

CHECK = "content_preserved"
#: What `base` may be spelt as: a commit id, abbreviated or full. Never a ref name and never an
#: option, so the value placed on git's command line is only ever an object id.
COMMIT = re.compile(r"[0-9a-f]{4,64}")
#: What a `preserve` pattern may be spelt as: a relative glob, never an option or an absolute path.
PATTERN = re.compile(r"[A-Za-z0-9._*?/\[\]!+@-]+")
#: Modes of a tree entry whose content is a file's bytes. A symlink's blob is its target and a
#: gitlink is another repository's commit; neither is a body.
FILE_MODES = frozenset({"100644", "100755"})


def _text(data: bytes) -> str:
    return data.decode("utf-8", "replace")


def _ascii(data: bytes) -> list[str]:
    """`data` as ASCII words; git's object ids, modes and headers are never anything else."""
    return data.decode("ascii", "replace").split(" ")


def _git(checkout: str, *args: str, stdin: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=checkout, input=stdin, capture_output=True)


def body(data: bytes) -> bytes:
    """`data` after its leading YAML frontmatter block, with the blank lines that follow it removed.

    A block opens with a first line that is `---` and closes at the next line that is `---`, either
    one allowing surrounding whitespace, as the link-stub reader in `adr_links` allows it: the two
    gates read one file format and have to agree on where its frontmatter ends. A file whose first
    line does not open a block, or whose block never closes, has no frontmatter and its body is the
    whole text.
    """
    text = data.replace(b"\r\n", b"\n")
    lines = text.split(b"\n")
    if lines[0].strip() != b"---":
        return text
    for i in range(1, len(lines)):
        if lines[i].strip() == b"---":
            return b"\n".join(lines[i + 1:]).lstrip(b"\n")
    return text


def _matchers(preserve: tuple[str, ...]) -> list[re.Pattern]:
    return [re.compile(globlib.translate(p, recursive=True, include_hidden=True)) for p in preserve]


def _preserved_at_base(checkout: str, base: str,
                       preserve: tuple[str, ...]) -> list[tuple[str, str]] | None:
    """`(path, blob id)` for every file at `base` a pattern matches, or None if `base` is unreadable.

    `**` spans any number of directories, including none; every other character means what it
    means to `fnmatch`. Paths are relative to the checkout.
    """
    listing = _git(checkout, "ls-tree", "-r", "-z", base)
    if listing.returncode != 0:
        return None
    matchers = _matchers(preserve)
    found = []
    for record in listing.stdout.split(b"\0"):
        meta, sep, raw = record.partition(b"\t")
        if not sep:
            continue
        mode, kind, blob = _ascii(meta)
        path = raw.decode("utf-8", "surrogateescape")
        if mode in FILE_MODES and kind == "blob" and any(m.fullmatch(path) for m in matchers):
            found.append((path, blob))
    return found


def _blobs(checkout: str, ids: list[str]) -> dict[str, bytes] | None:
    """The bytes of each blob id, read in one `git cat-file --batch`, or None if git refused."""
    if not ids:
        return {}
    proc = _git(checkout, "cat-file", "--batch", stdin="".join(f"{i}\n" for i in ids).encode())
    if proc.returncode != 0:
        return None
    out, at, blobs = proc.stdout, 0, {}
    for blob in ids:
        eol = out.find(b"\n", at)
        header = _ascii(out[at:eol]) if eol >= 0 else []
        if len(header) != 3 or header[1] != "blob" or not header[2].isdigit():
            return None
        size = int(header[2])
        blobs[blob] = out[eol + 1:eol + 1 + size]
        at = eol + 1 + size + 1
    return blobs


def _working_bytes(checkout: str, path: str) -> bytes | None:
    """The file's bytes in the working tree, or None where it is not there (or not inside it)."""
    real = within(os.path.join(checkout, path), checkout)
    if real is None or not os.path.isfile(real):
        return None
    with open(real, "rb") as fh:
        return fh.read()


def changed_lines(before: bytes, after: bytes) -> int:
    """How many lines a unified diff of the two bodies adds or removes."""
    a, b = _text(before).splitlines(), _text(after).splitlines()
    return sum(1 for line in difflib.unified_diff(a, b, lineterm="", n=0)
               if line[:1] in "+-" and not line.startswith(("+++", "---")))


def _offences(checkout: str, preserved: list[tuple[str, str]],
              blobs: dict[str, bytes]) -> list[str]:
    offences = []
    for path, blob in preserved:
        now = _working_bytes(checkout, path)
        if now is None:
            offences.append(f"{path} was deleted")
            continue
        before, after = body(blobs[blob]), body(now)
        if before != after:
            offences.append(f"{path} ({changed_lines(before, after)} lines changed below the "
                            f"frontmatter)")
    return offences


def valid_preserve(preserve) -> tuple[str, ...] | None:
    """`preserve` as a tuple of patterns, or None where one of them is not a relative glob."""
    patterns = tuple(preserve or ())
    if not all(PATTERN.fullmatch(p) and not p.startswith(("/", "-")) for p in patterns):
        return None
    return patterns


def gate(checkout: str, base: str | None, preserve) -> Gate:
    """The `content_preserved` gate for one checkout."""
    patterns = valid_preserve(preserve)
    if patterns is None:
        return Gate(CHECK, NOT_RUN, f"a preserve pattern is not a relative glob: {preserve!r}",
                    available=False)
    if not patterns:
        return Gate(CHECK, NOT_RUN, "the task names nothing to preserve")
    if not base or not COMMIT.fullmatch(base):
        return Gate(CHECK, NOT_RUN, f"no readable base commit ({base!r}), so nothing the task "
                    "said to preserve could be compared", available=False)
    preserved = _preserved_at_base(checkout, base, patterns)
    blobs = None if preserved is None else _blobs(checkout, [b for _, b in preserved])
    if blobs is None:
        return Gate(CHECK, NOT_RUN, f"the base commit {base} is not readable in the checkout's "
                    "repository", available=False)
    if not preserved:
        return Gate(CHECK, NOT_RUN, f"no file at {base[:12]} matches {list(patterns)}")
    offences = _offences(checkout, preserved, blobs)
    if offences:
        return Gate(CHECK, FAIL, f"{len(offences)} of {len(preserved)} preserved files changed; "
                    f"first: {offences[0]}")
    return Gate(CHECK, PASS, f"Checked {len(preserved)} preserved files: every body is identical "
                f"to {base[:12]}")

