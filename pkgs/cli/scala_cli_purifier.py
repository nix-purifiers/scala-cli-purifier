# SPDX-License-Identifier: MIT
"""scala-cli-purifier: lock the downloads of a scala-cli project, and run
scala-cli on the locked downloads.

  scala-cli-purifier lock [--test] [--name NAME] [--] [ARGS...]
  scala-cli-purifier exec [--] COMMAND [ARGS...]

`lock` runs `scala-cli package` (and `scala-cli test` with --test) with an
empty coursier cache, records every file scala-cli downloaded in
scala-cli-purifier.lock.json, and checks that the project builds offline
from those files alone, the way the Nix build runs it. ARGS are the
scala-cli inputs and options; the default input is the current directory.

`exec` runs a scala-cli command offline on the locked downloads, which it
realizes through Nix into a coursier cache under .scala-cli-purifier/.

The Nix package of this program sets the SCALA_CLI_PURIFIER_* variables
below.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# The wrapper runs this file with `python3 -I`, which leaves the file's own
# directory off sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import coursier_cache

LOCK_FILE = "scala-cli-purifier.lock.json"
LOCK_VERSION = 1
STATE_DIR = ".scala-cli-purifier"


class PurifierError(Exception):
    pass


def env(name):
    value = os.environ.get(name)
    if not value:
        raise PurifierError(f"{name} is not set; run scala-cli-purifier through its Nix package")
    return value


# --- running scala-cli ----------------------------------------------------


def isolated_env(work, cache, offline):
    """The environment of a lock run or its offline check: a fresh home and
    the given coursier cache, the way the Nix build runs scala-cli."""
    home = work / "home"
    home.mkdir(parents=True, exist_ok=True)
    e = {k: v for k, v in os.environ.items() if not k.startswith("COURSIER_")}
    e.update(
        HOME=str(home),
        XDG_CACHE_HOME=str(home / ".cache"),
        XDG_CONFIG_HOME=str(home / ".config"),
        XDG_DATA_HOME=str(home / ".local/share"),
        XDG_STATE_HOME=str(home / ".local/state"),
        COURSIER_CACHE=str(cache),
        COURSIER_ARCHIVE_CACHE=str(work / "archive-cache"),
    )
    e.pop("SCALA_CLI_HOME", None)
    if offline:
        e["COURSIER_MODE"] = "offline"
        for k in list(e):
            if k.lower() in ("http_proxy", "https_proxy", "all_proxy"):
                del e[k]
    return e


def scala_cli_commands(java_home, arguments, test, out_jar, offline):
    """The scala-cli invocations of a lock run; the Nix build runs the same
    ones (nix/lib/build-scala-cli-project.nix)."""
    scala_cli = env("SCALA_CLI_PURIFIER_SCALA_CLI")
    # The native scala-cli reads Java properties only from -D options placed
    # before its subcommand.
    props = [] if offline else coursier_cache.java_proxy_properties()
    common = ["--power", "--server=false", "--java-home", java_home]
    if offline:
        common.append("--offline")
    commands = [
        [scala_cli, *props, "package", *common, "--assembly", "--preamble=false", "-o", str(out_jar), "-f", *arguments]
    ]
    if test:
        commands.append([scala_cli, *props, "test", *common, *arguments])
    return commands


def run_all(commands, cwd, environment, quiet):
    for command in commands:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if quiet else None,
            stderr=subprocess.STDOUT if quiet else None,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return result.stdout or ""
    return None


def java_version(java_home):
    """The JAVA_VERSION of a JDK's release file."""
    release = Path(java_home, "release")
    if release.is_file():
        match = re.search(r'^JAVA_VERSION="([^"]+)"', release.read_text(), re.MULTILINE)
        if match:
            return match.group(1)
    raise PurifierError(f"{java_home} has no release file naming its Java version")


def default_java_home():
    return os.environ.get("JAVA_HOME") or env("SCALA_CLI_PURIFIER_JAVA_HOME")


# --- lock -----------------------------------------------------------------


def check_offline(root, java_home, arguments, test, source_cache, paths, work):
    """Build offline from `paths` of `source_cache` alone, in a fresh home.
    Returns None on success and the build's output on failure."""
    cache = work / "cache"
    for rel in paths:
        target = cache / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_cache / rel, target)
    commands = scala_cli_commands(java_home, arguments, test, work / "out.jar", offline=True)
    return run_all(commands, root, isolated_env(work, cache, offline=True), quiet=True)


