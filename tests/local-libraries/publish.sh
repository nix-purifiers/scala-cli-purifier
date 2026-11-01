#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# Publishes the libraries of tests/local-libraries into the home the local
# fixture is locked with, tests/fixtures/local-home: shout into its
# .ivy2/local (scala-cli publish local) and whisper into its .m2/repository.
# Run from the repository root; it needs the network. On a machine whose
# /tmp is read-only, point SCALA_CLI at a launcher of the JVM build of
# scala-cli, as for tests/relock-fixtures.sh.
set -euo pipefail
scala_cli=${SCALA_CLI:-$(nix build --no-link --print-out-paths 'nixpkgs#scala-cli')/bin/scala-cli}
home="$PWD/tests/fixtures/local-home"
rm -rf "$home"
mkdir -p "$home/.m2/repository"

# A scratch home and coursier cache, and the proxy of the environment as
# the Java options scala-cli reads before its subcommand.
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
export HOME="$scratch" XDG_CACHE_HOME="$scratch/.cache" XDG_CONFIG_HOME="$scratch/.config" XDG_DATA_HOME="$scratch/.local/share"
export COURSIER_CACHE="$scratch/coursier"
read -r -a props < <(python3 -I -c 'import sys; sys.path.insert(0, "pkgs/cli"); import coursier_cache; print(" ".join(coursier_cache.java_proxy_properties()))')

(cd tests/local-libraries/shout && "$scala_cli" "${props[@]}" publish local --power --server=false --ivy2-home "$home/.ivy2" .)
(cd tests/local-libraries/whisper && "$scala_cli" "${props[@]}" publish --power --server=false --publish-repository "file://$home/.m2/repository" .)
rm -rf tests/local-libraries/*/.scala-build
