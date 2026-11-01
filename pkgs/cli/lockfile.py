# SPDX-License-Identifier: MIT
"""The integrity and freshness of a scala-cli-purifier lockfile.

A lockfile carries two hashes besides its entries:

  checksum         the sha256 (hex) of the lockfile's content without the
                   checksum, as compact JSON with sorted keys, which is
                   what Nix's builtins.toJSON writes; a lockfile edited by
                   hand no longer matches it.
  directivesHash   the sha256 (hex) of the project's `//> using` directives,
                   file by file; a project whose directives changed since
                   the lock, such as one with a dependency added, no longer
                   matches it.

The commands check both before they use a lockfile; Nix checks both at
evaluation (nix/lib/lock.nix).
"""

import hashlib
import json
import re
from pathlib import Path

LOCK_FILE = "scala-cli-purifier.lock.json"

# The files scala-cli reads `//> using` directives from.
SOURCE_SUFFIXES = (".scala", ".sc", ".java", ".md")
# Directories that are build output or editor state, never sources; the Nix
# build leaves them out of its copy of the project too.
SKIPPED_DIRECTORIES = {".scala-build", ".scala-cli-purifier", ".bsp", ".metals", ".bloop", ".git", ".idea"}
DIRECTIVE = re.compile(r"^[ \t]*//>")


class LockfileError(Exception):
    pass


def canonical(data):
    """Compact JSON with sorted keys, as Nix's builtins.toJSON writes it."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def checksum(data):
    """The checksum of a lockfile's content, without its checksum field."""
    rest = {k: v for k, v in data.items() if k != "checksum"}
    return hashlib.sha256(canonical(rest).encode("utf-8")).hexdigest()


def source_files(root, arguments):
    """The source files of the inputs among `arguments`, relative to
    `root`, sorted: the directories and files they name, and every source
    file below a directory. Options and inputs that are no paths are
    skipped."""
    found = set()
    for argument in arguments:
        if argument.startswith("-"):
            continue
        path = (root / argument).resolve()
        # Symbolic links are skipped, as Nix's readDir lists them apart from
        # files and directories.
        if path.is_file() and path.suffix in SOURCE_SUFFIXES:
            found.add(path)
        elif path.is_dir():
            for file in path.rglob("*"):
                parts = set(file.relative_to(path).parts)
                if not file.is_symlink() and file.is_file() and file.suffix in SOURCE_SUFFIXES and not parts & SKIPPED_DIRECTORIES:
                    found.add(file)
    root = root.resolve()
    return sorted(f.relative_to(root).as_posix() if f.is_relative_to(root) else f.as_posix() for f in found)


def directives_hash(root, arguments):
    """The hash of the `//> using` directives of the project's sources: the
    sha256 of the canonical JSON list of [file, [directive, ...]] for each
    source file that has directives. Nix computes the same hash at
    evaluation (nix/lib/lock.nix), so the two split lines and strip blanks
    the same way."""
    root = Path(root)
    found = []
    for rel in source_files(root, arguments):
        try:
            lines = (root / rel).read_text(encoding="utf-8", errors="replace").split("\n")
        except OSError:
            continue
        directives = [line.strip(" \t\r") for line in lines if DIRECTIVE.match(line)]
        if directives:
            found.append([rel, directives])
    return hashlib.sha256(canonical(found).encode("utf-8")).hexdigest()


def read(path):
    """A lockfile's content, after checking that it is JSON and that its
    checksum matches."""
    try:
        data = json.loads(Path(path).read_text())
    except json.JSONDecodeError as e:
        raise LockfileError(
            f"{path} is not a scala-cli-purifier lockfile: not valid JSON ({e}); "
            "run `scala-cli-purifier lock` to write it again"
        ) from None
    if not isinstance(data, dict) or "artifacts" not in data:
        raise LockfileError(f"{path} is not a scala-cli-purifier lockfile; run `scala-cli-purifier lock` to write it again")
    if data.get("checksum") != checksum(data):
        raise LockfileError(
            f"{path} was edited after `scala-cli-purifier lock` wrote it: its checksum does not match its content."
            " Run `scala-cli-purifier lock` to write it again"
        )
    return data


def check_directives(root, data, path):
    """Raises when the project at `root` has other `//> using` directives
    than the ones the lockfile was made from."""
    if directives_hash(root, data.get("arguments", ["."])) != data.get("directivesHash"):
        raise LockfileError(
            f"the project's `//> using` directives changed since {path} was made, so it may lack what they need."
            " Run `scala-cli-purifier lock` to lock them"
        )
