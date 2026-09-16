"""
gui/styles.py — Thème sombre HD pour l'application
Destination finale : gem_hunter/gui/styles.py

NOTE (audit) : fichier non utilisé — gui/main_window.py rend l'intégralité du
dashboard via QWebEngineView + gui/web/index.html (CSS inline dans le HTML),
pas via des QWidget stylés en QSS. Conservé tel quel (pas de risque de dérive
puisqu'il ne partage aucun état avec le code actif), à supprimer si confirmé
inutile lors d'un futur nettoyage.
"""

DARK_THEME = """
QWidget {
    background-color: #0d1117;
    color: #e6edf3;
    font-family: 'Segoe UI', 'Inter', sans-serif;
    font-size: 13px;
}

QMainWindow {
    background-color: #0d1117;
}

QPushButton {
    background-color: #21262d;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 8px 14px;
    color: #e6edf3;
    font-weight: 600;
}
QPushButton:hover {
    background-color: #30363d;
    border: 1px solid #58a6ff;
}
QPushButton:pressed {
    background-color: #1f6feb;
}
QPushButton:checkable:checked {
    background-color: #238636;
    border: 1px solid #2ea043;
}

QPushButton#dangerButton {
    background-color: #21262d;
    border: 1px solid #f85149;
    color: #f85149;
}
QPushButton#dangerButton:hover {
    background-color: #f85149;
    color: white;
}

QPushButton#deleteButton {
    background-color: transparent;
    border: none;
    color: #8b949e;
    font-size: 16px;
    padding: 2px 8px;
}
QPushButton#deleteButton:hover {
    color: #f85149;
}

QFrame#signalCard {
    background-color: #161b22;
    border: 1px solid #30363d;
    border-radius: 10px;
}
QFrame#signalCard[risk="Faible"] {
    border-left: 4px solid #3fb950;
}
QFrame#signalCard[risk="Modéré"] {
    border-left: 4px solid #d29922;
}
QFrame#signalCard[risk="Élevé"] {
    border-left: 4px solid #f85149;
}

QLabel#scoreLabel {
    font-size: 20px;
    font-weight: 700;
    color: #3fb950;
}

QLabel#tickerLabel {
    font-size: 16px;
    font-weight: 700;
    color: #58a6ff;
}

QLabel#sectionTitle {
    font-size: 15px;
    font-weight: 700;
    color: #e6edf3;
    padding: 4px 0px;
}

QScrollArea {
    border: none;
    background-color: transparent;
}

QStatusBar {
    background-color: #161b22;
    color: #8b949e;
    border-top: 1px solid #30363d;
}

QComboBox {
    background-color: #21262d;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 6px;
}

QTextEdit {
    background-color: #010409;
    border: 1px solid #30363d;
    border-radius: 6px;
    color: #7ee787;
    font-family: 'Consolas', monospace;
}
"""
