# SPDX-License-Identifier: MIT
"""scala-cli-purifier: lock the downloads of a scala-cli project, prefetch
them for offline work, and publish what Nix built.

  scala-cli-purifier lock [--lockfile PATH] [--test] [--publish] [--no-ide]
                          [--name NAME] [--] [ARGS...]
  scala-cli-purifier update [--lockfile PATH] [MODULE...]
  scala-cli-purifier prefetch [--lockfile PATH]
  scala-cli-purifier publish [--lockfile PATH] [OPTIONS] REPOSITORY

`lock` runs `scala-cli package` with an empty coursier cache (and
`scala-cli test` with --test, and `scala-cli publish` to a local directory
with --publish), opens the project as an editor does (unless --no-ide),
records every file scala-cli downloaded in scala-cli-purifier.lock.json,
and checks that the project builds offline from those files alone, the way
the Nix build runs it. ARGS are the scala-cli inputs and options; the
default input is the current directory. --lockfile names another place for
the lockfile, such as one outside the project; every command reads
SCALA_CLI_PURIFIER_LOCKFILE when --lockfile is not given.

A relock keeps the version listings the current lockfile holds, so a
version range or a `latest` selector resolves as it did. `update` relocks
with the options of the current lockfile and downloads the listings again:
those of the named modules (`name`, `org:name` or `org::name`), or every
one, so ranges resolve to what is published now.

`prefetch` realizes every file of the lockfile in the Nix store, with a GC
root in .scala-cli-purifier/ beside the lockfile, so the everyday loop and the editor work
offline. The package installs this program as `scala-cli` too: in a project
with a lockfile it runs scala-cli on the prefetched downloads, offline, and
elsewhere it is scala-cli itself. It reads SCALA_CLI_PURIFIER_LOCKFILE
too.

`publish` builds the project's release in the Nix sandbox (the files
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

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

# The wrapper runs this file with `python3 -I`, which leaves the file's own
# directory off sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
if os.environ.get("SCALA_CLI_PURIFIER_PURIFIER"):
    sys.path.insert(0, str(Path(os.environ["SCALA_CLI_PURIFIER_PURIFIER"]) / "tools"))
import bsp
import coursier_cache
import local_repositories
import lockfile
import offline_mirror
import publication

LOCK_FILE = lockfile.LOCK_FILE
MAVEN_CENTRAL = "https://repo1.maven.org/maven2"
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
    # Both the Bloop daemon and its BSP session bind Unix domain sockets.
    # Keep their roots short and check both against Linux's 107-byte limit.
    data = work / "d"
    cache_home = work / "c"
    sockets = (
        data / "scalacli/bloop/daemon/socket",
        cache_home / "scalacli/bsp-sockets/proc-2147483647",
    )
    limit = 107
    # On macOS scala-cli and coursier ignore the XDG directories and keep
    # theirs under ~/Library; SCALA_CLI_HOME and COURSIER_CONFIG_DIR keep
    # them in the work directory. macOS allows a socket path 103 bytes.
    scala_cli_home = work / "s"
    if sys.platform == "darwin":
        sockets = (
            scala_cli_home / "data-local/bloop/daemon/socket",
            scala_cli_home / "data-local/bsp-sockets/proc-2147483647",
        )
        limit = 103
    for socket in sockets:
        if len(os.fsencode(socket)) > limit:
            raise PurifierError(
                f"the build server's socket would be {socket}, which is longer than a Unix domain socket"
                " path may be; set TMPDIR to a shorter directory"
            )
    e = {k: v for k, v in os.environ.items() if not k.startswith("COURSIER_")}
    e.update(
        HOME=str(home),
        XDG_CACHE_HOME=str(cache_home),
        XDG_CONFIG_HOME=str(home / ".config"),
        XDG_DATA_HOME=str(data),
        XDG_STATE_HOME=str(home / ".local/state"),
        COURSIER_CACHE=str(cache),
        COURSIER_ARCHIVE_CACHE=str(work / "archive-cache"),
    )
    e.pop("SCALA_CLI_HOME", None)
    if sys.platform == "darwin":
        e.update(SCALA_CLI_HOME=str(scala_cli_home), COURSIER_CONFIG_DIR=str(home / ".config/coursier"))
    if offline:
        e["COURSIER_MODE"] = "offline"
        for k in list(e):
            if k.lower() in ("http_proxy", "https_proxy", "all_proxy"):
                del e[k]
    return e


def central_mirrors():
    """The mirrors of Maven Central the lock run downloads through, from
    SCALA_CLI_PURIFIER_MAVEN_CENTRAL_MIRROR, as coursier_cache.canonical
    takes them."""
    mirror = os.environ.get("SCALA_CLI_PURIFIER_MAVEN_CENTRAL_MIRROR")
    return {MAVEN_CENTRAL: [mirror.rstrip("/")]} if mirror else {}


def configure_mirrors(environment, mirrors):
    """Makes coursier download each repository of `mirrors` from its first
    mirror, through a mirror.properties file in the config directory of the
    lock run's isolated home."""
    if not mirrors:
        return
    lines = []
    for n, (base, alternates) in enumerate(mirrors.items()):
        lines += [f"mirror{n}.from={base}", f"mirror{n}.to={alternates[0]}"]
    config = Path(environment["XDG_CONFIG_HOME"], "coursier")
    config.mkdir(parents=True, exist_ok=True)
    (config / "mirror.properties").write_text("\n".join(lines) + "\n")


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


