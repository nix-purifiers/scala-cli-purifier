# SPDX-License-Identifier: MIT
"""scala-cli-purifier: lock the downloads of a scala-cli project, run
scala-cli on the locked downloads, and publish what Nix built.

  scala-cli-purifier lock [--test] [--publish] [--name NAME] [--] [ARGS...]
  scala-cli-purifier exec [--] COMMAND [ARGS...]
  scala-cli-purifier publish [OPTIONS] REPOSITORY

`lock` runs `scala-cli package` with an empty coursier cache (and
`scala-cli test` with --test, and `scala-cli publish` to a local directory
with --publish), records every file scala-cli downloaded in
scala-cli-purifier.lock.json, and checks that the project builds offline
from those files alone, the way the Nix build runs it. ARGS are the
scala-cli inputs and options; the default input is the current directory.

`exec` runs a scala-cli command offline on the locked downloads, which it
realizes through Nix into a coursier cache under .scala-cli-purifier/.

`publish` builds the project's publication in the Nix sandbox (the files
`scala-cli publish` writes, from a lock made with --publish) and uploads
those files to REPOSITORY: an http(s) Maven repository, which takes them
by HTTP PUT, or a directory (a path or a file: URL). Its options:

  --user-env VAR       the variable holding the repository user name
                       (default SCALA_CLI_PURIFIER_PUBLISH_USER)
  --password-env VAR   the variable holding its password
                       (default SCALA_CLI_PURIFIER_PUBLISH_PASSWORD)
  --gpg-key KEY        sign every file with gpg, as KEY, adding .asc files
  --bundle FILE        write a zip of the files (a Maven Central Portal
                       bundle) instead of uploading; REPOSITORY is omitted

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
import local_repositories
import offline_mirror
import publication

LOCK_FILE = "scala-cli-purifier.lock.json"
LOCK_VERSION = 1
STATE_DIR = ".scala-cli-purifier"
PLATFORMS = ("JVM", "JS", "Native")


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


def java_properties(work, mirror_options=None, user_home=None):
    """The -D options of every scala-cli invocation: online, the options for
    the proxy the environment names; offline, the options that send
    requests to the offline mirror. `user_home` replaces the home whose
    .ivy2/local and .m2/repository scala-cli reads. The native scala-cli
    reads Java properties only from -D options placed before its
    subcommand."""
    tmp = work / "java-tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    props = [f"-Djava.io.tmpdir={tmp}"]
    if mirror_options is None:
        props += coursier_cache.java_proxy_properties()
    else:
        props += mirror_options
    if user_home is not None:
        props.append(f"-Duser.home={user_home}")
    return props


def home_override():
    """The home SCALA_CLI_PURIFIER_USER_HOME names, which scala-cli then reads
    .ivy2/local and .m2/repository from, or None."""
    return local_repositories.user_home() if os.environ.get("SCALA_CLI_PURIFIER_USER_HOME") else None


def start_mirror(cache, state, urls, java_home):
    """Starts the offline mirror (offline_mirror.py) on `cache`, with
    certificates for the hosts of `urls`, and returns its Java options and
    its server."""
    keytool = Path(java_home, "bin", "keytool")
    if not keytool.is_file():
        raise PurifierError(f"{java_home} has no bin/keytool, which the offline mirror needs")
    return offline_mirror.start(cache, state, offline_mirror.hosts_of(urls), str(keytool))


def common_options(java_home, platform, offline):
    """The options every scala-cli command of a lock run takes; the Nix
    build passes the same ones (nix/lib/build-scala-cli-project.nix)."""
    options = ["--power", "--server=false", "--java-home", java_home]
    if offline:
        options.append("--offline")
    if platform == "JS":
        # The default Scala.js linker is a prebuilt native executable, which
        # does not run in the Nix sandbox; this runs the linker on the JVM.
        options.append("--js-cli-on-jvm")
    return options


def package_options(platform, out):
    """How `scala-cli package` packages a project of each platform."""
    if platform == "JVM":
        return ["--assembly", "--preamble=false", "-o", str(out / "program.jar")]
    if platform == "JS":
        return ["-o", str(out / "program.js")]
    return ["-o", str(out / "program")]


def scala_cli_commands(
    work, java_home, arguments, platform, test, publish, mirror_options=None, user_home=None, extra=()
):
    """The scala-cli invocations of a lock run, online, or of its offline
    check, given the offline mirror's Java options; the Nix build runs the
    same ones. `extra` are more options for each command."""
    scala_cli = env("SCALA_CLI_PURIFIER_SCALA_CLI")
    offline = mirror_options is not None
    props = java_properties(work, mirror_options, user_home)
    # scala-cli keeps compiled classes and generated Scaladoc in the
    # workspace's .scala-build and reuses them, which would skip downloads
    # the Nix build needs; each run gets an empty workspace, as a fresh
    # copy of the project in the sandbox has.
    common = [*common_options(java_home, platform, offline), "--workspace", str(work / "workspace"), *extra]
    out = work / "out"
    out.mkdir(parents=True, exist_ok=True)
    commands = [[scala_cli, *props, "package", *common, *package_options(platform, out), "-f", *arguments]]
    if test:
        commands.append([scala_cli, *props, "test", *common, *arguments])
    if publish:
        repo = work / "publication"
        commands.append([scala_cli, *props, "publish", *common, "--publish-repository", repo.as_uri(), *arguments])
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


def detect_platform(root, work, java_home, arguments, environment, user_home):
    """The platform the project builds for (JVM, JS or Native, which
    `using platform` directives or options decide) and the resolvers of
    its scopes."""
    export = work / "export"
    command = [
        env("SCALA_CLI_PURIFIER_SCALA_CLI"),
        *java_properties(work, user_home=user_home),
        "export",
        "--power",
        "--json",
        "--server=false",
        "--java-home",
        java_home,
        "--workspace",
        str(work / "export-workspace"),
        "-o",
        str(export),
        *arguments,
    ]
    failure = run_all([command], root, environment, quiet=True)
    if failure is not None:
        sys.stderr.write(failure)
        raise PurifierError("`scala-cli export --json` failed, so the project's platform is unknown")
    exported = json.loads((export / "export.json").read_text())
    platform = exported.get("platform")
    if platform not in PLATFORMS:
        raise PurifierError(f"scala-cli reports platform {platform!r}, which this version does not build")
    resolvers = []
    for scope in exported.get("scopes", {}).values():
        resolvers += [r for r in scope.get("resolvers", []) if r not in resolvers]
    return platform, resolvers


def class_path(root, work, java_home, arguments, platform, test_scope, environment, user_home):
    """The class path of the project's main scope, or with `test_scope` of
    its test scope, as `scala-cli compile --print-class-path` prints it."""
    command = [
        env("SCALA_CLI_PURIFIER_SCALA_CLI"),
        *java_properties(work, user_home=user_home),
        "compile",
        *common_options(java_home, platform, offline=False),
        "--workspace",
        str(work / "workspace"),
        "--print-class-path",
        *(["--test"] if test_scope else []),
        *arguments,
    ]
    result = subprocess.run(
        command, cwd=root, env=environment, stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        raise PurifierError("`scala-cli compile --print-class-path` failed")
    lines = result.stdout.strip().splitlines()
    return lines[-1].split(os.pathsep) if lines else []


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


def stage_local_repositories(locals_, sources, home, other_root):
    """Copies the recorded files of each local repository into the places
    an offline build reads them from, as the Nix build does: ivy2Local and
    m2Local under `home`, which the build passes as user.home, and any other
    repository under `other_root`, which the build adds with --repository.
    Returns those --repository options."""
    extra = []
    for repo in locals_:
        name = repo["name"]
        if name == "ivy2Local":
            target = home / ".ivy2" / "local"
        elif name == "m2Local":
            target = home / ".m2" / "repository"
        else:
            target = other_root / name
            extra += ["--repository", local_repositories.resolver_for(repo, target)]
        local_repositories.stage(name, sources[name], target, repo["files"])
    return extra


def check_offline(root, java_home, arguments, platform, test, publish, source_cache, paths, work, locals_, sources):
    """Build offline from `paths` of `source_cache` and the recorded files of
    the local repositories alone, in a fresh home. Returns None on success
    and the build's output on failure."""
    cache = work / "cache"
    for rel in paths:
        target = cache / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_cache / rel, target)
    user_home = work / "user-home"
    user_home.mkdir(parents=True, exist_ok=True)
    extra = stage_local_repositories(locals_, sources, user_home, work / "local-repositories")
    urls = [coursier_cache.url_of(p) for p in paths]
    unlocked = []
    mirror_options, server = start_mirror(cache, work / "mirror", urls, java_home)
    offline_mirror.Handler.log = staticmethod(lambda message: unlocked.append(message))
    try:
        commands = scala_cli_commands(
            work, java_home, arguments, platform, test, publish, mirror_options, user_home, extra
        )
        output = run_all(commands, root, isolated_env(work, cache, offline=True), quiet=True)
    finally:
        server.shutdown()
    if output is not None and unlocked:
        output += "".join(f"{line}\n" for line in unlocked)
    return output


