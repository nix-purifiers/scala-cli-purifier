# SPDX-License-Identifier: MIT
# Local, opt-in source build; not exported by either existing package scope.
{
  lib,
  stdenvNoCC,
  fetchurl,
  writeText,
  jdk17_headless,
  python3,
  git,
  coreutils,
  checkpoint ? "meta-build",
}:
let
  manifest = lib.importJSON ./inputs.json;
  inputs = map (
    input:
    input
    // {
      storePath = fetchurl {
        inherit (input) name url;
        hash = lib.convertHash {
          hash = input.sha256;
          hashAlgo = "sha256";
          toHashFormat = "sri";
        };
      };
    }
  ) manifest.inputs;
  distributionReceipt = writeText "scala-native-distribution.json" (
    builtins.toJSON {
      schemaVersion = 1;
      inherit (manifest) distribution;
      publicationVersion = manifest.variant;
      sourcePatchSha256 = builtins.hashFile "sha256" ./source.patch;
      inputManifestSha256 = builtins.hashFile "sha256" ./inputs.json;
      sourceRevisions = map (input: { inherit (input) revision url sha256; }) (
        builtins.filter (input: input.kind == "source") manifest.inputs
      );
      buildJavaVersion = jdk17_headless.version;
    }
  );
  inputManifest = writeText "scala-native-frozen-inputs.json" (
    builtins.toJSON (manifest // { inherit inputs; })
  );
in
assert lib.assertMsg (builtins.elem checkpoint [
  "meta-build"
  "sdk"
]) "unknown Native SDK checkpoint";
stdenvNoCC.mkDerivation {
  pname = "scala-native-source-${checkpoint}";
  version = "${manifest.variant}-${manifest.distribution.id}";
  dontUnpack = true;
  nativeBuildInputs = [
    jdk17_headless
    python3
    git
    coreutils
  ];
  inherit inputManifest checkpoint;
  sourcePatch = ./source.patch;
  buildPhase = ''
    runHook preBuild
    export HOME="$TMPDIR/home"
    mkdir -p "$HOME"
    python3 ${./prepare.py} "$inputManifest" "$PWD"
    bash ${./build.sh} "$checkpoint"
    if [ "$checkpoint" = sdk ]; then
      python3 ${./verify-publication.py} "$inputManifest" "$PWD/output-maven" "$PWD/publication"
    fi
    runHook postBuild
  '';
  installPhase = ''
    mkdir -p "$out"
    mkdir -p "$out/receipts"
    cp receipts/verified-inputs.json "$out/receipts/"
    cp ${distributionReceipt} "$out/receipts/distribution.json"
    # Transient SBT logs stay in the captured Nix build log, never in a
    # reproducible output. The checkpoint marker has no timing/scratch paths.
    printf '%s\n' "$checkpoint" > "$out/receipts/checkpoint"
    if [ "$checkpoint" = sdk ]; then
      cp -R publication/maven "$out/maven"
      cp publication/publication.json "$out/receipts/"
    fi
  '';
  passthru = {
    inherit manifest inputManifest;
    # Fetch-only inputs can be seeded from the reviewed acquisition directory
    # with nix-store --add-fixed sha256. No SDK compiler runs when evaluating.
    frozenInputs = map (input: input.storePath) inputs;
  };
  meta.description = "Opt-in coherent Scala Native source build with frozen offline inputs";
}
