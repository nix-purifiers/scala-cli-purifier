# SPDX-License-Identifier: MIT
#
# Reads a scala-cli-purifier lockfile (written by `scala-cli-purifier lock`).
#
# Needs no nixpkgs: `scala-cli-purifier exec` evaluates this with nothing
# but the Nix installation it runs.
{ lockFile }:
let
  lock = builtins.fromJSON (builtins.readFile lockFile);

  supportedVersion = import ./lock-version.nix;

  # A store path name from a file name. Store path names allow
  # [A-Za-z0-9+._?=-], must not start with a dot, and are at most 211
  # characters; every other character becomes "_". `exec` and the
  # sandboxed build both name their fetches with this function, so they
  # share store paths.
  storeName =
    fileName:
    let
      # builtins.split alternates the text between matches with a list per
      # match.
      replaced = builtins.concatStringsSep "" (
        map (part: if builtins.isList part then "_" else part) (
          builtins.split "[^A-Za-z0-9+._?=-]" fileName
        )
      );
      undotted = builtins.head (builtins.match "\\.*(.*)" replaced);
      nonEmpty = if undotted == "" then "file" else undotted;
    in
    builtins.substring 0 200 nonEmpty;

  artifactName = artifact: storeName (builtins.baseNameOf artifact.path);
in
assert
  lock.version == supportedVersion
  || throw "scala-cli-purifier: ${toString lockFile} has lock format version ${toString lock.version}; this library reads version ${toString supportedVersion}. Re-lock it with the scala-cli-purifier version that provides this library.";
lock
// {
  inherit storeName artifactName;

  # File entries { path, url, hash }, which are fetched.
  fetched = builtins.filter (a: a ? url) lock.artifacts;

  # The store file of an inline entry { path, contents }: the lock run kept
  # the file's text, because the file's URL serves new content over time.
  # builtins.toFile makes the same store path wherever it is evaluated.
  inlineFile = artifact: builtins.toFile (artifactName artifact) artifact.contents;
  inlined = builtins.filter (a: a ? contents) lock.artifacts;
}
