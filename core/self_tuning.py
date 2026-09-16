"""
core/self_tuning.py — Auto-correction des poids du scoring
Destination finale : gem_hunter/core/self_tuning.py

Toutes les LEARN_CYCLE_EVERY_N_SCANS itérations du scanner, ce module ajuste
les poids du score à partir des rendements réellement mesurés par
core/performance_tracker.py.

CORRECTIF DE SÉCURITÉ — L'APPRENTISSAGE POUVAIT DÉSARMER LA SÉCURITÉ.
La descente de gradient s'appliquait à TOUS les poids, avec pour seule borne
basse WEIGHT_MIN_BOUND (2 sur une échelle de 100). Sur un échantillon court, et
en marché haussier, les tokens les plus rentables sont souvent aussi les plus
risqués : le gradient poussait donc mécaniquement le poids de la sécurité vers
sa borne basse. Le bot n'était pas seulement mal réglé au départ, il apprenait
activement à ignorer la sécurité, et d'autant plus vite qu'il avait eu de la
chance. Les poids listés dans config.PINNED_SCORE_WEIGHTS sont désormais
exclus de l'apprentissage.

Deuxième correctif : la somme des poids était laissée libre de dériver, si bien
que les scores n'étaient plus comparables d'une session à l'autre (un seuil de
80 ne représentait plus la même exigence). Elle est maintenant renormalisée à
100 après chaque cycle.
"""
import logging
import math
import time

from config import (
    DEFAULT_SCORE_WEIGHTS, LEARNING_RATE, WEIGHT_MIN_BOUND, WEIGHT_MAX_BOUND,
    PINNED_SCORE_WEIGHTS,
)
from storage import db
import config as cfg

logger = logging.getLogger("gem_hunter.self_tuning")

TRAINING_HORIZON = "6h"   # horizon de référence pour l'apprentissage
MIN_SAMPLES_TO_LEARN = 30  # relevé de 10 à 30 : à 10 échantillons, le gradient
                           # suit le bruit, pas le signal


def _is_degenerate(weights: dict) -> bool:
    """
    Vrai si les poids appris se sont effondrés : la quasi-totalité des poids
    apprenables collée à son plancher pendant qu'un seul monopolise le score.
    C'est l'état observé dans les journaux (« liquidity_quality=43.1, ...,
    momentum_5m=2.2 » => 24 sous le seuil, 0 signal). Dans ce cas on repart des
    valeurs par défaut plutôt que de laisser un curseur devenir inatteignable.
    """
    learnable = [v for k, v in weights.items() if k not in PINNED_SCORE_WEIGHTS]
    if len(learnable) < 3:
        return False
    at_floor = sum(1 for v in learnable if v <= WEIGHT_MIN_BOUND + 0.5)
    return at_floor >= len(learnable) - 1 and max(learnable) > 2 * WEIGHT_MIN_BOUND


def load_weights() -> dict:
    """Charge les poids courants (persistés) ou les valeurs par défaut."""
    saved = db.get_state("score_weights")
    saved_version = db.get_state("score_weights_version")
    version_ok = saved_version == getattr(cfg, "SCORE_WEIGHTS_VERSION", 1)
    if (saved and isinstance(saved, dict)
            and set(saved.keys()) == set(DEFAULT_SCORE_WEIGHTS.keys())
            and version_ok
            and not _is_degenerate(saved)):
        # Les poids épinglés sont réimposés à chaque chargement, y compris sur
        # une base sauvegardée par une version antérieure du bot.
        for key in PINNED_SCORE_WEIGHTS:
            saved[key] = DEFAULT_SCORE_WEIGHTS[key]
        return saved
    if saved and not version_ok:
        logger.info(
            "Poids de scoring sauvegardés obsolètes (structure changée) — "
            "réinitialisation aux valeurs par défaut."
        )
    elif saved and isinstance(saved, dict) and _is_degenerate(saved):
        logger.warning(
            "Poids de scoring appris effondrés (un seul critère domine) — "
            "réinitialisation aux valeurs par défaut."
        )
    return dict(DEFAULT_SCORE_WEIGHTS)


