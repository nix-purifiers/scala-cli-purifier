# SPDX-License-Identifier: MIT
{ ... }:
{
  config,
  inputs,
  lib,
  ...
}:
{

  imports = [ inputs.flake-parts.flakeModules.partitions ];

  debug = false;
  caisson = {
    libOverlays.exported = libOverlays: {
      inherit (libOverlays) default;
    };

    # The `default` entry of this flake's package overlay registry
    # (pkg-overlays/) is applied to the package set by default, and the flake
    # exports it as `pkgOverlays` and as the plain `overlays.default`. The
    # flake publishes the derivations of the scope itself, below, so the
    # export of the packages of the overlay is off.
    nixpkgs = {
      packages.export.enabled = false;
    };
  };

  # `nix run github:nix-purifiers/scala-cli-purifier -- lock` locks the
  # scala-cli project in the current directory.
  perSystem =
    { pkgs, self', ... }:
    {
      # The derivations of the scope pkgs.scala-cli-purifier, and the
      # default package.
      packages = lib.filterAttrs (_: lib.isDerivation) pkgs.scala-cli-purifier // {
        default = self'.packages.cli;
      };
      devShells.default = pkgs.mkShell {
        packages = [
          self'.packages.cli
          pkgs.scala-cli
        ];
      };
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
            checks = import ../../../tests {
              inherit pkgs;
              overlay = self.overlays.default;
              flakeLib = self.lib;
              inherit (self.packages.${system}) cli;
            };
            treefmt.programs.nixfmt.enable = true;
          };
      };
  };

}
