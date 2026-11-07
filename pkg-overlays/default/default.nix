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
    in
    native
    // {
      scala-cli-purifier-jvm = native.scala-cli-purifier.overrideScope (
        _: _: {
          scala-cli = final.scala-cli-purifier.jvm-sdk;
        }
      );
    };
}
