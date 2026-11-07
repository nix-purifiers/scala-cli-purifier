# SPDX-License-Identifier: MIT
{
  pkgs,
  overlay,
  flakeLib,
}:
let
  # Alias the named scope only inside this test package set, so existing
  # consumer and composition checks exercise the matching command/builder.
  testOverlay =
    final: prev:
    let
      base = overlay final prev;
    in
    base // { scala-cli-purifier = base.scala-cli-purifier-jvm; };
  selected = (pkgs.extend overlay).scala-cli-purifier-jvm;
  all = import ./default.nix {
    inherit pkgs flakeLib;
    overlay = testOverlay;
    cli = selected.cli;
    scala-cli = (pkgs.extend overlay).scala-cli-purifier.jvm-sdk;
  };
  names = [
    "toolchain-receipt"
    "conformance-offline"
    "hello-runs"
    "hello-tests"
    "script-runs"
    "sources-runs"
    "url-runs"
    "local-runs"
    "js-runs"
    "js-tests"
    "native-runs"
    "native-tests"
    "library-release"
    "composition"
    "example-besom-core"
    "consumer-package"
    "builder-override"
    "scope-override"
  ];
  checks = pkgs.lib.getAttrs names all;
in
pkgs.runCommand "scala-cli-jvm-profile"
  {
    dependencies = builtins.attrValues checks;
    passthru = checks;
  }
  ''
    mkdir -p "$out"
    cp ${selected.jvm-sdk.manifest} "$out/initial-frontend.json"
    echo 'JVM profile fixture checks passed' > "$out/checks.log"
  ''
