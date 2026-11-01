#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# Tests the commands that need the Nix daemon, so they are not flake
# checks: `prefetch` of the hello and url fixtures and the everyday loop on
# the scala-cli wrapper, an external lockfile, a `file:` repository, and
# `publish` of the library fixture, which builds its release with Nix and
# copies it to a repository directory or writes a Maven Central bundle.
# Run from the repository root; the lock of the scratch project needs the
# network.
set -euo pipefail
bin=$(nix build --no-link --print-out-paths '.#cli')/bin
cli=$bin/scala-cli-purifier
scala_cli=$bin/scala-cli
root=$PWD

# scala-cli keeps caches of its own in the home directory; a temporary home
# keeps the test independent of the caches in the caller's home directory.
home=$(mktemp -d)
trap 'rm -rf "$home"' EXIT
export HOME="$home" XDG_CACHE_HOME="$home/.cache" XDG_CONFIG_HOME="$home/.config" XDG_DATA_HOME="$home/.local/share"

# Runs the program of the project in the current directory through the
# wrapper and requires the output $1.
expect_run() {
  local output
  output=$("$scala_cli" run .)
  echo "$output"
  if [ "$output" != "$1" ]; then
    echo "unexpected output from scala-cli run in $PWD" >&2
    exit 1
  fi
}

# Before the prefetch, the wrapper says what to run.
cd "$root/tests/fixtures/hello"
rm -rf .scala-cli-purifier .scala-build .bsp
if "$scala_cli" compile . 2> "$home/unprefetched.log"; then
  echo "the wrapper ran without a prefetch" >&2
  exit 1
fi
grep -q 'scala-cli-purifier prefetch' "$home/unprefetched.log"

# The everyday loop and the editor on the prefetched downloads.
"$cli" prefetch
test -L .scala-cli-purifier/downloads
expect_run '{"hello":"Nix World"}'
"$scala_cli" test .
"$scala_cli" setup-ide .
python3 -I "$root/pkgs/cli/bsp.py" .
rm -rf .scala-cli-purifier .scala-build .bsp

# A url= dependency resolves offline through the offline mirror, in the
# loop and in the build server.
cd "$root/tests/fixtures/url"
rm -rf .scala-cli-purifier .scala-build .bsp
"$cli" prefetch
expect_run 'hiccup from a URL: true'
"$scala_cli" setup-ide .
python3 -I "$root/pkgs/cli/bsp.py" .
rm -rf .scala-cli-purifier .scala-build .bsp

# An external lockfile: the project holds only its sources, and the
# lockfile lies in a packaging directory that SCALA_CLI_PURIFIER_LOCKFILE
# names.
packaging="$home/packaging"
external="$home/external-project"
mkdir -p "$packaging" "$external"
cp "$root"/tests/fixtures/hello/*.scala "$external"
cp "$root/tests/fixtures/hello/scala-cli-purifier.lock.json" "$packaging"
(
  cd "$external"
  export SCALA_CLI_PURIFIER_LOCKFILE="$packaging/scala-cli-purifier.lock.json"
  "$cli" prefetch
  test -L "$packaging/.scala-cli-purifier/downloads"
  expect_run '{"hello":"Nix World"}'
)

# A `file:` repository outside the project: lock a scratch project that
# names one by its absolute path, then build it in the sandbox with the
# repository given as localRepositories.<name>.
repository="$home/shared-repository"
project="$home/file-repository-project"
mkdir -p "$repository" "$project"
cp -r "$root/tests/fixtures/local-home/.m2/repository/." "$repository"
cat > "$project/Use.scala" <<EOF
//> using scala 3.3.4
//> using repository file://$repository
//> using dep example.local::whisper:0.1.0
@main def main(): Unit = println(whisper.whisper("FROM A FILE REPOSITORY"))
EOF
(cd "$project" && "$cli" lock)
name=$(python3 -I -c 'import json, sys; print(json.load(open(sys.argv[1]))["localRepositories"][0]["name"])' "$project/scala-cli-purifier.lock.json")
echo "the lock names the file: repository $name"
built=$(nix build --no-link --print-out-paths --impure --expr "
  let
    flake = builtins.getFlake \"git+file://$root\";
    pkgs = flake.inputs.nixpkgs.legacyPackages.\${builtins.currentSystem}.extend flake.overlays.default;
  in
  (pkgs.buildScalaCliProject {
    src = $project;
    localRepositories.\"$name\" = $repository;
  }).packages.file-repository-project")
output=$("$built/bin/file-repository-project")
echo "$output"
if [ "$output" != 'from a file repository...' ]; then
  echo "unexpected output from the build of the file: repository project" >&2
  exit 1
fi

cd "$root/tests/fixtures/library"
repo="$home/repository"
"$cli" publish "$repo"
version=io/github/nix-purifiers/example/greeting_3/1.0.0
for f in greeting_3-1.0.0.jar greeting_3-1.0.0.pom greeting_3-1.0.0-sources.jar greeting_3-1.0.0-javadoc.jar; do
  test -f "$repo/$version/$f" || { echo "publish did not write $f" >&2; exit 1; }
done
echo "publish wrote $(find "$repo" -type f | wc -l) files to a repository directory"
"$cli" publish --bundle "$home/bundle.zip"
python3 -I -c 'import sys, zipfile; names = zipfile.ZipFile(sys.argv[1]).namelist(); assert any(n.endswith(".jar.sha256") for n in names), names; print(f"publish --bundle wrote {len(names)} files")' "$home/bundle.zip"
