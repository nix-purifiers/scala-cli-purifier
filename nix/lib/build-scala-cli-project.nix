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
#   package           the project's program, packaged for its platform:
#                     JVM     share/java/<name>.jar, an assembly, and
#                             bin/<name>, which runs it on the JDK
#                     JS      share/<name>/<name>.js, linked by Scala.js,
#                             and bin/<name>, which runs it on Node.js
#                     Native  bin/<name>, linked by Scala Native
#   packages.<name>   the same package, under the name the lock records
#   publication       what `scala-cli publish` writes, as a Maven
#                     repository directory: the project's jar, sources,
#                     Scaladoc and POM, with checksums. It needs a lock made
#                     with --publish; `scala-cli-purifier publish` uploads it.
#   artifacts         every output file by name, known from the lock before
#                     anything is built: `program` (bin/<name>), the
#                     program's `<name>.jar` or `<name>.js`, and each file
#                     of the publication under its name without the
#                     version, such as `greeting_3.jar`,
#                     `greeting_3-sources.jar` or `greeting_3.pom`.
#                     `package.artifacts` and `publication.artifacts` hold
#                     each half.
#   jdk               the JDK the build runs on and a JVM program runs with
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
  # Scala.js programs and tests run on Node.js.
  nodejs,
  # Scala Native compiles and links with clang, which it finds with `which`.
  clang,
  which,
  # The offline mirror (pkgs/cli/offline_mirror.py) runs on Python and makes
  # its certificates with openssl.
  python3,
  openssl,
}:
{
  # The root of the scala-cli project.
  src,
  # The lockfile; the default is the one `scala-cli-purifier lock` writes at
  # the project root.
  lockFile ? src + "/scala-cli-purifier.lock.json",
  # The JDK that runs scala-cli's compiler and a JVM program.
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
  platform = lock.platform or "JVM";
  name = lock.name;

  defaultSrcFilter =
    path: type:
    let
      base = baseNameOf path;
    in
    !(
      (
        type == "directory"
        && lib.elem base [
          ".scala-build"
          ".scala-cli-purifier"
          ".bsp"
          ".metals"
          ".bloop"
          ".git"
          ".idea"
        ]
      )
      || lib.hasPrefix "result" base
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

  dependencies = callPackage ./dependencies.nix { inherit lockFile fetchurl; };

  # The options of every scala-cli invocation; `scala-cli-purifier lock`
  # runs scala-cli the same way (pkgs/cli/scala_cli_purifier.py,
  # common_options), so a lock made with it holds what these need.
  common = [
    "--power"
    "--server=false"
    "--offline"
    "--java-home"
    "${javaHome}"
  ]
  ++ lib.optional (platform == "JS") "--js-cli-on-jvm";

  # scala-cli reads Java properties only from -D options placed before its
  # subcommand. $mirrorOptions sends its requests to the offline mirror.
  scalaCli =
    command:
    ''scala-cli -Djava.io.tmpdir="$TMPDIR/java-tmp" $mirrorOptions ${command} ${lib.escapeShellArgs common}'';

  # The offline mirror and the coursier cache module it imports.
  mirrorSource = lib.fileset.toSource {
    root = ../../pkgs/cli;
    fileset = lib.fileset.unions [
      ../../pkgs/cli/offline_mirror.py
      ../../pkgs/cli/coursier_cache.py
    ];
  };

  # The hosts of the locked URLs, which the offline mirror makes
  # certificates for.
  hosts = lib.unique (
    lib.concatMap (
      a:
      let
        m = builtins.match "https?://([^/:]+).*" a.url;
      in
      if m == null then [ ] else m
    ) lock.fetched
  );

  packageOptions =
    {
      JVM = ''--assembly --preamble=false -o "$TMPDIR/out/program.jar"'';
      JS = ''-o "$TMPDIR/out/program.js"'';
      Native = ''-o "$TMPDIR/out/program"'';
    }
    .${platform}
      or (throw "scala-cli-purifier: the lock builds for platform ${platform}, which this library does not know");

  platformInputs =
    {
      JVM = [ ];
      JS = [ nodejs ];
      Native = [
        clang
        which
      ];
    }
    .${platform};

  # The environment of every build: a fresh home, a writable copy of the
  # locked coursier cache, offline, and the offline mirror on that cache.
  # Coursier sends a HEAD request to the URL of a url= dependency on every
  # resolution, outside its cache; the mirror answers it from the cache.
  setup = ''
    export HOME="$TMPDIR/home"
    export XDG_CACHE_HOME="$HOME/.cache" XDG_CONFIG_HOME="$HOME/.config"
    export XDG_DATA_HOME="$HOME/.local/share" XDG_STATE_HOME="$HOME/.local/state"
    export COURSIER_CACHE="$TMPDIR/coursier-cache"
    export COURSIER_ARCHIVE_CACHE="$TMPDIR/coursier-archive-cache"
    export COURSIER_MODE=offline
    mkdir -p "$HOME" "$COURSIER_CACHE" "$TMPDIR/java-tmp" "$TMPDIR/out" "$TMPDIR/mirror"
    # Coursier writes lock and marker files next to what it reads, so the
    # cache is a writable tree of links into the store.
    cp -rs --no-preserve=mode ${dependencies}/. "$COURSIER_CACHE"
    PATH="${jdk'}/bin:$PATH" ${python3.interpreter} -I ${mirrorSource}/offline_mirror.py \
      "$COURSIER_CACHE" "$TMPDIR/mirror" ${lib.escapeShellArgs hosts} &
    mirrorPid=$!
    for _ in $(seq 300); do
      [ -s "$TMPDIR/mirror/java-options" ] && break
      kill -0 "$mirrorPid" 2>/dev/null || { echo "scala-cli-purifier: the offline mirror did not start" >&2; exit 1; }
      sleep 0.1
    done
    mirrorOptions=$(tr '\n' ' ' < "$TMPDIR/mirror/java-options")
  '';
  stopMirror = ''kill "$mirrorPid" 2>/dev/null || true'';

  installPhase =
    {
      JVM = ''
        install -Dm644 "$TMPDIR/out/program.jar" "$out/share/java/${name}.jar"
        makeWrapper ${jdk'}/bin/java "$out/bin/${name}" \
          --add-flags "-jar $out/share/java/${name}.jar"
      '';
      JS = ''
        install -Dm644 "$TMPDIR/out/program.js" "$out/share/${name}/${name}.js"
        if [ -f "$TMPDIR/out/program.js.map" ]; then
          install -Dm644 "$TMPDIR/out/program.js.map" "$out/share/${name}/${name}.js.map"
        fi
        makeWrapper ${nodejs}/bin/node "$out/bin/${name}" \
          --add-flags "$out/share/${name}/${name}.js"
      '';
      Native = ''
        install -Dm755 "$TMPDIR/out/program" "$out/bin/${name}"
      '';
    }
    .${platform};

  # The files of the package by name, relative to its output.
  programFiles = {
    program = "bin/${name}";
  }
  // {
    JVM."${name}.jar" = "share/java/${name}.jar";
    JS."${name}.js" = "share/${name}/${name}.js";
    Native = { };
  }
  .${platform};

  # The files of the publication by name, relative to its output, as the
  # lock run recorded them.
  publicationFiles = builtins.listToAttrs (
    map (f: {
      inherit (f) name;
      value = f.path;
    }) (lock.publication or [ ])
  );

  package = stdenvNoCC.mkDerivation (finalAttrs: {
    pname = name;
    version = "locked";
    src = filteredSrc;
    nativeBuildInputs = [
      scala-cli
      makeWrapper
      openssl
    ]
    ++ platformInputs;
    dontConfigure = true;
    postUnpack = setup;

    buildPhase = ''
      runHook preBuild
      ${scalaCli "package"} ${packageOptions} -f ${lib.escapeShellArgs lock.arguments} </dev/null
      runHook postBuild
    '';

    doCheck = if doCheck != null then doCheck else lock.test;
    checkPhase = ''
      runHook preCheck
      ${scalaCli "test"} ${lib.escapeShellArgs lock.arguments} </dev/null
      runHook postCheck
    '';

    installPhase = ''
      runHook preInstall
      ${stopMirror}
      ${installPhase}
      runHook postInstall
    '';

    passthru = {
      inherit dependencies lock;
      jdk = jdk';
      artifacts = builtins.mapAttrs (_: rel: "${finalAttrs.finalPackage}/${rel}") programFiles;
    };
    meta = {
      description = "${name}, built by scala-cli-purifier";
      mainProgram = name;
    };
  });

  publication =
    assert
      lock.publish or false
      || throw "scala-cli-purifier: ${toString lockFile} was not made with --publish, so it lacks what `scala-cli publish` downloads; re-lock with `scala-cli-purifier lock --publish`.";
    stdenvNoCC.mkDerivation (finalAttrs: {
      pname = "${name}-publication";
      version = "locked";
      src = filteredSrc;
      nativeBuildInputs = [
        scala-cli
        openssl
      ]
      ++ platformInputs;
      dontConfigure = true;
      dontInstall = true;
      # The output is jars, POMs and checksums of them; fixup must not
      # change them.
      dontFixup = true;
      postUnpack = setup;
      buildPhase = ''
        runHook preBuild
        mkdir -p $out
        ${scalaCli "publish"} --publish-repository "file://$out" ${lib.escapeShellArgs lock.arguments} </dev/null
        ${stopMirror}
        for rel in ${lib.escapeShellArgs (builtins.attrValues publicationFiles)}; do
          if [ ! -f "$out/$rel" ]; then
            echo "scala-cli-purifier: the lock names $rel, which this build did not publish; re-lock with --publish" >&2
            exit 1
          fi
        done
        runHook postBuild
      '';
      passthru = {
        inherit dependencies lock;
        artifacts = builtins.mapAttrs (_: rel: "${finalAttrs.finalPackage}/${rel}") publicationFiles;
      };
      meta.description = "the publication of ${name}, built by scala-cli-purifier";
    });
in
assert
  scala-cli.version == lock.scalaCliVersion
  || throw "scala-cli-purifier: ${toString lockFile} was made with scala-cli ${lock.scalaCliVersion}, and the package set's scala-cli is ${scala-cli.version}. Re-lock with the scala-cli-purifier revision whose overlay provides this builder, or pass that scala-cli through buildScalaCliProject.override.";
{
  inherit
    lock
    dependencies
    package
    publication
    ;
  packages.${name} = package;
  artifacts = package.artifacts // lib.optionalAttrs (lock.publish or false) publication.artifacts;
  jdk = jdk';
}
