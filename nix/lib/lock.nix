# SPDX-License-Identifier: MIT
#
# Reads a scala-cli-purifier lockfile (written by `scala-cli-purifier lock`),
# after checking that it is JSON with the fields of a lockfile, its format
# version and its checksum. Its `artifacts` are entries of the shared
# nix-purifiers library: file entries { path, url, mirrors, hash } and
# inline entries { path, contents }.
#
# Needs no nixpkgs: `scala-cli-purifier prefetch` evaluates this before it
# imports nixpkgs.
{ lockFile }:
let
  fix = "Run `scala-cli-purifier lock` to write it again.";

  text =
    if builtins.pathExists lockFile then
      builtins.readFile lockFile
    else
      throw "scala-cli-purifier: there is no lockfile at ${toString lockFile}. Run `scala-cli-purifier lock` in the project to write one.";

  lock = builtins.seq text (
    builtins.addErrorContext "while reading ${toString lockFile}, which is not a scala-cli-purifier lockfile. ${fix}" (
      builtins.fromJSON text
    )
  );

  fields = [
    "version"
    "scalaCliVersion"
    "javaVersion"
    "name"
    "arguments"
    "directivesHash"
    "artifacts"
    "checksum"
  ];
  absent = builtins.filter (field: !(builtins.isAttrs lock && lock ? ${field})) fields;

  supportedVersion = import ./lock-version.nix;

  # The checksum `scala-cli-purifier lock` writes: the sha256 of the
  # lockfile's content without it, as builtins.toJSON writes that content
  # (pkgs/cli/lockfile.py writes the same JSON).
  checksum = builtins.hashString "sha256" (builtins.toJSON (removeAttrs lock [ "checksum" ]));
in
assert
  absent == [ ]
  || throw "scala-cli-purifier: ${toString lockFile} is not a scala-cli-purifier lockfile: it lacks ${builtins.concatStringsSep ", " absent}. ${fix}";
assert
  lock.version == supportedVersion
  || throw "scala-cli-purifier: ${toString lockFile} has lock format version ${toString lock.version}; this library reads version ${toString supportedVersion}. Run `scala-cli-purifier lock` with the scala-cli-purifier whose flake provides this library.";
assert
  lock.checksum == checksum
  || throw "scala-cli-purifier: ${toString lockFile} was edited after `scala-cli-purifier lock` wrote it: its checksum does not match its content. ${fix}";
lock
