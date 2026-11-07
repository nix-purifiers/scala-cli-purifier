# SPDX-License-Identifier: MIT
# Initial official JVM frontend transport; ordinary Scala CLI semantics apply.
{
  lib,
  stdenvNoCC,
  fetchurl,
  runCommand,
  writeText,
  runtimeShell,
  jdk21_headless,
}:
let
  version = "1.18.0";
  payloadHash = "1ec9f3f906d4cad5ef603cb5fef1561f98beb678c5c8f4a157004aa5ad1dcc91";
  payloadUrl = "https://github.com/VirtusLab/scala-cli/releases/download/v${version}/scala-cli.jar";
  jar = fetchurl {
    url = payloadUrl;
    sha256 = payloadHash;
  };
  javaExecutable = lib.getExe' jdk21_headless "java";
  requestedStartupProperties = lib.optionalAttrs stdenvNoCC.hostPlatform.isDarwin {
    "java.net.preferIPv4Stack" = "true";
  };
  launcherText = "#!${runtimeShell}\n" + ''
    exec ${javaExecutable} ${lib.optionalString stdenvNoCC.hostPlatform.isDarwin "-Djava.net.preferIPv4Stack=true"} -jar ${jar} "$@"
  '';
  launcher = writeText "scala-cli-jvm-sdk-launcher" launcherText;
  identity = {
    scope = "initial-frontend-transport";
    kind = "official-jvm";
    initialScalaCliVersion = version;
    upstreamCommit = "65126b818f5a135ed3d18eb77e8010cbdb0daae9";
    inherit
      payloadUrl
      payloadHash
      javaExecutable
      requestedStartupProperties
      ;
    initialJavaHome = jdk21_headless.home;
    platform = stdenvNoCC.hostPlatform.system;
    jar = toString jar;
    launcherSha256 = builtins.hashString "sha256" launcherText;
    argumentHandling = "unchanged upstream CLI argv after jar";
    environmentHandling = "preserved, including Java option variables and JAVA_HOME";
    projectCompilerSelection = "upstream semantics and purifier-owned operation options";
    versionRelaunch = "upstream behavior preserved; initial identity is not effective operation identity";
  };
  manifest = writeText "scala-cli-jvm-sdk-manifest.json" (builtins.toJSON identity);
in
runCommand "scala-cli-jvm-sdk-${version}"
  {
    inherit version;
    passthru = { inherit manifest jar; };
    meta.mainProgram = "scala-cli";
  }
  ''
    install -Dm755 ${launcher} "$out/bin/scala-cli"
    mkdir -p "$out/share/scala-cli-jvm-sdk"
    cp ${manifest} "$out/share/scala-cli-jvm-sdk/manifest.json"
  ''