def save_weights(weights: dict):
    db.set_state("score_weights", weights)
    db.set_state("score_weights_version", getattr(cfg, "SCORE_WEIGHTS_VERSION", 1))
    record_snapshot(weights)


def record_snapshot(weights: dict):
    history = db.get_state("score_weights_history", [])
    history.append({"ts": time.time(), "weights": weights})
    db.set_state("score_weights_history", history[-200:])


def _normalize(weights: dict) -> dict:
    """
    Ramène la somme totale des poids à 100 pour qu'un score de 80 signifie
    toujours la même chose dans le temps.

    Les poids épinglés gardent leur valeur exacte : ce sont les poids
    apprenables qui se partagent le solde. Renormaliser l'ensemble puis
    réimposer les valeurs épinglées par-dessus, comme le faisait une première
    version de ce correctif, laissait la somme dériver au-dessus de 100 et
    diluait donc en douce le poids relatif de la sécurité — l'inverse du but
    recherché.
    """
    pinned = {k: DEFAULT_SCORE_WEIGHTS[k] for k in PINNED_SCORE_WEIGHTS if k in DEFAULT_SCORE_WEIGHTS}
    learnable = {k: v for k, v in weights.items() if k not in pinned}

    budget = 100 - sum(pinned.values())
    learnable_total = sum(learnable.values())
    if budget <= 0 or learnable_total <= 0:
        return dict(DEFAULT_SCORE_WEIGHTS)

    factor = budget / learnable_total
    normalized = {k: round(v * factor, 4) for k, v in learnable.items()}
    normalized.update(pinned)
    return normalized


def update_weights() -> dict:
    """
    Descente de gradient sur les poids NON épinglés, à partir des couples
    (features, rendement réel) mesurés.
    """
    weights = load_weights()
    data = db.get_training_data(horizon=TRAINING_HORIZON)

    if len(data) < MIN_SAMPLES_TO_LEARN:
        logger.info(
            f"Auto-correction : seulement {len(data)} échantillon(s) à l'horizon "
            f"{TRAINING_HORIZON} (minimum {MIN_SAMPLES_TO_LEARN}) — poids inchangés."
        )
        return weights

    learnable = [k for k in weights if k not in PINNED_SCORE_WEIGHTS]
    total_weight = sum(weights.values()) or 1
    gradients = {k: 0.0 for k in learnable}

    for features, return_pct in data:
        return_frac = return_pct / 100.0
        predicted = sum(features.get(k, 0.25) * weights.get(k, 0) for k in weights) / total_weight
        # Cible : un rendement fortement positif vise un score proche de 1, une
        # perte un score proche de 0. Un rug enregistré à -100% tire donc
        # violemment vers le bas les features qui l'accompagnaient — ce qui
        # n'était possible qu'une fois le biais de survie corrigé dans
        # core/performance_tracker.py.
        target = 1 / (1 + math.exp(-3 * return_frac))
        error = target - predicted
        for k in learnable:
            gradients[k] += error * features.get(k, 0.25)

    n = len(data)
    for k in learnable:
        weights[k] += LEARNING_RATE * (gradients[k] / n) * total_weight
        weights[k] = max(WEIGHT_MIN_BOUND, min(WEIGHT_MAX_BOUND, weights[k]))

    weights = _normalize(weights)
    save_weights(weights)
    logger.info(
        f"Auto-correction appliquée sur {n} échantillon(s). Poids épinglés "
        f"(non modifiables) : {', '.join(PINNED_SCORE_WEIGHTS)}. Nouveaux poids : "
        + ", ".join(f"{k}={v:.1f}" for k, v in sorted(weights.items(), key=lambda kv: -kv[1]))
    )
    return weights


def apply_weights_to_config(weights: dict):
    """Applique les poids en mémoire pour que core/scoring.py les utilise immédiatement."""
    cfg.SCORE_WEIGHTS = weights


def run_learning_cycle():
    weights = update_weights()
    apply_weights_to_config(weights)
    return weights
