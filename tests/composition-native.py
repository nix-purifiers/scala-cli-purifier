#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Exercise native locking and execution with a Nix-provided JVM module."""
from pathlib import Path
import json, os, shutil, subprocess, tempfile, hashlib
from urllib.parse import urlsplit, unquote
import xml.etree.ElementTree as ET


profile = os.environ.get("SCALA_CLI_TIER2_PROFILE", "native")
if profile not in ("native", "jvm"):
    raise RuntimeError("profile must be native or jvm")
cli_attribute = "jvm-profile.fixture-cli" if profile == "jvm" else "fixture-cli"
check_prefix = "jvm-profile." if profile == "jvm" else ""
os.environ["NIX_CONFIG"] = os.environ.get("NIX_CONFIG", "") + "\nsandbox = true\nmax-jobs = 2\ncores = 2\neval-cache = false\n"
results = Path(os.environ["SCALA_CLI_COMPOSITION_RESULTS"]).resolve() if os.environ.get("SCALA_CLI_COMPOSITION_RESULTS") else None
receipt = {"profile": profile, "commands": [], "cases": [], "complete": False}
if results:
    results.mkdir(parents=True, exist_ok=False)

def save():
    if results:
        (results / "report.json").write_text(json.dumps(receipt, indent=2) + "\n")

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
receipt.update(revision=revision, source=snapshot,
               sourceNarHash=subprocess.check_output(['nix','hash','path',snapshot],text=True).strip())
save()

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
        ['nix', 'build', 'path:' + snapshot + '#checks.' + system + '.' + cli_attribute, '--print-out-paths',
         '--out-link', str(temporary / 'cli')], text=True).strip().splitlines()[-1]
    cli = Path(package) / 'bin/scala-cli-purifier'
    receipt["commandPackage"] = {"output":package,
        "derivation":subprocess.check_output(['nix-store','-q','--deriver',package],text=True).strip(),
        "narHash":subprocess.check_output(['nix','hash','path',package],text=True).strip()}
    if profile == "jvm":
        sdk = subprocess.check_output(['nix','build','path:'+snapshot+'#jvm-sdk','--print-out-paths',
            '--out-link',str(temporary/'initial-frontend')],text=True).strip().splitlines()[-1]
        receipt["initialFrontend"] = json.loads((Path(sdk)/'share/scala-cli-jvm-sdk/manifest.json').read_text())
    save()

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
        reference='path:'+snapshot+'#checks.'+system+'.'+check_prefix+'composition.'+attribute
        env=dict(os.environ,SCALA_CLI_PURIFIER_MAVEN_CENTRAL_MIRROR='https://repo.maven.apache.org/maven2')
        def run(*args):
            command = [str(x) for x in args]
            if results:
                name = str(len(receipt["commands"])) + "-" + case
                process = subprocess.run(command,cwd=workspace,env=env,text=True,capture_output=True)
                output = process.stdout + process.stderr
                (results / (name + ".log")).write_text(output)
                receipt["commands"].append({"command":command,"returncode":process.returncode,"log":name+".log"})
                save()
                print(output, end="", flush=True)
                process.check_returncode()
            else:
                subprocess.run(command,cwd=workspace,env=env,check=True)
        capture = [cli,'lock','--input',reference,'--name','library','--publish','--no-ide']
        if profile == "jvm":
            capture += ['--','--verbose','.']
        run(*capture)
        locked=json.loads((workspace/'scala-cli-purifier.lock.json').read_text())
        if results:
            shutil.copy2(workspace/'scala-cli-purifier.lock.json', results/(case+'-lock.json'))
            for source in workspace.glob('*.scala'):
                shutil.copy2(source, results/(case+'-'+source.name))

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
        # Maven does not consume HTTP_PROXY automatically. Use its public
        # settings format in this isolated downstream test; never print it.
        settings = ET.Element("settings")
        mirror = ET.SubElement(ET.SubElement(settings, "mirrors"), "mirror")
        for key, value in {"id":"test-central", "mirrorOf":"external:*", "url":"https://repo.maven.apache.org/maven2"}.items():
            ET.SubElement(mirror, key).text = value

        proxy_url = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY") or os.environ.get("http_proxy") or os.environ.get("HTTP_PROXY")
        if proxy_url:
            proxy = urlsplit(proxy_url)
            entry = ET.SubElement(ET.SubElement(settings, "proxies"), "proxy")
            values = {"id":"test-environment", "active":"true", "protocol":proxy.scheme,
                      "host":proxy.hostname, "port":str(proxy.port or 80)}
            no_proxy = os.environ.get("no_proxy") or os.environ.get("NO_PROXY")
            if no_proxy:
                values["nonProxyHosts"] = "|".join(x.strip() for x in no_proxy.split(",") if x.strip())
            if proxy.username is not None:
                values["username"] = unquote(proxy.username)
                values["password"] = unquote(proxy.password or "")
            for key, value in values.items():
                ET.SubElement(entry, key).text = value
        settings_path = temporary / "ordinary-maven-settings.xml"
        settings_path.write_bytes(ET.tostring(settings, encoding="utf-8", xml_declaration=True))
        settings_path.chmod(0o600)

        subprocess.run([str(tools / 'bin/mvn'), '-B', '-s', str(settings_path), '-Dmaven.repo.local='+str(temporary / 'ordinary-m2'),
                        'package', 'org.apache.maven.plugins:maven-dependency-plugin:3.11.0:build-classpath', '-Dmdep.outputFile=classpath'],
                       cwd=downstream, env=plain, check=True)
        subprocess.run([str(tools / 'jdk/bin/java'), '-cp', str(downstream / 'target/classes')+os.pathsep
                        +(downstream / 'classpath').read_text().strip(), 'Downstream'],
                       cwd=downstream, env=plain, check=True)
        receipt["cases"].append({"case":case,"lockSha256":hashlib.sha256((workspace/'scala-cli-purifier.lock.json').read_bytes()).hexdigest(),
            "recordedLockSdk":{k:locked[k] for k in ('scalaCliVersion','javaVersion')},
            "projectInputs":locked['projectInputs'],"downstream":"ORDINARY MAVEN PASSED"})
        save()
        print('SCALA CLI '+case.upper()+' NATIVE ROUNDTRIP PASSED', flush=True)

receipt["complete"] = True
save()
