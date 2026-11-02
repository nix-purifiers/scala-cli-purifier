#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Exercise native locking and execution with a Nix-provided JVM module."""
from pathlib import Path
import json, os, shutil, subprocess, tempfile

root = Path(__file__).resolve().parent.parent
system = subprocess.check_output(
    ['nix', 'eval', '--impure', '--raw', '--expr', 'builtins.currentSystem'], text=True).strip()
snapshot = json.loads(subprocess.check_output(
    ['nix', 'flake', 'metadata', '--json', '--no-write-lock-file', 'path:' + str(root)], text=True))['path']
with tempfile.TemporaryDirectory(prefix="purifier-native-composition-") as temporary:
    temporary = Path(temporary)
    package = subprocess.check_output(
        ['nix', 'build', str(root) + '#default', '--print-out-paths',
         '--out-link', str(temporary / 'cli')], text=True).strip().splitlines()[-1]
    cli = Path(package) / 'bin/scala-cli-purifier'
    workspace = temporary / 'project'
    shutil.copytree(root / 'tests/composition-source', workspace)
    (workspace / 'scala-cli-purifier.lock.json').unlink(missing_ok=True)
    reference='path:'+snapshot+'#checks.'+system+'.composition.provider'
    env=dict(os.environ,SCALA_CLI_PURIFIER_MAVEN_CENTRAL_MIRROR='https://repo.maven.apache.org/maven2')
    def run(*args):
        subprocess.run([str(x) for x in args],cwd=workspace,env=env,check=True)
    run(cli,'lock','--input',reference,'--name','library','--publish','--no-ide')
    locked=json.loads((workspace/'scala-cli-purifier.lock.json').read_text())
    assert len(locked['projectInputs'])==1
    assert not any('/example/project-input/' in entry['path'] for entry in locked['artifacts'])
    run(cli,'prefetch')
    run(cli.parent/'scala-cli','run','.')
    run(cli,'publish',str(workspace/'published'))
    print('SCALA CLI NATIVE ROUNDTRIP PASSED')
