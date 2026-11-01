# SPDX-License-Identifier: MIT
#
# The default package overlay. It adds the packages of pkgs/ under
# `pkgs.scala-cli-purifier`: `.cli`, the scala-cli-purifier command and its
# scala-cli wrapper. It also adds the builder `pkgs.buildScalaCliProject`
# (nix/lib/build-scala-cli-project.nix) beside them, where nixpkgs keeps its
# own builders, so a package written for callPackage takes it by name. The
# builder takes the purifier library of this flake. A consumer that lists
# this flake in `projects` holds the overlay as `scala-cli-purifier/default`,
# which its package sets apply by default.
{ closure-lib, ... }:
let
  packages = closure-lib.caisson.nixpkgs.mkPackagesOverlay ../../pkgs "scala-cli-purifier";
in
{
  overlay =
    final: prev:
    packages final prev
    // {
      buildScalaCliProject = final.callPackage ../../nix/lib/build-scala-cli-project.nix {
        inherit (closure-lib) purifier;
      };
    };
}
