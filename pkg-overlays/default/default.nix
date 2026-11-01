# SPDX-License-Identifier: MIT
#
# The default package overlay. It adds the packages of pkgs/ under
# `pkgs.scala-cli-purifier`: `.cli`, the scala-cli-purifier command and its
# scala-cli wrapper. A consumer that lists this flake in `projects` holds it
# as `scala-cli-purifier/default`, which its package sets apply by default.
# The builder is in the library, lib.scala-cli-purifier.buildScalaCliProject.
{ closure-lib, ... }:
{
  overlay = closure-lib.caisson.nixpkgs.mkPackagesOverlay ../../pkgs "scala-cli-purifier";
}
