#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# Tests the commands that need the Nix daemon, so they are not flake
# checks: `exec` on the hello fixture, which realizes its lockfile through
# Nix and runs scala-cli offline, and `publish` of the library fixture,
# which builds its publication with Nix and copies it to a repository
# directory or writes a Maven Central bundle. Run from the repository root.
set -euo pipefail
cli=$(nix build --no-link --print-out-paths '.#cli')/bin/scala-cli-purifier
root=$PWD

# scala-cli keeps caches of its own in the home directory; a temporary home
# keeps the test independent of the caches in the caller's home directory.
home=$(mktemp -d)
trap 'rm -rf "$home"' EXIT
export HOME="$home" XDG_CACHE_HOME="$home/.cache" XDG_CONFIG_HOME="$home/.config" XDG_DATA_HOME="$home/.local/share"

cd "$root/tests/fixtures/hello"
rm -rf .scala-cli-purifier .scala-build
output=$("$cli" exec run .)
echo "$output"
if [ "$output" != '{"hello":"Nix World"}' ]; then
  echo "unexpected output from exec run" >&2
  exit 1
fi
"$cli" exec test .
echo "exec realized $(find .scala-cli-purifier/coursier-cache -type l | wc -l) locked files"
rm -rf .scala-cli-purifier .scala-build

# A url= dependency resolves offline through the offline mirror.
cd "$root/tests/fixtures/url"
rm -rf .scala-cli-purifier .scala-build
output=$("$cli" exec run .)
echo "$output"
if [ "$output" != 'geny from a URL: 2,4,6' ]; then
  echo "unexpected output from exec run of the url fixture" >&2
  exit 1
fi
rm -rf .scala-cli-purifier .scala-build

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
