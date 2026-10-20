"""Mode relais et mode PHP intégré.

Relais : AikoServe se place devant un serveur qui tourne déjà sur l'ordinateur (MAMP, Local,
Vite, Laravel…) et le rend accessible au téléphone, avec le rechargement automatique.
PHP intégré : AikoServe lance son propre PHP (`php -S`) puis le relaie de la même façon.
"""
from __future__ import annotations

import http.client
import os
import re
import shutil
import ssl
import subprocess
import sys
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .server import Hub, Watcher, free_port, inject, local_addresses, serve_events

HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
              "transfer-encoding", "upgrade", "content-length", "content-encoding"}
TEXT_TYPES = ("text/", "application/javascript", "application/json", "application/xml", "image/svg+xml",
              "application/x-javascript", "application/ld+json", "application/manifest+json")

# Ce qu'affiche PHP quand la base de données est injoignable
DB_ERROR_PATTERNS = [rb"SQLSTATE\[HY000\] \[2002\]", rb"SQLSTATE\[HY000\] \[1045\]", rb"SQLSTATE\[HY000\] \[2054\]",
                     rb"mysqli_sql_exception", rb"Error establishing a database connection",
                     rb"could not find driver", rb"mysqli_connect\(\)", rb"Connection refused.{0,80}mysql",
                     rb"SQLSTATE\[08006\]", rb"Class \"mysqli\" not found", rb"Class \"PDO\" not found",
                     rb"Call to undefined function mysqli_", rb"Call to undefined function mysql_",
                     rb"Class \"PDO\" not found", rb"could not find driver"]
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def normalize_url(text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    if re.fullmatch(r"\d{2,5}", text):  # juste un port : « 8888 »
        text = f"localhost:{text}"
    if "://" not in text:
        text = "http://" + text
    return text.rstrip("/")


def make_proxy_handler(upstream: str, hub: Hub, state: dict):
    up = urlsplit(upstream)
    host = up.hostname or "127.0.0.1"
    port = up.port or (443 if up.scheme == "https" else 80)
    origins = {f"{up.scheme}://{up.netloc}"}
    if host in ("localhost", "127.0.0.1"):
        for h in ("localhost", "127.0.0.1"):
            origins.add(f"{up.scheme}://{h}:{port}" if up.port else f"{up.scheme}://{h}")

    class ProxyHandler(BaseHTTPRequestHandler):
        server_version = "AikoServe"
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def do_GET(self):
            self._proxy()

        do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = do_OPTIONS = do_GET

        def _front(self) -> str:
            return f"http://{self.headers.get('Host') or 'localhost'}"

        def _rewrite(self, data: bytes) -> bytes:
            """Les adresses « localhost:8888 » du site deviennent celle vue par le téléphone."""
            front = self._front().encode()
            for o in origins:
                ob = o.encode()
                data = data.replace(ob, front)
                data = data.replace(ob.replace(b"/", b"\\/"), front.replace(b"/", b"\\/"))  # JSON échappé
            return data

        def _proxy(self):
            if self.path.startswith("/__aikoserve/events"):
                return serve_events(self, hub, state)
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else None
            headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_BY_HOP and k.lower() != "host"}
            headers["Host"] = up.netloc
            headers["Accept-Encoding"] = "identity"
            headers["X-Forwarded-Host"] = self.headers.get("Host", "")
            headers["X-Forwarded-Proto"] = "http"
            for k in ("Origin", "Referer"):
                if k in headers:
                    headers[k] = headers[k].replace(self._front(), f"{up.scheme}://{up.netloc}")
            try:
                if up.scheme == "https":
                    conn = http.client.HTTPSConnection(host, port, timeout=120,
                                                       context=ssl._create_unverified_context())  # certificats locaux
                else:
                    conn = http.client.HTTPConnection(host, port, timeout=120)
                conn.request(self.command, self.path, body=body, headers=headers)
                resp = conn.getresponse()
                data = resp.read()
                conn.close()
            except (ConnectionRefusedError, OSError, http.client.HTTPException) as e:
                return self._unreachable(e)

            ctype = resp.getheader("Content-Type", "") or ""
            if any(ctype.startswith(t) for t in TEXT_TYPES):
                data = self._rewrite(data)
            if ctype.startswith("text/html"):
                if any(re.search(p, data, re.I | re.S) for p in DB_ERROR_PATTERNS):
                    state["on_db_error"]()
                data = inject(data)

            self.send_response(resp.status, resp.reason)
            for k, v in resp.getheaders():
                kl = k.lower()
                if kl in HOP_BY_HOP:
                    continue
                if kl == "location":
                    for o in origins:
                        v = v.replace(o, self._front())
                if kl == "set-cookie":
                    v = re.sub(r";\s*domain=[^;]+", "", v, flags=re.I)  # cookies valables aussi pour le téléphone
                    v = re.sub(r";\s*secure", "", v, flags=re.I) if up.scheme == "https" else v
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def _unreachable(self, err):
            msg = (f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
                   f"<div style='font:15px system-ui;margin:32px;line-height:1.5'><h3>Le site ne répond pas</h3>"
                   f"<p>AikoServe n'arrive pas à joindre <b>{upstream}</b>.</p>"
                   f"<p>Vérifie qu'il est bien lancé (MAMP, Local, <code>npm run dev</code>…), puis recharge.</p>"
                   f"<p style='color:#888'>{type(err).__name__}</p></div>").encode()
            data = inject(msg)
            self.send_response(HTTPStatus.BAD_GATEWAY)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return ProxyHandler


