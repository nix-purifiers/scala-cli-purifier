#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# The conformance-relock check of the purifier contract, which needs the
# network, so CI runs it as a job: relocks a copy of every fixture and
# requires byte identity when the selected SDK matches the recorded SDK.
# Darwin may select a different Java patch from the same pinned package
# set: require that exact executable's queried SDK and a valid checksum,
# with every other lock field unchanged. A provided Native SDK removes only
# its published files from ordinary downloads. The second configured relock
# must be byte-identical. Original locks stay untouched. A
# relock keeps the version listings a lockfile holds inline (the sources
# and js fixtures hold some), so those fixtures reproduce too. Run from the
# repository root.
set -euo pipefail
copy=$(mktemp -d)
trap 'rm -rf "$copy"' EXIT
system=$(nix eval --impure --raw --expr builtins.currentSystem)
toolchain=$(nix build --no-link --print-out-paths ".#checks.$system.toolchain-receipt" \
  --option sandbox true --option max-jobs 2 --option cores 2 --option eval-cache false)
native_sdk=$(nix eval --offline --impure --raw --expr '
  let f = builtins.getFlake (toString ./.);
      sdk = f.packages.${builtins.currentSystem}.cli.configuredNativeSDK;
  in if sdk == null then "" else toString sdk')
if [ -n "$native_sdk" ] && [ -n "${NATIVE_SDK_DIAGNOSTIC_DIR:-}" ]; then
  nix build --no-link ".#cli.configuredNativeSDK" \
    --option sandbox true --option max-jobs 2 --option cores 2 --option eval-cache false
  python3 tests/native-sdk-snapshot.py "$native_sdk" "$NATIVE_SDK_DIAGNOSTIC_DIR"
fi
cp -r tests/fixtures/. "$copy"
chmod -R u+w "$copy"
fixtures=(hello script sources url local js native library)
FIXTURES="$copy" bash tests/relock-fixtures.sh "${fixtures[@]}"
status=0
for fixture in "${fixtures[@]}"; do
  if python3 -B tests/relock-compare.py "tests/fixtures/$fixture/scala-cli-purifier.lock.json" \
    "$copy/$fixture/scala-cli-purifier.lock.json" "$toolchain/versions.json" "$system" "$native_sdk"; then
    echo "$fixture: the relock reproduces the expected native SDK lock"
  else
    echo "$fixture: the relock differs from the committed lockfile:" >&2
    diff "tests/fixtures/$fixture/scala-cli-purifier.lock.json" "$copy/$fixture/scala-cli-purifier.lock.json" | head -40 >&2 || true
    status=1
  fi
done
if [ "$status" -eq 0 ] && [ -n "$native_sdk" ]; then
  cp "$copy/native/scala-cli-purifier.lock.json" "$copy/native-before.json"
  FIXTURES="$copy" bash tests/relock-fixtures.sh native
  python3 -B tests/relock-compare.py "$copy/native-before.json" \
    "$copy/native/scala-cli-purifier.lock.json" "$toolchain/versions.json" "$system" "$native_sdk"
  cmp "$copy/native-before.json" "$copy/native/scala-cli-purifier.lock.json"
fi
exit "$status"
