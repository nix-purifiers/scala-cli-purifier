# SPDX-License-Identifier: MIT
"""A local HTTP proxy that serves URLs from a coursier cache, for the
requests a build makes outside coursier's cache.

Coursier reads a `url=` dependency by sending a HEAD request to its URL on
every resolution, even in offline mode and with the file in its cache
(coursier.util.InMemoryRepository.exists). Offline, in the Nix sandbox or in
the scala-cli wrapper of the package, nothing answers that request. This proxy does:
the build's JVM sends its requests here (-Dhttp(s).proxyHost/Port), the
proxy terminates TLS with a certificate for the requested host, signed by a
certificate authority made for this run, which the JVM trusts through
-Djavax.net.ssl.trustStore, and it answers HEAD and GET from the file at the
URL's coursier cache path. A URL the cache lacks gets 404, and the proxy
logs it, since it names a download the lockfile is missing.

  python3 offline_mirror.py CACHE STATE LOCKFILE

writes the certificates and trust store to STATE, serves until killed, and
writes the Java options for the build to STATE/java-options once it
listens. It serves every URL of LOCKFILE, mirror URLs included, from the
cache path the lockfile records, and makes certificates for their hosts.
"""

import hashlib
import http.server
import json
import shutil
import ssl
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import coursier_cache

# The trust store holds only the certificate authority made for this run,
# whose key never leaves STATE, so its password protects nothing; this is
# the JDK's default trust store password.
STORE_PASS = "changeit"


def stderr_log(message):
    print(message, file=sys.stderr)


def openssl(*args):
    subprocess.run(["openssl", *args], check=True, capture_output=True)


def make_certificates(state, hosts, keytool):
    """A certificate authority, a certificate for each host signed by it,
    and a PKCS12 trust store holding the authority."""
    state.mkdir(parents=True, exist_ok=True)
    ca_key, ca_cert = state / "ca.key", state / "ca.crt"
    openssl(
        "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
        "-keyout", str(ca_key), "-out", str(ca_cert), "-days", "2",
        "-subj", "/CN=scala-cli-purifier offline mirror",
        "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign",
    )  # fmt: skip
    certificates = {}
    for host in hosts:
        key, csr, cert, ext = (state / f"{host}.{suffix}" for suffix in ("key", "csr", "crt", "ext"))
        ext.write_text(f"subjectAltName=DNS:{host}\nbasicConstraints=CA:FALSE\nextendedKeyUsage=serverAuth\n")
        openssl(
            "req", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
            "-keyout", str(key), "-out", str(csr), "-subj", f"/CN={host}",
        )  # fmt: skip
        openssl(
            "x509", "-req", "-in", str(csr), "-CA", str(ca_cert), "-CAkey", str(ca_key),
            "-CAcreateserial", "-out", str(cert), "-days", "2", "-extfile", str(ext),
        )  # fmt: skip
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        certificates[host] = context
    truststore = state / "truststore.p12"
    truststore.unlink(missing_ok=True)
    subprocess.run(
        [keytool, "-importcert", "-noprompt", "-alias", "offline-mirror", "-file", str(ca_cert),
         "-keystore", str(truststore), "-storetype", "PKCS12", "-storepass", STORE_PASS],
        check=True, capture_output=True,
    )  # fmt: skip
    return certificates, truststore


class Handler(http.server.BaseHTTPRequestHandler):
    """Answers HEAD and GET for one URL host from the cache. `origin` is
    `https://host[:port]` for a tunnel, and empty for a plain proxy request,
    whose path is the whole URL."""

    protocol_version = "HTTP/1.1"
    cache = Path(".")
    aliases = {}
    origin = ""
    log = staticmethod(stderr_log)

    def trace(self, event, status=None):
        # Bounded diagnostics identify URLs without printing credentials,
        # query strings, headers, or TLS exception text.
        with self.server.trace_lock:
            if self.server.trace_count >= 64:
                return
            self.server.trace_count += 1
        record = {"event": event, "urlSha256": hashlib.sha256(self.url().encode()).hexdigest()}
        if status is not None:
            record["status"] = status
        Handler.log("offline mirror trace: " + json.dumps(record, sort_keys=True))

    def url(self):
        return self.origin + self.path if self.origin else self.path

    def file(self):
        url = self.url()
        if url in self.aliases:
            return self.cache / self.aliases[url]
        try:
            return self.cache / coursier_cache.cache_path(url)
        except ValueError:
            return None

    def answer(self, body):
        path = self.file()
        if path is None or not path.is_file():
            self.trace(self.command, 404)
            Handler.log("offline mirror: requested URL is not in the locked downloads")
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.trace(self.command, 200)
        self.send_response(200)
        self.send_header("Content-Length", str(path.stat().st_size))
        self.end_headers()
        if body:
            with open(path, "rb") as f:
                shutil.copyfileobj(f, self.wfile)

    def do_HEAD(self):
        self.answer(body=False)

    def do_GET(self):
        self.answer(body=True)

    def log_message(self, format, *args):
        pass


