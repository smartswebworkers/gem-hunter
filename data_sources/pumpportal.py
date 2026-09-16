"""
data_sources/pumpportal.py — Flux temps réel des créations de tokens pump.fun
Destination finale : gem_hunter/data_sources/pumpportal.py

PumpPortal (https://pumpportal.fun) est une API tierce gratuite et non
officielle pour pump.fun. Son flux WebSocket "subscribeNewToken" pousse
un événement à chaque création de token sur Solana, en quelques
millisecondes — c'est la source la plus pertinente pour capter des
memecoins dans une fourchette de market cap de quelques milliers de
dollars, bien avant qu'ils n'apparaissent sur DexScreener/GeckoTerminal
(qui n'indexent qu'après migration vers un pool Raydium classique).

Fonctionne en tâche de fond (thread daemon), avec reconnexion automatique.
Aucune clé API requise pour ce flux de base.
"""
import json
import logging
import threading
import time
from collections import deque

import websocket

logger = logging.getLogger("gem_hunter.pumpportal")

WS_URL = "wss://pumpportal.fun/api/data"

_lock = threading.Lock()
# maxlen relevé de 1000 à 4000 : avec une fenêtre de suivi de 30 min
# (config.PUMP_RECENT_WINDOW_SECONDS), un pic de créations pump.fun pouvait
# évincer des tokens encore dans la fenêtre avant qu'un cycle de scan ne les ait
# vus.
_recent_tokens: deque = deque(maxlen=4000)
_started = False


def _on_message(ws, message):
    try:
        data = json.loads(message)
    except (ValueError, TypeError):
        return
    if not isinstance(data, dict) or "mint" not in data:
        return  # message de confirmation d'abonnement, pas un token
    with _lock:
        _recent_tokens.append({**data, "_received_at": time.time()})


def _on_open(ws):
    logger.info("Connexion PumpPortal établie — abonnement aux nouveaux tokens Solana.")
    ws.send(json.dumps({"method": "subscribeNewToken"}))


def _on_error(ws, error):
    logger.warning(f"Erreur WebSocket PumpPortal : {error}")


def _on_close(ws, code, msg):
    logger.warning("Connexion PumpPortal fermée — nouvelle tentative dans 5s.")


def _run_forever():
    while True:
        try:
            app = websocket.WebSocketApp(
                WS_URL,
                on_open=_on_open,
                on_message=_on_message,
                on_error=_on_error,
                on_close=_on_close,
            )
            app.run_forever(ping_interval=20, ping_timeout=10)
        except Exception:
            logger.exception("Exception dans la boucle WebSocket PumpPortal.")
        time.sleep(5)


def ensure_started():
    """Démarre le thread d'écoute une seule fois (idempotent)."""
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_run_forever, daemon=True, name="pumpportal-ws").start()


def get_recent_tokens(max_age_seconds: float = 600) -> list[dict]:
    """Tokens créés dans les `max_age_seconds` dernières secondes (défaut 10 min)."""
    now = time.time()
    with _lock:
        return [t for t in _recent_tokens if now - t["_received_at"] <= max_age_seconds]