def lock(args):
    test = False
    name = None
    arguments = []
    it = iter(args)
    for arg in it:
        if arg == "--test":
            test = True
        elif arg == "--name":
            name = next(it, None)
            if not name:
                raise PurifierError("--name needs a value")
        elif arg == "--":
            arguments += list(it)
        else:
            arguments.append(arg)
    if not arguments:
        arguments = ["."]
    root = Path.cwd()
    if name is None:
        name = root.name
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+._-]*", name):
        raise PurifierError(f"'{name}' is not a usable package name; pass --name")
    java_home = default_java_home()

    with tempfile.TemporaryDirectory(prefix="scala-cli-purifier-") as tmp:
        tmp = Path(tmp)
        run_dir = tmp / "lock-run"
        cache = run_dir / "cache"
        cache.mkdir(parents=True)
        print("scala-cli-purifier: building with an empty coursier cache", file=sys.stderr)
        commands = scala_cli_commands(java_home, arguments, test, run_dir / "out.jar", offline=False)
        failure = run_all(commands, root, isolated_env(run_dir, cache, offline=False), quiet=False)
        if failure is not None:
            raise PurifierError("the lock run failed; see scala-cli's output above")

        files = coursier_cache.downloaded_files(cache)
        stable = [p for p in files if not coursier_cache.is_version_listing(p)]
        print("scala-cli-purifier: checking the build offline from the recorded files", file=sys.stderr)
        output = check_offline(root, java_home, arguments, test, cache, stable, tmp / "check")
        locked = stable
        if output is not None and len(stable) < len(files):
            listings = [p for p in files if coursier_cache.is_version_listing(p)]
            print(
                "scala-cli-purifier: the offline build needs version listings; locking them too.\n"
                "  Their content changes when a version is published, so a machine without them\n"
                "  cached fails to fetch them once that happens. Pinning exact versions avoids this:\n"
                + "".join(f"    {coursier_cache.url_of(p)}\n" for p in listings),
                file=sys.stderr,
            )
            output = check_offline(root, java_home, arguments, test, cache, files, tmp / "check-all")
            locked = files
        if output is not None:
            sys.stderr.write(output)
            raise PurifierError("the project does not build offline from the files the lock run downloaded")

        artifacts = coursier_cache.entries(cache, locked)

    lockfile = {
        "version": LOCK_VERSION,
        "scalaCliVersion": env("SCALA_CLI_PURIFIER_SCALA_CLI_VERSION"),
        "javaVersion": java_version(java_home),
        "name": name,
        "arguments": arguments,
        "test": test,
        "artifacts": artifacts,
    }
    (root / LOCK_FILE).write_text(json.dumps(lockfile, indent=2) + "\n")
    print(f"scala-cli-purifier: wrote {LOCK_FILE} ({len(artifacts)} files)", file=sys.stderr)


# --- exec -----------------------------------------------------------------


def realize(root, lockfile_path):
    """The coursier cache of the lock: every locked file fetched into the
    store, with one GC root on the manifest that names them all, and a
    writable tree of links into the store, since coursier writes lock and
    marker files next to what it reads."""
    lib = env("SCALA_CLI_PURIFIER_LIB")
    state = root / STATE_DIR
    state.mkdir(exist_ok=True)
    expr = ["--arg", "lockFile", str(lockfile_path), f"{lib}/realize.nix"]
    subprocess.run(["nix-build", "--no-out-link", "-A", "fetches", *expr], check=True, stdout=subprocess.DEVNULL)
    manifest = subprocess.run(
        # Read-write mode, so the evaluation writes the builtins.toFile
        # manifest to the store.
        ["nix-instantiate", "--eval", "--read-write-mode", "--raw", "-A", "manifest", *expr],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["nix-store", "--realise", manifest, "--add-root", str(state / "manifest"), "--indirect"],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    cache = state / "coursier-cache"
    stamp = state / "coursier-cache.manifest"
    if not (cache.is_dir() and stamp.is_file() and stamp.read_text() == manifest):
        shutil.rmtree(cache, ignore_errors=True)
        for rel, target in json.loads(Path(manifest).read_text()).items():
            link = cache / rel
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(target)
        stamp.write_text(manifest)
    return cache


def exec_(args):
    if args and args[0] == "--":
        args = args[1:]
    if not args or args[0].startswith("-"):
        raise PurifierError("exec needs a scala-cli command, such as `scala-cli-purifier exec run .`")
    root = Path.cwd()
    lockfile_path = root / LOCK_FILE
    if not lockfile_path.is_file():
        raise PurifierError(f"no {LOCK_FILE} here; run `scala-cli-purifier lock` first")
    locked = json.loads(lockfile_path.read_text())
    if locked.get("version") != LOCK_VERSION:
        raise PurifierError(f"{LOCK_FILE} has lock format {locked.get('version')}; this version reads {LOCK_VERSION}. Re-lock.")
    version = env("SCALA_CLI_PURIFIER_SCALA_CLI_VERSION")
    if locked.get("scalaCliVersion") != version:
        raise PurifierError(
            f"{LOCK_FILE} was made with scala-cli {locked.get('scalaCliVersion')}, and this is {version}. Re-lock."
        )
    cache = realize(root, lockfile_path)
    environment = dict(os.environ, COURSIER_CACHE=str(cache), COURSIER_MODE="offline")
    command, rest = args[0], args[1:]
    scala_cli = env("SCALA_CLI_PURIFIER_SCALA_CLI")
    argv = [scala_cli, command, "--power", "--server=false", "--offline", "--java-home", default_java_home(), *rest]
    os.execve(scala_cli, argv, environment)


def main(argv):
    if len(argv) < 2 or argv[1] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0 if len(argv) >= 2 else 2
    verb, args = argv[1], argv[2:]
    try:
        if verb == "lock":
            lock(args)
        elif verb == "exec":
            exec_(args)
        else:
            raise PurifierError(f"unknown command '{verb}'; the commands are lock and exec")
    except PurifierError as e:
        print(f"scala-cli-purifier: {e}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as e:
        print(f"scala-cli-purifier: {' '.join(map(str, e.cmd[:2]))} failed with exit code {e.returncode}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