class RelayServer:
    """Relaie un serveur existant (ex. http://localhost:8888) sur le réseau local."""
    kind = "relay"

    def __init__(self, upstream: str, watch: Path | None = None, port: int = 8080,
                 on_reload=lambda kind: None, on_clients=lambda n: None, on_db_error=lambda: None):
        self.upstream = normalize_url(upstream)
        self.root = watch.resolve() if watch else None
        self.hub = Hub()
        self._db_flag = False

        def db_error():
            if not self._db_flag:
                self._db_flag = True
                on_db_error()

        self.state = {"stopping": False, "on_clients": lambda: on_clients(self.hub.count), "on_db_error": db_error}
        self.on_reload = on_reload
        self.port = free_port(port)
        self.httpd = ThreadingHTTPServer(("0.0.0.0", self.port),
                                         make_proxy_handler(self.upstream, self.hub, self.state))
        self.httpd.daemon_threads = True
        self.watcher = Watcher(self.root, self._changed) if self.root else None

    @property
    def label(self) -> str:
        return f"Relais → {self.upstream.split('://', 1)[-1]}"

    spa = False  # sans objet ici

    def start(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        if self.watcher:
            self.watcher.start()

    def stop(self):
        self.state["stopping"] = True
        if self.watcher:
            self.watcher.stop()
        self.httpd.shutdown()
        self.httpd.server_close()

    def watch(self, folder: Path):
        if self.watcher:
            self.watcher.stop()
        self.root = folder.resolve()
        self.watcher = Watcher(self.root, self._changed)
        self.watcher.start()

    def _changed(self, paths: set[str]):
        kind = "css" if all(p.lower().endswith(".css") for p in paths) else "reload"
        self.hub.send(kind)
        self.on_reload(kind)

    def urls(self) -> list[str]:
        return [f"http://{ip}:{self.port}/" for ip in local_addresses()]


# ---------------------------------------------------------------- PHP intégré

def app_base() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))


def find_php() -> Path | None:
    """PHP fourni avec l'appli, sinon celui de l'ordinateur."""
    env = os.environ.get("AIKOSERVE_PHP")
    if env and Path(env).exists():
        return Path(env)
    exe = "php.exe" if sys.platform == "win32" else "php"
    for cand in (app_base() / "php" / exe, app_base().parent / "php" / exe):
        if cand.exists():
            return cand
    found = shutil.which("php")
    return Path(found) if found else None


def php_args(php: Path) -> list[str]:
    """Options : erreurs visibles (on développe), et extensions courantes pour le PHP Windows officiel."""
    args = ["-d", "display_errors=1", "-d", "error_reporting=E_ALL", "-d", "memory_limit=512M"]
    ext = php.parent / "ext"
    if sys.platform == "win32" and ext.is_dir():
        args += ["-d", f"extension_dir={ext}"]
        for name in ("mbstring", "openssl", "curl", "fileinfo", "gd", "intl", "exif", "zip", "sodium",
                     "pdo_sqlite", "sqlite3", "mysqli", "pdo_mysql"):
            if (ext / f"php_{name}.dll").exists():
                args += ["-d", f"extension={name}"]
    return args