def start_mirror(cache, state, aliases, java_home):
    """Starts the offline mirror (offline_mirror.py) on `cache`, serving the
    URLs of `aliases` from their cache paths, with certificates for their
    hosts, and returns its Java options and its server."""
    keytool = Path(java_home, "bin", "keytool")
    if not keytool.is_file():
        raise PurifierError(f"{java_home} has no bin/keytool, which the offline mirror needs")
    return offline_mirror.start(cache, state, offline_mirror.hosts_of(aliases), str(keytool), aliases=aliases)


def native_sdk_repository():
    """The complete Native backend provided by the configured Nix scope."""
    package = os.environ.get("SCALA_CLI_PURIFIER_NATIVE_SDK_PACKAGE")
    return Path(package) / "maven" if package else None


def configure_native_sdk(environment, platform):
    """Add the provided repository after caller-selected repositories."""
    repository = native_sdk_repository()
    if platform != "Native" or repository is None:
        return
    caller = environment.get("COURSIER_REPOSITORIES")
    environment["COURSIER_REPOSITORIES"] = (
        caller + "|" + repository.as_uri() if caller
        else repository.as_uri() + "|" + MAVEN_CENTRAL
    )


def common_options(java_home, platform, offline):
    """The options every scala-cli command of a lock run takes; the Nix
    build passes the same ones (pkgs/build-scala-cli-project/default.nix)."""
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