class ProxyHandler(Handler):
    """The first request on a connection: CONNECT opens a TLS tunnel to a
    host the proxy has a certificate for; anything else is a plain HTTP
    proxy request."""

    certificates = {}

    def do_CONNECT(self):
        self.trace("CONNECT")
        host, _, port = self.path.partition(":")
        context = self.certificates.get(host)
        if context is None:
            self.trace("CONNECT", 404)
            Handler.log("offline mirror: CONNECT target is not a host of the locked downloads")
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200, "Connection established")
        self.end_headers()
        self.wfile.flush()
        try:
            tls = context.wrap_socket(self.connection, server_side=True)
        except ssl.SSLError:
            self.trace("TLS-failed")
            raise
        self.trace("TLS-established")
        origin = f"https://{host}" if port in ("", "443") else f"https://{host}:{port}"
        tunnel = type("TunnelHandler", (Handler,), {"origin": origin})
        try:
            tunnel(tls, self.client_address, self.server)
        finally:
            tls.close()
        self.close_connection = True


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        self.trace_lock = threading.Lock()
        self.trace_count = 0
        super().__init__(*args, **kwargs)

    def get_request(self):
        request = super().get_request()
        with self.trace_lock:
            if self.trace_count < 64:
                self.trace_count += 1
                Handler.log('offline mirror trace: {"event": "TCP-accepted"}')
        return request


def start(cache, state, hosts, keytool, log=None, aliases=None):
    """Starts the mirror in a thread and returns the Java options that send
    a JVM's requests to it, and the server, for shutdown(). `aliases` maps
    URLs to the cache paths of their files, for a URL whose file the cache
    holds under another URL's path, such as a mirror's URL."""
    certificates, truststore = make_certificates(Path(state), sorted(set(hosts)), keytool)
    Handler.cache = Path(cache)
    Handler.aliases = dict(aliases or {})
    Handler.log = staticmethod(log or stderr_log)
    ProxyHandler.certificates = certificates
    server = Server(("127.0.0.1", 0), ProxyHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    options = [
        "-Dhttp.proxyHost=127.0.0.1",
        f"-Dhttp.proxyPort={port}",
        "-Dhttps.proxyHost=127.0.0.1",
        f"-Dhttps.proxyPort={port}",
        "-Dhttp.nonProxyHosts=",
        f"-Djavax.net.ssl.trustStore={truststore}",
        f"-Djavax.net.ssl.trustStorePassword={STORE_PASS}",
        "-Djavax.net.ssl.trustStoreType=PKCS12",
    ]
    return options, server


def hosts_of(urls):
    """The hosts of the http(s) URLs among `urls`."""
    return sorted({urlsplit(u).hostname for u in urls if urlsplit(u).scheme in ("http", "https")})


def lock_aliases(artifacts):
    """The URL aliases of a lockfile's file entries: each entry's URL and
    mirror URLs, mapped to its cache path."""
    aliases = {}
    for entry in artifacts:
        if "url" in entry:
            for url in [entry["url"], *entry.get("mirrors", [])]:
                aliases[url] = entry["path"]
    return aliases


def main(argv):
    if len(argv) != 4:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    cache, state = argv[1], Path(argv[2])
    aliases = lock_aliases(json.loads(Path(argv[3]).read_text())["artifacts"])
    keytool = shutil.which("keytool")
    if keytool is None:
        print("offline mirror: keytool is not on the PATH", file=sys.stderr)
        return 1
    options, server = start(cache, state, hosts_of(aliases), keytool, aliases=aliases)
    (state / "java-options").write_text("\n".join(options) + "\n")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
