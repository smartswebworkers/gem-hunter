"""
chains/arc.py — Arc Network (Circle, chain ID 5042)
Destination finale : gem_hunter/chains/arc.py

Arc est le layer-1 EVM lancé par Circle (mainnet public le 16 septembre 2026),
avec l'USDC comme actif de gas natif. Validateurs fondateurs institutionnels
(BlackRock, Visa, Mastercard, DTCC...), accès développeur annoncé permissionless.
Chaîne pensée pour les paiements en stablecoin, le FX et les actifs tokenisés —
pas un terrain de memecoins comme Solana ou pump.fun, mais l'accès développeur
ouvert n'exclut pas qu'un écosystème de tokens/DEX permissionless y apparaisse.

MÊME PRINCIPE QUE chains/robinhood.py, ENCORE PLUS JUSTIFIÉ ICI : la chaîne a
été ajoutée le lendemain de son lancement mainnet. Aucun indexeur (GoPlus,
GeckoTerminal, DexScreener) ne la couvrait au moment d'écrire ce module, et
aucune donnée empirique de marché n'existe pour calibrer un seuil de liquidité
ou de volume spécifique (contrairement à CHAIN_MARKET_OVERRIDES pour BSC/Base/
Robinhood, justifié par des mesures réelles) — on n'invente donc aucune
surcharge, la chaîne utilise les seuils globaux du profil actif tels quels.

Ce module teste la disponibilité réelle au lieu de la supposer, exactement
comme Robinhood Chain :
  1. GeckoTerminal indexe-t-il le réseau ?  (découverte des nouvelles pools)
  2. DexScreener indexe-t-il la chaîne ?    (prix, volumes, âge des paires)
  3. GoPlus couvre-t-il le chain ID 5042 ?  (sécurité du contrat)

MISE À JOUR DU 23/09/2026 — Nansen couvre désormais Arc, confirmé en direct
(pas supposé) : /token-screener et /tgm/holders renvoient tous deux de vrais
tokens et détenteurs Smart Money labellisés sur Arc (ex. ARGUS, chain ID 5042).
Le mapping a donc été ajouté à NANSEN_CHAIN_MAP et NANSEN_SCREENER_CHAIN_MAP
(data_sources/nansen.py), ce qui active automatiquement l'exigence
« smart money requis » (REQUIRE_SMART_MONEY, mode « quality ») sur Arc au même
titre que Solana/BSC/Base/Ethereum — un renforcement, jamais un relâchement,
de la sécurité. GeckoTerminal, DexScreener et GoPlus couvrent également déjà
la chaîne à cette date : la découverte ne dépend donc plus uniquement d'eux
deux, mais _probe() ci-dessous ne teste toujours que ces trois-là, puisqu'ils
suffisent seuls à activer la chaîne (voir chains/robinhood.py pour le cas où
un module a besoin du screener Nansen comme voie de découverte de secours).
"""
import logging
import time

from chains import evm_common
from core.i18n import t
from data_sources import dexscreener, geckoterminal, goplus

logger = logging.getLogger("gem_hunter.chains.arc")

CHAIN = "arc"

_REPROBE_INTERVAL_SECONDS = 1800  # 30 min — une chaîne aussi récente peut être indexée à tout moment

_status: dict = {
    "probed_at": 0.0,
    "discovery_available": False,
    "security_available": False,
    "geckoterminal_network": None,
    "dexscreener_chain": None,
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
    goplus_confirmed = bool(goplus.get_supported_chains())
    goplus_ok = goplus.supports_chain(CHAIN)

    _status["geckoterminal_network"] = gt_network
    _status["dexscreener_chain"] = ds_chain
    _status["discovery_available"] = bool(gt_network or ds_chain)
    _status["security_available"] = bool(goplus_ok)
    _status["security_confirmed"] = goplus_confirmed and goplus_ok

    if _status["discovery_available"] and _status["security_confirmed"]:
        _status["detail"] = "chaîne pleinement disponible (découverte et sécurité confirmées)"
        logger.info("Arc Network détectée et activée : découverte et vérification de sécurité disponibles.")
    elif _status["discovery_available"] and _status["security_available"]:
        _status["detail"] = (
            "découverte disponible ; couverture GoPlus non confirmée "
            "(endpoint injoignable au moment du test)"
        )
        logger.info(
            "Arc Network détectée et activée pour la découverte. La couverture GoPlus "
            "n'a pas pu être confirmée : les tokens resteront en VEILLE tant que leurs "
            "données de sécurité ne reviendront pas."
        )
    elif _status["discovery_available"]:
        _status["detail"] = (
            "découverte disponible, sécurité non couverte par GoPlus — "
            "scan en niveau VEILLE uniquement"
        )
        logger.info(
            "Arc Network : découverte disponible, mais pas encore couverte par GoPlus — "
            "scan actif en niveau VEILLE (NON VÉRIFIÉ), sans signal validé."
        )
    else:
        _status["detail"] = "chaîne pas encore indexée par les sources de découverte"
        logger.info(
            "Arc Network pas encore indexée par GeckoTerminal ni DexScreener — "
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
        for candidate in candidates:
            candidate["security"] = goplus.empty_security(
                t("unavailable.arc_goplus")
            )
        return candidates

    return evm_common.enrich_batch(CHAIN, candidates, should_stop)


def enrich_with_security(candidate: dict) -> dict:
    if not is_security_verifiable():
        candidate["security"] = goplus.empty_security(
            t("unavailable.arc_goplus")
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
