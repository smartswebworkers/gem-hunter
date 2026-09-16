"""
gui/bridge.py — Pont QWebChannel entre le backend Python (scanner) et le
dashboard HTML/JS affiché dans QWebEngineView.
Destination finale : gem_hunter/gui/bridge.py
"""
import json
import logging

from PyQt6.QtCore import QObject, pyqtSlot, pyqtSignal, QUrl
from PyQt6.QtGui import QDesktopServices

from storage import db
import config
from core import performance_tracker, self_upgrade, i18n, signal_recap
from chains import robinhood

# Langue d'affichage du dashboard (FR/EN/中文), distincte de config.ALERT_LANGUAGES
# qui ne pilote que Telegram. Persistée en base pour survivre à un redémarrage.
_UI_LANGUAGE_STATE_KEY = "ui_language"
_DEFAULT_UI_LANGUAGE = "fr"


class Bridge(QObject):
    # --- Signaux Python -> JS ---
    newSignal = pyqtSignal(str)     # JSON du signal
    logMessage = pyqtSignal(str)    # ligne de log formatée
    statsUpdated = pyqtSignal(str)  # JSON des stats de performance {1h,6h,24h}

    def __init__(self, scanner, parent=None):
        super().__init__(parent)
        self.scanner = scanner
        self.scanner.state.on_new_signal = self._on_new_signal
        self.scanner.state.on_stats_update = self._on_stats_update

    def _ui_language(self) -> str:
        lang = db.get_state(_UI_LANGUAGE_STATE_KEY, _DEFAULT_UI_LANGUAGE)
        return lang if lang in i18n.SUPPORTED_LANGS else _DEFAULT_UI_LANGUAGE

    def _localize_signal(self, signal: dict) -> dict:
        """
        Copie du signal avec ses raisons et vérifications manquantes rendues
        dans la langue d'affichage choisie dans le dashboard. Le moteur
        (core/security_checks.py, core/scoring.py) produit toujours ces textes
        en français ; c'est ici, au moment de les envoyer au JS, qu'ils sont
        traduits — jamais en base, pour que Telegram (config.ALERT_LANGUAGES)
        et le dashboard puissent choisir des langues différentes sans se
        marcher dessus.
        """
        lang = self._ui_language()
        if lang == "fr":
            return signal
        out = dict(signal)
        # translate_joined (pas translate_all) : un item comme le rendu de
        # missing.header peut lui-même contenir une liste de messages
        # concaténés ("Non vérifiable pour l'instant : a, b, c."), c'est le
        # même helper qu'utilise core/telegram_alerts.py pour ce cas.
        if out.get("reasons"):
            out["reasons"] = [i18n.translate_joined(str(r), lang) for r in out["reasons"]]
        if out.get("missing_checks"):
            out["missing_checks"] = [i18n.translate_joined(str(m), lang) for m in out["missing_checks"]]
        return out

    def _on_new_signal(self, signal: dict):
        localized = self._localize_signal(signal)
        self.newSignal.emit(json.dumps(localized, ensure_ascii=False, default=str))

    def _on_stats_update(self, stats: dict):
        self.statsUpdated.emit(json.dumps(stats, ensure_ascii=False, default=str))

    # --- Appels JS -> Python (slots) ---
    @pyqtSlot(result=str)
    def getExistingSignals(self) -> str:
        signals = [self._localize_signal(s) for s in db.get_active_signals()]
        return json.dumps(signals, ensure_ascii=False, default=str)

    @pyqtSlot(result=str)
    def getVerifiedSignals(self) -> str:
        """Uniquement les signaux entièrement vérifiés (niveau SIGNAL)."""
        signals = [self._localize_signal(s) for s in db.get_active_signals(tier=config.TIER_SIGNAL)]
        return json.dumps(signals, ensure_ascii=False, default=str)

    @pyqtSlot(result=str)
    def getWatchSignals(self) -> str:
        """Uniquement les détections précoces non vérifiées (niveau VEILLE)."""
        signals = [self._localize_signal(s) for s in db.get_active_signals(tier=config.TIER_WATCH)]
        return json.dumps(signals, ensure_ascii=False, default=str)

    @pyqtSlot(result=str)
    def getUiLanguage(self) -> str:
        """Langue d'affichage du dashboard (fr/en/zh), persistée entre les lancements."""
        return self._ui_language()

    @pyqtSlot(str)
    def setUiLanguage(self, lang: str):
        if lang not in i18n.SUPPORTED_LANGS:
            lang = _DEFAULT_UI_LANGUAGE
        db.set_state(_UI_LANGUAGE_STATE_KEY, lang)

    @pyqtSlot(result=str)
    def getWatchlist(self) -> str:
        """Tokens en file de promotion, re-testés à chaque cycle."""
        return json.dumps(self.scanner.get_watchlist(), ensure_ascii=False, default=str)

    @pyqtSlot(result=str)
    def getChainAvailability(self) -> str:
        """
        État de détection des chaînes dont la disponibilité n'est pas garantie.
        Robinhood Chain s'active toute seule dès qu'elle est indexée : cette
        méthode permet à l'interface d'afficher où en est la détection plutôt
        que de laisser croire à une panne.
        """
        return json.dumps({"robinhood": robinhood.get_status()}, ensure_ascii=False, default=str)

    @pyqtSlot(result=str)
    def getWeights(self) -> str:
        return json.dumps(config.SCORE_WEIGHTS, ensure_ascii=False)

    @pyqtSlot(result=str)
    def getStats(self) -> str:
        return json.dumps(performance_tracker.get_all_stats(), ensure_ascii=False, default=str)

    @pyqtSlot(result=str)
    def getHealthReport(self) -> str:
        """Diagnostic d'auto-correction : santé des sources de données,
        chaînes suspectées en panne silencieuse, journal des événements
        d'auto-réparation (core/self_upgrade.py)."""
        return json.dumps(self_upgrade.get_health_report(), ensure_ascii=False, default=str)

    @pyqtSlot(int, result=str)
    def getSignalRecap(self, signal_id: int) -> str:
        """
        Recap narratif prêt à coller pour les réseaux sociaux, rendu dans la
        langue d'affichage du dashboard (fr/en/zh). Indépendant de
        config.ALERT_LANGUAGES (Telegram). Renvoie une chaîne vide si le signal
        n'existe plus.
        """
        try:
            return signal_recap.build_recap(signal_id, lang=self._ui_language())
        except Exception:
            logging.getLogger("gem_hunter.bridge").exception(
                "Échec de génération du recap pour le signal %s", signal_id
            )
            return ""

    @pyqtSlot(int)
    def dismissSignal(self, signal_id: int):
        db.dismiss_signal(signal_id)

    @pyqtSlot()
    def clearAll(self):
        for s in db.get_active_signals():
            db.dismiss_signal(s["id"])

    @pyqtSlot(int)
    def setMinScore(self, value: int):
        self.scanner.set_min_score(value)

    @pyqtSlot()
    def run(self):
        self.scanner.run()

    @pyqtSlot()
    def stop(self):
        self.scanner.stop()

    @pyqtSlot(bool)
    def togglePause(self, paused: bool):
        if paused:
            self.scanner.pause()
        else:
            self.scanner.resume()

    @pyqtSlot(bool)
    def setAutoBuy(self, enabled: bool):
        self.scanner.set_auto_buy(enabled)

    @pyqtSlot(bool)
    def setTelegram(self, enabled: bool):
        self.scanner.set_telegram(enabled)

    @pyqtSlot(bool)
    def setWatchEnabled(self, enabled: bool):
        """Active ou coupe les alertes de veille précoce (non vérifiées)."""
        self.scanner.set_watch_enabled(enabled)

    @pyqtSlot(str)
    def setMode(self, mode: str):
        self.scanner.set_mode(mode)

    @pyqtSlot(str)
    def setScanProfile(self, name: str):
        self.scanner.set_scan_profile(name)

    @pyqtSlot(result=str)
    def getScanProfile(self) -> str:
        return config.SCAN_PROFILE

    @pyqtSlot(bool)
    def setMultichain(self, enabled: bool):
        self.scanner.set_multichain(enabled)

    @pyqtSlot(str, bool)
    def toggleChain(self, chain: str, enabled: bool):
        """Active/désactive une chaîne pour le scan ET les alertes. Une chaîne
        désactivée n'est plus scannée : ni carte dashboard, ni alerte Telegram."""
        self.scanner.toggle_chain(chain, enabled)

    @pyqtSlot(result=str)
    def getActiveChains(self) -> str:
        """Liste JSON des chaînes réellement scannées (state.active_chains)."""
        return json.dumps(sorted(self.scanner.state.active_chains))

    @pyqtSlot(bool)
    def setNansenEnabled(self, enabled: bool):
        self.scanner.set_nansen_enabled(enabled)

    @pyqtSlot(str)
    def openExternalUrl(self, url: str):
        """Ouvre un lien (Axiom, Solscan...) dans le navigateur par défaut du
        système — jamais dans la fenêtre de l'app elle-même."""
        QDesktopServices.openUrl(QUrl(url))


class QtToBridgeLogHandler(logging.Handler):
    """Redirige les logs Python vers le pont, qui les pousse au JS."""

    def __init__(self, bridge: Bridge):
        super().__init__()
        self.bridge = bridge

    def emit(self, record):
        try:
            self.bridge.logMessage.emit(self.format(record))
        except RuntimeError:
            pass  # bridge/fenêtre détruite pendant la fermeture de l'app
