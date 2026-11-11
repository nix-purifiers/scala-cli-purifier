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
      -Duser.home="$HOME" \
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
# Upstream defines project-scoped publishTo settings. Override each selected
# project and verify the effective values before invoking any publish task.
publish_destination="Some(Resolver.file(\"derived-sdk\", file(\"$build_root/output-maven\"))(Resolver.mavenStylePatterns))"
offline_resolvers="Seq(sbt.librarymanagement.MavenRepository(\"derived-sdk\", file(\"$build_root/output-maven\").toURI.toString), sbt.librarymanagement.MavenRepository(\"frozen-inputs\", file(\"$build_root/input-maven\").toURI.toString))"
make_publish_options() {
  local target reference separator="" settings="set Seq("
  local verify="set TaskKey[Unit](\"verifyDerivedPublishDestinations\") := { val expected = $publish_destination; val expectedResolvers = $offline_resolvers; require(expectedResolvers.forall(r => new java.net.URI(r.root).getScheme == \"file\"), \"resolution roots must be owned file URLs\"); "
  publish_options=()
  for target in "$@"; do
    reference="LocalProject(\"$target\")"
    settings+="$separator$reference / publishTo := $publish_destination, $reference / resolvers := $offline_resolvers"
    verify+="require(($reference / publishTo).value == expected, \"$target publishTo differs from the owned repository\"); "
    verify+="require(($reference / resolvers).value == expectedResolvers && ($reference / resolvers).value.forall(_.isInstanceOf[sbt.librarymanagement.MavenRepository]), \"$target resolvers differ from the frozen file repositories\"); "
    separator=", "
  done
  publish_options=("$settings)" "$verify() }")
  for target in "$@"; do
    publish_options+=("show $target / publishTo" "show $target / resolvers")
  done
  publish_options+=("verifyDerivedPublishDestinations")
}
make_publish_options \
  javalibintf utilJVM2_12 nirJVM2_12 toolsJVM2_12 \
  nscplugin2_13 nativelib2_13 clib2_13 auxlib2_13 scalalib2_13 \
  nscplugin3 nativelib3 clib3 posixlib3 windowslib3 javalib3 auxlib3 \
  testInterfaceSbtDefs3 scala3lib3 testInterface3
core_publish_options=("${publish_options[@]}")
make_publish_options cli
cli_publish_options=("${publish_options[@]}")
# toolsJVM's upstream BuildInfo computes these source-only 2.12 classpaths.
# They are compilation prerequisites, not extra consumer publications.
make_build_source_options() {
  local target reference separator="" settings="set Seq("
  local verify="set TaskKey[Unit](\"verifyInternalBuildResolvers\") := { val expectedResolvers = $offline_resolvers; require(expectedResolvers.forall(r => new java.net.URI(r.root).getScheme == \"file\"), \"resolution roots must be owned file URLs\"); "
  build_source_options=()
  for target in "$@"; do
    reference="LocalProject(\"$target\")"
    settings+="$separator$reference / resolvers := $offline_resolvers"
    verify+="require(($reference / resolvers).value == expectedResolvers && ($reference / resolvers).value.forall(_.isInstanceOf[sbt.librarymanagement.MavenRepository]), \"$target internal build resolvers differ from frozen file repositories\"); "
    separator=", "
  done
  build_source_options=("$settings)" "$verify() }")
  for target in "$@"; do
    build_source_options+=("show $target / resolvers")
  done
  build_source_options+=("verifyInternalBuildResolvers")
}
make_build_source_options \
  nscplugin2_12 nativelib2_12 clib2_12 auxlib2_12 scalalib2_12 \
  javalib2_12 posixlib2_12 windowslib2_12
core_build_source_options=("${build_source_options[@]}")
run_sbt core "$launcher_core" core-publication \
  '++3.1.3' 'set ThisBuild / offline := true' \
  'set javalibintf / scalaVersion := "3.1.3"' \
  'set LocalProject("javalibintf") / Compile / doc / javacOptions += "-notimestamp"' \
  'show javalibintf / Compile / doc / javacOptions' \
  'set TaskKey[Unit]("verifyJavadocOptions") := { require((LocalProject("javalibintf") / Compile / doc / javacOptions).value.contains("-notimestamp"), "javalibintf documentation timestamp option is missing") }' \
  'verifyJavadocOptions' "${core_publish_options[@]}" "${core_build_source_options[@]}" \
  'javalibintf / publish' \
  'utilJVM2_12 / publish' 'nirJVM2_12 / publish' 'toolsJVM2_12 / publish' \
  'nscplugin2_13 / publish' 'nativelib2_13 / publish' 'clib2_13 / publish' \
  'auxlib2_13 / publish' 'scalalib2_13 / publish' \
  'nscplugin3 / publish' 'nativelib3 / publish' 'clib3 / publish' \
  'posixlib3 / publish' 'windowslib3 / publish' 'javalib3 / publish' \
  'auxlib3 / publish' 'testInterfaceSbtDefs3 / publish' \
  'scala3lib3 / publish' 'testInterface3 / publish' \
  '++3.3.4' 'verifyDerivedPublishDestinations' 'verifyInternalBuildResolvers' 'nscplugin3 / publish' 'scala3lib3 / publish'
run_sbt native-cli "$launcher_cli" cli-publication \
  '++2.12.21' 'set ThisBuild / offline := true' "${cli_publish_options[@]}" 'cli / publish'