def php_version(php: Path) -> str:
    try:
        out = subprocess.run([str(php), *php_args(php), "-r", "echo PHP_VERSION;"], capture_output=True,
                             text=True, timeout=20, creationflags=NO_WINDOW)
        return out.stdout.strip() or "?"
    except Exception:
        return "?"


def has_php(folder: Path) -> bool:
    if (folder / "index.php").exists():
        return True
    for i, p in enumerate(folder.rglob("*.php")):
        if "vendor" not in p.parts and "node_modules" not in p.parts:
            return True
        if i > 400:
            break
    return False


DB_CODE = re.compile(rb"mysqli_connect|new\s+mysqli\b|mysql_connect\(|new\s+PDO\s*\(\s*['\"](mysql|pgsql|sqlsrv)|"
                     rb"['\"](mysql|pgsql):host=", re.I)


def detect_database(folder: Path) -> str | None:
    """Repère un site qui a besoin d'une base MySQL/PostgreSQL (SQLite fonctionne, lui, en mode intégré)."""
    if (folder / "wp-config.php").exists() or (folder / "wp-load.php").exists() \
            or (folder / "wp-config-sample.php").exists():
        return "WordPress (base MySQL)"
    env = folder / ".env"
    if env.exists():
        try:
            m = re.search(r"^DB_CONNECTION\s*=\s*(\w+)", env.read_text(errors="ignore"), re.M)
            if m and m.group(1).lower() != "sqlite":
                name = "Laravel" if (folder / "artisan").exists() else "Ce site"
                return f"{name} (base {m.group(1)})"
        except OSError:
            pass
    if (folder / "artisan").exists() and (folder / "public" / "index.php").exists():
        return None if env.exists() else "Laravel"
    seen = 0
    for p in folder.rglob("*.php"):
        if any(part in ("vendor", "node_modules", ".git") for part in p.parts):
            continue
        seen += 1
        if seen > 2000:
            break
        try:
            with open(p, "rb") as f:
                if DB_CODE.search(f.read(300_000)):
                    return "Connexion MySQL dans le code"
        except OSError:
            continue
    return None


class PhpServer(RelayServer):
    """Lance PHP sur un port interne, puis le relaie sur le réseau local (même mécanique que le relais)."""
    kind = "php"

    def __init__(self, root: Path, port: int = 8080, on_reload=lambda kind: None, on_clients=lambda n: None,
                 on_db_error=lambda: None, php: Path | None = None):
        self.php = php or find_php()
        if not self.php:
            raise OSError("PHP introuvable")
        self.version = php_version(self.php)
        self.internal = free_port(18080)
        docroot = root.resolve()
        if not (docroot / "index.php").exists() and (docroot / "public" / "index.php").exists():
            docroot = docroot / "public"  # Laravel, Symfony…
        env = dict(os.environ)
        if sys.platform != "win32":
            env["PHP_CLI_SERVER_WORKERS"] = "4"  # plusieurs requêtes à la fois (images, CSS…)
        self.proc = subprocess.Popen(
            [str(self.php), *php_args(self.php), "-S", f"127.0.0.1:{self.internal}", "-t", str(docroot)],
            cwd=str(docroot), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=env, creationflags=NO_WINDOW)
        self._wait_ready()
        super().__init__(f"http://127.0.0.1:{self.internal}", watch=root, port=port, on_reload=on_reload,
                         on_clients=on_clients, on_db_error=on_db_error)

    @property
    def label(self) -> str:
        return f"PHP {self.version}"

    def _wait_ready(self):
        import socket

        for _ in range(60):
            if self.proc.poll() is not None:
                raise OSError("PHP s'est arrêté au démarrage")
            try:
                with socket.create_connection(("127.0.0.1", self.internal), timeout=0.2):
                    return
            except OSError:
                time.sleep(0.1)
        raise OSError("PHP ne répond pas")

    def stop(self):
        super().stop()
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
