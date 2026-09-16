"""
chains/evm_common.py — Logique de découverte et d'enrichissement partagée par
toutes les chaînes EVM (BNB Chain, Robinhood Chain, Ethereum).
Destination finale : gem_hunter/chains/evm_common.py

NOUVEAU MODULE. chains/bsc.py et chains/ethereum.py étaient deux copies du même
code à un identifiant de chaîne près, et chains/robinhood.py une troisième
copie inachevée. Toute correction appliquée à l'un devait être répétée à
l'identique dans les autres, ce qui n'avait déjà pas été fait : la version
Ethereum n'avait jamais reçu certains correctifs de la version BNB. La logique
vit désormais ici, une seule fois.
"""
import logging

from core.i18n import t
from data_sources import dexscreener, geckoterminal, goplus, nansen

import config as cfg

logger = logging.getLogger("gem_hunter.chains.evm")


def _prefilter_liquidity() -> float:
    # Présélection large : on ne veut pas dépenser des appels GoPlus sur des
    # pools manifestement morts, mais le filtrage fin appartient au moteur de
    # sécurité, pas à la découverte.
    return cfg.MIN_LIQUIDITY_USD / 4


def discover_candidates(chain: str) -> list[dict]:
    """
    Découverte de candidats sur une chaîne EVM, à partir de deux sources
    complémentaires : les nouvelles pools GeckoTerminal (meilleur signal de
    fraîcheur) et les nouveaux tokens listés chez DexScreener.
    """
    candidates: list[dict] = []
    prefilter = _prefilter_liquidity()

    # --- Source primaire : pools GeckoTerminal ---
    # Profil « quality » : les pools qui MONTENT (trending), pas les plus
    # récentes — ces dernières sont massivement des rugs. Profil « degen » :
    # les nouvelles pools, pour capter le plus tôt possible.
    # On n'interroge GeckoTerminal que si le réseau y est réellement servi :
    # inutile de dépenser du budget de rate-limit (et d'itérer la liste des
    # réseaux) pour Robinhood Chain, qui n'y est pas indexée.
    pool_batches = []
    if geckoterminal.resolve_network(chain):
        pool_batches.append(geckoterminal.get_new_pools(chain))
        if getattr(cfg, "USE_TRENDING_DISCOVERY", False):
            pool_batches.insert(0, geckoterminal.get_trending_pools(chain))

    for pools, token_index in pool_batches:
        for pool in pools:
            normalized = geckoterminal.normalize_pool(pool, token_index)
            if not normalized.get("contract"):
                continue
            if (normalized.get("liquidity") or 0) < prefilter:
                continue
            normalized["chain"] = chain
            candidates.append(normalized)

    # --- Source Nansen : Token Screener ---
    # Seule source de découverte à forte valeur qui couvre BNB Chain, Base ET
    # Robinhood Chain. Sur ces chaînes, GeckoTerminal « nouvelles pools » ne
    # remonte quasiment que des rugs ; le screener, lui, est ordonné par flux
    # net décroissant et filtré sur un minimum de traders.
    #
    # Le profil « quality » l'active via config.USE_NANSEN_DISCOVERY. Le profil
    # « degen » le coupe pour économiser des crédits — mais dès qu'une clé
    # Nansen est configurée, on l'utilise pour la découverte EVM quel que soit
    # le profil : sur un compte Pro le coût est négligeable (~1 appel groupé de
    # ~5 crédits par chaîne et par cycle, mis en cache 40 s), et c'est
    # précisément ce qui débloque BNB / Base / Robinhood. Cela ne change QUE le
    # vivier de candidats : chacun passe ensuite le préfiltre marché,
    # l'enrichissement GoPlus, le moteur de veto et le scoring à l'identique.
    # L'interrupteur runtime nansen.set_enabled(False) (bouton NANSEN de l'UI)
    # reste prioritaire — screen_tokens() renvoie [] s'il est coupé.
    _use_nansen_discovery = getattr(cfg, "USE_NANSEN_DISCOVERY", False) or (
        chain != "solana"
        and bool(cfg.API_KEYS.get("nansen"))
        and nansen.is_enabled()
    )
    if _use_nansen_discovery and nansen.screener_supports_chain(chain):
        for normalized in nansen.screen_tokens(chain):
            if not normalized.get("contract"):
                continue
            if (normalized.get("liquidity") or 0) < prefilter:
                continue
            normalized["chain"] = chain
            candidates.append(normalized)

    # --- Source secondaire : nouveaux tokens DexScreener ---
    # Remplace l'ancien `search_pairs(nom_de_la_chaîne)`, qui faisait une
    # recherche textuelle et ne renvoyait rien d'utilisable. L'appel est groupé :
    # une résolution par adresse tenait le scanner bloqué plusieurs minutes dès
    # que DexScreener ralentissait.
    addresses = dexscreener.discover_token_addresses(chain)
    for normalized in dexscreener.get_best_pairs_bulk(chain, addresses):
        if not normalized.get("contract"):
            continue
        if (normalized.get("liquidity") or 0) < prefilter:
            continue
        normalized["chain"] = chain
        candidates.append(normalized)

    # --- Dédoublonnage par contrat, en gardant la version la plus riche ---
    by_contract: dict[str, dict] = {}
    for c in candidates:
        addr = (c.get("contract") or "").lower()
        if not addr:
            continue
        existing = by_contract.get(addr)
        if existing is None:
            by_contract[addr] = c
        else:
            # On fusionne : GeckoTerminal a l'âge de la pool, DexScreener a
            # souvent des compteurs d'achats/ventes plus complets.
            merged = {**existing}
            for key, value in c.items():
                if merged.get(key) in (None, 0, "") and value not in (None, ""):
                    merged[key] = value
            by_contract[addr] = merged

    unique = list(by_contract.values())
    logger.info(f"[{chain}] {len(unique)} candidat(s) présélectionné(s).")
    return unique


def enrich_batch(chain: str, candidates: list[dict], should_stop=None) -> list[dict]:
    """
    Enrichit tous les candidats d'un cycle en un seul appel GoPlus groupé,
    pour ne pas déclencher le rate limit sur les cycles chargés.

    `should_stop` : callable optionnel renvoyant True si un STOP a été demandé.
    L'enrichissement EVM est un unique appel groupé (rapide), on se contente
    donc d'un test juste avant de le lancer.
    """
    if not candidates:
        return []

    if should_stop is not None and should_stop():
        return candidates

    addresses = [c["contract"] for c in candidates if c.get("contract")]
    bulk_results = goplus.check_evm_tokens_bulk(chain, addresses)

    for candidate in candidates:
        addr = (candidate.get("contract") or "").lower()
        # dex_paid n'est PAS vérifié ici : c'est un appel par candidat, sans
        # endpoint groupé chez DexScreener, soit jusqu'à 40 requêtes par cycle
        # et par chaîne pour un critère qui ne concerne que la poignée de
        # candidats en passe de devenir un signal. La vérification est faite
        # juste avant la promotion, dans core/scanner.py.
        raw = bulk_results.get(addr)
        candidate["security"] = goplus.normalize_security(chain, raw)
        if raw is None:
            candidate["security"]["unavailable_reason"] = t(
                "unavailable.goplus_not_indexed"
            )
    return candidates


def enrich_with_security(chain: str, candidate: dict) -> dict:
    """Version unitaire, pour une vérification à la demande depuis l'UI."""
    candidate["dex_paid"] = dexscreener.check_dex_paid(chain, candidate["contract"])
    raw = goplus.check_token(chain, candidate["contract"])  # noqa: E501
    candidate["security"] = goplus.normalize_security(chain, raw)
    return candidate
