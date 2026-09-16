"""
chains/robinhood.py — Robinhood Chain (Arbitrum Orbit, chain ID 4663)
Destination finale : gem_hunter/chains/robinhood.py

REMPLACE LE MODE DÉGRADÉ EN DUR.
La version précédente contenait `AUTO_SCAN_ENABLED = False` écrit en dur, avec
un commentaire expliquant que la chaîne n'était pas encore indexée « au moment
de l'écriture de ce module ». Deux conséquences : la chaîne ne pouvait jamais
s'activer, même une fois indexée, sans modification manuelle du code ; et son
enrich_with_security renvoyait un dict avec data_available=False, ce qui la
faisait rejeter systématiquement tout en consommant un créneau de scan.

Ce module teste maintenant la disponibilité réelle au lieu de la supposer. Au
premier scan il interroge les trois fournisseurs dont dépend une évaluation
sérieuse, puis s'active tout seul si les conditions sont réunies :

  1. GeckoTerminal indexe-t-il le réseau ? (découverte des nouvelles pools)
  2. DexScreener indexe-t-il la chaîne ?    (prix, volumes, âge des paires)
  3. GoPlus couvre-t-il le chain ID 4663 ?  (sécurité du contrat)

Si les sources de découverte répondent mais pas GoPlus, la chaîne est scannée
en niveau VEILLE uniquement : on voit passer les tokens avant tout le monde,
mais aucun ne devient un SIGNAL tant que sa sécurité n'est pas vérifiable. La
détection est re-testée périodiquement, donc la chaîne bascule d'elle-même en
mode complet le jour où GoPlus l'ajoute.
"""
import logging
import time

import config as cfg
from chains import evm_common
from core.i18n import t
from data_sources import dexscreener, geckoterminal, goplus, nansen

logger = logging.getLogger("gem_hunter.chains.robinhood")

CHAIN = "robinhood"

# Intervalle entre deux re-tests de disponibilité, tant que la chaîne n'est pas
# pleinement disponible. Une chaîne récente peut être ajoutée par un indexeur
# n'importe quand, il n'y a aucune raison d'attendre un redémarrage du bot.
_REPROBE_INTERVAL_SECONDS = 1800  # 30 min

_status: dict = {
    "probed_at": 0.0,
    "discovery_available": False,
    "security_available": False,
    "geckoterminal_network": None,
    "dexscreener_chain": None,
    "nansen_screener": False,
    "security_confirmed": False,
    "detail": "jamais testé",
}


def get_status() -> dict:
    """État de détection, exposé à l'interface (gui/bridge.py)."""
    return dict(_status)


