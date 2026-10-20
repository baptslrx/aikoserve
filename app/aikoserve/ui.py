"""La fenêtre d'AikoServe."""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QObject, QRectF, QSettings, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFontMetrics, QGuiApplication, QPainter
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QMessageBox,
                               QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from . import __version__
from .server import SiteServer
from .theme import ACCENT, ON_ACCENT, css, theme


class Bridge(QObject):
    reloaded = Signal(str)
    clients = Signal(int)


class QrView(QWidget):
    """QR code dessiné en noir sur blanc (même en mode sombre : sinon les téléphones le lisent mal)."""

    def __init__(self):
        super().__init__()
        self.matrix: list[list[bool]] = []
        self.setFixedSize(232, 232)

    def set_text(self, text: str):
        try:
            import segno

            qr = segno.make(text, error="m", micro=False)
            self.matrix = [[bool(v) for v in row] for row in qr.matrix]
        except Exception:
            self.matrix = []
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#FFFFFF"))
        p.drawRoundedRect(QRectF(self.rect()), 18, 18)
        n = len(self.matrix)
        if n:
            quiet = 16
            cell = (self.width() - 2 * quiet) / n
            p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            p.setBrush(QColor("#0A0A0C"))
            for y, row in enumerate(self.matrix):
                for x, on in enumerate(row):
                    if on:
                        p.drawRect(QRectF(quiet + x * cell, quiet + y * cell, cell + 0.5, cell + 0.5))
        p.end()


