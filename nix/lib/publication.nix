# SPDX-License-Identifier: MIT
#
# What `scala-cli-purifier publish` builds: the publication of a project, by
# buildScalaCliProject (build-scala-cli-project.nix), called with the
# nixpkgs package set the command was built with. The command passes the
# arguments as strings:
#
#   nixpkgs   the nixpkgs source directory
#   src       the project root
{
  nixpkgs,
  src,
}:
let
  pkgs = import nixpkgs { };
in
(pkgs.callPackage ./build-scala-cli-project.nix { } { src = /. + src; }).publication
