# Development

## Layout

The repository is a caisson flake, laid out like sbt-nix-backend.

- `pkgs/cli/` is the `scala-cli-purifier` command.
  - `scala_cli_purifier.py` holds the `lock`, `exec` and `publish` verbs.
  - `coursier_cache.py` maps coursier cache paths to URLs and turns a cache
    into lockfile entries. The Mill purifier keeps a byte-identical copy of
    it, until the shared nix-purifiers `purifier` library holds it.
  - `offline_mirror.py` is the local proxy that answers the requests
    scala-cli makes outside coursier's cache, from the locked files. The
    offline check of `lock`, `exec` and the Nix build run it.
  - `publication.py` uploads a publication, signs it, or writes a Maven
    Central bundle.
  - `local_repositories.py` finds a build's local repositories among its
    resolvers, records the files its class path reaches into, and stages
    them with their hashes checked, in the offline check and in the Nix
    build.
- `nix/lib/` is the Nix library. `lock.nix` reads a lockfile, `fetch.nix`
  fetches one locked file, `dependencies.nix` builds the coursier cache of a
  lockfile, `realize.nix` is what `exec` builds without nixpkgs,
  `publication.nix` is what `publish` builds, and
  `build-scala-cli-project.nix` is `buildScalaCliProject`.
- `pkg-overlays/default/` adds the packages of `pkgs/` under
  `pkgs.scala-cli-purifier` and `buildScalaCliProject` beside them.
- `tests/` holds the flake checks, the fixtures and their lockfiles.

The lockfile's `artifacts` are entries of the shared library's format. A
file entry is `{ path, url, mirrors, hash }`, where `path` is a relative
path in the coursier cache and `mirrors` is empty. An inline entry is
`{ path, contents }`, for version listings the offline build needs.

## Commands

Run each from the repository root.

```zsh
nix flake check                      # unit tests, fixture builds, outputs, nixfmt
nix build '.#checks.x86_64-linux.url-runs'
bash tests/interactive.sh            # `exec` and `publish`; needs the Nix daemon
bash tests/relock-fixtures.sh        # re-lock the fixtures; needs the network
nix fmt
```

Re-lock the fixtures after a change to the scala-cli invocations, to the
lockfile format, or to the scala-cli version of the nixpkgs pin. The
native scala-cli writes Scala.js linker output and publications to `/tmp`;
on a machine whose `/tmp` is read-only, point `SCALA_CLI_PURIFIER_SCALA_CLI`
at a launcher of the JVM build of the same scala-cli version
(`org.virtuslab.scala-cli:cli_3`) for the `js` and `library` fixtures.

## Testing

The checks build each fixture with `buildScalaCliProject` in the sandbox
and run its program:

| Fixture | Covers |
|---|---|
| `hello` | Scala 3 with a dependency; its munit suite runs in the build |
| `script` | a single-file Scala 2.13 program |
| `sources` | a repository besides Maven Central, a jar in the project, a version range held inline |
| `url` | a dependency downloaded from a URL, through the offline mirror |
| `local` | dependencies from `ivy2Local` and `m2Local`, given as `localRepositories` |
| `js` | Scala.js, linked on the JVM, run and tested on Node.js |
| `native` | Scala Native, compiled with clang, tested in the build |
| `library` | `--publish`: the publication and its files |

The `*-outputs` checks require that `artifacts` names exactly the expected
outputs at evaluation time, then build and link each one. The `cli`
package runs the unit tests of `coursier_cache.py`, `publication.py` and
`offline_mirror.py` in its build. `tests/interactive.sh` covers `exec`,
including a URL dependency, a `file:` repository outside the project
(locked, then built in the sandbox with `localRepositories`), and
`publish` to a directory and to a bundle.

The `local` fixture is locked with `SCALA_CLI_PURIFIER_USER_HOME` pointing
at `tests/fixtures/local-home`, whose repositories
`tests/local-libraries/publish.sh` writes.