class DropArea(QFrame):
    clicked = Signal()

    def __init__(self):
        super().__init__()
        self.setObjectName("dropArea")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.addStretch(1)
        big = QLabel("Glisse le dossier de ton site ici")
        big.setObjectName("dropTitle")
        big.setAlignment(Qt.AlignmentFlag.AlignCenter)
        small = QLabel("ou clique pour le choisir · HTML, CSS, JS, images…")
        small.setObjectName("hint")
        small.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(big)
        lay.addWidget(small)
        lay.addStretch(1)

    def mousePressEvent(self, _):
        self.clicked.emit()

    def set_hot(self, on: bool):
        self.setProperty("hot", on)
        self.style().unpolish(self)
        self.style().polish(self)


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AikoServe")
        self.resize(480, 660)
        self.setMinimumSize(440, 600)
        self.setAcceptDrops(True)
        self.settings = QSettings("Aikographic", "AikoServe")
        self.server: SiteServer | None = None
        self.n_clients = 0
        self.last_reload: float | None = None
        self.bridge = Bridge()
        self.bridge.reloaded.connect(self._reloaded)
        self.bridge.clients.connect(self._clients)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(14)

        head = QHBoxLayout()
        title = QLabel("AikoServe")
        title.setObjectName("title")
        sub = QLabel("Ton site sur ton téléphone")
        sub.setObjectName("hint")
        head.addWidget(title)
        head.addSpacing(10)
        head.addWidget(sub)
        head.addStretch(1)
        root.addLayout(head)

        self.stack = QStackedWidget()

        # Page 1 : déposer un dossier
        empty = QWidget()
        el = QVBoxLayout(empty)
        el.setContentsMargins(0, 0, 0, 0)
        el.setSpacing(12)
        self.drop = DropArea()
        self.drop.clicked.connect(self._choose)
        el.addWidget(self.drop, 1)
        self.last_btn = QPushButton()
        self.last_btn.setObjectName("ghost")
        self.last_btn.clicked.connect(self._reopen_last)
        el.addWidget(self.last_btn)
        self.stack.addWidget(empty)

        # Page 2 : site en ligne
        live = QWidget()
        ll = QVBoxLayout(live)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(12)

        card = QFrame()
        card.setObjectName("card")
        cl = QHBoxLayout(card)
        cl.setContentsMargins(14, 10, 14, 10)
        names = QVBoxLayout()
        names.setSpacing(2)
        self.site_name = QLabel()
        self.site_name.setObjectName("siteName")
        self.site_path = QLabel()
        self.site_path.setObjectName("hint")
        names.addWidget(self.site_name)
        names.addWidget(self.site_path)
        self.live_dot = QLabel("● En ligne")
        self.live_dot.setObjectName("live")
        cl.addLayout(names, 1)
        cl.addWidget(self.live_dot)
        ll.addWidget(card)

        self.qr = QrView()
        ll.addWidget(self.qr, 0, Qt.AlignmentFlag.AlignHCenter)
        scan = QLabel("Scanne avec l'appareil photo de ton téléphone (connecté au même Wi-Fi)")
        scan.setObjectName("hint")
        scan.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scan.setWordWrap(True)
        ll.addWidget(scan)

        self.url_box = QComboBox()
        self.url_box.setObjectName("url")
        self.url_box.currentTextChanged.connect(self.qr.set_text)
        ll.addWidget(self.url_box)

        row = QHBoxLayout()
        self.open_btn = QPushButton("Ouvrir ici")
        self.open_btn.setObjectName("primary")
        self.open_btn.clicked.connect(self._open_browser)
        self.copy_btn = QPushButton("Copier l'adresse")
        self.copy_btn.setObjectName("ghost")
        self.copy_btn.clicked.connect(self._copy)
        self.folder_btn = QPushButton("Autre dossier…")
        self.folder_btn.setObjectName("ghost")
        self.folder_btn.clicked.connect(self._choose)
        self.stop_btn = QPushButton("Arrêter")
        self.stop_btn.setObjectName("ghost")
        self.stop_btn.clicked.connect(self.stop)
        for b in (self.open_btn, self.copy_btn, self.folder_btn, self.stop_btn):
            row.addWidget(b)
        ll.addLayout(row)

        self.spa = QCheckBox("Mode application (React, Vue… : toutes les adresses mènent à index.html)")
        self.spa.setChecked(self.settings.value("spa", False, type=bool))
        self.spa.toggled.connect(self._toggle_spa)
        ll.addWidget(self.spa)
        ll.addStretch(1)
        self.stack.addWidget(live)
        root.addWidget(self.stack, 1)

        self.status = QLabel(f"AikoServe {__version__}")
        self.status.setObjectName("hint")
        root.addWidget(self.status)

        self._tick = QTimer(self)
        self._tick.timeout.connect(self._refresh_status)
        self._tick.start(1000)

        self.apply_theme()
        self._show_empty()

    # ------------------------------------------------------------ thème

    def apply_theme(self):
        t = theme()
        self.setStyleSheet(f"""
            QWidget {{ background: {css(t['bg'])}; color: {css(t['text'])}; font-size: 13px; }}
            QLabel {{ background: transparent; }}
            QLabel#title {{ font-size: 18px; font-weight: 700; }}
            QLabel#hint {{ color: {css(t['subtext'])}; font-size: 12px; }}
            QLabel#siteName {{ font-size: 15px; font-weight: 600; }}
            QLabel#live {{ color: {css(t['accent_text'])}; font-size: 12px; font-weight: 600; }}
            QLabel#dropTitle {{ font-size: 17px; font-weight: 600; }}
            QFrame#dropArea {{ background: {css(t['surface'])}; border: 1.5px dashed {css(t['line'])};
                              border-radius: 16px; }}
            QFrame#dropArea[hot="true"] {{ border: 2px solid {css(ACCENT)}; }}
            QFrame#card {{ background: {css(t['surface'])}; border: 1px solid {css(t['line'])}; border-radius: 10px; }}
            QFrame#card QLabel {{ background: transparent; }}
            QComboBox#url {{ background: {css(t['surface'])}; border: 1px solid {css(t['line'])}; border-radius: 8px;
                            padding: 8px 12px; font-size: 16px; font-weight: 600; color: {css(t['text'])}; }}
            QComboBox#url::drop-down {{ border: none; width: 24px; }}
            QPushButton#primary {{ background: {css(ACCENT)}; color: {css(ON_ACCENT)}; border: none; border-radius: 8px;
                                  padding: 8px 14px; font-weight: 700; }}
            QPushButton#ghost {{ background: transparent; color: {css(t['text'])}; border: 1px solid {css(t['line'])};
                                border-radius: 8px; padding: 7px 12px; }}
            QPushButton#ghost:hover {{ border-color: {css(ACCENT)}; }}
            QCheckBox {{ background: transparent; spacing: 7px; color: {css(t['subtext'])}; font-size: 12px; }}
            QCheckBox::indicator {{ width: 15px; height: 15px; border-radius: 4px; border: 1.5px solid {css(t['subtext'])}; }}
            QCheckBox::indicator:checked {{ background: {css(ACCENT)}; border-color: {css(ACCENT)}; }}
        """)

    # ------------------------------------------------------------ états

    def _show_empty(self):
        self.stack.setCurrentIndex(0)
        last = self.settings.value("last", "", type=str)
        if last and Path(last).is_dir():
            self.last_btn.setText(f"Relancer « {Path(last).name} »")
            self.last_btn.show()
        else:
            self.last_btn.hide()
        self.status.setText(f"AikoServe {__version__}")

    def serve(self, folder: Path):
        if not folder.is_dir():
            folder = folder.parent
        self.stop(show_empty=False)
        try:
            self.server = SiteServer(folder, port=int(self.settings.value("port", 8080)), spa=self.spa.isChecked(),
                                     on_reload=lambda kind: self.bridge.reloaded.emit(kind),
                                     on_clients=lambda n: self.bridge.clients.emit(n))
            self.server.start()
        except OSError as e:
            QMessageBox.warning(self, "AikoServe", f"Impossible de démarrer le serveur :\n{e}")
            self.server = None
            self._show_empty()
            return
        self.settings.setValue("last", str(folder))
        self.site_name.setText(folder.name)
        fm = QFontMetrics(self.site_path.font())
        self.site_path.setText(fm.elidedText(str(folder), Qt.TextElideMode.ElideMiddle, 300))
        self.site_path.setToolTip(str(folder))
        self.url_box.blockSignals(True)
        self.url_box.clear()
        self.url_box.addItems(self.server.urls())
        self.url_box.blockSignals(False)
        self.qr.set_text(self.url_box.currentText())
        self.n_clients, self.last_reload = 0, None
        if not (folder / "index.html").exists() and not (folder / "index.htm").exists():
            self.status.setText("Pas de index.html : la liste des fichiers s'affichera.")
        self.stack.setCurrentIndex(1)
        self._refresh_status()

    def stop(self, show_empty: bool = True):
        if self.server:
            self.server.stop()
            self.server = None
        if show_empty:
            self._show_empty()

    # ------------------------------------------------------------ actions

    def _choose(self):
        folder = QFileDialog.getExistingDirectory(self, "Choisir le dossier du site")
        if folder:
            self.serve(Path(folder))

    def _reopen_last(self):
        last = self.settings.value("last", "", type=str)
        if last:
            self.serve(Path(last))

    def _local_url(self) -> str:
        return f"http://localhost:{self.server.port}/" if self.server else ""

    def _open_browser(self):
        if self.server:
            QDesktopServices.openUrl(QUrl(self._local_url()))

    def _copy(self):
        QGuiApplication.clipboard().setText(self.url_box.currentText())
        self.copy_btn.setText("Copié ✓")
        QTimer.singleShot(1400, lambda: self.copy_btn.setText("Copier l'adresse"))

    def _toggle_spa(self, on: bool):
        self.settings.setValue("spa", on)
        if self.server:
            self.server.spa = on

    def _reloaded(self, kind: str):
        self.last_reload = time.time()
        self._refresh_status()

    def _clients(self, n: int):
        self.n_clients = n
        self._refresh_status()

    def _refresh_status(self):
        if not self.server:
            return
        parts = [f"{self.n_clients} appareil{'s' if self.n_clients > 1 else ''} connecté"
                 f"{'s' if self.n_clients > 1 else ''}"]
        if self.last_reload:
            ago = int(time.time() - self.last_reload)
            parts.append("rechargé à l'instant" if ago < 3 else f"rechargé il y a {ago} s" if ago < 60
                         else f"rechargé il y a {ago // 60} min")
        else:
            parts.append("les pages se rechargent à chaque modification")
        self.status.setText(" · ".join(parts))

    # ------------------------------------------------------------ glisser-déposer

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self.drop.set_hot(True)

    def dragLeaveEvent(self, _):
        self.drop.set_hot(False)

    def dropEvent(self, e):
        self.drop.set_hot(False)
        urls = [u for u in e.mimeData().urls() if u.isLocalFile()]
        if urls:
            e.acceptProposedAction()
            self.serve(Path(urls[0].toLocalFile()))

    def closeEvent(self, e):
        self.stop(show_empty=False)
        e.accept()
