#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail
checkpoint=$1
build_root=$PWD
variant=$(python3 -c 'import json; print(json.load(open("build-manifest.json"))["variant"])')
export SOURCE_DATE_EPOCH=1
export BACKPORT_RELEASE=1 CI_NATIVE_VERSION="$variant"
export COURSIER_CACHE="$HOME/coursier" COURSIER_REPOSITORIES="file://$build_root/output-maven|file://$build_root/input-maven"
mkdir -p "$HOME" "$build_root/receipts"
cat > "$build_root/repositories" <<REPOSITORIES
[repositories]
  local
  derived-sdk: file://$build_root/output-maven/
  frozen-inputs: file://$build_root/input-maven/
REPOSITORIES
# Ordinary Nix sandbox derivation: no fixed-output networking exception.
python3 - <<'PY'
import errno, socket
probe = socket.socket()
probe.settimeout(2)
try:
    result = probe.connect_ex(("1.1.1.1", 443))
    assert result in (errno.ENETUNREACH, errno.EPERM, errno.EACCES), result
finally:
    probe.close()
PY
(cd core && git apply --check "$sourcePatch" && git apply "$sourcePatch")
launcher_core="$build_root/input-maven/org/scala-sbt/sbt-launch/1.12.9/sbt-launch-1.12.9.jar"
launcher_cli="$build_root/input-maven/org/scala-sbt/sbt-launch/1.11.6/sbt-launch-1.11.6.jar"
run_sbt() {
  local source_dir=$1 launcher=$2 log_name=$3
  shift 3
  local budget=1200
  case "$log_name" in
    core-publication) budget=3600 ;;
    cli-publication) budget=900 ;;
  esac
  (
    cd "$build_root/$source_dir"
    timeout --signal=TERM --kill-after=30s "$budget" java -Xmx3G -XX:ActiveProcessorCount=2 \
      -Dsbt.override.build.repos=true -Dsbt.repository.config="$build_root/repositories" \
      -Dsbt.ivy.home="$HOME/ivy" -Dsbt.global.base="$HOME/sbt-global" \
      -Dsbt.boot.directory="$HOME/sbt-boot" -Dsbt.coursier.home="$COURSIER_CACHE" \
      -Dsbt.offline=true -Dsbt.server.autostart=false -Dsbt.supershell=false \
      -jar "$launcher" "$@"
  ) 2>&1 | tee "$build_root/receipts/$log_name.log"
}
# Startup compiles embedded core meta-build sources. This is an explicit
# compilation checkpoint, never a metadata-only action.
run_sbt core "$launcher_core" core-meta-build \
  'projects' '++3.1.3' 'set ThisBuild / offline := true' \
  'set javalibintf / scalaVersion := "3.1.3"' \
  'show ThisBuild / version' 'show nscplugin3 / scalaVersion' \
  'show toolsJVM2_12 / scalaVersion' 'nscplugin3 / update' 'toolsJVM2_12 / update'
run_sbt native-cli "$launcher_cli" cli-meta-build \
  'projects' '++2.12.21' 'set ThisBuild / offline := true' \
  'show ThisBuild / version' 'show cli / scalaVersion' 'show cli / libraryDependencies'
if [ "$checkpoint" = meta-build ]; then
  exit 0
fi
# Publish tasks produce genuine new POMs and jars. Neither downloaded SDK
# binaries nor POM version fields are edited. No root cross-version aggregate,
# cliPack, assembly, test execution or upstream source cache is used.
publish_setting="set ThisBuild / publishTo := Some(Resolver.file(\"derived-sdk\", file(\"$build_root/output-maven\"))(Resolver.mavenStylePatterns))"
run_sbt core "$launcher_core" core-publication \
  '++3.1.3' 'set ThisBuild / offline := true' \
  'set javalibintf / scalaVersion := "3.1.3"' "$publish_setting" \
  'javalibintf / publish' \
  'utilJVM2_12 / publish' 'nirJVM2_12 / publish' 'toolsJVM2_12 / publish' \
  'nscplugin2_13 / publish' 'nativelib2_13 / publish' 'clib2_13 / publish' \
  'auxlib2_13 / publish' 'scalalib2_13 / publish' \
  'nscplugin3 / publish' 'nativelib3 / publish' 'clib3 / publish' \
  'posixlib3 / publish' 'windowslib3 / publish' 'javalib3 / publish' \
  'auxlib3 / publish' 'testInterfaceSbtDefs3 / publish' \
  'scala3lib3 / publish' 'testInterface3 / publish' \
  '++3.3.4' 'nscplugin3 / publish' 'scala3lib3 / publish'
run_sbt native-cli "$launcher_cli" cli-publication \
  '++2.12.21' 'set ThisBuild / offline := true' "$publish_setting" 'cli / publish'
