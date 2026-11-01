#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# Runs the hello fixture with `scala-cli-purifier exec`, which realizes its
# lockfile through Nix and runs scala-cli offline. It needs the Nix daemon,
# so it is not a flake check. Run from the repository root.
set -euo pipefail
cli=$(nix build --no-link --print-out-paths '.#cli')/bin/scala-cli-purifier
cd tests/fixtures/hello
rm -rf .scala-cli-purifier .scala-build
# scala-cli keeps caches of its own in the home directory; a temporary home
# keeps the test independent of the caches in the caller's home directory.
home=$(mktemp -d)
trap 'rm -rf "$home"' EXIT
export HOME="$home" XDG_CACHE_HOME="$home/.cache" XDG_CONFIG_HOME="$home/.config" XDG_DATA_HOME="$home/.local/share"
output=$("$cli" exec run .)
echo "$output"
if [ "$output" != '{"hello":"Nix World"}' ]; then
  echo "unexpected output from exec run" >&2
  exit 1
fi
"$cli" exec test .
echo "exec realized $(find .scala-cli-purifier/coursier-cache -type l | wc -l) locked files"
rm -rf .scala-cli-purifier .scala-build
