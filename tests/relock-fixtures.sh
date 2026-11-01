#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# Re-locks the test fixtures with the scala-cli-purifier command of this
# checkout: the fixtures named as arguments, or all of them. Run from the
# repository root; it needs the network.
#
# The native scala-cli writes Scala.js linker output and publications to
# /tmp. On a machine whose /tmp is read-only, point
# SCALA_CLI_PURIFIER_SCALA_CLI at a launcher of the JVM build of the same
# scala-cli version (org.virtuslab.scala-cli:cli_3), which reads
# java.io.tmpdir.
set -euo pipefail
cli=$(nix build --no-link --print-out-paths '.#cli')/bin/scala-cli-purifier

lock() {
  local fixture=$1
  shift
  echo "== $fixture" >&2
  (cd "tests/fixtures/$fixture" && "$cli" lock "$@")
}

fixtures=("$@")
[ ${#fixtures[@]} -gt 0 ] || fixtures=(hello script sources url local js native library)
for fixture in "${fixtures[@]}"; do
  case $fixture in
    hello | js | native) lock "$fixture" --test ;;
    library) lock "$fixture" --publish ;;
    # The local fixture reads ivy2Local and m2Local of tests/fixtures/local-home
    # (tests/local-libraries/publish.sh writes them).
    local) SCALA_CLI_PURIFIER_USER_HOME="$PWD/tests/fixtures/local-home" lock "$fixture" ;;
    *) lock "$fixture" ;;
  esac
done
