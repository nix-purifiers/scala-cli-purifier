#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# Re-locks the test fixtures with the scala-cli-purifier command of this
# checkout: the fixtures named as arguments, or all of them. Run from the
# repository root; it needs the network. FIXTURES names another copy of
# tests/fixtures to relock.
#
# The lock runs download Maven Central's files through Google's mirror,
# which the lockfiles record as each file's fallback URL.
#
# The native scala-cli writes Scala.js linker output and publications to
# /tmp. On a machine whose /tmp is read-only, point
# SCALA_CLI_PURIFIER_SCALA_CLI at a launcher of the JVM build of the same
# scala-cli version (org.virtuslab.scala-cli:cli_3), which reads
# java.io.tmpdir.
set -euo pipefail
cli=$(nix build --no-link --print-out-paths '.#cli')/bin/scala-cli-purifier
fixtures=$(cd "${FIXTURES:-tests/fixtures}" && pwd)
export SCALA_CLI_PURIFIER_MAVEN_CENTRAL_MIRROR=https://maven-central.storage-download.googleapis.com/maven2

lock() {
  local fixture=$1
  shift
  echo "== $fixture" >&2
  (cd "$fixtures/$fixture" && "$cli" lock "$@")
}

names=("$@")
[ ${#names[@]} -gt 0 ] || names=(hello script sources url local js native library)
for fixture in "${names[@]}"; do
  case $fixture in
    hello | js | native) lock "$fixture" --test ;;
    library) lock "$fixture" --publish ;;
    # The local fixture reads ivy2Local and m2Local of local-home
    # (tests/local-libraries/publish.sh writes them).
    local) SCALA_CLI_PURIFIER_USER_HOME="$fixtures/local-home" lock "$fixture" ;;
    *) lock "$fixture" ;;
  esac
done
