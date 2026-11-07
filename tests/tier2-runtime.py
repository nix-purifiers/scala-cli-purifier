#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Retain exact-source evidence for fresh capture, prefetch and sandbox builds.

Run from any directory with a new, external results directory as the argument.
Networked capture is separate from ordinary wrapper execution in a Nix sandbox.
The report describes this system and these SDK points only.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


root = Path(__file__).resolve().parent.parent
results = Path(sys.argv[1]).resolve()
results.mkdir(parents=True, exist_ok=False)
report = {"commands": [], "complete": False}


def save():
    (results / "report.json").write_text(json.dumps(report, indent=2) + "\n")


def run(name, args, cwd=root, env=None):
    command = [str(arg) for arg in args]
    entry = {"name": name, "command": command, "cwd": str(cwd),
             "started": time.time(), "log": name + ".log"}
    report["commands"].append(entry)
    save()
    with (results / entry["log"]).open("w") as log:
        process = subprocess.run(command, cwd=cwd, env=env, text=True,
                                 stdout=log, stderr=subprocess.STDOUT)
    entry.update(returncode=process.returncode, finished=time.time())
    save()
    if process.returncode:
        raise RuntimeError(f"{name} failed: see {results / entry['log']}")
    return (results / entry["log"]).read_text()


def output(*args):
    return subprocess.check_output(args, cwd=root, text=True).strip()


report.update(revision=output("git", "rev-parse", "HEAD"),
              dirty=output("git", "status", "--porcelain"),
              system=output("nix", "eval", "--impure", "--raw", "--expr", "builtins.currentSystem"))
metadata = json.loads(output("nix", "flake", "metadata", "--json", "--no-write-lock-file", "path:" + str(root)))
snapshot = metadata["path"]
report.update(source=snapshot, sourceNarHash=output("nix", "hash", "path", snapshot))
run("retain-source", ["nix-store", "--add-root", results / "source", "--realise", snapshot])
cli = Path(run("build-cli", ["nix", "build", "path:" + snapshot + "#cli", "--print-out-paths",
                            "--out-link", results / "cli"]).strip().splitlines()[-1])
project = results / "project"
shutil.copytree(Path(snapshot) / "tests/fixtures/hello", project)
for path in (project, *project.rglob("*")):
    path.chmod(path.stat().st_mode | 0o200)
(project / "scala-cli-purifier.lock.json").unlink()
scala_version = os.environ.get("SCALA_CLI_TIER2_SCALA_VERSION", "3.3.7")
directives = project / "project.scala"
directives.write_text(directives.read_text().replace("scala 3.3.4", "scala " + scala_version))
# This source syntax works on each explicitly selected Scala 2/3 compiler.
(project / "Hello.scala").unlink()
(project / "Main.scala").write_text('''package hello
object Main {
  def greeting: String = upickle.default.write(Map("hello" -> "Nix World"))
  def main(args: Array[String]): Unit = println(greeting)
}
''')
for path in project.glob("*.scala"):
    if "class HelloSuite" in path.read_text():
        path.write_text(path.read_text().replace("assertEquals(greeting,", "assertEquals(Main.greeting,"))
report["scalaVersion"] = scala_version
environment = dict(os.environ, SCALA_CLI_PURIFIER_MAVEN_CENTRAL_MIRROR="https://repo.maven.apache.org/maven2")
run("capture", [cli / "bin/scala-cli-purifier", "lock", "--test", "--no-ide", "--name", "hello"], project, environment)
lock = project / "scala-cli-purifier.lock.json"
report.update(lockSha256=hashlib.sha256(lock.read_bytes()).hexdigest(),
              sdk={key: json.loads(lock.read_text())[key] for key in ("scalaCliVersion", "javaVersion")})
compiler = "scala3-compiler_3" if scala_version.startswith("3.") else "scala-compiler"
if not any(f"/{compiler}/{scala_version}/{compiler}-{scala_version}.jar" in artifact["path"]
           for artifact in json.loads(lock.read_text())["artifacts"]):
    raise RuntimeError("capture did not record the requested Scala compiler")
run("relock", [cli / "bin/scala-cli-purifier", "lock", "--test", "--no-ide", "--name", "hello"], project, environment)
if hashlib.sha256(lock.read_bytes()).hexdigest() != report["lockSha256"]:
    raise RuntimeError("relocking changed the freshly captured lock")
run("prefetch", [cli / "bin/scala-cli-purifier", "prefetch"], project, environment)
# Only source and the prefetch state enter the sandbox, never native warm caches.
for name in (".scala-build", ".bsp"):
    shutil.rmtree(project / name, ignore_errors=True)
