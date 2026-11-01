#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# Relocks a copy of the fixtures and requires that each new lockfile is the
# committed one, byte for byte. Run from the repository root; it needs the
# network.
#
# The sources and js fixtures are left out: their lockfiles hold version
# listings inline, whose content changes upstream whenever a version is
# published.
set -euo pipefail
copy=$(mktemp -d)
trap 'rm -rf "$copy"' EXIT
cp -r tests/fixtures/. "$copy"
chmod -R u+w "$copy"
fixtures=(hello script url local native library)
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
