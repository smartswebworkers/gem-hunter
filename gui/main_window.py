"""
gui/main_window.py — Fenêtre principale de l'application
Destination finale : gem_hunter/gui/main_window.py

L'intégralité du dashboard (cartes de signaux, jauges, commandes) est
rendue en HTML/CSS/JS via QWebEngineView (gui/web/index.html), pour
obtenir des animations fluides impossibles à reproduire nativement en
PyQt6 pur. L'application reste 100% desktop : rien n'est servi ni ouvert
dans un navigateur, la page HTML est chargée depuis le disque local.
"""
import os
import logging
from PyQt6.QtWidgets import QMainWindow
from PyQt6.QtCore import QUrl
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebChannel import QWebChannel

from core.scanner import Scanner, ScannerState
from gui.bridge import Bridge, QtToBridgeLogHandler

WEB_DIR = os.path.join(os.path.dirname(__file__), "web")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI Multichain Gem Hunter — v5")
        self.resize(1440, 860)

        self.state = ScannerState()
        self.scanner = Scanner(self.state)

        self.bridge = Bridge(self.scanner)
        self._wire_logging()

        self.view = QWebEngineView()
        self.channel = QWebChannel()
        self.channel.registerObject("bridge", self.bridge)
        self.view.page().setWebChannel(self.channel)

        index_path = os.path.join(WEB_DIR, "index.html")
        if not os.path.exists(index_path):
            msg = (
                f"[ERREUR] index.html introuvable à : {index_path}\n"
                f"Vérifie que le fichier est bien placé dans gem_hunter/gui/web/index.html "
                f"(le dossier 'web' doit exister à côté de main_window.py)."
            )
            print(msg)
            logging.getLogger("gem_hunter").error(msg)
        self.view.load(QUrl.fromLocalFile(index_path))

        self.setCentralWidget(self.view)

    def _wire_logging(self):
        handler = QtToBridgeLogHandler(self.bridge)
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S"))
        logging.getLogger("gem_hunter").addHandler(handler)
        logging.getLogger("gem_hunter").setLevel(logging.INFO)

    def closeEvent(self, event):
        self.scanner.stop()
        super().closeEvent(event)
