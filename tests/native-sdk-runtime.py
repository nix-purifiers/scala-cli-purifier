#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Verify a source-built Native SDK with the official JVM frontend."""
import base64
import functools
import hashlib
import http.server
import json
import os
from pathlib import Path
import re
import shutil
import shlex
import socket
import signal
import time
import subprocess
import sys
import threading
from urllib.parse import urlsplit

root = Path(__file__).resolve().parent.parent
results = Path(sys.argv[1]).resolve()
results.mkdir(parents=True)
report = {"commands": [], "complete": False}
deadline = time.monotonic() + 9000


def save():
    (results / "report.json").write_text(json.dumps(report, indent=2) + "\n")


def run(name, *args, cwd=root, env=None):
    command = list(map(str, args))
    if command[:2] == ["nix", "build"]:
        command += ["--option", "sandbox", "true", "--option", "max-jobs", "2",
                    "--option", "cores", "2", "--option", "eval-cache", "false"]
    entry = {"name": name, "argv": command, "cwd": str(cwd), "log": name + ".log"}
    report["commands"].append(entry)
    save()
    path = results / entry["log"]
    with path.open("w") as log:
        budget = 5400 if name == "sdk" else 1200
        budget = min(budget, deadline - time.monotonic())
        if budget <= 0:
            raise TimeoutError("Native SDK evidence deadline reached")
        process = subprocess.Popen(command, cwd=cwd, env=env, stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        try:
            process.wait(timeout=budget)
        except subprocess.TimeoutExpired:
            entry["timedOut"] = True
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        entry["returncode"] = process.returncode
        save()
    if entry.get("timedOut"):
        raise TimeoutError(f"{name} exceeded its execution budget")
    if process.returncode:
        raise subprocess.CalledProcessError(process.returncode, command)
    return path.read_text().strip()


def build(name, expression):
    path = results / (name + ".nix")
    path.write_text(expression + "\n")
    return Path(run(name, "nix", "build", "--impure", "--file", path, "--print-build-logs",
                    "--out-link", results / name, "--print-out-paths").splitlines()[-1])


def identify(name, output):
    drv = run(name + "-deriver", "nix-store", "-q", "--deriver", output)
    run(name + "-derivation", "nix", "derivation", "show", drv)
    return {"output": str(output), "derivation": drv,
            "narHash": run(name + "-nar", "nix", "hash", "path", output)}


revision = run("revision", "git", "rev-parse", "HEAD")
if run("tracked-status", "git", "status", "--porcelain", "--untracked-files=no"):
    raise RuntimeError("commit the source before collecting evidence")
system = run("system", "nix", "eval", "--impure", "--raw", "--expr", "builtins.currentSystem")
assert system == os.environ.get("NATIVE_SDK_SYSTEM", system)
assert run("sandbox", "nix", "config", "show", "sandbox", "--option", "sandbox", "true") == "true"
reference = f"git+file://{root}?ref=HEAD&rev={revision}"
snapshot = Path(json.loads(run("archive", "nix", "flake", "archive", "--json",
                               "--no-update-lock-file", reference))["path"])
run("retain-source", "nix-store", "--add-root", results / "source", "--realise", snapshot)
report.update(revision=revision, system=system, source=str(snapshot),
              sourceNarHash=run("source-nar", "nix", "hash", "path", snapshot))
common = f'''let
  f = builtins.getFlake "path:{snapshot}?narHash={report['sourceNarHash']}";
  pkgs = import f.inputs.nixpkgs {{ system = "{system}"; overlays = [ f.overlays.default ]; }};
in '''
sdk = build("sdk", common + 'pkgs.callPackage (f.outPath + "/pkgs/native-sdk") { checkpoint = "sdk"; }')
publication = json.loads((sdk / "receipts/publication.json").read_text())
variant = publication["variant"]
shutil.copyfile(sdk / "receipts/publication.json", results / "publication.json")
report["nativeSDK"] = identify("sdk", sdk)
cli = Path(run("cli", "nix", "build", f"path:{snapshot}#cli-jvm", "--print-out-paths",
               "--out-link", results / "cli").splitlines()[-1]) / "bin/scala-cli-purifier"
frontend = Path(run("frontend", "nix", "build", f"path:{snapshot}#jvm-sdk", "--print-out-paths",
                    "--out-link", results / "frontend").splitlines()[-1])
report["initialFrontend"] = json.loads((frontend / "share/scala-cli-jvm-sdk/manifest.json").read_text())
fixture = snapshot / "tests/fixtures/native"
approved = json.loads((snapshot / "tests/native-sdk-external-paths.json").read_text())
downloads = build("approved", common + f'''f.lib.purifier.tree {{
  inherit pkgs;
  name = "native-sdk-fixture-dependencies";
  entries = builtins.filter (e: builtins.elem e.path (builtins.fromJSON
    (builtins.readFile (f.outPath + "/tests/native-sdk-external-paths.json"))))
    (f.lib.purifier.entriesFor "{system}" (f.lib.scala-cli-purifier.readLock {{
      src = f.outPath + "/tests/fixtures/native";
    }}));
}}''')
repository = results / "approved-repository"
repository.mkdir()
approved_hashes = {}
for path in approved:
    relative = path.split("/maven2/", 1)[1]
    destination = repository / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(downloads / path, destination)
    approved_hashes[relative] = "sha256-" + base64.b64encode(
        hashlib.sha256(destination.read_bytes()).digest()).decode()

project = results / "project"
project.mkdir()
for source in fixture.glob("*.scala"):
    shutil.copyfile(source, project / source.name)
home = results / "capture-home"
home.mkdir()


class RepositoryHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


server = http.server.ThreadingHTTPServer(
    ("127.0.0.1", 0), functools.partial(RepositoryHandler, directory=repository))
with server:
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    environment = dict(os.environ, COURSIER_REPOSITORIES=endpoint,
                       SCALA_CLI_PURIFIER_USER_HOME=str(home))
    try:
        run("capture", cli, "lock", "--test", "--no-ide", "--name", "native-sdk-fixture",
            "--", "--verbose", "--native-version", variant, "--repository", (sdk / "maven").as_uri(),
            "--repository", endpoint, ".", cwd=project, env=environment)
    finally:
        server.shutdown()
        worker.join()
with socket.socket() as probe:
    assert probe.connect_ex(("127.0.0.1", server.server_port)) != 0
report["fixtureRepositoryStopped"] = endpoint
lockfile = project / "scala-cli-purifier.lock.json"
lock = json.loads(lockfile.read_text())
assert lock["version"] == 1
shutil.copyfile(lockfile, results / lockfile.name)
seed = results / "seed"
seed.mkdir()
seen = set()
for entry in lock["artifacts"]:
    url = urlsplit(entry["url"])
    assert f"{url.scheme}://{url.netloc}" == endpoint
    relative = url.path.lstrip("/")
    assert entry["hash"] == approved_hashes[relative]
    assert relative not in seen
    seen.add(relative)
    source = repository / relative
    target = seed / source.name
    shutil.copyfile(source, target)
    run("seed-" + str(len(seen)), "nix-store", "--add-fixed", "sha256", target)
assert seen == approved_hashes.keys()
shutil.rmtree(repository)
shutil.rmtree(seed)
run("prefetch", cli, "prefetch", cwd=project)
project_store = run("retain-project", "nix", "store", "add-path", project).splitlines()[-1]
package_expression = common.removesuffix("in ") + f'''
  sdk = builtins.storePath "{sdk}";
  project = pkgs.scala-cli-purifier-jvm.buildScalaCliProject {{
    src = builtins.storePath "{project_store}";
    localRepositories.maven = sdk + "/maven";
  }};
in '''
policy = build("network-policy", common + """pkgs.runCommand "native-sdk-network-policy" {
  __darwinAllowLocalNetworking = pkgs.stdenv.buildPlatform.isDarwin;
  nativeBuildInputs = [ pkgs.python3 ];
} ''
  python3 ${f.outPath + "/tests/local-networking.py"} > "$out" 2>&1
'' """)
report["networkPolicy"] = identify("network-policy", policy)
package = build("pure-package", package_expression + "project.packages.native-sdk-fixture")
tests = build("pure-tests", package_expression + "project.tests")
report["package"] = identify("pure-package", package)
report["tests"] = identify("pure-tests", tests)
run("pure-package-rebuild", "nix", "build", "--impure", "--file",
    results / "pure-package.nix", "--rebuild", "--print-build-logs",
    "--out-link", results / "pure-package")

assert run("native-executable", package / "bin/native-sdk-fixture") == "hello from Scala Native"
test_log = (tests / "test.log").read_text()
assert "Starting process " in test_log and "FrameworkAdapter" in test_log
assert "+ greeting names Scala Native" in re.sub(r"\x1b\[[0-9;]*m", "", test_log)
shutil.copyfile(tests / "test.log", results / "native-test.log")
sdk_artifacts = {row["artifact"]: row for row in publication["artifacts"]}
locked_sdk = {row["path"]: row["hash"] for repo in lock["localRepositories"]
              if repo["name"] == "maven" for row in repo["files"]}
selected = set()
classpaths = []
for phase in ("pure-package", "pure-tests"):
    log = results / (phase + ".log")
    prefix = ("native-sdk-fixture-tests-locked> " if phase == "pure-tests"
              else "native-sdk-fixture-locked> ")
    for line_number, line in enumerate(log.read_text().splitlines(), 1):
        if not line.startswith(prefix + "Running "):
            continue
        arguments = shlex.split(line.removeprefix(prefix + "Running "))
        if "dotty.tools.dotc.Main" not in arguments and not any(
                "scala-native-cli_2.12" in argument for argument in arguments):
            continue
        assert "/openjdk/bin/java" in arguments[0] or arguments[0].endswith("/bin/java")
        paths = []
        for index, argument in enumerate(arguments):
            if argument in ("-cp", "-classpath", "--classpath"):
                paths.extend(arguments[index + 1].split(os.pathsep))
            if argument.startswith("-Xplugin:"):
                paths.extend(argument.removeprefix("-Xplugin:").split(os.pathsep))
        chosen = []
        for index, path in enumerate(paths):
            if "/org/scala-native/" not in path or not path.endswith(".jar"):
                continue
            relative = "org/scala-native/" + path.split("/org/scala-native/", 1)[1]
            row = sdk_artifacts["maven/" + relative]
            assert variant in row["version"]
            digest = hashlib.sha256((sdk / "maven" / relative).read_bytes()).digest()
            assert digest.hex() == row["sha256"]
            if not path.startswith(str(sdk / "maven") + "/"):
                assert "/local-repositories/maven/" in path
                assert locked_sdk[relative] == "sha256-" + base64.b64encode(digest).decode()
            selected.add(row["module"])
            chosen.append({"index": index, "path": path, "coordinate": [
                "org.scala-native", row["module"], row["version"]], "canonicalSDKHash": digest.hex(),
                           "binding": "logged selection joined to immutable SDK or verified staged lock entry"})
        classpaths.append({"phase": phase, "line": line_number, "arguments": arguments,
                           "selectedNativeArtifacts": chosen})
expected = {
    "auxlib_native0.5_3", "clib_native0.5_3", "javalib_native0.5_3",
    "nativelib_native0.5_3", "posixlib_native0.5_3", "windowslib_native0.5_3",
    "test-interface_native0.5_3", "test-interface-sbt-defs_native0.5_3",
    "nscplugin_3.3.4", "tools_2.12", "nir_2.12", "util_2.12",
    "scala-native-cli_2.12", "scala3lib_native0.5_3", "scalalib_native0.5_2.13",
}
assert selected == expected, selected
(results / "classpaths.json").write_text(json.dumps(classpaths, indent=2) + "\n")
report["complete"] = True
save()