def _probe(force: bool = False) -> dict:
    now = time.time()
    fully_available = _status["discovery_available"] and _status["security_available"]
    if not force and fully_available:
        return _status
    if not force and (now - _status["probed_at"]) < _REPROBE_INTERVAL_SECONDS:
        return _status

    _status["probed_at"] = now

    gt_network = geckoterminal.resolve_network(CHAIN)
    ds_chain = dexscreener.resolve_chain_id(CHAIN)
    # Le Token Screener Nansen couvre Robinhood Chain alors que GeckoTerminal et
    # DexScreener ne l'indexent pas : c'est aujourd'hui la seule voie de
    # découverte réelle pour cette chaîne (voir data_sources/nansen.py).
    nansen_screener = (
        nansen.screener_supports_chain(CHAIN) and bool(cfg.API_KEYS.get("nansen"))
    )
    # supports_chain() renvoie True quand l'endpoint des chaînes supportées est
    # injoignable, faute de pouvoir conclure. On distingue donc « confirmé
    # couvert » de « supposé couvert », pour ne pas annoncer une vérification
    # de sécurité disponible sur la foi d'un appel qui a échoué.
    goplus_confirmed = bool(goplus.get_supported_chains())
    goplus_ok = goplus.supports_chain(CHAIN)

    _status["geckoterminal_network"] = gt_network
    _status["dexscreener_chain"] = ds_chain
    _status["nansen_screener"] = nansen_screener
    _status["discovery_available"] = bool(gt_network or ds_chain or nansen_screener)
    _status["security_available"] = bool(goplus_ok)

    _status["security_confirmed"] = goplus_confirmed and goplus_ok

    if _status["discovery_available"] and _status["security_confirmed"]:
        _status["detail"] = "chaîne pleinement disponible (découverte et sécurité confirmées)"
        logger.info("Robinhood Chain détectée et activée : découverte et vérification de sécurité disponibles.")
    elif _status["discovery_available"] and _status["security_available"]:
        _status["detail"] = (
            "découverte disponible ; couverture GoPlus non confirmée "
            "(endpoint injoignable au moment du test)"
        )
        logger.info(
            "Robinhood Chain détectée et activée pour la découverte. La couverture GoPlus "
            "n'a pas pu être confirmée : les tokens resteront en VEILLE tant que leurs "
            "données de sécurité ne reviendront pas."
        )
    elif _status["discovery_available"]:
        via = "Nansen" if (nansen_screener and not (gt_network or ds_chain)) else "les sources de marché"
        _status["detail"] = (
            f"découverte via {via}, sécurité non couverte par GoPlus — "
            "scan en niveau VEILLE uniquement"
        )
        logger.info(
            f"Robinhood Chain : découverte disponible via {via}, mais pas encore couverte par GoPlus — "
            "scan actif en niveau VEILLE (NON VÉRIFIÉ), sans signal validé. "
            "Les cartes restent sous le seuil d'un curseur élevé : baisser le curseur pour les voir."
        )
    else:
        _status["detail"] = "chaîne pas encore indexée par les sources de découverte"
        logger.info(
            "Robinhood Chain pas encore indexée par GeckoTerminal ni DexScreener — "
            f"nouveau test automatique dans {_REPROBE_INTERVAL_SECONDS // 60} min."
        )

    return _status


def is_available() -> bool:
    return _probe()["discovery_available"]


def is_security_verifiable() -> bool:
    return _probe()["security_available"]


def discover_candidates() -> list[dict]:
    status = _probe()
    if not status["discovery_available"]:
        return []
    return evm_common.discover_candidates(CHAIN)


def enrich_batch(candidates: list[dict], should_stop=None) -> list[dict]:
    if not candidates:
        return []

    if should_stop is not None and should_stop():
        return candidates

    if not is_security_verifiable():
        # Sécurité non vérifiable sur cette chaîne : on ne fabrique surtout pas
        # un dict « rien à signaler ». On marque explicitement l'indisponibilité,
        # ce qui envoie ces candidats en VEILLE et jamais en SIGNAL.
        for candidate in candidates:
            # dex_paid est vérifié plus tard, juste avant une promotion en
            # signal (voir core/scanner.py) : inutile de dépenser un appel par
            # candidat ici, aucun ne peut devenir un signal de toute façon.
            candidate["security"] = goplus.empty_security(
                t("unavailable.robinhood_goplus")
            )
        return candidates

    return evm_common.enrich_batch(CHAIN, candidates, should_stop)


def enrich_with_security(candidate: dict) -> dict:
    if not is_security_verifiable():
        candidate["security"] = goplus.empty_security(
            t("unavailable.robinhood_goplus")
        )
        return candidate
    return evm_common.enrich_with_security(CHAIN, candidate)


def check_contract_manually(token_address: str) -> dict:
    """Vérification à la demande d'un contrat précis, depuis l'interface."""
    status = _probe(force=True)
    candidate = {"chain": CHAIN, "contract": token_address}
    pair = dexscreener.get_best_pair(CHAIN, token_address)
    if pair:
        candidate.update(pair)
        candidate["chain"] = CHAIN
    enrich_with_security(candidate)
    candidate["availability"] = status["detail"]
    return candidate