def published_files(repository):
    """The files `scala-cli publish` wrote, besides checksums, each with a
    name: its file name without the version, such as `greeting_3.jar`,
    `greeting_3-sources.jar` or `greeting_3.pom`. The lockfile records them,
    so Nix knows the files of the publication before it builds."""
    entries = []
    for rel in publication.files(repository):
        if rel.endswith(publication.CHECKSUMS):
            continue
        path = Path(rel)
        version = path.parent.name
        entries.append({"name": path.name.replace(f"-{version}", "", 1), "path": rel})
    names = [e["name"] for e in entries]
    if len(set(names)) != len(names):
        raise PurifierError(f"the publication has two files with the same name: {names}")
    return entries


def parse_lock_args(args):
    options = {"test": False, "publish": False, "name": None}
    arguments = []
    it = iter(args)
    for arg in it:
        if arg in ("--test", "--publish"):
            options[arg[2:]] = True
        elif arg == "--name":
            options["name"] = next(it, None)
            if not options["name"]:
                raise PurifierError("--name needs a value")
        elif arg == "--":
            arguments += list(it)
        else:
            arguments.append(arg)
    return options, arguments or ["."]


def lock(args):
    options, arguments = parse_lock_args(args)
    test, publish = options["test"], options["publish"]
    root = Path.cwd()
    name = options["name"] or root.name
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+._-]*", name):
        raise PurifierError(f"'{name}' is not a usable package name; pass --name")
    java_home = default_java_home()

    with tempfile.TemporaryDirectory(prefix="scala-cli-purifier-") as tmp:
        tmp = Path(tmp)
        run_dir = tmp / "lock-run"
        cache = run_dir / "cache"
        cache.mkdir(parents=True)
        environment = isolated_env(run_dir, cache, offline=False)
        user_home = home_override()
        platform, resolvers = detect_platform(root, run_dir, java_home, arguments, environment, user_home)
        print(f"scala-cli-purifier: building for {platform} with an empty coursier cache", file=sys.stderr)
        commands = scala_cli_commands(run_dir, java_home, arguments, platform, test, publish, user_home=user_home)
        if run_all(commands, root, environment, quiet=False) is not None:
            raise PurifierError("the lock run failed; see scala-cli's output above")

        # Coursier reads local repositories in place; the files the class
        # path reaches into are recorded with their hashes.
        found = local_repositories.roots(resolvers, local_repositories.user_home(), run_dir / "home")
        class_paths = class_path(root, run_dir, java_home, arguments, platform, False, environment, user_home)
        if test:
            class_paths += class_path(root, run_dir, java_home, arguments, platform, True, environment, user_home)
        locals_ = local_repositories.record(found, class_paths)
        sources = {repo["name"]: repo["root"] for repo in found}

        files = coursier_cache.downloaded_files(cache)
        fetched = [p for p in files if not coursier_cache.is_version_listing(p)]
        listings = [p for p in files if coursier_cache.is_version_listing(p)]
        print("scala-cli-purifier: checking the build offline from the recorded files", file=sys.stderr)
        check = (root, java_home, arguments, platform, test, publish, cache)
        output = check_offline(*check, fetched, tmp / "check", locals_, sources)
        inlined = []
        if output is not None and listings:
            # The project needs version listings, for a version range or a
            # `latest` selector. Their content changes upstream whenever a
            # version is published, so the lock keeps the listings this run
            # read as inline entries, and the offline build resolves to the
            # versions this run picked.
            output = check_offline(*check, files, tmp / "check-listings", locals_, sources)
            inlined = listings
            print(
                "scala-cli-purifier: the offline build needs version listings; the lockfile holds them inline:\n"
                + "".join(f"    {coursier_cache.url_of(p)}\n" for p in listings),
                file=sys.stderr,
            )
        if output is not None:
            sys.stderr.write(output)
            raise PurifierError(
                "the project does not build offline from the files the lock run downloaded"
                " and the files it read from local repositories"
            )
        artifacts = coursier_cache.entries(cache, fetched) + coursier_cache.inline_entries(cache, inlined)
        artifacts.sort(key=lambda e: e["path"])
        published = published_files(run_dir / "publication") if publish else []

    lockfile = {
        "version": LOCK_VERSION,
        "scalaCliVersion": env("SCALA_CLI_PURIFIER_SCALA_CLI_VERSION"),
        "javaVersion": java_version(java_home),
        "name": name,
        "platform": platform,
        "arguments": arguments,
        "test": test,
        "publish": publish,
        "publication": published,
        "localRepositories": locals_,
        "artifacts": artifacts,
    }
    (root / LOCK_FILE).write_text(json.dumps(lockfile, indent=2) + "\n")
    print(f"scala-cli-purifier: wrote {LOCK_FILE} ({len(artifacts)} files)", file=sys.stderr)


