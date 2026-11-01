# Development

## Layout

The repository is a caisson flake, laid out like sbt-nix-backend.

- `pkgs/cli/` is the `scala-cli-purifier` command. `scala_cli_purifier.py`
  holds the `lock` and `exec` verbs. `coursier_cache.py` maps coursier cache
  paths to URLs and turns a cache into lockfile entries; the Mill purifier
  keeps a byte-identical copy of it, until the shared nix-purifiers
  `purifier` library holds it.
- `nix/lib/` is the Nix library. `lock.nix` reads a lockfile, `fetch.nix`
  fetches one locked file, `dependencies.nix` builds the coursier cache of a
  lockfile, `realize.nix` is what `exec` builds without nixpkgs, and
  `build-scala-cli-project.nix` is `buildScalaCliProject`.
- `pkg-overlays/default/` adds the packages of `pkgs/` under
  `pkgs.scala-cli-purifier` and `buildScalaCliProject` beside them.
- `tests/` holds the flake checks, the fixtures and their lockfiles.

The lockfile's `artifacts` are file entries of the shared library's format:
`{ path, url, hash }`, where `path` is a relative path in the coursier
cache.

## Commands

Run each from the repository root.

```zsh
nix flake check                      # unit tests, fixture builds, nixfmt
nix build '.#checks.x86_64-linux.hello-runs'
bash tests/interactive.sh            # `exec` on the hello fixture; needs the Nix daemon
bash tests/relock-fixtures.sh        # re-lock the fixtures; needs the network
nix fmt
```

Re-lock the fixtures after a change to the scala-cli invocations, to the
lockfile format, or to the scala-cli version of the nixpkgs pin.

## Testing

The checks build each fixture with `buildScalaCliProject` in the sandbox
and run its program. The `hello` fixture was locked with `--test`, so its
build also runs its munit suite. The `cli` package runs the unit tests of
`coursier_cache.py` in its build.
