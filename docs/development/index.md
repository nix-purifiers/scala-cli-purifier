# Development

## Layout

The repository is a caisson flake, laid out like sbt-nix-backend.

- `pkgs/cli/` is the `scala-cli-purifier` command and the `scala-cli`
  wrapper the package installs beside it.
  - `scala_cli_purifier.py` holds the `lock`, `update`, `prefetch` and
    `publish` verbs, and the wrapper, which runs when the program is
    started as `scala-cli`.
  - `lockfile.py` checks the checksum and the directives hash of a
    lockfile, with the same definitions as `nix/lib/lock.nix`.
  - `coursier_cache.py` maps coursier cache paths to URLs and turns a cache
    into lockfile entries. The Mill purifier keeps a byte-identical copy of
    it, until the shared nix-purifiers `purifier` library holds it.
  - `offline_mirror.py` is the local proxy that answers the requests
    scala-cli makes outside the coursier cache, from the locked files. The
    offline check of `lock`, the wrapper and the Nix build run it.
  - `bsp.py` is a Build Server Protocol client that opens a project the way
    an editor does, for the lock run, its offline check and the tests.
  - `publication.py` uploads a release, signs it, or writes a Maven
    Central bundle.
  - `local_repositories.py` finds a build's local repositories among its
    resolvers, records the files its class path reaches into, and stages
    them with their hashes checked, in the offline check and in the Nix
    build.
- `nix/lib/` is the Nix library. `lock.nix` holds `readLock`, which reads
  a lockfile into the lock in memory of the purifier contract, with its
  problems; `build-scala-cli-project.nix` is `buildScalaCliProject`;
  `prefetch.nix` is what `prefetch` builds and `release.nix` what
  `publish` builds. The shared
  [purifier](https://github.com/nix-purifiers/purifier) library checks the
  lock (`checkLock`), picks the entries of a system (`entriesFor`), fetches
  the locked files and lays them out (`tree`). The builder takes it as
  `lib.purifier` of the flake, and `prefetch` and `publish` import its root
  `default.nix` at the revision of the flake's `flake.lock`.
- `lib-overlays/default/` adds `readLock` to the library as
  `lib.scala-cli-purifier.readLock`.
- `pkg-overlays/default/` adds the scope `pkgs.scala-cli-purifier`, which
  `pkgs/default.nix` lists: the command `cli` and the builder
  `buildScalaCliProject`, a callPackage package that takes the purifier
  library of this flake.
- `tests/` holds the flake checks, the fixtures and their lockfiles.

The lockfile's `artifacts` are entries of the shared library's format,
without their mirrors, which `readLock` adds. A file entry is
`{ path, url, hash }`, where `path` is a relative path in the coursier
cache. An inline entry is `{ path, contents }`, for version listings the
offline build needs.

## Commands

Run each from the repository root.

```zsh
nix flake check                      # unit tests, fixture builds, conformance checks, nixfmt
nix build '.#checks.x86_64-linux.url-runs'
bash tests/interactive.sh            # prefetch, the wrapper, publish; needs the Nix daemon
bash tests/relock-fixtures.sh        # re-lock the fixtures; needs the network
bash tests/relock-reproduces.sh      # conformance-relock; needs the network
bash tests/conformance-update.sh     # conformance-update; needs the network
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
| `hello` | Scala 3 with a dependency and a munit suite |
| `script` | a single-file Scala 2.13 program |
| `sources` | a repository besides Maven Central, a jar in the project, a version range held inline |
| `url` | a dependency downloaded from a URL, through the offline mirror |
| `local` | dependencies from `ivy2Local` and `m2Local`, given as `localRepositories` |
| `js` | Scala.js, linked on the JVM, run and tested on Node.js |
| `native` | Scala Native, compiled with clang, run and tested |
| `library` | `--publish`: the release and its files |

The `*-outputs` checks require that `artifacts` names exactly the expected
outputs at evaluation time, then build and link each one, and `read-lock`
requires that `checkLock` accepts the lock of every fixture. The checks
named `conformance-*` are those of the purifier contract; the two that need
the network, `conformance-relock` and `conformance-update`, run as CI jobs
of those names. The `cli` package runs the unit tests in its build.
`tests/interactive.sh` covers `prefetch` and the wrapper, including a URL
dependency and an external lockfile, a `file:` repository outside the
project (locked, then built in the sandbox with `localRepositories`), and
`publish` to a directory and to a bundle.

The `local` fixture is locked with `SCALA_CLI_PURIFIER_USER_HOME` pointing
at `tests/fixtures/local-home`, whose repositories
`tests/local-libraries/publish.sh` writes.
