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
