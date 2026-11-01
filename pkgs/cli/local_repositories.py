# SPDX-License-Identifier: MIT
"""The local repositories a scala-cli build reads: ivy2Local, m2Local and
any other `file:` repository.

Coursier reads a `file:` repository in place rather than through its
cache, so a lock run's cache never holds what came from one. A lock run
instead finds the build's local repositories among the resolvers
`scala-cli export --json` lists, takes the build's class path, and records
every file of each module version directory the class path reaches into,
with its SRI sha256 hash, by its path relative to the repository's root.
The Nix build is given each repository as a directory (buildScalaCliProject's
`localRepositories`), copies the recorded files from it, checks their
hashes, and points scala-cli at the copy.

  python3 local_repositories.py stage SPEC BASE

copies and checks the files SPEC (a JSON file) names: a list of
{"name", "source", "target", "files"}, where `target` is relative to BASE
and `files` are lockfile entries {"path", "hash"}.
"""

import json
import os
import pwd
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import coursier_cache


class LocalRepositoryError(Exception):
    pass


def user_home():
    """The home whose .ivy2/local and .m2/repository scala-cli reads: the
    JVM's user.home, which comes from the password database rather than
    from HOME, or SCALA_CLI_PURIFIER_USER_HOME, which replaces it."""
    override = os.environ.get("SCALA_CLI_PURIFIER_USER_HOME")
    if override:
        return Path(override).resolve()
    return Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()


def parse_resolver(resolver):
    """The layout, root directory and Ivy pattern of a `file:` resolver as
    `scala-cli export --json` writes it, or None for any other resolver.
    An Ivy resolver is `ivy:file:<root>/<pattern>`, where the pattern
    starts at the first `[` or `(`."""
    layout = "maven"
    if resolver.startswith("ivy:"):
        layout = "ivy"
        resolver = resolver[len("ivy:") :]
    if not resolver.startswith("file:"):
        return None
    pattern = ""
    if layout == "ivy":
        match = re.search(r"[\[(]", resolver)
        if match:
            resolver, pattern = resolver[: match.start()], resolver[match.start() :]
    root = Path(unquote(urlsplit(resolver).path)).resolve()
    return layout, root, pattern


def roots(resolvers, home, internal):
    """The local repositories among `resolvers`, as dicts with `name`,
    `layout`, `root` and `pattern`. ivy2Local and m2Local of `home` get
    those names; any other repository is named for its directory. A
    resolver under `internal`, scala-cli's own repository of its tools, is
    left out."""
    found = []
    names = set()
    for resolver in resolvers:
        parsed = parse_resolver(resolver)
        if parsed is None:
            continue
        layout, root, pattern = parsed
        if root.is_relative_to(internal):
            continue
        if root == home / ".ivy2" / "local":
            name = "ivy2Local"
        elif root == home / ".m2" / "repository":
            name = "m2Local"
        else:
            base = re.sub(r"[^A-Za-z0-9_-]", "-", root.name) or "repository"
            name, n = base, 2
            while name in names or name in ("ivy2Local", "m2Local"):
                name, n = f"{base}-{n}", n + 1
        names.add(name)
        found.append({"name": name, "layout": layout, "root": root, "pattern": pattern})
    return found


def module_directory(layout, file):
    """The module version directory of a file in a repository: the
    directory holding a Maven artifact, or the revision directory above an
    Ivy artifact's type directory (jars/, bundles/, srcs/)."""
    if layout == "ivy" and file.parent.name.endswith("s"):
        return file.parent.parent
    return file.parent


def record(found, class_path):
    """The lockfile entries of the local repositories the class path
    reaches into: each repository with every file of the module version
    directories of its class path entries, sorted."""
    directories = {}
    for entry in class_path:
        path = Path(entry).resolve()
        for repo in found:
            if path.is_relative_to(repo["root"]) and path.is_file():
                directories.setdefault(repo["name"], set()).add(module_directory(repo["layout"], path))
    entries = []
    for repo in found:
        if repo["name"] not in directories:
            continue
        files = []
        for directory in sorted(directories[repo["name"]]):
            for file in sorted(p for p in directory.rglob("*") if p.is_file()):
                files.append(
                    {"path": file.relative_to(repo["root"]).as_posix(), "hash": coursier_cache.sri_sha256(file)}
                )
        entries.append(
            {"name": repo["name"], "layout": repo["layout"], "pattern": repo["pattern"], "files": files}
        )
    return entries


def stage(name, source, target, files):
    """Copies `files` of a local repository from `source` to `target` and
    checks each one's hash."""
    source, target = Path(source), Path(target)
    for entry in files:
        origin = source / entry["path"]
        if not origin.is_file():
            raise LocalRepositoryError(f"local repository {name} ({source}) lacks {entry['path']}, which the lockfile records")
        if coursier_cache.sri_sha256(origin) != entry["hash"]:
            raise LocalRepositoryError(
                f"{entry['path']} in local repository {name} ({source}) differs from the file the lockfile records; re-lock"
            )
        destination = target / entry["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, destination)


def resolver_for(repo, directory):
    """A scala-cli --repository value for `directory`, laid out as `repo`."""
    uri = Path(directory).resolve().as_uri()
    if repo["layout"] == "ivy":
        return f"ivy:{uri}/{repo['pattern']}"
    return uri


def main(argv):
    if len(argv) != 4 or argv[1] != "stage":
        print(__doc__.strip(), file=sys.stderr)
        return 2
    base = Path(argv[3])
    try:
        for spec in json.loads(Path(argv[2]).read_text()):
            stage(spec["name"], spec["source"], base / spec["target"], spec["files"])
    except LocalRepositoryError as e:
        print(f"scala-cli-purifier: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
