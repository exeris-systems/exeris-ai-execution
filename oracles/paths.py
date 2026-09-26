"""Where a path this layer opens or hands to a subprocess is allowed to come from.

Shared by every oracle module: a directory arrives from a caller, is resolved once at the boundary,
and every path under it is built from what a listing of it reported and checked to stay inside it.
"""
from __future__ import annotations

import argparse
import os
import re

#: What a path may be spelt as before it reaches a checker's command line: absolute, and made of
#: the characters a path in a checkout is made of. A path that resolves to anything else is not
#: one this oracle hands to a subprocess, whatever tree it was found in.
PATH_GRAMMAR = re.compile(r"/[A-Za-z0-9._+@-]+(?:/[A-Za-z0-9._+@-]+)*")


# --------------------------------------------------------------------------------------------
# Where a path under a caller-supplied directory is allowed to come from.
# --------------------------------------------------------------------------------------------


def existing_directory(value: str) -> str:
    """An `argparse` `type=` admitting only a directory already on disk, as its real path.

    A directory this oracle is handed rather than one it creates is resolved once, at the boundary,
    to the real path its symlinks point at — so every path built under it afterwards is checked
    against the same tree a listing of it would show, not against a spelling that a link could
    still lead somewhere else from.
    """
    resolved = os.path.realpath(value)
    if not os.path.isdir(resolved):
        raise argparse.ArgumentTypeError(f"{value!r} is not a directory")
    return resolved


def existing_file(value: str) -> str:
    """An `argparse` `type=` admitting only a file already on disk, as its real path."""
    resolved = os.path.realpath(value)
    if not os.path.isfile(resolved):
        raise argparse.ArgumentTypeError(f"{value!r} is not a file")
    return resolved


def _listed_child(directory: str, name: str) -> str | None:
    """`name`, joined onto `directory`, only once a listing of `directory` has named it.

    A checkout, a shared guardrails clone and an agent bundle's tools directory all arrive from
    outside this process, so a path under one of them is built from what its own listing contains
    rather than from the directory string concatenated with a name this process already knew. None
    where the directory cannot be listed or does not hold that name, which reads the same as the
    name not being on disk.
    """
    try:
        entries = os.listdir(directory)
    except OSError:
        return None
    for entry in entries:
        if entry == name:
            return os.path.join(directory, entry)
    return None


def listed_path(root: str, *parts: str) -> str | None:
    """Join `parts` onto `root` one directory listing at a time, or None where the walk runs out.

    Every step advances only onto a name the directory in fact contains, so the path this returns
    is assembled from what `os.listdir` reported at each level rather than from the arguments as
    given.
    """
    cur = root
    for part in parts:
        cur = _listed_child(cur, part)
        if cur is None:
            return None
    real, top = os.path.realpath(cur), os.path.realpath(root)
    if os.path.commonprefix((real, top)) != top or not real.startswith(top + os.sep):
        return None
    if not PATH_GRAMMAR.fullmatch(real):
        return None
    return real


def within(path: str, root: str) -> str | None:
    """`path`'s real path where it lies strictly inside `root`'s, else None."""
    real, top = os.path.realpath(path), os.path.realpath(root)
    if os.path.commonprefix((real, top)) != top or not real.startswith(top + os.sep):
        return None
    return real
