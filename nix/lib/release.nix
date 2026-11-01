# SPDX-License-Identifier: MIT
#
# What `scala-cli-purifier publish` builds: the release of a project, by
# buildScalaCliProject (build-scala-cli-project.nix), with the nixpkgs the
# command was built with. The command passes the arguments as strings:
#
#   nixpkgs          the nixpkgs source directory
#   purifierSource   the source of the shared library, at the revision of
#                    the command's flake.lock
#   src              the project root
#   lockFile         the lockfile, which may lie outside the project root
{
  nixpkgs,
  purifierSource,
  src,
  lockFile,
}:
let
  pkgs = import nixpkgs { };
  buildScalaCliProject = pkgs.callPackage ./build-scala-cli-project.nix {
    purifier = import purifierSource { inherit (pkgs) lib; };
  };
in
(buildScalaCliProject {
  src = /. + src;
  lockFile = /. + lockFile;
}).release
