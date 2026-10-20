"""Serveur local d'AikoServe : fichiers du site + rechargement automatique du navigateur.

Uniquement la bibliothèque standard de Python. Le serveur écoute sur le réseau local
pour qu'un téléphone sur le même Wi-Fi puisse ouvrir le site.
"""
from __future__ import annotations

import mimetypes
import os
import posixpath
import queue
import socket
import threading
import time
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

IGNORED_DIRS = {".git", "node_modules", ".svn", ".hg", "__pycache__", ".idea", ".vscode", ".next", ".cache"}

EXTRA_TYPES = {
    ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css", ".json": "application/json",
    ".svg": "image/svg+xml", ".webp": "image/webp", ".avif": "image/avif", ".wasm": "application/wasm",
    ".woff": "font/woff", ".woff2": "font/woff2", ".ttf": "font/ttf", ".otf": "font/otf",
    ".webmanifest": "application/manifest+json", ".ico": "image/x-icon", ".mp4": "video/mp4",
    ".webm": "video/webm", ".md": "text/markdown", ".txt": "text/plain", ".xml": "application/xml",
    ".html": "text/html", ".htm": "text/html",
}

# Script ajouté aux pages HTML : recharge la page (ou seulement les CSS) quand un fichier change.
RELOAD_SCRIPT = """
<script>/* AikoServe — rechargement automatique */
(function () {
  if (window.__aikoServe) return; window.__aikoServe = true;
  function connect() {
    var es = new EventSource('/__aikoserve/events');
    es.addEventListener('css', function () {
      document.querySelectorAll('link[rel="stylesheet"]').forEach(function (l) {
        var u = new URL(l.href); u.searchParams.set('_aiko', Date.now()); l.href = u.toString();
      });
    });
    es.addEventListener('reload', function () { location.reload(); });
    es.onerror = function () { es.close(); setTimeout(connect, 1000); };
  }
  connect();
})();
</script>
"""


def local_addresses() -> list[str]:
    """Adresses IP de cet ordinateur sur le réseau local (la principale en premier)."""
    found: list[str] = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))  # aucun paquet n'est envoyé : sert juste à choisir l'interface
        found.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in found and not ip.startswith("127."):
                found.append(ip)
    except OSError:
        pass
    return [ip for ip in found if not ip.startswith("127.")] or ["127.0.0.1"]


def free_port(preferred: int = 8080) -> int:
    for port in [preferred, *range(8081, 8100), 0]:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("0.0.0.0", port))
            return s.getsockname()[1]
        except OSError:
            continue
        finally:
            s.close()
    return 0


class Hub:
    """Garde la liste des navigateurs connectés et leur envoie les événements."""

    def __init__(self):
        self._clients: list[queue.Queue] = []
        self._lock = threading.Lock()

    def add(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._clients.append(q)
        return q

    def remove(self, q: queue.Queue):
        with self._lock:
            if q in self._clients:
                self._clients.remove(q)

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._clients)

    def send(self, event: str):
        with self._lock:
            for q in self._clients:
                q.put(event)


class Watcher(threading.Thread):
    """Surveille le dossier (sans dépendance : on compare les dates de modification)."""

    def __init__(self, root: Path, on_change, interval: float = 0.4):
        super().__init__(daemon=True)
        self.root, self.on_change, self.interval = root, on_change, interval
        self._halt = threading.Event()

    def snapshot(self) -> dict[str, float]:
        snap: dict[str, float] = {}
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS and not d.startswith(".")]
            for f in filenames:
                if f.startswith(".") or f.endswith(("~", ".swp", ".tmp")):
                    continue
                p = os.path.join(dirpath, f)
                try:
                    snap[p] = os.stat(p).st_mtime
                except OSError:
                    pass
        return snap

    def run(self):
        before = self.snapshot()
        while not self._halt.wait(self.interval):
            now = self.snapshot()
            if now != before:
                changed = {p for p in set(now) | set(before) if now.get(p) != before.get(p)}
                before = now
                self.on_change(changed)

    def stop(self):
        self._halt.set()


def serve_events(handler: BaseHTTPRequestHandler, hub: "Hub", state: dict):
    """Flux d'événements (Server-Sent Events) : le navigateur y apprend quand recharger."""
    handler.send_response(HTTPStatus.OK)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Connection", "keep-alive")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    q = hub.add()
    state["on_clients"]()
    try:
        handler.wfile.write(b": ok\n\n")
        handler.wfile.flush()
        while not state["stopping"]:
            try:
                event = q.get(timeout=15)
                handler.wfile.write(f"event: {event}\ndata: {int(time.time())}\n\n".encode())
            except queue.Empty:
                handler.wfile.write(b": ping\n\n")  # garde la connexion ouverte
            handler.wfile.flush()
    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
        pass
    finally:
        hub.remove(q)
        state["on_clients"]()
        handler.close_connection = True


