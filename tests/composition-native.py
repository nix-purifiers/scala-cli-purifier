#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Exercise native locking and execution with a Nix-provided JVM module."""
from pathlib import Path
import json, os, shutil, subprocess, tempfile

root = Path(__file__).resolve().parent.parent
system = subprocess.check_output(
    ['nix', 'eval', '--impure', '--raw', '--expr', 'builtins.currentSystem'], text=True).strip()
revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()
if subprocess.check_output(['git', 'status', '--porcelain'], cwd=root, text=True).strip():
    raise RuntimeError('commit the reviewed source before capturing composition evidence')
source_reference = f'git+file://{root}?ref=HEAD&rev={revision}'
snapshot = json.loads(subprocess.check_output(
    ['nix', 'flake', 'archive', '--json', '--no-update-lock-file', source_reference], text=True))['path']
if not Path(snapshot).is_dir() or Path(snapshot).resolve() != Path(snapshot):
    raise RuntimeError('archived source must be a materialized canonical directory')
with tempfile.TemporaryDirectory(prefix="purifier-native-composition-") as temporary:
    temporary = Path(temporary)
    subprocess.run(['nix-store', '--add-root', str(temporary / 'source'), '--realise', snapshot], check=True)
    expression = """let
      f = builtins.getFlake ("path:" + builtins.getEnv "COMPOSITION_FLAKE");
      p = f.inputs.nixpkgs.legacyPackages.${builtins.currentSystem};
    in p.runCommand "composition-downstream-tools" {} ''
      mkdir -p "$out/bin"
      ln -s ${p.maven}/bin/mvn "$out/bin/mvn"
      ln -s ${p.jdk21_headless.home} "$out/jdk"
    '' """
    tools = Path(subprocess.check_output(['nix', 'build', '--impure', '--print-out-paths',
        '--out-link', str(temporary / 'tools'), '--expr', expression], text=True,
        env=dict(os.environ, COMPOSITION_FLAKE=snapshot)).strip())
    package = subprocess.check_output(
        ['nix', 'build', 'path:' + snapshot + '#default', '--print-out-paths',
         '--out-link', str(temporary / 'cli')], text=True).strip().splitlines()[-1]
    cli = Path(package) / 'bin/scala-cli-purifier'
    for case, attribute in (('direct', 'provider'), ('transitive', 'transitiveProvider')):
        workspace = temporary / case
        shutil.copytree(Path(snapshot) / 'tests/composition-source', workspace)
        for path in (workspace, *workspace.rglob('*')):
            path.chmod(path.stat().st_mode | 0o200)
        (workspace / 'scala-cli-purifier.lock.json').unlink(missing_ok=True)
        if case == 'transitive':
            source = workspace / 'Greeting.scala'
            contents = source.read_text().replace('example:project-input:99.0', 'org.apache.commons:commons-text:1.13.0')
            contents = contents.replace('projectinput.Greeting.message(name)',
                '"[" + org.apache.commons.lang3.StringUtils.purifierIdentity() + "] " + org.apache.commons.text.WordUtils.capitalizeFully(name)')
            source.write_text(contents)
        reference='path:'+snapshot+'#checks.'+system+'.composition.'+attribute
        env=dict(os.environ,SCALA_CLI_PURIFIER_MAVEN_CENTRAL_MIRROR='https://repo.maven.apache.org/maven2')
        def run(*args):
            subprocess.run([str(x) for x in args],cwd=workspace,env=env,check=True)
        run(cli,'lock','--input',reference,'--name','library','--publish','--no-ide')
        locked=json.loads((workspace/'scala-cli-purifier.lock.json').read_text())
        assert len(locked['projectInputs'])==1
        supplied = '/example/project-input/' if case == 'direct' else '/org/apache/commons/commons-lang3/'
        assert not any(supplied in entry['path'] for entry in locked['artifacts'])
        run(cli,'prefetch')
        run(cli.parent/'scala-cli','run','.')
        run(cli,'publish',str(workspace/'published'))
        repository = workspace / 'published'
        pom = next((repository / 'io/github/nix-purifiers/example/greeting_3').glob('*/*.pom'))
        group, artifact = 'io.github.nix-purifiers.example', 'greeting_3'
        names = ['projectinput.Greeting'] if case == 'direct' else [
            'org.apache.commons.lang3.StringUtils', 'org.apache.commons.text.WordUtils']
        invoke = 'Class.forName("greeting.main").getMethod("main", String[].class).invoke(null, (Object)new String[0]);'
        downstream = temporary / (case+'-downstream')
        source = downstream / 'src/main/java/Downstream.java'
        source.parent.mkdir(parents=True)
        checks = '\n'.join('''String origin%d = Class.forName(%s).getProtectionDomain().getCodeSource().getLocation().toString();
          if (!origin%d.contains("-nix-")) throw new AssertionError(origin%d);''' % (index, json.dumps(name), index, index)
          for index, name in enumerate(names))
        source.write_text('public final class Downstream { public static void main(String[] args) throws Exception {'
                          + invoke + checks + 'System.out.println("ORDINARY MAVEN PASSED"); } }')
        (downstream / 'pom.xml').write_text(f'''<project><modelVersion>4.0.0</modelVersion>
          <groupId>example</groupId><artifactId>downstream</artifactId><version>1</version>
          <properties><maven.compiler.release>21</maven.compiler.release></properties>
          <repositories><repository><id>projected</id><url>{repository.as_uri()}</url></repository></repositories>
          <dependencies><dependency><groupId>{group}</groupId><artifactId>{artifact}</artifactId>
          <version>{pom.parent.name}</version></dependency></dependencies></project>''')
        plain = dict(os.environ, JAVA_HOME=str(tools / 'jdk'))
        subprocess.run([str(tools / 'bin/mvn'), '-B', '-Dmaven.repo.local='+str(temporary / 'ordinary-m2'),
                        'package', 'dependency:build-classpath', '-Dmdep.outputFile=classpath'],
                       cwd=downstream, env=plain, check=True)
        subprocess.run([str(tools / 'jdk/bin/java'), '-cp', str(downstream / 'target/classes')+os.pathsep
                        +(downstream / 'classpath').read_text().strip(), 'Downstream'],
                       cwd=downstream, env=plain, check=True)
        print('SCALA CLI '+case.upper()+' NATIVE ROUNDTRIP PASSED', flush=True)