def editor_session(root, work, java_home, arguments, platform, environment, mirror_options=None, user_home=None, extra=()):
    """What an editor asks scala-cli's build server on opening the project
    (bsp.editor_session): online in the lock run, so the lockfile holds the
    sources jars and the rest an editor needs, and offline in its check.
    Returns None when the compile succeeds and every dependency source is a
    file, and what went wrong otherwise.

    The native scala-cli serves an editor through the Bloop compilation
    server, which downloads a compiler bridge and its own jars, while the
    JVM build of scala-cli compiles in its own process there. So the session
    first compiles the main and test scopes on Bloop, which downloads those
    files whichever build of scala-cli runs the lock."""
    options = common_options(java_home, platform, offline=mirror_options is not None)
    workspace = ["--workspace", str(work / "bsp-workspace"), *extra, *arguments]
    # The Bloop server is a JVM of its own, which takes its temporary
    # directory from these options rather than from the -D options of
    # scala-cli.
    on_bloop = [o for o in options if o != "--server=false"] + [
        "--server=true",
        f"--bloop-java-opt=-Djava.io.tmpdir={work / 'java-tmp'}",
    ]
    for scope in ([], ["--test"]):
        compiled = subprocess.run(
            [
                env("SCALA_CLI_PURIFIER_SCALA_CLI"),
                *java_properties(work, mirror_options, user_home),
                "compile",
                *on_bloop,
                *scope,
                *workspace,
            ],
            cwd=root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
        if compiled.returncode != 0:
            return compiled.stdout + "\nthe compilation server could not compile the project"
    argv = [
        env("SCALA_CLI_PURIFIER_SCALA_CLI"),
        *java_properties(work, mirror_options, user_home),
        "bsp",
        *options,
        *workspace,
    ]
    messages = []
    try:
        result = bsp.editor_session(argv, root, root.as_uri(), env=environment, log=messages.append)
    except bsp.BspError as e:
        return "\n".join([*messages, f"the editor session failed: {e}"])
    missing = [p for p in result["dependencySources"] if not Path(p).is_file()]
    if result["compileStatus"] != 1:
        return "\n".join([*messages, "the editor session could not compile the project"])
    if missing:
        return "the build server names dependency sources that are missing:\n" + "\n".join(missing)
    return None


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


def class_path(root, work, java_home, arguments, platform, test_scope, environment, user_home, quiet=False):
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
        output = result.stdout + result.stderr
        if not quiet:
            sys.stderr.write(output)
        raise PurifierError("`scala-cli compile --print-class-path` failed" + ("\n" + output if quiet else ""))
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


def default_java_home(arguments=()):
    """Discover the caller's project runtime without selecting a Nix SDK.

    The frontend may use an implementation JVM of its own. Its JVM is not
    the project runtime passed to upstream Scala CLI by common_options.
    """
    selected = os.environ.get("SCALA_CLI_PURIFIER_JAVA_HOME") or os.environ.get("JAVA_HOME")
    options = iter(arguments)
    for option in options:
        if option == "--":
            break
        if option == "--java-home":
            selected = next(options, None)
            if not selected:
                raise PurifierError("--java-home needs the installed project runtime's path")
        elif option.startswith("--java-home="):
            selected = option.split("=", 1)[1]
            if not selected:
                raise PurifierError("--java-home needs the installed project runtime's path")
    java = str(Path(selected, "bin", "java")) if selected else shutil.which("java")
    if not java:
        raise PurifierError(
            "no project Java runtime found; set JAVA_HOME or put your installed java on PATH"
            " (SCALA_CLI_PURIFIER_JAVA_HOME is an explicit override)"
        )
    try:
        probe = subprocess.run(
            [java, "-XshowSettings:properties", "-version"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
        )
    except OSError as error:
        raise PurifierError(f"cannot execute selected project Java {java}: {error}") from None
    details = probe.stdout + "\n" + probe.stderr
    if probe.returncode:
        raise PurifierError(f"selected project Java {java} failed ({probe.returncode}):\n{details.strip()}")
    home = re.search(r"^\s*java\.home\s*=\s*(.+?)\s*$", details, re.MULTILINE)
    if not home:
        raise PurifierError(f"selected project Java {java} did not report java.home")
    actual = Path(home.group(1)).resolve()
    # Java 8 reports its nested JRE as java.home; preserve the installed
    # JDK root when that is the runtime the caller selected.
    if actual.name == "jre" and (actual.parent / "release").is_file():
        actual = actual.parent
    if selected and Path(selected).resolve() != actual:
        raise PurifierError(f"selected project Java home {selected} runs a different runtime at {actual}")
    java_version(actual)
    if not (actual / "bin" / "keytool").is_file():
        raise PurifierError(f"selected project Java {actual} has no bin/keytool, required by the offline mirror")
    return str(actual)


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


def check_offline(
    root, java_home, arguments, platform, test, publish, ide, source_cache, mirrors, paths, work, locals_, sources, views=()
):
    """Build offline from `paths` of `source_cache`, each at its canonical
    cache path, and the recorded files of the local repositories alone, in
    a fresh home, as the Nix build does. Returns None on success and the
    build's output on failure."""
    cache = work / "cache"
    aliases = {}
    for rel in paths:
        path, url, alternates = coursier_cache.canonical(rel, mirrors)
        for alias in [coursier_cache.url_of(rel), url, *alternates]:
            aliases[alias] = path
        target = cache / path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_cache / rel, target)
    user_home = work / "user-home"
    user_home.mkdir(parents=True, exist_ok=True)
    extra = stage_local_repositories(locals_, sources, user_home, work / "local-repositories")
    if views:
        import project_inputs
        project_inputs.stage_coursier(cache, views, root)
    unlocked = []
    mirror_options, server = start_mirror(cache, work / "mirror", aliases, java_home)
    offline_mirror.Handler.log = staticmethod(lambda message: unlocked.append(message))
    try:
        commands = scala_cli_commands(
            work, java_home, arguments, platform, test, publish, mirror_options, user_home, extra
        )
        environment = isolated_env(work, cache, offline=True)
        configure_native_sdk(environment, platform)
        output = run_all(commands, root, environment, quiet=True)
        if output is None and ide:
            output = editor_session(
                root, work, java_home, arguments, platform, environment, mirror_options, user_home, extra
            )
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


def module_of(path):
    """The organization path and the module directory of a version listing
    at a cache path, such as ("com/lihaoyi", "sourcecode_3")."""
    parts = PurePosixPath(coursier_cache.url_of(path).split("://", 1)[1]).parts
    # A listing lies in the module directory, or in a version directory
    # below it for a snapshot.
    module = len(parts) - 2
    if module >= 2 and re.match(r"\d", parts[module]):
        module -= 1
    return "/".join(parts[1:module]), parts[module]


def selected(path, names):
    """Whether the version listing at `path` belongs to a module of `names`:
    `name`, `org:name` or `org::name`, where `name` also matches the module
    with a Scala suffix, such as sourcecode_3 for sourcecode."""
    org, module = module_of(path)
    for spec in names:
        spec_org, _, spec_name = spec.replace("::", ":").rpartition(":")
        if spec_org and not org.endswith(spec_org.replace(".", "/")):
            continue
        if module == spec_name or module.startswith(spec_name + "_"):
            return True
    return False


def kept_listings(lockfile_path, update):
    """The version listings of the current lockfile that the lock run keeps:
    every one for `lock` (update is None), none for `update` with no module
    names, and those of other modules for `update` with names. The lock run
    resolves version ranges and `latest` selectors against the listings it
    keeps, as the lock that wrote them did."""
    if update == [] or not lockfile_path.is_file():
        return []
    try:
        current = lockfile.read(lockfile_path)
    except lockfile.LockfileError:
        return []
    listings = [
        e for e in current["artifacts"] if "contents" in e and coursier_cache.is_version_listing(e["path"])
    ]
    return [e for e in listings if not (update and selected(e["path"], update))]


def seed_listings(cache, listings, mirrors):
    """Writes each kept listing into the empty cache of the lock run at the
    path the lock run reads it from: that of the first mirror of its
    repository, which the lock run downloads the repository through, or its
    own. Coursier takes a cached listing as up to date when its checksum file
    is beside it and it was checked within a day, which the hidden marker
    files `.<name>.checked` record."""
    for entry in listings:
        url = coursier_cache.url_of(entry["path"])
        rel = entry["path"]
        for base, alternates in mirrors.items():
            if url.startswith(base.rstrip("/") + "/") and alternates:
                rel = coursier_cache.cache_path(alternates[0].rstrip("/") + url[len(base.rstrip("/")) :])
        target = cache / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        contents = entry["contents"].encode("utf-8")
        target.write_bytes(contents)
        checksum = target.with_name(target.name + ".sha1")
        checksum.write_text(hashlib.sha1(contents).hexdigest())
        for written in (target, checksum):
            (written.parent / f".{written.name}.checked").touch()


def parse_lock_args(args):
    options = {"test": False, "publish": False, "ide": True, "name": None}
    arguments = []
    it = iter(args)
    for arg in it:
        if arg in ("--test", "--publish"):
            options[arg[2:]] = True
        elif arg == "--no-ide":
            options["ide"] = False
        elif arg == "--name":
            options["name"] = next(it, None)
            if not options["name"]:
                raise PurifierError("--name needs a value")
        elif arg == "--":
            arguments += list(it)
        else:
            arguments.append(arg)
    return options, arguments or ["."]


def take_project_inputs(args):
    references, remaining = None, []
    arguments = iter(args)
    for argument in arguments:
        if argument == "--":
            remaining += [argument, *arguments]
            break
        if argument == "--input":
            reference = next(arguments, None)
            if reference is None:
                raise PurifierError("--input needs a flake package")
            if references is None:
                references = []
            references.append(reference)
        else:
            remaining.append(argument)
    return references, remaining


def lock(args, update=None):
    """Writes the lockfile. With `update` (a list of module names, empty for
    every module), the lock run downloads the version listings of those
    modules again instead of keeping the ones the current lockfile holds."""
    lockfile_path, args = take_lockfile(args)
    input_refs, args = take_project_inputs(args)
    options, arguments = parse_lock_args(args)
    previous_inputs = []
    if lockfile_path.is_file():
        try:
            previous_inputs = lockfile.read(lockfile_path).get("projectInputs", [])
        except lockfile.LockfileError:
            pass
    if input_refs is None:
        input_refs = [entry["reference"] for entry in previous_inputs]
    inputs = []
    if input_refs:
        import project_inputs
        inputs = project_inputs.pin(input_refs, previous_inputs, refresh=update == [])
    test, publish, ide = options["test"], options["publish"], options["ide"]
    root = Path.cwd()
    name = options["name"] or root.name
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9+._-]*", name):
        raise PurifierError(f"'{name}' is not a usable package name; pass --name")
    java_home = default_java_home(arguments)

    # macOS's per-user TMPDIR is too long for the build server's sockets
    # under the lock run's directory; /tmp is short enough.
    work_root = "/tmp" if sys.platform == "darwin" else None
    with tempfile.TemporaryDirectory(prefix="scala-cli-purifier-", dir=work_root) as tmp:
        tmp = Path(tmp)
        run_dir = tmp / "lock-run"
        cache = run_dir / "cache"
        cache.mkdir(parents=True)
        environment = isolated_env(run_dir, cache, offline=False)
        mirrors = central_mirrors()
        configure_mirrors(environment, mirrors)
        seed_listings(cache, kept_listings(lockfile_path, update), mirrors)
        views, provided = [], set()
        if inputs:
            views = project_inputs.maven_views(inputs, tmp / "project-inputs")
            provided = project_inputs.stage_coursier(cache, views, root)
        user_home = home_override()
        platform, resolvers = detect_platform(root, run_dir, java_home, arguments, environment, user_home)
        configure_native_sdk(environment, platform)
        if views:
            # The first native resolution discovers transitive constraints in
            # POMs. Restage supplied artifacts after that discovery, even if a
            # nonexistent supplied version made the first resolution fail.
            for test_scope in ([False, True] if test else [False]):
                while True:
                    failure = None
                    try:
                        class_path(root, run_dir, java_home, arguments, platform, test_scope,
                                   environment, user_home, quiet=True)
                    except PurifierError as error:
                        failure = error
                    aliases = project_inputs.stage_coursier(cache, views, root)
                    added = aliases - provided
                    provided.update(aliases)
                    if added:
                        continue
                    if failure is not None:
                        raise failure
                    break
        print(f"scala-cli-purifier: building for {platform} with an empty coursier cache", file=sys.stderr)
        commands = scala_cli_commands(run_dir, java_home, arguments, platform, test, publish, user_home=user_home)
        if run_all(commands, root, environment, quiet=False) is not None:
            raise PurifierError("the lock run failed; see scala-cli's output above")
        if ide:
            print("scala-cli-purifier: opening the project as an editor does", file=sys.stderr)
            failure = editor_session(root, run_dir, java_home, arguments, platform, environment, user_home=user_home)
            if failure is not None:
                sys.stderr.write(failure + "\n")
                raise PurifierError("the editor session of the lock run failed; `lock --no-ide` locks without it")

        # Coursier reads local repositories in place; the files the class
        # path reaches into are recorded with their hashes.
        found = local_repositories.roots(
            resolvers, local_repositories.user_home(), run_dir / "home",
            provided=[native_sdk_repository()] if platform == "Native" and native_sdk_repository() else [],
        )
        class_paths = class_path(root, run_dir, java_home, arguments, platform, False, environment, user_home)
        if test:
            class_paths += class_path(root, run_dir, java_home, arguments, platform, True, environment, user_home)
        locals_ = local_repositories.record(found, class_paths)
        sources = {repo["name"]: repo["root"] for repo in found}

        files = [path for path in coursier_cache.downloaded_files(cache) if path not in provided]
        fetched = [p for p in files if not coursier_cache.is_version_listing(p)]
        listings = [p for p in files if coursier_cache.is_version_listing(p)]
        print("scala-cli-purifier: checking the build offline from the recorded files", file=sys.stderr)
        check = (root, java_home, arguments, platform, test, publish, ide, cache, mirrors)
        output = check_offline(*check, fetched, tmp / "check", locals_, sources, views=views)
        inlined = []
        if output is not None and listings:
            # The project needs version listings, for a version range or a
            # `latest` selector. Their content changes upstream whenever a
            # version is published, so the lock keeps the listings this run
            # read as inline entries, and the offline build resolves to the
            # versions this run picked.
            output = check_offline(*check, files, tmp / "check-listings", locals_, sources, views=views)
            inlined = listings
            print(
                "scala-cli-purifier: the offline build needs version listings; the lockfile holds them inline:\n"
                + "".join(f"    {coursier_cache.canonical(p, mirrors)[1]}\n" for p in listings),
                file=sys.stderr,
            )
        if output is not None:
            sys.stderr.write(output)
            raise PurifierError(
                "the project does not build offline from the files the lock run downloaded"
                " and the files it read from local repositories"
            )
        artifacts = coursier_cache.entries(cache, fetched, mirrors) + coursier_cache.inline_entries(
            cache, inlined, mirrors
        )
        # Nix adds the mirrors of each URL at evaluation (lib-overlays/default/lock.nix),
        # so the lockfile is the same whichever mirror the lock run used.
        for entry in artifacts:
            entry.pop("mirrors", None)
        # A file the lock run read under a mirror and under its repository
        # has one canonical path, and the lockfile holds it once.
        artifacts = list({e["path"]: e for e in sorted(artifacts, key=lambda e: e["path"])}.values())
        published = published_files(run_dir / "publication") if publish else []

    data = {
        "version": LOCK_VERSION,
        "scalaCliVersion": env("SCALA_CLI_PURIFIER_SCALA_CLI_VERSION"),
        "javaVersion": java_version(java_home),
        "name": name,
        "platform": platform,
        "arguments": arguments,
        "directivesHash": lockfile.directives_hash(root, arguments),
        "test": test,
        "publish": publish,
        "ide": ide,
        "publication": published,
        "localRepositories": locals_,
        "artifacts": artifacts,
    }
    if inputs:
        data["projectInputs"] = inputs
    data["checksum"] = lockfile.checksum(data)
    lockfile_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print(f"scala-cli-purifier: wrote {lockfile_path} ({len(artifacts)} files)", file=sys.stderr)


