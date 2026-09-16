"""
data_sources/http_utils.py — Session HTTP partagée, résiliente aux coupures réseau
Destination finale : gem_hunter/data_sources/http_utils.py

Utilisé par tous les clients d'API (dexscreener, geckoterminal, goplus, etc.)
pour éviter que le scanner ne s'arrête sur une simple erreur réseau transitoire
(reset de connexion, rate limit, timeout...).

NOUVEAU — AUTO-CORRECTION (disjoncteur / circuit breaker) : ce module est le
point de passage unique de tous les appels API sortants (par domaine). On y
ajoute un suivi de santé par domaine : si une source échoue de façon
persistante (API down, clé invalide, rate limit dur), le bot arrête tout seul
de la solliciter pendant un temps de repos, plutôt que de perdre du temps et
du budget de rate-limit sur des appels voués à l'échec à chaque cycle de scan
(toutes les 45s). Il retente automatiquement après le cooldown, et se
réactive dès qu'un appel repasse au vert — sans aucune intervention manuelle.
Consultable via core/self_upgrade.get_source_health() et exposé à l'UI via
gui/bridge.py::getSourceHealth().
"""
import logging
import time
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("gem_hunter.http")

# urllib3 journalise chaque tentative en WARNING, ce qui noie complètement les
# journaux du bot dès qu'une API ralentit. On garde nos propres messages, qui
# disent la même chose en une ligne au lieu de quatre.
logging.getLogger("urllib3.connectionpool").setLevel(logging.ERROR)

# --- Disjoncteur par domaine -------------------------------------------------
_CB_WINDOW = 20          # nombre d'appels récents observés par domaine
_CB_MIN_CALLS = 4        # ne jamais couper avant d'avoir vu au moins ce volume.
                         # Abaissé de 8 à 4 : à 8, une API qui répondait par des
                         # timeouts de 15s coûtait deux minutes avant que le
                         # disjoncteur ne daigne s'armer.
_CB_FAILURE_RATIO = 0.8  # coupe si >= 80% d'échecs sur la fenêtre récente
_CB_COOLDOWN_SECONDS = 120  # durée de repos avant de réautoriser un essai

_health: dict[str, dict] = {}  # domaine -> {"results": deque[bool], "tripped_until": float, "total_calls": int, "total_failures": int}


def _domain(url: str) -> str:
    try:
        return urlparse(url).netloc or url
    except Exception:
        return url


def _get_bucket(domain: str) -> dict:
    from collections import deque
    if domain not in _health:
        _health[domain] = {
            "results": deque(maxlen=_CB_WINDOW),
            "tripped_until": 0.0,
            "total_calls": 0,
            "total_failures": 0,
        }
    return _health[domain]


def _record(domain: str, success: bool):
    bucket = _get_bucket(domain)
    bucket["results"].append(success)
    bucket["total_calls"] += 1
    if not success:
        bucket["total_failures"] += 1

    results = bucket["results"]
    if len(results) >= _CB_MIN_CALLS:
        failure_ratio = 1 - (sum(results) / len(results))
        now = time.time()
        if failure_ratio >= _CB_FAILURE_RATIO and bucket["tripped_until"] < now:
            bucket["tripped_until"] = now + _CB_COOLDOWN_SECONDS
            logger.warning(
                f"Disjoncteur ARMÉ pour {domain} : {failure_ratio*100:.0f}% d'échecs "
                f"sur les {len(results)} derniers appels — pause de {_CB_COOLDOWN_SECONDS}s "
                f"avant nouvel essai automatique."
            )


def _is_tripped(domain: str) -> bool:
    bucket = _health.get(domain)
    if not bucket:
        return False
    now = time.time()
    if bucket["tripped_until"] > now:
        return True
    if bucket["tripped_until"] != 0.0 and bucket["tripped_until"] <= now:
        # Le cooldown vient d'expirer : on réinitialise la fenêtre pour ne
        # pas re-déclencher immédiatement sur les vieux échecs, et on laisse
        # passer cet appel comme "essai de reprise".
        bucket["results"].clear()
        bucket["tripped_until"] = 0.0
        logger.info(f"Disjoncteur RÉARMÉ pour {domain} — reprise des appels.")
    return False


def get_source_health() -> dict:
    """Snapshot de santé par domaine, pour l'UI / diagnostics (core/self_upgrade.py)."""
    now = time.time()
    snapshot = {}
    for domain, bucket in _health.items():
        results = bucket["results"]
        success_rate = (sum(results) / len(results) * 100) if results else None
        snapshot[domain] = {
            "success_rate_recent_pct": round(success_rate, 1) if success_rate is not None else None,
            "total_calls": bucket["total_calls"],
            "total_failures": bucket["total_failures"],
            "circuit_open": bucket["tripped_until"] > now,
            "cooldown_remaining_s": max(0, round(bucket["tripped_until"] - now)) if bucket["tripped_until"] > now else 0,
        }
    return snapshot

_session = requests.Session()
# Tentatives volontairement peu nombreuses. Le scanner tourne en boucle toutes
# les 45 secondes : réessayer trois fois avec un recul croissant sur une API
# lente coûte plus d'une minute pour une donnée qui sera de toute façon
# redemandée au cycle suivant. Mieux vaut abandonner vite, laisser le
# disjoncteur mettre la source au repos, et repartir.
_retry = Retry(
    total=1,
    backoff_factor=0.5,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"],
)
_session.mount("https://", HTTPAdapter(max_retries=_retry))
_session.mount("http://", HTTPAdapter(max_retries=_retry))
_session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) GemHunter/1.0",
    "Accept": "application/json",
})


def safe_get_json(url: str, params: dict | None = None, timeout: int = 8, headers: dict | None = None) -> dict | list | None:
    """
    GET résilient : ne lève jamais d'exception réseau, renvoie None en cas
    d'échec (connexion réinitialisée, timeout, statut HTTP non-200, JSON invalide).
    Coupé automatiquement si le domaine est en disjoncteur ouvert (voir plus haut).
    """
    domain = _domain(url)
    if _is_tripped(domain):
        return None

    try:
        resp = _session.get(url, params=params, timeout=timeout, headers=headers)
    except requests.exceptions.RequestException as e:
        logger.warning(f"Requête réseau échouée ({url}) : {e}")
        _record(domain, False)
        return None
    if resp.status_code != 200:
        logger.warning(f"Réponse HTTP {resp.status_code} pour {url}")
        _record(domain, False)
        return None
    try:
        result = resp.json()
    except ValueError:
        logger.warning(f"Réponse non-JSON pour {url}")
        _record(domain, False)
        return None
    _record(domain, True)
    return result


def safe_post_json(url: str, json_body: dict, headers: dict | None = None, timeout: int = 8) -> dict | None:
    """Équivalent POST de safe_get_json, résilient aux erreurs réseau et au même disjoncteur par domaine."""
    domain = _domain(url)
    if _is_tripped(domain):
        return None

    try:
        resp = _session.post(url, json=json_body, headers=headers, timeout=timeout)
    except requests.exceptions.RequestException as e:
        logger.warning(f"Requête réseau échouée ({url}) : {e}")
        _record(domain, False)
        return None
    if resp.status_code != 200:
        logger.warning(f"Réponse HTTP {resp.status_code} pour {url} : {resp.text[:200]}")
        _record(domain, False)
        return None
    try:
        result = resp.json()
    except ValueError:
        logger.warning(f"Réponse non-JSON pour {url}")
        _record(domain, False)
        return None
    _record(domain, True)
    return result