def make_handler(root: Path, hub: Hub, state: dict):
    class Handler(BaseHTTPRequestHandler):
        server_version = "AikoServe"
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):  # pas de journal dans la console
            pass

        def _events(self):
            serve_events(self, hub, state)

        # ------------------------------------------------ fichiers
        def _resolve(self, url_path: str) -> Path | None:
            path = urllib.parse.unquote(urllib.parse.urlsplit(url_path).path)
            path = posixpath.normpath(path).lstrip("/")
            full = (root / path).resolve()
            try:
                full.relative_to(root.resolve())  # jamais en dehors du dossier du site
            except ValueError:
                return None
            if full.is_dir():
                for index in ("index.html", "index.htm"):
                    if (full / index).exists():
                        return full / index
                return full
            return full

        def do_GET(self):
            self._serve(head=False)

        def do_HEAD(self):
            self._serve(head=True)

        def _serve(self, head: bool):
            if self.path.startswith("/__aikoserve/events"):
                return self._events()
            target = self._resolve(self.path)
            if target is not None and target.is_dir():
                if not self.path.split("?")[0].endswith("/"):
                    self.send_response(HTTPStatus.MOVED_PERMANENTLY)
                    self.send_header("Location", self.path.split("?")[0] + "/")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                return self._listing(target, head)
            if target is None or not target.is_file():
                fallback = root / "index.html"
                last = posixpath.basename(urllib.parse.urlsplit(self.path).path)
                if state.get("spa") and fallback.exists() and "." not in last:
                    target = fallback  # mode application : les routes renvoient vers index.html
                else:
                    return self._not_found(head)
            ctype = EXTRA_TYPES.get(target.suffix.lower()) or mimetypes.guess_type(target.name)[0] \
                or "application/octet-stream"
            try:
                data = target.read_bytes()
            except OSError:
                return self._not_found(head)
            if ctype == "text/html":
                data = inject(data)
            if ctype.startswith("text/") or ctype in ("application/json", "application/xml", "image/svg+xml"):
                ctype += "; charset=utf-8"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            if not head:
                self.wfile.write(data)

        def _listing(self, folder: Path, head: bool):
            items = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            rel = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
            rows = "".join(
                f'<li><a href="{urllib.parse.quote(p.name)}{"/" if p.is_dir() else ""}">'
                f'{p.name}{"/" if p.is_dir() else ""}</a></li>'
                for p in items if not p.name.startswith(".") and p.name not in IGNORED_DIRS)
            page = (f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
                    f"<title>{rel}</title><style>body{{font:15px system-ui;margin:32px;color:#0A0D10}}"
                    f"a{{color:#1F7FC8;text-decoration:none}}li{{margin:6px 0}}</style>"
                    f"<h3>{rel}</h3><ul>{rows}</ul>").encode()
            data = inject(page)
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if not head:
                self.wfile.write(data)

        def _not_found(self, head: bool):
            data = inject(b"<!doctype html><meta charset=utf-8><title>404</title>"
                          b"<p style='font:15px system-ui;margin:32px'>Page introuvable (404)</p>")
            self.send_response(HTTPStatus.NOT_FOUND)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if not head:
                self.wfile.write(data)

    return Handler


def inject(html: bytes) -> bytes:
    tag = RELOAD_SCRIPT.encode()
    low = html.lower()
    i = low.rfind(b"</body>")
    if i == -1:
        i = low.rfind(b"</html>")
    return html[:i] + tag + html[i:] if i != -1 else html + tag


class SiteServer:
    kind = "static"
    label = "Site statique"

    """Démarre/arrête le serveur d'un dossier. `on_reload(kind)` et `on_clients(n)` sont appelés
    depuis d'autres fils : l'interface doit les relayer (signaux Qt)."""

    def __init__(self, root: Path, port: int = 8080, spa: bool = False,
                 on_reload=lambda kind: None, on_clients=lambda n: None):
        self.root = root.resolve()
        self.hub = Hub()
        self.state = {"spa": spa, "stopping": False, "on_clients": lambda: on_clients(self.hub.count)}
        self.on_reload = on_reload
        self.port = free_port(port)
        self.httpd = ThreadingHTTPServer(("0.0.0.0", self.port), make_handler(self.root, self.hub, self.state))
        self.httpd.daemon_threads = True
        self.watcher = Watcher(self.root, self._changed)

    @property
    def spa(self) -> bool:
        return self.state["spa"]

    @spa.setter
    def spa(self, on: bool):
        self.state["spa"] = on

    def start(self):
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.watcher.start()

    def stop(self):
        self.state["stopping"] = True
        self.watcher.stop()
        self.httpd.shutdown()
        self.httpd.server_close()

    def _changed(self, paths: set[str]):
        only_css = all(p.lower().endswith(".css") for p in paths)
        kind = "css" if only_css else "reload"
        self.hub.send(kind)
        self.on_reload(kind)

    def urls(self) -> list[str]:
        return [f"http://{ip}:{self.port}/" for ip in local_addresses()]
