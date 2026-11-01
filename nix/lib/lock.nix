# SPDX-License-Identifier: MIT
#
# Reading a scala-cli-purifier lockfile (written by `scala-cli-purifier
# lock`). The flake publishes `readLock` as lib.scala-cli-purifier.readLock:
#
#   readLock { src; lockFile ? src + "/scala-cli-purifier.lock.json"; }
#
# gives the lock in memory of the purifier contract (CONTRACT.md of the
# shared library):
#
#   purifier   "scala-cli-purifier"
#   entries    the lockfile's `artifacts` as entries of the shared library,
#              for every system: file entries { path, url, mirrors, hash },
#              with the mirrors this file adds, and inline entries
#              { path, contents }
#   outputs    the outputs of buildScalaCliProject by name, each with the
#              files it holds: `<name>` (the program), `tests` when the
#              lock was made with --test, and `release` when it was made
#              with --publish
#   problems   why the lockfile cannot be used, each naming the command
#              that fixes it: it is missing, lacks fields, has another
#              format version, was edited (its checksum does not match),
#              or is stale (the `//> using` directives of the project at
#              `src` changed since the lock)
#
# A lockfile that is not JSON fails evaluation, with a message that names
# the command that fixes it.
#
# `parse lockFile` gives the lockfile's content, which buildScalaCliProject
# reads after checkLock of the shared library accepts the lock.
{ lib }:
let
  lockFileName = "scala-cli-purifier.lock.json";
  fix = "Run `scala-cli-purifier lock` to write it again.";

  parse =
    lockFile:
    let
      text = builtins.readFile lockFile;
    in
    builtins.seq text (
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

  supportedVersion = import ./lock-version.nix;

  # The mirrors of a locked URL, which the fetcher tries when the URL
  # fails: Google's mirror for a file of Maven Central, which answers
  # where Maven Central limits the rate of requests. The lockfile records
  # no mirrors, so it is the same whichever mirror the lock run used.
  mavenCentral = "https://repo1.maven.org/maven2/";
  mirrorsOf =
    url:
    lib.optional (lib.hasPrefix mavenCentral url)
      "https://maven-central.storage-download.googleapis.com/maven2/${lib.removePrefix mavenCentral url}";
  withMirrors = entry: if entry ? url then entry // { mirrors = mirrorsOf entry.url; } else entry;

  # The checksum `scala-cli-purifier lock` writes: the sha256 of the
  # lockfile's content without it, as builtins.toJSON writes that content
  # (pkgs/cli/lockfile.py writes the same JSON).
  checksumOf = data: builtins.hashString "sha256" (builtins.toJSON (removeAttrs data [ "checksum" ]));

  # The files scala-cli reads `//> using` directives from, and the
  # directories that hold build output or editor state; the same lists as
  # pkgs/cli/lockfile.py.
  sourceSuffixes = [
    ".scala"
    ".sc"
    ".java"
    ".md"
  ];
  skippedDirectories = [
    ".scala-build"
    ".scala-cli-purifier"
    ".bsp"
    ".metals"
    ".bloop"
    ".git"
    ".idea"
  ];
  isSource = name: lib.any (suffix: lib.hasSuffix suffix name) sourceSuffixes;

  # The source files below `dir`, as paths relative to the project root,
  # where `rel` is the path of `dir` relative to it.
  sourcesBelow =
    dir: rel:
    lib.concatLists (
      lib.mapAttrsToList (
        name: type:
        let
          path = if rel == "" then name else "${rel}/${name}";
        in
        if type == "directory" then
          lib.optionals (!lib.elem name skippedDirectories) (sourcesBelow (dir + "/${name}") path)
        else
          lib.optional (type == "regular" && isSource name) path
      ) (builtins.readDir dir)
    );

  # The source files of the inputs among `arguments`, as
  # pkgs/cli/lockfile.py selects them.
  sourceFiles =
    src: arguments:
    let
      inputs = builtins.filter (argument: !lib.hasPrefix "-" argument) arguments;
      normalize = argument: lib.removeSuffix "/" (lib.removePrefix "./" argument);
      filesOf =
        argument:
        let
          rel = if normalize argument == "." then "" else normalize argument;
          path = if rel == "" then src else src + "/${rel}";
          type = if builtins.pathExists path then builtins.readFileType path else null;
        in
        if type == "directory" then
          sourcesBelow path rel
        else
          lib.optional (type == "regular" && isSource rel) rel;
    in
    lib.sort lib.lessThan (lib.unique (lib.concatMap filesOf inputs));

  # A `//> using` directive of a line, without the surrounding blanks, or
  # null.
  directiveOf =
    line:
    let
      m = builtins.match "[ \t]*(//>.*[^ \t\r]|//>)[ \t\r]*" line;
    in
    if m == null then null else builtins.head m;

  # The hash `scala-cli-purifier lock` records as `directivesHash`: the
  # sha256 of the JSON list of [file, [directive, ...]] for each source file
  # that has directives (pkgs/cli/lockfile.py computes the same).
  directivesHash =
    src: arguments:
    let
      withDirectives = lib.concatMap (
        rel:
        let
          directives = builtins.filter (d: d != null) (
            map directiveOf (lib.splitString "\n" (builtins.readFile (src + "/${rel}")))
          );
        in
        lib.optional (directives != [ ]) [
          rel
          directives
        ]
      ) (sourceFiles src arguments);
    in
    builtins.hashString "sha256" (builtins.toJSON withDirectives);

  readLock =
    {
      src,
      lockFile ? src + "/${lockFileName}",
    }:
    let
      exists = builtins.pathExists lockFile;
      data = if exists then parse lockFile else { };
      isLock = builtins.isAttrs data;
      absent = builtins.filter (field: !(isLock && data ? ${field})) fields;
      usable = exists && absent == [ ];
      name = data.name or "program";

      problems =
        if !exists then
          [
            "scala-cli-purifier: there is no lockfile at ${toString lockFile}. Run `scala-cli-purifier lock` in the project to write one."
          ]
        else if absent != [ ] then
          [
            "scala-cli-purifier: ${toString lockFile} is not a scala-cli-purifier lockfile: it lacks ${lib.concatStringsSep ", " absent}. ${fix}"
          ]
        else
          lib.optional (data.version != supportedVersion)
            "scala-cli-purifier: ${toString lockFile} has lock format version ${toString data.version}; this library reads version ${toString supportedVersion}. Run `scala-cli-purifier lock` with the scala-cli-purifier whose flake provides this library."
          ++ lib.optional (data.checksum != checksumOf data)
            "scala-cli-purifier: ${toString lockFile} was edited after `scala-cli-purifier lock` wrote it: its checksum does not match its content. ${fix}"
          ++ lib.optional (data.directivesHash != directivesHash src data.arguments)
            "scala-cli-purifier: the project's `//> using` directives changed since ${toString lockFile} was made, so it may lack what they need. Run `scala-cli-purifier lock` to lock them.";

      platform = data.platform or "JVM";
      programFiles = {
        program = "bin/${name}";
      }
      // {
        JVM."${name}.jar" = "share/java/${name}.jar";
        JS."${name}.js" = "share/${name}/${name}.js";
        Native = { };
      }
      .${platform} or { };
    in
    {
      purifier = "scala-cli-purifier";
      entries = if usable then map withMirrors data.artifacts else [ ];
      outputs =
        lib.optionalAttrs usable {
          ${name} = {
            inherit platform;
            files = programFiles;
          };
        }
        // lib.optionalAttrs (usable && data.test or false) { tests.files = { }; }
        // lib.optionalAttrs (usable && data.publish or false) {
          release.files = builtins.listToAttrs (
            map (f: {
              inherit (f) name;
              value = f.path;
            }) data.publication
          );
        };
      inherit problems;
    };
in
{
  inherit parse readLock directivesHash;
}