def update(args):
    """`lock` with the options of the current lockfile, downloading the
    version listings of the named modules, or of every module, again, so
    version ranges and `latest` selectors resolve to what is published
    now."""
    lockfile_path, names = take_lockfile(args)
    unknown = [n for n in names if n.startswith("-")]
    if unknown:
        raise PurifierError(f"update takes module names, such as com.lihaoyi::sourcecode, and --lockfile: {unknown}")
    if not lockfile_path.is_file():
        raise PurifierError(f"no {lockfile_path.name} at {lockfile_path.parent}; run `scala-cli-purifier lock` first")
    current = lockfile.read(lockfile_path)
    options = ["--lockfile", str(lockfile_path), "--name", current["name"]]
    options += ["--test"] if current.get("test") else []
    options += ["--publish"] if current.get("publish") else []
    options += [] if current.get("ide", True) else ["--no-ide"]
    lock([*options, "--", *current["arguments"]], update=names)


# --- reading a lockfile ---------------------------------------------------


def read_lock(root, lockfile_path):
    """The lockfile at `lockfile_path`, after checking that it is intact,
    that this version reads it, and that the directives of the project at
    `root` still match it."""
    if not lockfile_path.is_file():
        raise PurifierError(f"no {lockfile_path.name} at {lockfile_path.parent}; run `scala-cli-purifier lock` first")
    locked = lockfile.read(lockfile_path)
    lockfile.check_directives(root, locked, lockfile_path.name)
    if locked.get("version") != LOCK_VERSION:
        raise PurifierError(
            f"{lockfile_path} has lock format {locked.get('version')}; this version reads {LOCK_VERSION}."
            " Run `scala-cli-purifier lock` to write it again"
        )
    version = env("SCALA_CLI_PURIFIER_SCALA_CLI_VERSION")
    if locked.get("scalaCliVersion") != version:
        raise PurifierError(
            f"{lockfile_path} was made with scala-cli {locked.get('scalaCliVersion')}, and this is {version}."
            " Run `scala-cli-purifier lock` to lock with this one"
        )
    return locked


