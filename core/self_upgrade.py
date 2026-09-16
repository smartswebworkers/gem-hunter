"""
core/self_upgrade.py — Supervision et auto-diagnostic du bot
Destination finale : gem_hunter/core/self_upgrade.py

NOUVEAU MODULE. Complète core/self_tuning.py (qui corrige déjà en continu les
POIDS du score à partir des performances réelles) avec une couche de
supervision plus large : détecter automatiquement des problèmes de
fonctionnement — pas seulement de stratégie — et réagir sans intervention
manuelle quand c'est sûr de le faire, sinon les journaliser clairement.

Ce que fait ce module à chaque cycle de supervision (appelé depuis
core/scanner.py, cadence configurable) :
  1. Santé des sources de données (data_sources/http_utils.py) : détecte les
     domaines en échec persistant et le signale (le disjoncteur agit déjà
     seul ; ce module en garde un historique et alerte si ça persiste).
  2. Sécheresse de signaux : si un nombre anormalement élevé de cycles de
     scan de suite ne produisent aucun candidat du tout (pas juste "rejeté"
     ou "sous le seuil", mais zéro candidat brut) sur une chaîne active,
     c'est le symptôme typique d'une source de découverte cassée
     silencieusement (ex: format d'API changé) plutôt que d'un marché calme.
  3. Qualité des données d'entraînement pour l'auto-correction des poids
     (core/self_tuning.py) : vérifie que le pipeline features -> performance
     alimente bien des échantillons exploitables.

Toute action et tout diagnostic est journalisé dans app_state
("self_upgrade_log", borné aux 300 dernières entrées) pour audit, et exposé à
l'UI via gui/bridge.py::getHealthReport().

Volontairement, ce module ne touche JAMAIS aux seuils de rejet de sécurité
(REJECTION_THRESHOLDS) ni au seuil de score minimum (MIN_SCORE_TO_BUY, choisi
manuellement par l'utilisateur par design — voir config.py) : l'auto-upgrade
porte sur la fiabilité opérationnelle du bot, pas sur son niveau de risque.
"""
import logging
import time

from data_sources import http_utils
from storage import db

logger = logging.getLogger("gem_hunter.self_upgrade")

# Nombre de cycles de scan consécutifs sans le moindre candidat brut sur une
# chaîne avant de considérer que sa source de découverte est probablement
# cassée (et pas juste un marché calme).
DRY_STREAK_ALERT_THRESHOLD = 15

_dry_streaks: dict[str, int] = {}   # chain -> cycles consécutifs à 0 candidat
_dry_alerted: set[str] = set()      # chaînes déjà signalées (évite le spam de logs)


def _log_event(event_type: str, message: str, extra: dict | None = None):
    logger.warning(f"[self-upgrade] {message}")
    history = db.get_state("self_upgrade_log", [])
    history.append({
        "ts": time.time(),
        "type": event_type,
        "message": message,
        "extra": extra or {},
    })
    db.set_state("self_upgrade_log", history[-300:])


def record_cycle_candidates(chain: str, candidates_count: int):
    """
    À appeler par core/scanner.py après chaque discover_candidates() par
    chaîne, avec le nombre brut de candidats trouvés (avant tout filtrage).
    """
    if candidates_count > 0:
        if _dry_streaks.get(chain, 0) >= DRY_STREAK_ALERT_THRESHOLD:
            _log_event(
                "source_recovered",
                f"[{chain}] La source de découverte est repartie après "
                f"{_dry_streaks[chain]} cycle(s) sans aucun candidat.",
            )
        _dry_streaks[chain] = 0
        _dry_alerted.discard(chain)
        return

    _dry_streaks[chain] = _dry_streaks.get(chain, 0) + 1
    if _dry_streaks[chain] >= DRY_STREAK_ALERT_THRESHOLD and chain not in _dry_alerted:
        _dry_alerted.add(chain)
        _log_event(
            "dry_source_suspected",
            f"[{chain}] Aucun candidat brut depuis {_dry_streaks[chain]} cycles de scan — "
            f"probable panne silencieuse de la source de découverte (format d'API changé, "
            f"endpoint down...) plutôt qu'un marché simplement calme. À vérifier manuellement.",
            {"chain": chain, "streak": _dry_streaks[chain]},
        )


def run_supervision_cycle():
    """
    Cycle de supervision périodique — appelé depuis core/scanner.py à une
    cadence plus large que le scan lui-même (voir SELF_UPGRADE_CYCLE_EVERY_N_SCANS).
    """
    _check_source_health()
    _check_training_pipeline()


def _check_source_health():
    health = http_utils.get_source_health()
    for domain, stats in health.items():
        if stats["circuit_open"]:
            _log_event(
                "circuit_open",
                f"Disjoncteur ouvert pour {domain} — {stats['total_failures']}/"
                f"{stats['total_calls']} échecs au total, reprise automatique "
                f"dans {stats['cooldown_remaining_s']}s.",
                stats,
            )


def _check_training_pipeline():
    from core.self_tuning import TRAINING_HORIZON, MIN_SAMPLES_TO_LEARN
    data = db.get_training_data(horizon=TRAINING_HORIZON)
    if len(data) == 0:
        # Silence normal en tout début de vie du bot : pas d'alerte avant
        # qu'un premier horizon de suivi (6h par défaut) ait eu le temps de
        # s'écouler depuis le tout premier signal.
        return
    logger.debug(
        f"[self-upgrade] Pipeline d'auto-correction : {len(data)} échantillon(s) "
        f"disponible(s) (seuil d'apprentissage : {MIN_SAMPLES_TO_LEARN})."
    )


def get_health_report() -> dict:
    """Snapshot consolidé pour l'UI (gui/bridge.py::getHealthReport)."""
    return {
        "source_health": http_utils.get_source_health(),
        "dry_streaks": dict(_dry_streaks),
        "recent_events": db.get_state("self_upgrade_log", [])[-50:],
    }
