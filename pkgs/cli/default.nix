# SPDX-License-Identifier: MIT
#
# The scala-cli-purifier command: `lock`, `update`, `prefetch` and
# `publish` (see scala_cli_purifier.py), and the scala-cli wrapper of the
# everyday loop. It runs the scala-cli of this package set, which is the
# one buildScalaCliProject checks locks against, and runs scala-cli's
# compiler on the JDK 21 of this set unless SCALA_CLI_PURIFIER_JAVA_HOME
# names another; it ignores JAVA_HOME. Node.js,
# clang and `which` come first on the PATH, as in the Nix build, for
# Scala.js tests and for Scala Native, which finds clang with `which`, and
# openssl makes the offline mirror's certificates.
{
  lib,
  path,
  stdenvNoCC,
  makeWrapper,
  python3,
  gnupg,
  scala-cli,
  native-sdk ? null,
  jdk21_headless,
  nodejs,
  clang,
  which,
  openssl,
}:
let
  # The shared nix-purifiers library at the revision of this flake's
  # flake.lock, which `prefetch` and `publish` evaluate outside the flake,
  # so the command and the flake use the same library.
  flakeLock = builtins.fromJSON (builtins.readFile ../../flake.lock);
  purifierSource = builtins.fetchTree flakeLock.nodes.${flakeLock.nodes.root.inputs.purifier}.locked;

  # The Nix library, which `prefetch` and `publish` evaluate, with the offline
  # mirror it runs, at the same paths relative to each other as in the
  # repository.
  librarySource = lib.fileset.toSource {
    root = ../..;
    fileset = lib.fileset.unions [
      ../../nix/lib
      ./offline_mirror.py
      ./local_repositories.py
      ./lockfile.py
      ./coursier_cache.py
    ];
  };
in
stdenvNoCC.mkDerivation {
  pname = "scala-cli-purifier";
  version = lib.trim (builtins.readFile ../../version.txt);
  passthru = {
    inherit librarySource;
    initialScalaCli = scala-cli;
    configuredJava = jdk21_headless;
    configuredNativeSDK = native-sdk;
  };

  src = lib.fileset.toSource {
    root = ./.;
    fileset = lib.fileset.unions [
      ./scala_cli_purifier.py
      ./bsp.py
      ./coursier_cache.py
      ./local_repositories.py
      ./lockfile.py
      ./offline_mirror.py
      ./publication.py
      ./test_coursier_cache.py
      ./test_local_repositories.py
      ./test_lockfile.py
      ./test_offline_mirror.py
      ./test_scala_cli_purifier.py
      ./test_publication.py
    ];
  };

  nativeBuildInputs = [ makeWrapper ];
  nativeCheckInputs = [
    python3
    gnupg
    openssl
    jdk21_headless
  ];

  doCheck = true;
  # Mirror unit tests start the same loopback-only server as a locked build.
  __darwinAllowLocalNetworking = stdenvNoCC.buildPlatform.isDarwin;
  checkPhase = ''
    runHook preCheck
    export HOME="$TMPDIR/home"
    python3 -m unittest -v test_coursier_cache test_local_repositories test_lockfile test_publication \
      test_offline_mirror test_scala_cli_purifier
    runHook postCheck
  '';

  installPhase = ''
    runHook preInstall
    install -Dm644 -t "$out/libexec/scala-cli-purifier" \
      scala_cli_purifier.py bsp.py coursier_cache.py local_repositories.py lockfile.py offline_mirror.py publication.py
    wrapperArgs=(
      --add-flags "-I $out/libexec/scala-cli-purifier/scala_cli_purifier.py"
      --prefix PATH : ${
        lib.makeBinPath [
          nodejs
          clang
          which
          openssl
        ]
      }
      --set-default SCALA_CLI_PURIFIER_SCALA_CLI ${lib.getExe' scala-cli "scala-cli"}
      --set SCALA_CLI_PURIFIER_SCALA_CLI_VERSION ${scala-cli.version}
      --set SCALA_CLI_PURIFIER_SCALA_CLI_PACKAGE ${scala-cli}
      --set-default SCALA_CLI_PURIFIER_JAVA_HOME ${jdk21_headless.home}
      --set SCALA_CLI_PURIFIER_LIB ${librarySource}/nix/lib
      --set SCALA_CLI_PURIFIER_NIXPKGS ${path}
      --set SCALA_CLI_PURIFIER_PURIFIER ${purifierSource}
      --set SCALA_CLI_PURIFIER_WRAPPER "$out/bin/scala-cli"
      ${lib.optionalString (native-sdk != null) ''
        --set SCALA_CLI_PURIFIER_NATIVE_SDK_PACKAGE ${native-sdk}
      ''}
    )
    makeWrapper ${python3.interpreter} "$out/bin/scala-cli-purifier" "''${wrapperArgs[@]}"
    # The same program as `scala-cli`: in a project with a lockfile it runs
    # scala-cli offline on the prefetched downloads.
    makeWrapper ${python3.interpreter} "$out/bin/scala-cli" "''${wrapperArgs[@]}" \
      --set SCALA_CLI_PURIFIER_AS_SCALA_CLI 1
    runHook postInstall
  '';

  meta = {
    description = "Lock the downloads of a scala-cli project, prefetch them for offline work, and publish what Nix built";
    mainProgram = "scala-cli-purifier";
    license = lib.licenses.mit;
  };
}