# --- exec -----------------------------------------------------------------


def read_lock(root):
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
    return lockfile_path, locked


def realize(root, lockfile_path):
    """The coursier cache of the lock: every locked file in the store, with
    one GC root on the manifest that names them all, and a writable tree of
    links into the store, since coursier writes lock and marker files next
    to what it reads."""
    lib = env("SCALA_CLI_PURIFIER_LIB")
    state = root / STATE_DIR
    state.mkdir(exist_ok=True)
    expr = ["--arg", "lockFile", str(lockfile_path), f"{lib}/realize.nix"]
    subprocess.run(["nix-build", "--no-out-link", "-A", "fetches", *expr], check=True, stdout=subprocess.DEVNULL)
    manifest = subprocess.run(
        # Read-write mode, so the evaluation writes the builtins.toFile
        # manifest and inline entries to the store.
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
    lockfile_path, locked = read_lock(root)
    cache = realize(root, lockfile_path)
    environment = dict(os.environ, COURSIER_CACHE=str(cache), COURSIER_MODE="offline")
    command, rest = args[0], args[1:]
    java_home = default_java_home()
    work = root / STATE_DIR
    urls = [a["url"] for a in locked["artifacts"] if "url" in a]
    mirror_options, server = start_mirror(cache, work / "mirror", urls, java_home)
    try:
        # exec reads local repositories in place, from the home scala-cli
        # reads them from.
        argv = [
            env("SCALA_CLI_PURIFIER_SCALA_CLI"),
            *java_properties(work, mirror_options, home_override()),
            command,
            *common_options(java_home, locked.get("platform", "JVM"), offline=True),
            *rest,
        ]
        return subprocess.run(argv, env=environment, check=False).returncode
    finally:
        server.shutdown()


# --- publish --------------------------------------------------------------


def build_publication(root):
    """The publication of the project, built by Nix: a Maven repository
    directory of what `scala-cli publish` wrote in the sandbox."""
    lib = env("SCALA_CLI_PURIFIER_LIB")
    out = subprocess.run(
        [
            "nix-build",
            "--no-out-link",
            f"{lib}/publication.nix",
            "--argstr",
            "nixpkgs",
            env("SCALA_CLI_PURIFIER_NIXPKGS"),
            "--argstr",
            "src",
            str(root),
        ],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    return Path(out.splitlines()[-1])


def publish(args):
    repository = None
    user_env = "SCALA_CLI_PURIFIER_PUBLISH_USER"
    password_env = "SCALA_CLI_PURIFIER_PUBLISH_PASSWORD"
    gpg_key = None
    bundle = None
    it = iter(args)
    for arg in it:
        if arg in ("--user-env", "--password-env", "--gpg-key", "--bundle"):
            value = next(it, None)
            if not value:
                raise PurifierError(f"{arg} needs a value")
            if arg == "--user-env":
                user_env = value
            elif arg == "--password-env":
                password_env = value
            elif arg == "--gpg-key":
                gpg_key = value
            else:
                bundle = value
        elif arg.startswith("-"):
            raise PurifierError(f"unknown publish option {arg}")
        elif repository is None:
            repository = arg
        else:
            raise PurifierError("publish takes one repository")
    if (repository is None) == (bundle is None):
        raise PurifierError("publish needs a repository, or --bundle FILE instead of one")

    root = Path.cwd()
    _, locked = read_lock(root)
    if not locked.get("publish"):
        raise PurifierError(f"{LOCK_FILE} was not made with --publish; run `scala-cli-purifier lock --publish`")
    built = build_publication(root)
    with tempfile.TemporaryDirectory(prefix="scala-cli-purifier-publish-") as tmp:
        staged = Path(tmp) / "publication"
        shutil.copytree(built, staged)
        for f in staged.rglob("*"):
            f.chmod(0o755 if f.is_dir() else 0o644)
        if gpg_key:
            publication.sign(staged, gpg_key)
        if bundle:
            publication.write_bundle(staged, Path(bundle))
            print(f"scala-cli-purifier: wrote {bundle}", file=sys.stderr)
            return
        user = os.environ.get(user_env)
        password = os.environ.get(password_env)
        for rel in publication.upload(staged, repository, user, password):
            print(f"scala-cli-purifier: published {rel}", file=sys.stderr)


def main(argv):
    if len(argv) < 2 or argv[1] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0 if len(argv) >= 2 else 2
    verb, args = argv[1], argv[2:]
    try:
        if verb == "lock":
            lock(args)
        elif verb == "exec":
            return exec_(args)
        elif verb == "publish":
            publish(args)
        else:
            raise PurifierError(f"unknown command '{verb}'; the commands are lock, exec and publish")
    except (PurifierError, publication.PublishError) as e:
        print(f"scala-cli-purifier: {e}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as e:
        print(f"scala-cli-purifier: {' '.join(map(str, e.cmd[:2]))} failed with exit code {e.returncode}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
