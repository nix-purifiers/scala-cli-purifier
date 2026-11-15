#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
#
# The conformance-update check of the purifier contract, which needs the
# network, so CI runs it as a job. It copies the sources fixture, whose
# dependency com.lihaoyi::sourcecode has the version range [0.3.0,0.4.0),
# and trims the version listing in its lockfile to the versions up to
# 0.3.0, as if the lock had been made before 0.3.1 was published. Then:
#
#   lock      keeps the listing, so the lockfile still holds sourcecode 0.3.0
#   update    downloads the listing again, so the lockfile holds 0.3.1
#
# and after each, the lockfile passes the stale check of readLock. Run from
# the repository root.
set -euo pipefail
root=$PWD
system=$(nix eval --impure --raw --expr builtins.currentSystem)
cli=$(nix build --no-link --print-out-paths ".#checks.$system.fixture-cli")/bin/scala-cli-purifier
# The physical path: Nix refuses a path with a symlink in it, such as
# macOS's /var, which mktemp's directory is under.
work=$(mktemp -d)
work=$(cd "$work" && pwd -P)
trap 'rm -rf "$work"' EXIT
export SCALA_CLI_PURIFIER_MAVEN_CENTRAL_MIRROR=${SCALA_CLI_PURIFIER_MAVEN_CENTRAL_MIRROR-https://maven-central.storage-download.googleapis.com/maven2}

cp -r "$root/tests/fixtures/sources" "$work/sources"
chmod -R u+w "$work/sources"
project=$work/sources
lockfile=$project/scala-cli-purifier.lock.json

python3 -I - "$root/pkgs/cli" "$lockfile" <<'EOF'
import json, re, sys
sys.path.insert(0, sys.argv[1])
import lockfile
path = sys.argv[2]
data = json.load(open(path))
for entry in data["artifacts"]:
    if entry["path"].endswith("com/lihaoyi/sourcecode_3/maven-metadata.xml"):
        def keep(m):
            major, minor, patch = (int(x) for x in re.match(r"(\d+)\.(\d+)\.(\d+)", m.group(1)).groups())
            return m.group(0) if (major, minor, patch) <= (0, 3, 0) else ""
        entry["contents"] = re.sub(r"\s*<version>([^<]*)</version>", keep, entry["contents"])
        entry["contents"] = re.sub(r"<(latest|release)>[^<]*</\1>", r"<\1>0.3.0</\1>", entry["contents"])
data.pop("checksum")
data["checksum"] = lockfile.checksum(data)
open(path, "w").write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
EOF

# Passes when the lockfile holds the sourcecode jar of version $1 and
# readLock finds no problem in it.
expect() {
  grep -q "sourcecode_3/$1/sourcecode_3-$1.jar" "$lockfile" || {
    echo "the lockfile does not hold sourcecode $1" >&2
    exit 1
  }
  problems=$(nix eval --impure --json --expr "
    let flake = builtins.getFlake \"git+file://$root\"; in
    (flake.lib.scala-cli-purifier.readLock { src = $project; }).problems")
  [ "$problems" = "[]" ] || {
    echo "readLock found problems: $problems" >&2
    exit 1
  }
  echo "the lockfile holds sourcecode $1 and passes the stale check"
}

(cd "$project" && "$cli" lock)
expect 0.3.0
(cd "$project" && "$cli" update com.lihaoyi::sourcecode)
expect 0.3.1
