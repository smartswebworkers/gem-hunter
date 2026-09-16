"""
main.py — Point d'entrée de l'application AI Multichain Gem Hunter
Destination finale : gem_hunter/main.py

Lancement : python main.py  (depuis la racine du projet gem_hunter/)
"""
import sys
import logging
from PyQt6.QtWidgets import QApplication

import config
from storage.db import init_db
from gui.main_window import MainWindow


def main():
    logging.basicConfig(level=logging.INFO)
    init_db()

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()

    # Un détecteur de gems « dès leur création » doit scanner sans attendre un
    # clic. Le bouton STOP de l'interface reste disponible ; désactivable via
    # config.AUTOSTART_SCANNER.
    if getattr(config, "AUTOSTART_SCANNER", False):
        window.scanner.run()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