project_store = run("retain-project", ["nix", "store", "add-path", project]).strip().splitlines()[-1]
report.update(projectSource=project_store, projectNarHash=output("nix", "hash", "path", project_store))
downloads = (project / ".scala-cli-purifier/downloads").resolve()
report["downloads"] = str(downloads)
system = report["system"]
common = f'''let
  f = builtins.getFlake "path:{snapshot}?narHash={report['sourceNarHash']}";
  pkgs = import f.inputs.nixpkgs {{ system = "{system}"; overlays = [ f.overlays.default ]; }};
in '''
expression = common + f'''pkgs.runCommand "scala-cli-fresh-prefetch-offline" {{
  nativeBuildInputs = [ f.packages.{system}.cli pkgs.python3 ];
  downloads = {downloads};
}} ''
  export HOME=$TMPDIR/home
  mkdir -p "$HOME"
  export SCALA_CLI_HOME=$HOME/.scala-cli SCALA_CLI_PURIFIER_USER_HOME=$HOME
  export JAVA_TOOL_OPTIONS=-Duser.home=$HOME
  python3 - <<'PY'
  import socket
  try:
      connection = socket.create_connection(("1.1.1.1", 443), timeout=2)
  except OSError:
      print("external network unavailable in runtime sandbox")
  else:
      connection.close()
      raise SystemExit("runtime sandbox unexpectedly permits external network")
  PY
  cp -R ${{{project_store}}} project
  chmod -R u+w project
  cd project
  scala-cli compile .
  scala-cli test . | tee test.log
  grep -F '+ greeting is JSON' test.log
  scala-cli run . > run.log
  grep -qxF '{{"hello":"Nix World"}}' run.log
  mkdir "$out"
  cp run.log "$out/"
  cp test.log "$out/"
  rm -rf .scala-cli-purifier
  if scala-cli compile . > missing.log 2>&1; then
    echo 'missing prefetch unexpectedly succeeded' >&2
    exit 1
  fi
  grep -F 'scala-cli-purifier prefetch' missing.log
  cp missing.log "$out/"
'' '''
pure = f'(pkgs.scala-cli-purifier.buildScalaCliProject {{ src = {project_store}; }}).packages.hello'
pure_runtime = common + f'''let package = {pure}; in
  pkgs.runCommand "scala-cli-pure-runtime" {{}} ''
    ${{package}}/bin/hello > output
    grep -qxF '{{"hello":"Nix World"}}' output
    mkdir "$out"
    cp output "$out/"
  '' '''
for name, expression in (
    ("ordinary-offline", expression),
    ("pure-build", common + pure),
    ("pure-runtime", pure_runtime),
):
    path = results / (name + ".nix")
    path.write_text(expression)
    command = ["nix", "build", "--expr", expression, "--print-build-logs", "--option", "sandbox", "true",
               "--out-link", results / name]
    run(name, command)
    if name != "pure-build":
        run(name + "-rebuild", [*command, "--rebuild"])
    built = str((results / name).resolve())
    report.setdefault("derivations", {})[name] = {
        "derivation": output("nix-store", "--query", "--deriver", built),
        "output": built, "narHash": output("nix", "hash", "path", built)}
    run(name + "-derivation", ["nix", "derivation", "show", report["derivations"][name]["derivation"]])
if run("pure-executable", [results / "pure-build/bin/hello"], results).strip() != '{"hello":"Nix World"}':
    raise RuntimeError("pure executable returned the wrong dependency-derived value")
sample_command = ["nix", "build", "path:" + snapshot + f"#checks.{system}.example-besom-core",
                  "--print-build-logs", "--out-link", results / "besom-core"]
run("independent-sample", sample_command)
run("independent-sample-rebuild", [*sample_command, "--rebuild"])
built = str((results / "besom-core").resolve())
report["derivations"]["independent-sample"] = {
    "attribute": f"checks.{system}.example-besom-core",
    "derivation": output("nix-store", "--query", "--deriver", built),
    "output": built, "narHash": output("nix", "hash", "path", built)}
run("independent-sample-derivation", ["nix", "derivation", "show",
                                    report["derivations"]["independent-sample"]["derivation"]])
report["sample"] = {"repository": "https://github.com/VirtusLab/besom",
                    "revision": "6017c8814ac885df3c10d06969d90e2c2d8b3f9d",
                    "scope": "core compilation, class presence and NonEmptyString runtime acceptance/rejection"}
report["complete"] = True
save()
print(results / "report.json")
