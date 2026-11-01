# SPDX-License-Identifier: MIT
#
# buildScalaCliProject: the sandboxed build of a scala-cli project, from the
# lockfile `scala-cli-purifier lock` writes at the project root.
#
# The file is a package for callPackage: the first argument set is what it
# takes from the package set, and the result is the builder. The
# scala-cli-purifier overlay (`overlays.default`) adds it to the package set
# as `buildScalaCliProject`, so a package written for callPackage takes it
# like any other dependency:
#
#   { buildScalaCliProject }: (buildScalaCliProject { src = ./.; }).package
#
# and `buildScalaCliProject.override` replaces what it takes from the set.
# The builder returns
#
#   dependencies      the coursier cache of every locked artifact
#   package           the project's program: its assembly jar at
#                     share/java/<name>.jar and bin/<name>, which runs it
#   packages.<name>   the same package, under the name the lock records
#   jdk               the JDK the build runs on and the program runs with
{
  lib,
  stdenvNoCC,
  callPackage,
  makeWrapper,
  # The JDKs a build can run on; the default JDK of a build is the headless
  # one of the major version its lock was made with.
  javaPackages,
  # The scala-cli the lock was made with. scala-cli downloads tools of its
  # own version, so another version would ask for files the lock lacks.
  scala-cli,
}:
{
  # The root of the scala-cli project.
  src,
  # The lockfile; the default is the one `scala-cli-purifier lock` writes at
  # the project root.
  lockFile ? src + "/scala-cli-purifier.lock.json",
  # The JDK that runs scala-cli's compiler and the program.
  jdk ? null,
  # Replaces the built-in fetcher; see fetch.nix.
  fetchurl ? null,
  # Runs the project's tests (`scala-cli test`) in the build. The default is
  # whether the lock was made with `--test`, which locks the test
  # dependencies too.
  doCheck ? null,
  # Filters the project root before it is copied into the store, as the
  # `filter` of builtins.path. The default leaves out build outputs, editor
  # state and the purifier's own state.
  srcFilter ? null,
}:
let
  lock = import ./lock.nix { inherit lockFile; };

  defaultSrcFilter =
    path: type:
    let
      name = baseNameOf path;
    in
    !(
      (
        type == "directory"
        && lib.elem name [
          ".scala-build"
          ".scala-cli-purifier"
          ".bsp"
          ".metals"
          ".bloop"
          ".git"
          ".idea"
        ]
      )
      || lib.hasPrefix "result" name
    );

  filteredSrc = builtins.path {
    path = src;
    name = "scala-cli-source";
    filter = if srcFilter != null then srcFilter else defaultSrcFilter;
  };

  javaMajor = builtins.head (lib.splitString "." lock.javaVersion);
  defaultJdk =
    javaPackages.compiler."openjdk${javaMajor}".headless
      or (throw "scala-cli-purifier: javaPackages has no openjdk${javaMajor} for the Java ${lock.javaVersion} the lock was made with; pass `jdk`.");
  jdk' = if jdk != null then jdk else defaultJdk;
  javaHome = jdk'.home or jdk';

  doCheck' = if doCheck != null then doCheck else lock.test;

  dependencies = callPackage ./dependencies.nix { inherit lockFile fetchurl; };

  # The options of every scala-cli invocation; `scala-cli-purifier lock`
  # runs scala-cli the same way, so a lock made with it holds what these
  # need.
  common = [
    "--power"
    "--server=false"
    "--offline"
    "--java-home"
    "${javaHome}"
  ];

  package = stdenvNoCC.mkDerivation {
    pname = lock.name;
    version = "locked";
    src = filteredSrc;
    nativeBuildInputs = [
      scala-cli
      makeWrapper
    ];
    dontConfigure = true;

    postUnpack = ''
      export HOME="$TMPDIR/home"
      export XDG_CACHE_HOME="$HOME/.cache" XDG_CONFIG_HOME="$HOME/.config"
      export XDG_DATA_HOME="$HOME/.local/share" XDG_STATE_HOME="$HOME/.local/state"
      export COURSIER_CACHE="$TMPDIR/coursier-cache"
      export COURSIER_ARCHIVE_CACHE="$TMPDIR/coursier-archive-cache"
      export COURSIER_MODE=offline
      mkdir -p "$HOME" "$COURSIER_CACHE"
      # Coursier writes lock and marker files next to what it reads, so the
      # cache is a writable tree of links into the store.
      cp -rs --no-preserve=mode ${dependencies}/. "$COURSIER_CACHE"
    '';

    buildPhase = ''
      runHook preBuild
      scala-cli package ${lib.escapeShellArgs common} \
        --assembly --preamble=false -o "$TMPDIR/${lock.name}.jar" -f \
        ${lib.escapeShellArgs lock.arguments} </dev/null
      runHook postBuild
    '';

    doCheck = doCheck';
    checkPhase = ''
      runHook preCheck
      scala-cli test ${lib.escapeShellArgs common} ${lib.escapeShellArgs lock.arguments} </dev/null
      runHook postCheck
    '';

    installPhase = ''
      runHook preInstall
      install -Dm644 "$TMPDIR/${lock.name}.jar" "$out/share/java/${lock.name}.jar"
      makeWrapper ${jdk'}/bin/java "$out/bin/${lock.name}" \
        --add-flags "-jar $out/share/java/${lock.name}.jar"
      runHook postInstall
    '';

    passthru = {
      inherit dependencies lock;
      jdk = jdk';
    };
    meta = {
      description = "${lock.name}, built by scala-cli-purifier";
      mainProgram = lock.name;
    };
  };
in
assert
  scala-cli.version == lock.scalaCliVersion
  || throw "scala-cli-purifier: ${toString lockFile} was made with scala-cli ${lock.scalaCliVersion}, and the package set's scala-cli is ${scala-cli.version}. Re-lock with the scala-cli-purifier revision whose overlay provides this builder, or pass that scala-cli through buildScalaCliProject.override.";
{
  inherit lock dependencies package;
  packages.${lock.name} = package;
  jdk = jdk';
}
