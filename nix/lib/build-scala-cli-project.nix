# SPDX-License-Identifier: MIT
#
# buildScalaCliProject: the sandboxed build of a scala-cli project, from the
# lockfile `scala-cli-purifier lock` writes at the project root.
#
# The file is a package for callPackage: the first argument set is what it
# takes from the package set, and the result is the builder. The
# scala-cli-purifier overlay (`overlays.default`) adds it to the scope
# `pkgs.scala-cli-purifier`, so a package written for callPackage takes it
# through the scope:
#
#   { scala-cli-purifier }:
#   (scala-cli-purifier.buildScalaCliProject { src = ./.; }).packages.app
#
# `buildScalaCliProject.override` replaces what it takes from the set, such
# as `scala-cli`, and `scala-cli-purifier.overrideScope` replaces it for the
# whole scope. The scope passes `purifier`, the library the nix-purifiers
# share, from the scala-cli-purifier flake. The builder returns
#
#   packages.<name>   the project's program, under the name the lock
#                     records, packaged for its platform:
#                     JVM     share/java/<name>.jar, an assembly, and
#                             bin/<name>, which runs it on the JDK
#                     JS      share/<name>/<name>.js, linked by Scala.js,
#                             and bin/<name>, which runs it on Node.js
#                     Native  bin/<name>, linked by Scala Native
#   tests             the project's test run (`scala-cli test`) in the
#                     sandbox; it needs a lock made with --test
#   release           what `scala-cli publish` writes, as a Maven
#                     repository directory: the project's jar, sources,
#                     Scaladoc and POM, with checksums. It needs a lock made
#                     with --publish; `scala-cli-purifier publish` uploads it.
#   fetches           the entries of the lock (readLock in lock.nix), in the
#                     format of the purifier library: what the build fetches
#   artifacts         every output file by name, known from the lock before
#                     anything is built: `program` (bin/<name>), the
#                     program's `<name>.jar` or `<name>.js`, and each file
#                     of the release under its name without the version,
#                     such as `greeting_3.jar`, `greeting_3-sources.jar` or
#                     `greeting_3.pom`
{
  lib,
  purifier,
  stdenvNoCC,
  makeWrapper,
  # The JDKs a build can run on; the default JDK of a build is the headless
  # one of the major version its lock was made with.
  javaPackages,
  nodejs,
  clang,
  which,
  python3,
  openssl,
  strip-nondeterminism,
  # What the `tree` of the purifier library fetches and lays out the locked
  # files with.
  fetchurl,
  runCommand,
  writeText,
  unzip,
  zstd,
  # The scala-cli the lock was made with. scala-cli downloads tools of its
  # own version, so another version would ask for files the lock lacks.
  scala-cli,
}:
{
  # The root of the scala-cli project.
  src,
  # Fetchers that replace the fetchers of nixpkgs, passed to the `tree` of
  # the purifier library; `fetchurl` takes the arguments the fetchurl of
  # nixpkgs takes, so a replacement can send credentials or use a company
  # mirror.
  fetchers ? { },
  # Nix-built JVM inputs, supplied independently of artifact download locks.
  projectDependencies ? purifier.lockedProjectInputs {
    inputs = (builtins.fromJSON (builtins.readFile lockFile)).projectInputs or [ ];
    system = stdenvNoCC.hostPlatform.system;
  },
  # The lockfile; the default is the one `scala-cli-purifier lock` writes at
  # the project root.
  lockFile ? src + "/scala-cli-purifier.lock.json",
  # The JDK that runs scala-cli's compiler and a JVM program.
  jdk ? null,
  # Filters the project root before it is copied into the store, as the
  # `filter` of builtins.path. The default leaves out build outputs, editor
  # state and the purifier's own state.
  srcFilter ? null,
  # The local repositories the lock read from, by the names the lockfile
  # gives them (`ivy2Local`, `m2Local`, or a `file:` repository's directory
  # name), each a directory laid out as the repository was. The build
  # copies the files the lockfile records from each one and checks their
  # hashes.
  localRepositories ? { },
}:
let
  lockLib = import ./lock.nix { inherit lib; };
  # The lock in memory, which checkLock of the purifier library accepts
  # only when the lockfile is usable for `src`; it fails evaluation with the
  # problems otherwise, each naming the command that fixes it.
  checked = purifier.checkLock (lockLib.readLock { inherit src lockFile; });
  lock = builtins.seq checked (lockLib.parse lockFile);
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

  javaMajor = builtins.head (lib.splitString "." (lib.removePrefix "1." lock.javaVersion));
  defaultJdk =
    javaPackages.compiler."openjdk${javaMajor}".headless
      or (throw "scala-cli-purifier: javaPackages has no openjdk${javaMajor} for the Java ${lock.javaVersion} the lock was made with; pass `jdk`.");
  jdk' = if jdk != null then jdk else defaultJdk;
  javaHome = jdk'.home or jdk';

  # The coursier cache: every locked file at its coursier-cache path, as a
  # link into the store.
  dependencies = purifier.tree (
    {
      inherit
        fetchurl
        runCommand
        writeText
        python3
        unzip
        zstd
        ;
      name = "scala-cli-coursier-cache";
      entries = purifier.entriesFor stdenvNoCC.buildPlatform.system checked;
    }
    // fetchers
  );

  provided = purifier.jvmProjections {
    repository = null;
    dependencies = projectDependencies;
    inherit runCommand python3;
    name = "scala-cli-provided-dependencies";
  };

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
    ''scala-cli -Djava.io.tmpdir="$TMPDIR/java-tmp" -Duser.home="$TMPDIR/user-home" $mirrorOptions ${command} ${lib.escapeShellArgs common} ${localRepositoryOptions}'';

  # The offline mirror, the staging of local repositories, and the
  # coursier cache module they import.
  mirrorSource = lib.fileset.toSource {
    root = ../../pkgs/cli;
    fileset = lib.fileset.unions [
      ../../pkgs/cli/offline_mirror.py
      ../../pkgs/cli/local_repositories.py
      ../../pkgs/cli/coursier_cache.py
    ];
  };

  # Where the build reads each local repository from, relative to $TMPDIR:
  # ivy2Local and m2Local under the home it passes as user.home, any other
  # `file:` repository under local-repositories/, which it adds with
  # --repository in the repository's layout.
  locals = lock.localRepositories or [ ];
  localTarget =
    repo:
    {
      ivy2Local = "user-home/.ivy2/local";
      m2Local = "user-home/.m2/repository";
    }
    .${repo.name} or "local-repositories/${repo.name}";
  localRepositoryOptions = lib.concatMapStrings (
    repo:
    lib.optionalString
      (
        !lib.elem repo.name [
          "ivy2Local"
          "m2Local"
        ]
      )
      ''--repository "${if repo.layout == "ivy" then "ivy:" else ""}file://$TMPDIR/${localTarget repo}${
        lib.optionalString (repo.layout == "ivy") "/${repo.pattern}"
      }" ''
  ) locals;
  localSpec = builtins.toJSON (
    map (repo: {
      inherit (repo) name files;
      source = "${localRepositories.${repo.name}
        or (throw "scala-cli-purifier: ${toString lockFile} reads the local repository ${repo.name}; pass it as localRepositories.${repo.name}")
      }";
      target = localTarget repo;
    }) locals
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
  nativeDeterminism = runCommand "scala-native-determinism" { } ''
    mkdir -p "$out"
    cp ${./DeterministicNative.java} DeterministicNative.java
    ${jdk'}/bin/javac --add-exports java.base/jdk.internal.org.objectweb.asm=ALL-UNNAMED \
      -d "$out" DeterministicNative.java
  '';
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
    ${lib.optionalString (platform == "Native") ''
      # Work on the cache copy, keeping the locked download untouched.
      # Stable hierarchy hashes give the same type IDs on each build.
      while IFS= read -r -d "" tools; do
        ${jdk'}/bin/java --add-exports java.base/jdk.internal.org.objectweb.asm=ALL-UNNAMED \
          -cp ${nativeDeterminism} DeterministicNative "$tools" "$tools.patched"
        mv -f "$tools.patched" "$tools"
      done < <(find -L "$COURSIER_CACHE" -path '*/org/scala-native/tools_*/*/tools_*.jar' \
        ! -name '*-sources.jar' ! -name '*-javadoc.jar' -print0)
    ''}
    ${lib.optionalString (projectDependencies != [ ]) ''
      ${python3}/bin/python ${purifier.coursierInputsTool}/coursier_inputs.py \
        --cache "$COURSIER_CACHE" --repository ${provided.maven} \
        --catalog ${provided.maven.catalog} --sources "$PWD" --lock ${lockFile}
    ''}
    mkdir -p "$TMPDIR/user-home"
    ${python3.interpreter} -I ${mirrorSource}/local_repositories.py stage "$localSpecPath" "$TMPDIR"
    PATH="${jdk'}/bin:$PATH" ${python3.interpreter} -I ${mirrorSource}/offline_mirror.py \
      "$COURSIER_CACHE" "$TMPDIR/mirror" ${lockFile} &
    mirrorPid=$!
    trap 'kill "$mirrorPid" 2>/dev/null || true' EXIT
    for _ in $(seq 300); do
      [ -s "$TMPDIR/mirror/java-options" ] && break
      kill -0 "$mirrorPid" 2>/dev/null || { echo "scala-cli-purifier: the offline mirror did not start" >&2; exit 1; }
      sleep 0.1
    done
    mirrorOptions=$(tr '\n' ' ' < "$TMPDIR/mirror/java-options")

    # Runs a scala-cli command; when it fails for want of a file, the
    # lockfile lacks a download the build needs.
    runScalaCli() {
      local status=0
      "$@" 2>&1 | tee -a "$TMPDIR/scala-cli.log" || status=''${PIPESTATUS[0]}
      if [ "$status" -ne 0 ]; then
        if grep -q -e 'Error downloading' -e 'not found:' -e 'is not in the locked downloads' "$TMPDIR/scala-cli.log"; then
          echo "scala-cli-purifier: scala-cli needs a file the lockfile does not hold, so the lockfile is incomplete." >&2
          echo "Run \`scala-cli-purifier lock\` in the project and commit the lockfile." >&2
        fi
        exit "$status"
      fi
    }
  ''
  # On macOS scala-cli ignores the XDG directories and keeps its own under the
  # Library of the home the password database names, which for a build user
  # is /var/empty; SCALA_CLI_HOME moves them.
  + lib.optionalString stdenvNoCC.buildPlatform.isDarwin ''
    export SCALA_CLI_HOME="$HOME/.scala-cli"
  '';
  stopMirror = ''kill "$mirrorPid" 2>/dev/null || true'';

  # A derivation that runs scala-cli on the project in the environment
  # above: `run` holds its commands, `install` what goes into $out.
  step =
    {
      pname,
      run,
      install,
      extraInputs ? [ ],
      passthru ? (_: { }),
      meta ? { },
    }:
    stdenvNoCC.mkDerivation (finalAttrs: {
      inherit pname;
      version = "locked";
      src = filteredSrc;
      nativeBuildInputs = [
        scala-cli
        openssl
      ]
      ++ platformInputs
      ++ extraInputs;
      dontConfigure = true;
      inherit localSpec;
      passAsFile = [ "localSpec" ];
      postUnpack = setup;
      buildPhase = ''
        runHook preBuild
        ${run}
        ${stopMirror}
        runHook postBuild
      '';
      installPhase = ''
        runHook preInstall
        ${install}
        runHook postInstall
      '';
      passthru = passthru finalAttrs;
      inherit meta;
    });

  installProgram =
    {
      JVM = ''
        # Jars hold the time each entry was written; normalizing them makes
        # a rebuild give the same bytes.
        strip-nondeterminism --type jar "$TMPDIR/out/program.jar"
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

  # The files of the package and of the release by name, relative to
  # their outputs, as the lock names them.
  programFiles = checked.outputs.${name}.files;
  releaseFiles = checked.outputs.release.files or { };

  package = step {
    pname = name;
    extraInputs = [
      makeWrapper
      strip-nondeterminism
    ];
    run = ''
      runScalaCli ${scalaCli "package"} ${packageOptions} -f ${lib.escapeShellArgs lock.arguments} </dev/null
    '';
    install = installProgram;
    passthru =
      finalAttrs:
      {
        artifacts = builtins.mapAttrs (_: rel: "${finalAttrs.finalPackage}/${rel}") programFiles;
      }
      // lib.optionalAttrs (lock.publish or false) {
        projections = purifier.jvmProjections {
          repository = release;
          runtimeRepository = "${release}/nix-support/runtime";
          dependencies = projectDependencies;
          inherit runCommand python3;
          name = "scala-cli-jvm-publications";
        };
      };
    meta = {
      description = "${name}, built by scala-cli-purifier";
      mainProgram = name;
    };
  };

  tests =
    assert
      lock.test or false
      || throw "scala-cli-purifier: ${toString lockFile} was not made with --test, so it lacks what `scala-cli test` downloads; re-lock with `scala-cli-purifier lock --test`.";
    step {
      pname = "${name}-tests";
      run = ''
        runScalaCli ${scalaCli "test"} ${lib.escapeShellArgs lock.arguments} </dev/null
      '';
      install = ''
        mkdir -p $out
        cp "$TMPDIR/scala-cli.log" $out/test.log
      '';
      meta.description = "the tests of ${name}, run by scala-cli-purifier";
    };

  release =
    assert
      lock.publish or false
      || throw "scala-cli-purifier: ${toString lockFile} was not made with --publish, so it lacks what `scala-cli publish` downloads; re-lock with `scala-cli-purifier lock --publish`.";
    step {
      pname = "${name}-release";
      extraInputs = [ strip-nondeterminism ];
      run = ''
        runScalaCli ${scalaCli "publish"} --publish-repository "file://$TMPDIR/release" ${lib.escapeShellArgs lock.arguments} </dev/null
                  ${scalaCli "compile"} --workspace "$TMPDIR/runtime-workspace" --print-class-path \
            ${lib.escapeShellArgs lock.arguments} </dev/null > "$TMPDIR/runtime-classpath.txt"
      '';
      install = ''
        cp -r "$TMPDIR/release" $out
        chmod -R u+w $out
        # Jars hold the time each entry was written; normalizing them makes
        # a rebuild give the same bytes, and the checksums are written again
        # for the normalized files.
        ${lib.optionalString (projectDependencies != [ ]) ''
          while IFS= read -r -d "" pom; do
            ${python3}/bin/python ${purifier.jvmRepositoryTool} \
              --rewrite-pom "$pom" --catalog ${provided.maven.catalog}
          done < <(find $out -name '*.pom' -print0)
        ''}
        find $out -name '*.jar' -print0 | xargs -0 -r strip-nondeterminism --type jar
        find $out -type f ! -name '*.md5' ! -name '*.sha1' | while read -r file; do
          if [ -f "$file.sha1" ]; then sha1sum "$file" | cut -d' ' -f1 | tr -d '\n' > "$file.sha1"; fi
          if [ -f "$file.md5" ]; then md5sum "$file" | cut -d' ' -f1 | tr -d '\n' > "$file.md5"; fi
        done
        for rel in ${lib.escapeShellArgs (builtins.attrValues releaseFiles)}; do
          if [ ! -f "$out/$rel" ]; then
            echo "scala-cli-purifier: the lock names $rel, which this build did not publish; re-lock with --publish" >&2
            exit 1
          fi
        done
                  ${python3}/bin/python ${./export-runtime.py} ${purifier.projectInputsTool} \
            "$TMPDIR/runtime-classpath.txt" "$TMPDIR/runtime-workspace" "$TMPDIR/coursier-cache" \
            ${provided.maven.catalog} "$out/nix-support/runtime"
      '';
      passthru = finalAttrs: {
        artifacts = builtins.mapAttrs (_: rel: "${finalAttrs.finalPackage}/${rel}") releaseFiles;
      };
      meta.description = "the release of ${name}, built by scala-cli-purifier";
    };
in
assert
  scala-cli.version == lock.scalaCliVersion
  || throw "scala-cli-purifier: ${toString lockFile} was made with scala-cli ${lock.scalaCliVersion}, and the build's scala-cli is ${scala-cli.version}. Re-lock with the scala-cli-purifier whose flake provides this builder, or pass that scala-cli as `scala-cli`.";
{
  packages.${name} = package;
  inherit tests release;
  projections = purifier.jvmProjections {
    repository = release;
    runtimeRepository = "${release}/nix-support/runtime";
    dependencies = projectDependencies;
    inherit runCommand python3;
    name = "scala-cli-jvm-publications";
  };
  fetches = checked.entries;
  artifacts = package.artifacts // lib.optionalAttrs (lock.publish or false) release.artifacts;
}
