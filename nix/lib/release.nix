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
  scalaCliPath ? null,
  scalaCliVersion ? null,
  nativeSdkPath ? null,
}:
let
  pkgs = import nixpkgs { };
  buildScalaCliProject = pkgs.callPackage ./build-scala-cli-project.nix {
    purifier = import purifierSource { inherit (pkgs) lib; };
    native-sdk = if nativeSdkPath == null then null else builtins.storePath nativeSdkPath;
    # The command already depends on this realized package. Preserve its
    # store dependency and version instead of selecting Nixpkgs' default
    # frontend again when a named scope publishes a project.
    scala-cli =
      if scalaCliPath == null then
        pkgs.scala-cli
      else
        pkgs.symlinkJoin {
          name = "scala-cli-selected-${scalaCliVersion}";
          paths = [ (builtins.storePath scalaCliPath) ];
          version = scalaCliVersion;
        };
  };
in
(buildScalaCliProject {
  src = /. + src;
  lockFile = /. + lockFile;
}).projections.maven
