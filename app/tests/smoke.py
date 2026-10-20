"""Test automatique d'AikoServe : vraies requêtes HTTP, rechargement après modification, captures."""
from __future__ import annotations

import faulthandler
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from aikoserve import theme  # noqa: E402
from aikoserve.relay import PhpServer, RelayServer, detect_database, find_php, has_php  # noqa: E402
from aikoserve.server import SiteServer, local_addresses  # noqa: E402
from aikoserve.ui import MainWindow  # noqa: E402

failures: list[str] = []


def check(cond: bool, what: str):
    print(("  ok   " if cond else "  FAIL ") + what, flush=True)
    if not cond:
        failures.append(what)
        if os.environ.get("GITHUB_ACTIONS"):
            print(f"::error title=Test::{what}")


def get(base: str, path: str):
    try:
        r = urllib.request.urlopen(base + path, timeout=5)
        return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), b""


def wait_event(base: str, action) -> str | None:
    got: list[str] = []

    def listen():
        try:
            r = urllib.request.urlopen(base + "/__aikoserve/events", timeout=10)
            for line in r:
                line = line.decode().strip()
                if line.startswith("event:"):
                    got.append(line.split(":", 1)[1].strip())
                    return
        except Exception:
            pass

    t = threading.Thread(target=listen, daemon=True)
    t.start()
    time.sleep(1.0)
    action()
    t.join(6)
    return got[0] if got else None


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    faulthandler.dump_traceback_later(300, exit=True)
    app = QApplication(sys.argv)
    shots = ROOT / "screenshots"
    shots.mkdir(exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        site = Path(tmp) / "mon-portfolio"
        (site / "css").mkdir(parents=True)
        (site / "js").mkdir()
        (site / "node_modules" / "x").mkdir(parents=True)
        (site / "index.html").write_text('<!doctype html><html><head><link rel="stylesheet" href="css/style.css">'
                                         '</head><body><h1>Bonjour</h1></body></html>', encoding="utf-8")
        (site / "css" / "style.css").write_text("h1{color:#7DC8FF}", encoding="utf-8")
        (site / "js" / "app.mjs").write_text("export const a = 1;", encoding="utf-8")
        (site / "image.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
        (site / "page 2.html").write_text("<p>accents é à</p>", encoding="utf-8")

        print("== serveur", flush=True)
        clients: list[int] = []
        s = SiteServer(site, 8080, on_clients=clients.append)
        s.start()
        base = f"http://127.0.0.1:{s.port}"
        print("  adresses :", s.urls(), local_addresses())
        check(all(u.startswith("http://") for u in s.urls()), "adresse(s) réseau")
        st, ct, body = get(base, "/")
        check(st == 200 and "text/html" in ct, "page d'accueil")
        check(b"/__aikoserve/events" in body and body.rfind(b"</body>") > body.find(b"aikoServe"),
              "script de rechargement ajouté avant </body>")
        check(get(base, "/css/style.css")[1].startswith("text/css"), "type CSS")
        check(get(base, "/js/app.mjs")[1].startswith("text/javascript"), "type module JS")
        check(get(base, "/image.svg")[1].startswith("image/svg+xml"), "type SVG")
        check(get(base, "/page%202.html")[0] == 200, "nom de fichier avec espace")
        check(get(base, "/../../etc/passwd")[0] == 404, "impossible de sortir du dossier du site")
        check(get(base, "/projets/42")[0] == 404, "adresse inconnue = 404")
        s.spa = True
        check(get(base, "/projets/42")[0] == 200, "mode application : retour sur index.html")
        s.spa = False

        print("== rechargement", flush=True)
        ev = wait_event(base, lambda: (site / "css" / "style.css").write_text("h1{color:red}", encoding="utf-8"))
        check(ev == "css", f"CSS modifié → rechargement des styles seulement (reçu : {ev})")
        ev = wait_event(base, lambda: (site / "index.html").write_text(
            (site / "index.html").read_text(encoding="utf-8") + "<!-- x -->", encoding="utf-8"))
        check(ev == "reload", f"HTML modifié → page rechargée (reçu : {ev})")
        ev = wait_event(base, lambda: (site / "node_modules" / "x" / "a.js").write_text("1", encoding="utf-8"))
        check(ev is None, "node_modules ignoré")
        check(max(clients or [0]) >= 1, "appareil connecté compté")
        s.stop()

        print("== PHP intégré", flush=True)
        php = find_php()
        print("  PHP :", php)
        check(php is not None, "PHP fourni avec l'appli")
        psite = Path(tmp) / "site-php"
        (psite / "inc").mkdir(parents=True)
        (psite / "index.php").write_text('<?php include "inc/head.php"; ?><body><h1>Bonjour <?= 20 + 22 ?></h1>'
                                         '<?php if ($_POST) echo "Recu: " . htmlspecialchars($_POST["n"]); ?>'
                                         '</body></html>', encoding="utf-8")
        (psite / "inc" / "head.php").write_text("<!doctype html><html><head><title>PHP</title></head>",
                                                encoding="utf-8")
        (psite / "db.php").write_text('<?php mysqli_report(MYSQLI_REPORT_OFF); $c = @new mysqli("127.0.0.1", '
                                      '"root", "root", "x", 3399); if ($c->connect_errno) { echo "Warning: '
                                      'mysqli_connect(): (HY000/2002): " . $c->connect_error; }', encoding="utf-8")
        check(has_php(psite) and detect_database(psite) == "Connexion MySQL dans le code", "site PHP + MySQL repéré")
        if php:
            db_flags: list[int] = []
            ps = PhpServer(psite, 8090, on_db_error=lambda: db_flags.append(1))
            ps.start()
            pbase = f"http://127.0.0.1:{ps.port}"
            print("  ", ps.label)
            st, ct, body = get(pbase, "/")
            check(st == 200 and b"Bonjour 42" in body, "page PHP exécutée (include compris)")
            check(b"/__aikoserve/events" in body, "rechargement auto ajouté aux pages PHP")
            r = urllib.request.urlopen(urllib.request.Request(pbase + "/", data=b"n=salut"), timeout=10).read()
            check(b"Recu: salut" in r, "formulaire (POST) traité")
            get(pbase, "/db.php")
            check(db_flags == [1], "base de données injoignable repérée")
            ev = wait_event(pbase, lambda: (psite / "inc" / "head.php").write_text(
                "<!doctype html><html><head><title>PHP 2</title></head>", encoding="utf-8"))
            check(ev == "reload", f"fichier PHP modifié → page rechargée (reçu : {ev})")
            ps.stop()
        wp = Path(tmp) / "mon-wordpress"
        wp.mkdir()
        (wp / "index.php").write_text("<?php echo 'wp';", encoding="utf-8")
        (wp / "wp-config.php").write_text("<?php define('DB_NAME', 'wp');", encoding="utf-8")
        check(detect_database(wp) == "WordPress (base MySQL)", "WordPress repéré")
        lite = Path(tmp) / "site-sqlite"
        lite.mkdir()
        (lite / "index.php").write_text('<?php $db = new PDO("sqlite:" . __DIR__ . "/d.db");', encoding="utf-8")
        check(detect_database(lite) is None, "SQLite : pas d'alerte (fonctionne en mode intégré)")

        print("== relais", flush=True)
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class Upstream(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                p = self.server.server_address[1]
                if self.path == "/old":
                    self.send_response(302)
                    self.send_header("Location", f"http://localhost:{p}/")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                b = (f'<html><head><link rel=stylesheet href="http://localhost:{p}/s.css"></head><body>'
                     f'<script>var u="http:\\/\\/localhost:{p}\\/api"</script></body></html>').encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Set-Cookie", "wp=1; path=/; domain=localhost")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

        up = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        threading.Thread(target=up.serve_forever, daemon=True).start()
        upport = up.server_address[1]
        rs = RelayServer(str(upport), watch=site, port=8095)
        rs.start()
        rbase = f"http://127.0.0.1:{rs.port}"
        st, ct, body = get(rbase, "/")
        check(st == 200 and f"localhost:{upport}".encode() not in body, "adresses localhost réécrites")
        check(f"127.0.0.1:{rs.port}/s.css".encode() in body, "lien CSS réécrit vers l'adresse du relais")
        check(b"/__aikoserve/events" in body, "rechargement auto ajouté au site relayé")

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None

        try:
            urllib.request.build_opener(NoRedirect).open(rbase + "/old", timeout=5)
            loc = ""
        except urllib.error.HTTPError as e:
            loc = e.headers.get("Location", "")
        check(loc == f"http://127.0.0.1:{rs.port}/", f"redirection réécrite ({loc})")
        rs.stop()
        bad = RelayServer("http://127.0.0.1:1", port=8096)
        bad.start()
        check(get(f"http://127.0.0.1:{bad.port}", "/")[0] == 502, "serveur éteint → page d'explication")
        bad.stop()
        up.shutdown()

        print("== fenêtre", flush=True)
        for dark in (True, False):
            theme.is_dark = lambda dark=dark: dark
            w = MainWindow()
            w.apply_theme()
            w.show()
            app.processEvents()
            w.grab().save(str(shots / f"fenetre-vide-{'sombre' if dark else 'clair'}.png"))
            w.serve(site)
            w.n_clients, w.last_reload = 2, time.time() - 5
            w._refresh_status()
            app.processEvents()
            check(len(w.qr.matrix) > 20, "QR code généré")
            w.grab().save(str(shots / f"fenetre-{'sombre' if dark else 'clair'}.png"))
            st, _, _ = get(f"http://127.0.0.1:{w.server.port}", "/")
            check(st == 200, "le serveur de la fenêtre répond")
            if php:
                w.serve(wp)
                app.processEvents()
                check(w.warn.isVisible() and "WordPress" in w.warn_text.text(), "bandeau base de données affiché")
                check(w.kind_label.text().startswith("PHP"), f"mode PHP affiché ({w.kind_label.text()})")
                w.grab().save(str(shots / f"fenetre-wordpress-{'sombre' if dark else 'clair'}.png"))
                w._go_relay()
                app.processEvents()
                check(w.stack.currentIndex() == 0 and w.pending_watch == wp, "« Passer en mode relais » prépare le relais")
                w.grab().save(str(shots / f"fenetre-relais-{'sombre' if dark else 'clair'}.png"))
            w.close()

    print("\nCaptures :", ", ".join(sorted(p.name for p in shots.glob("*.png"))))
    if failures:
        print("\nÉCHECS :\n" + "\n".join(failures))
        return 1
    print("\nTout est OK")
    return 0


if __name__ == "__main__":
    try:
        code = main()
        sys.stdout.flush()
        os._exit(code)
    except Exception:
        import traceback

        tb = traceback.format_exc()
        print(tb)
        if os.environ.get("GITHUB_ACTIONS"):
            print("::error title=Erreur fatale::" + " | ".join(tb.strip().splitlines()[-6:]))
        sys.stdout.flush()
        os._exit(1)
