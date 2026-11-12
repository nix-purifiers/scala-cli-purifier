# SPDX-License-Identifier: MIT
{ closure-lib, ... }:
{
  config,
  inputs,
  lib,
  ...
}:
{

  imports = [
    inputs.flake-parts.flakeModules.partitions
    # Publishes the derivations of the scope pkgs.scala-cli-purifier as the
    # packages of the flake.
    closure-lib.caisson.modules.flake."purifier/purifier-packages"
  ];

  purifier.scope = "scala-cli-purifier";

  debug = false;
  caisson = {
    libOverlays.exported = libOverlays: {
      inherit (libOverlays) default;
    };
  };

  # `nix run github:nix-purifiers/scala-cli-purifier -- lock` locks the
  # scala-cli project in the current directory.
  perSystem =
    { pkgs, self', ... }:
    {
      packages.default = self'.packages.cli;
      packages.cli-jvm = pkgs.scala-cli-purifier-jvm.cli;
      packages.cli-native = pkgs.scala-cli-purifier-native.cli;
      devShells.default = pkgs.mkShell {
        packages = [
          self'.packages.cli
          pkgs.scala-cli
        ];
      };
      devShells.jvm = pkgs.mkShell { packages = [ pkgs.scala-cli-purifier-jvm.cli ]; };
    };

  partitionedAttrs.checks = "checks";
  partitionedAttrs.formatter = "formatter";

  partitions.formatter = {
    extraInputs = (lib.caisson.pins.flake-compat ../../../tests/dependencies).sources;
    module =
      { inputs, ... }:
      {
        imports = [ inputs.treefmt-nix.flakeModule ];
        perSystem.treefmt.programs.nixfmt.enable = true;
      };
  };

  partitions.checks = {
    extraInputs = (lib.caisson.pins.flake-compat ../../../tests/dependencies).sources;
    module =
      { inputs, self, ... }:
      {
        imports = [ inputs.treefmt-nix.flakeModule ];
        perSystem =
          { pkgs, system, ... }:
          {
            checks =
              (import ../../../tests {
                inherit pkgs;
                overlay = self.overlays.default;
                flakeLib = self.lib;
                localCli = self.packages.${system}.cli;
              })
              // {
                jvm-profile = import ../../../tests/jvm-profile.nix {
                  inherit pkgs;
                  overlay = self.overlays.default;
                  flakeLib = self.lib;
                };
              };
            treefmt.programs.nixfmt.enable = true;
          };
      };
  };

}
