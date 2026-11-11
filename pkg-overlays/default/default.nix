# SPDX-License-Identifier: MIT
#
# The default package overlay. It adds one attribute to the package set,
# the scope `pkgs.scala-cli-purifier` (pkgs/default.nix): `.cli`, the
# scala-cli-purifier command and its scala-cli wrapper, and
# `.buildScalaCliProject`, the builder, which a package written for
# callPackage takes through `{ scala-cli-purifier }`. A consumer that lists
# this flake in `projects` holds the overlay as `scala-cli-purifier/default`,
# which its package sets apply by default.
{ closure-lib, ... }:
let
  nativeOverlay = closure-lib.caisson.nixpkgs.mkPackagesOverlay ../../pkgs "scala-cli-purifier";
in
{
  overlay =
    final: prev:
    let
      native = nativeOverlay final prev;
      base = native.scala-cli-purifier;
      jvm = base.overrideScope (
        _: previous: {
          scala-cli = previous.jvm-sdk;
          native-sdk = final.callPackage ../../pkgs/native-sdk { checkpoint = "sdk"; };
        }
      );
    in
    native
    // {
      scala-cli-purifier-native = base;
      scala-cli-purifier-jvm = jvm;
      scala-cli-purifier = if final.stdenv.buildPlatform.isDarwin then jvm else base;
    };
}
