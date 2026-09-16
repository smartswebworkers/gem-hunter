"""
gui/signal_panel.py — Liste des signaux actifs, avec suppression manuelle
Destination finale : gem_hunter/gui/signal_panel.py

NOTE (audit) : fichier non utilisé — gui/main_window.py rend la liste des
signaux via QWebEngineView + gui/web/index.html (cartes JS), pas via ces
QWidget natifs. Conservé tel quel (pas de risque de dérive puisqu'il ne
partage aucun état avec le code actif), à supprimer si confirmé inutile lors
d'un futur nettoyage.
"""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame,
    QPushButton, QScrollArea, QSizePolicy
)
from PyQt6.QtCore import Qt, pyqtSignal

from storage import db


class SignalCard(QFrame):
    """Une carte représentant un signal détecté, avec bouton de suppression."""

    dismissed = pyqtSignal(int)

    def __init__(self, signal: dict, parent=None):
        super().__init__(parent)
        self.signal = signal
        self.setObjectName("signalCard")
        self.setProperty("risk", signal.get("risk_level", "Modéré"))
        self._build_ui()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 10, 14, 10)

        header = QHBoxLayout()
        ticker_label = QLabel(f"{self.signal.get('ticker', '?')} — {self.signal.get('chain', '?').upper()}")
        ticker_label.setObjectName("tickerLabel")
        header.addWidget(ticker_label)
        header.addStretch()

        score_label = QLabel(f"{self.signal.get('score', 0)}/100")
        score_label.setObjectName("scoreLabel")
        header.addWidget(score_label)

        delete_btn = QPushButton("✕")
        delete_btn.setObjectName("deleteButton")
        delete_btn.setToolTip("Supprimer ce signal (inutile)")
        delete_btn.setFixedWidth(30)
        delete_btn.clicked.connect(self._on_delete)
        header.addWidget(delete_btn)

        outer.addLayout(header)

        details = QLabel(
            f"Contrat : {self.signal.get('contract', '?')}\n"
            f"Market Cap : ${self.signal.get('market_cap', 0):,.0f} | "
            f"Liquidité : ${self.signal.get('liquidity', 0):,.0f} | "
            f"Risque : {self.signal.get('risk_level', 'N/A')}\n"
            f"Entrée : ${self.signal.get('entry_price', 'N/A')} | "
            f"SL : ${self.signal.get('stop_loss', 'N/A')} | "
            f"TP1/2/3 : ${self.signal.get('tp1', 'N/A')} / "
            f"${self.signal.get('tp2', 'N/A')} / ${self.signal.get('tp3', 'N/A')}"
        )
        details.setWordWrap(True)
        outer.addWidget(details)

        reasons = self.signal.get("reasons", [])
        if reasons:
            reasons_label = QLabel("• " + "\n• ".join(reasons))
            reasons_label.setWordWrap(True)
            reasons_label.setStyleSheet("color: #8b949e; font-size: 12px;")
            outer.addWidget(reasons_label)

    def _on_delete(self):
        signal_id = self.signal.get("id")
        if signal_id is not None:
            db.dismiss_signal(signal_id)
        self.dismissed.emit(signal_id)
        self.setParent(None)
        self.deleteLater()


class SignalPanel(QScrollArea):
    """Conteneur scrollable de toutes les SignalCard actives."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self._container = QWidget()
        self._layout = QVBoxLayout(self._container)
        self._layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._layout.setSpacing(10)
        self.setWidget(self._container)
        self._cards: dict[int, SignalCard] = {}

        self.load_existing_signals()

    def load_existing_signals(self):
        for signal in db.get_active_signals():
            self.add_signal(signal)

    def add_signal(self, signal: dict):
        signal_id = signal.get("id")
        if signal_id in self._cards:
            return
        card = SignalCard(signal)
        card.dismissed.connect(self._on_card_dismissed)
        self._layout.insertWidget(0, card)  # les plus récents en haut
        if signal_id is not None:
            self._cards[signal_id] = card

    def _on_card_dismissed(self, signal_id: int):
        self._cards.pop(signal_id, None)

    def clear_all(self):
        """Supprime manuellement TOUS les signaux affichés (palette de nettoyage rapide)."""
        for signal_id, card in list(self._cards.items()):
            db.dismiss_signal(signal_id)
            card.setParent(None)
            card.deleteLater()
        self._cards.clear()
