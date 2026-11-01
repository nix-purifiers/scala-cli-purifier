#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# The conformance-relock check of the purifier contract, which needs the
# network, so CI runs it as a job: relocks a copy of every fixture and
# requires that each new lockfile is the committed one, byte for byte. A
# relock keeps the version listings a lockfile holds inline (the sources
# and js fixtures hold some), so those fixtures reproduce too. Run from the
# repository root.
set -euo pipefail
copy=$(mktemp -d)
trap 'rm -rf "$copy"' EXIT
cp -r tests/fixtures/. "$copy"
chmod -R u+w "$copy"
fixtures=(hello script sources url local js native library)
FIXTURES="$copy" bash tests/relock-fixtures.sh "${fixtures[@]}"
status=0
for fixture in "${fixtures[@]}"; do
  if cmp -s "tests/fixtures/$fixture/scala-cli-purifier.lock.json" "$copy/$fixture/scala-cli-purifier.lock.json"; then
    echo "$fixture: the relock reproduces the lockfile"
  else
    echo "$fixture: the relock differs from the committed lockfile:" >&2
    diff "tests/fixtures/$fixture/scala-cli-purifier.lock.json" "$copy/$fixture/scala-cli-purifier.lock.json" | head -40 >&2 || true
    status=1
  fi
done
exit "$status"