def take_lockfile(args):
    """The lockfile a command reads or writes, as a path, and the other
    arguments: the --lockfile option, else SCALA_CLI_PURIFIER_LOCKFILE, else
    the lockfile in the current directory."""
    rest = []
    path = os.environ.get("SCALA_CLI_PURIFIER_LOCKFILE") or None
    it = iter(args)
    for arg in it:
        if arg == "--lockfile":
            path = next(it, None)
            if not path:
                raise PurifierError("--lockfile needs a path")
        elif arg.startswith("--lockfile="):
            path = arg.split("=", 1)[1]
        else:
            rest.append(arg)
    return (Path(path).resolve() if path else Path.cwd() / LOCK_FILE), rest


# --- prefetch -------------------------------------------------------------


def prefetch(args):
    """Realizes every file of the lockfile in the Nix store, with a GC root
    beside the lockfile, so the scala-cli wrapper and the editor work with the
    network cut off: the build's downloads, the test dependencies and what
    an editor asks for."""
    lockfile_path, rest = take_lockfile(args)
    if rest:
        raise PurifierError(f"prefetch takes no arguments besides --lockfile: {rest}")
    root = Path.cwd()
    locked = read_lock(root, lockfile_path)
    state = lockfile_path.parent / STATE_DIR
    state.mkdir(exist_ok=True)
    subprocess.run(
        [
            "nix-build",
            f"{env('SCALA_CLI_PURIFIER_LIB')}/prefetch.nix",
            "--argstr",
            "nixpkgs",
            env("SCALA_CLI_PURIFIER_NIXPKGS"),
            "--argstr",
            "purifierSource",
            env("SCALA_CLI_PURIFIER_PURIFIER"),
            "--argstr",
            "src",
            str(root),
            "--argstr",
            "lockFile",
            str(lockfile_path),
            "--out-link",
            str(state / "downloads"),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    if locked.get("projectInputs"):
        import project_inputs
        views = project_inputs.maven_views(locked["projectInputs"], state / "project-inputs")
        (state / "project-inputs.json").write_text(json.dumps(
            [{key: str(value) for key, value in view.items()} for view in views]))
    (state / "prefetched").write_text(locked["checksum"] + "\n")
    print(
        f"scala-cli-purifier: prefetched {len(locked['artifacts'])} files into {state / 'downloads'};"
        " scala-cli works offline in this project",
        file=sys.stderr,
    )


# --- the scala-cli wrapper ------------------------------------------------

# The scala-cli commands that resolve or build, which the wrapper runs with
# the options of the lock run. Other commands, such as `version` or
# `config`, run with the locked cache and nothing else.
BUILD_COMMANDS = {
    "compile",
    "run",
    "test",
    "package",
    "repl",
    "console",
    "doc",
    "setup-ide",
    "bsp",
    "export",
    "fix",
    "fmt",
    "publish",
}


def find_lockfile():
    """The lockfile the wrapper reads: SCALA_CLI_PURIFIER_LOCKFILE, or the
    nearest scala-cli-purifier.lock.json in the current directory or above,
    or None."""
    named = os.environ.get("SCALA_CLI_PURIFIER_LOCKFILE")
    if named:
        return Path(named).resolve(), Path.cwd()
    for directory in [Path.cwd(), *Path.cwd().parents]:
        if (directory / LOCK_FILE).is_file():
            return directory / LOCK_FILE, directory
    return None, None


def prefetched_cache(lockfile_path, locked):
    """The writable coursier cache of a prefetched lockfile: a tree of links
    into the prefetched downloads, since coursier writes lock and marker
    files next to what it reads."""
    state = lockfile_path.parent / STATE_DIR
    stamp = state / "prefetched"
    downloads = state / "downloads"
    if not (downloads.exists() and stamp.is_file() and stamp.read_text().strip() == locked["checksum"]):
        raise PurifierError(
            "the locked downloads of this project are not prefetched for its lockfile."
            " Run `scala-cli-purifier prefetch`"
        )
    cache = state / "coursier-cache"
    cache_stamp = state / "coursier-cache.prefetched"
    if not (cache.is_dir() and cache_stamp.is_file() and cache_stamp.read_text() == stamp.read_text()):
        shutil.rmtree(cache, ignore_errors=True)
        target = downloads.resolve()
        for link in target.rglob("*"):
            if link.is_symlink() or link.is_file():
                destination = cache / link.relative_to(target)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.symlink_to(link.resolve())
        cache_stamp.write_text(stamp.read_text())
    if locked.get("projectInputs"):
        import project_inputs
        views = json.loads((state / "project-inputs.json").read_text())
        project_inputs.stage_coursier(cache, views, Path.cwd(), lockfile_path)
    return cache


def point_bsp_at_wrapper(root):
    """Makes .bsp/scala-cli.json, which `scala-cli setup-ide` wrote, start
    the build server through this wrapper, which runs it offline on the
    prefetched downloads and the offline mirror."""
    config_path = root / ".bsp" / "scala-cli.json"
    try:
        config = json.loads(config_path.read_text())
        argv = config["argv"]
    except (OSError, ValueError, KeyError) as e:
        raise PurifierError(f"scala-cli setup-ide wrote no usable {config_path} ({e})") from None
    # The launcher and its Java options come before `bsp`: the native
    # scala-cli, or a JVM and the scala-cli jar. The wrapper starts its own
    # scala-cli with its own options.
    if "bsp" not in argv:
        raise PurifierError(f"{config_path} does not start `scala-cli bsp`: {argv}")
    config["argv"] = [env("SCALA_CLI_PURIFIER_WRAPPER"), *argv[argv.index("bsp") :]]
    config_path.write_text(json.dumps(config, indent=2) + "\n")


def scala_cli_wrapper(args):
    """scala-cli, on the locked downloads in a project with a lockfile:
    offline, through the offline mirror, with the options of the lock run.
    Outside such a project it is scala-cli itself."""
    scala_cli = env("SCALA_CLI_PURIFIER_SCALA_CLI")
    lockfile_path, root = find_lockfile()
    if lockfile_path is None:
        print('scala-cli-purifier: warning: no scala-cli-purifier.lock.json found; running scala-cli impurely. Run `scala-cli-purifier lock` to use Nix-resolved dependencies.', file=sys.stderr)
        os.execv(scala_cli, [scala_cli, *args])
    locked = read_lock(root, lockfile_path)
    cache = prefetched_cache(lockfile_path, locked)
    environment = dict(os.environ, COURSIER_CACHE=str(cache), COURSIER_MODE="offline")
    configure_native_sdk(environment, locked.get("platform", "JVM"))
    command = args[0] if args and not args[0].startswith("-") else None
    java_home = default_java_home([*locked["arguments"], *args[1:]])
    work = lockfile_path.parent / STATE_DIR
    aliases = offline_mirror.lock_aliases(locked["artifacts"])
    mirror_options, server = start_mirror(cache, work / "mirror", aliases, java_home)
    try:
        if command in BUILD_COMMANDS:
            argv = [
                scala_cli,
                *java_properties(work, mirror_options, home_override()),
                command,
                *common_options(java_home, locked.get("platform", "JVM"), offline=True),
                *args[1:],
            ]
        else:
            argv = [scala_cli, *args]
        status = subprocess.run(argv, env=environment, check=False).returncode
        if status == 0 and command == "setup-ide":
            point_bsp_at_wrapper(Path.cwd())
        return status
    finally:
        server.shutdown()


# --- publish --------------------------------------------------------------


def build_release(root, lockfile_path):
    """The release of the project, built by Nix: a Maven repository
    directory of what `scala-cli publish` wrote in the sandbox."""
    lib = env("SCALA_CLI_PURIFIER_LIB")
    native_options = []
    if native_sdk_repository() is not None:
        native_options = ["--argstr", "nativeSdkPath", env("SCALA_CLI_PURIFIER_NATIVE_SDK_PACKAGE")]
    out = subprocess.run(
        [
            "nix-build",
            "--no-out-link",
            f"{lib}/release.nix",
            "--argstr",
            "scalaCliPath",
            env("SCALA_CLI_PURIFIER_SCALA_CLI_PACKAGE"),
            "--argstr",
            "scalaCliVersion",
            env("SCALA_CLI_PURIFIER_SCALA_CLI_VERSION"),
            "--argstr",
            "nixpkgs",
            env("SCALA_CLI_PURIFIER_NIXPKGS"),
            "--argstr",
            "purifierSource",
            env("SCALA_CLI_PURIFIER_PURIFIER"),
            "--argstr",
            "src",
            str(root),
            "--argstr",
            "lockFile",
            str(lockfile_path),
            *native_options,
        ],
        check=True,
        stdout=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    return Path(out.splitlines()[-1])


def publish(args):
    lockfile_path, args = take_lockfile(args)
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
    locked = read_lock(root, lockfile_path)
    if not locked.get("publish"):
        raise PurifierError(f"{lockfile_path} was not made with --publish; run `scala-cli-purifier lock --publish`")
    built = build_release(root, lockfile_path)
    with tempfile.TemporaryDirectory(prefix="scala-cli-purifier-publish-") as tmp:
        staged = Path(tmp) / "release"
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
    errors = (
        PurifierError,
        publication.PublishError,
        lockfile.LockfileError,
        local_repositories.LocalRepositoryError,
    )
    if os.environ.get("SCALA_CLI_PURIFIER_AS_SCALA_CLI"):
        # The package installs this program as `scala-cli` too.
        try:
            return scala_cli_wrapper(argv[1:])
        except errors as e:
            print(f"scala-cli-purifier: {e}", file=sys.stderr)
            return 1
    if len(argv) < 2 or argv[1] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0 if len(argv) >= 2 else 2
    verb, args = argv[1], argv[2:]
    try:
        if verb == "lock":
            lock(args)
        elif verb == "update":
            update(args)
        elif verb == "prefetch":
            prefetch(args)
        elif verb == "publish":
            publish(args)
        else:
            raise PurifierError(f"unknown command '{verb}'; the commands are lock, update, prefetch and publish")
    except (
        PurifierError,
        publication.PublishError,
        lockfile.LockfileError,
        local_repositories.LocalRepositoryError,
    ) as e:
        print(f"scala-cli-purifier: {e}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as e:
        print(f"scala-cli-purifier: {' '.join(map(str, e.cmd[:2]))} failed with exit code {e.returncode}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
