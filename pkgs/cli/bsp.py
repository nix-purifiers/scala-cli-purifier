# SPDX-License-Identifier: MIT
"""A minimal Build Server Protocol client, which asks a build server what an
editor such as Metals asks it on opening a project.

`editor_session` starts the server, initializes it, lists its build
targets, asks for their dependency sources (the sources jars an editor
navigates into), Scala and Java options and run environment, compiles
them, and shuts the server down. The lock run drives such a session so the
lockfile holds what an editor needs, and the tests drive one offline to
check that the editor's build server works from the lockfile alone.
"""

import json
import subprocess
import sys
import threading
from urllib.parse import unquote, urlsplit


class BspError(Exception):
    pass


class Server:
    def __init__(self, argv, cwd, env=None, log=None):
        self.process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.next_id = 0
        self.log = log or (lambda message: None)

    def send(self, message):
        body = json.dumps(message).encode()
        self.process.stdin.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
        self.process.stdin.flush()

    def receive(self):
        length = None
        while True:
            line = self.process.stdout.readline()
            if not line:
                raise BspError("the build server exited")
            line = line.strip()
            if not line:
                break
            name, _, value = line.decode().partition(":")
            if name.lower() == "content-length":
                length = int(value)
        if length is None:
            raise BspError("the build server sent a message without Content-Length")
        return json.loads(self.process.stdout.read(length))

    def request(self, method, params):
        self.next_id += 1
        self.send({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params})
        while True:
            message = self.receive()
            if message.get("id") == self.next_id and "method" not in message:
                if "error" in message:
                    raise BspError(f"{method} failed: {message['error']}")
                return message["result"]
            if message.get("method") in ("build/logMessage", "build/showMessage"):
                self.log(message.get("params", {}).get("message", ""))

    def notify(self, method, params=None):
        self.send({"jsonrpc": "2.0", "method": method, "params": params})


def editor_session(argv, cwd, root_uri, env=None, timeout=1800, log=None):
    """Runs the requests of an editor that opens the project and returns
    {"targets", "dependencySources", "compileStatus"}, where
    `dependencySources` are local paths. Raises BspError when a request
    fails or the server does not answer within `timeout` seconds."""
    server = Server(argv, cwd, env, log)
    timer = threading.Timer(timeout, server.process.kill)
    timer.start()
    try:
        server.request(
            "build/initialize",
            {
                "displayName": "scala-cli-purifier",
                "version": "1",
                "bspVersion": "2.1.1",
                "rootUri": root_uri,
                "capabilities": {"languageIds": ["scala", "java"]},
            },
        )
        server.notify("build/initialized")
        targets = [t["id"] for t in server.request("workspace/buildTargets", {})["targets"]]
        sources = server.request("buildTarget/dependencySources", {"targets": targets})
        server.request("buildTarget/scalacOptions", {"targets": targets})
        server.request("buildTarget/javacOptions", {"targets": targets})
        compiled = server.request("buildTarget/compile", {"targets": targets})
        server.request("build/shutdown", None)
        server.notify("build/exit")
        paths = []
        for item in sources.get("items", []):
            for uri in item.get("sources", []):
                parsed = urlsplit(uri)
                paths.append(unquote(parsed.path) if parsed.scheme == "file" else uri)
        return {"targets": targets, "dependencySources": paths, "compileStatus": compiled["statusCode"]}
    finally:
        timer.cancel()
        try:
            server.process.stdin.close()
            server.process.wait(timeout=30)
        except (subprocess.TimeoutExpired, OSError):
            server.process.kill()


def main(argv):
    """python3 bsp.py PROJECT: an editor session against the build server of
    PROJECT/.bsp/scala-cli.json, which passes when the compile succeeds and
    every dependency source the server names is a file."""
    from pathlib import Path

    project = Path(argv[1]).resolve()
    config = json.loads((project / ".bsp" / "scala-cli.json").read_text())
    result = editor_session(config["argv"], project, project.as_uri(), log=lambda m: print(m, file=sys.stderr))
    missing = [p for p in result["dependencySources"] if not Path(p).is_file()]
    print(f"build targets: {len(result['targets'])}, dependency sources: {len(result['dependencySources'])}")
    print(f"compile status: {result['compileStatus']}")
    if missing:
        print("dependency sources that are not files:", *missing, sep="\n  ", file=sys.stderr)
    return 0 if result["compileStatus"] == 1 and not missing and result["dependencySources"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
