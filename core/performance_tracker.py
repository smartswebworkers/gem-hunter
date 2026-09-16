"""
core/performance_tracker.py — Suivi réel de la performance des signaux
Destination finale : gem_hunter/core/performance_tracker.py

BIAIS DE SURVIE CORRIGÉ — LE BOT N'APPRENAIT QUE SUR LES SURVIVANTS.

Ancien comportement, en une ligne de code :

    current_price = _fetch_current_price(chain, contract)
    if current_price is None:
        continue   # « on laisse en attente, sera retenté au prochain cycle »

Or un token qui a été rug pull n'a plus de paire indexée : la liquidité a été
retirée, DexScreener ne renvoie plus rien, et _fetch_current_price renvoie
None. Ces signaux restaient donc éternellement « en attente » et n'entraient
JAMAIS dans les données d'apprentissage. Conséquence directe : le seul
échantillon vu par core/self_tuning.py était celui des tokens qui avaient
survécu. Le bot mesurait un taux de réussite calculé uniquement sur ses
succès, et réajustait ses poids en ne voyant jamais les caractéristiques
communes à ses pires décisions. Le pire résultat possible du bot était donc,
pour son propre apprentissage, un résultat neutre.

Un token dont le pool a disparu, ou dont la liquidité s'est effondrée, est
maintenant enregistré pour ce qu'il est : une perte totale. C'est ce qui donne
enfin à l'auto-correction de quoi apprendre à éviter les rugs.
"""
import logging

from data_sources import dexscreener
from storage import db
import config as cfg

logger = logging.getLogger("gem_hunter.performance")

# Chaînes pour lesquelles DexScreener fournit un prix exploitable. Robinhood
# Chain s'y ajoute automatiquement dès qu'elle est indexée.
_BASE_PRICEABLE_CHAINS = {"solana", "bsc", "ethereum"}


def _is_priceable(chain: str) -> bool:
    if chain in _BASE_PRICEABLE_CHAINS:
        return True
    return dexscreener.resolve_chain_id(chain) is not None


def _fetch_market_states(chain: str, contracts: list[str]) -> dict[str, dict]:
    """
    État de marché de PLUSIEURS tokens en un minimum d'appels.

    CORRECTIF DE PERFORMANCE : ce module interrogeait DexScreener une fois par
    signal en attente. Sur une base qui en contient une vingtaine, cela faisait
    vingt requêtes séquentielles à chaque cycle, en plus de celles de la
    découverte — de quoi déclencher les timeouts en cascade visibles au
    démarrage. L'endpoint groupé traite 30 adresses par appel.

    Renvoie {contrat_minuscule: {price, liquidity, pair_found}}. Une adresse
    absente du résultat n'a pas pu être interrogée (incident réseau) et ne doit
    surtout pas être confondue avec « aucune paire trouvée », qui elle signale
    une liquidité retirée.
    """
    if not _is_priceable(chain) or not contracts:
        return {}

    bulk = dexscreener.get_pairs_for_tokens_bulk(chain, contracts)
    states: dict[str, dict] = {}
    for address, pairs in bulk.items():
        if not pairs:
            states[address] = {"price": None, "liquidity": 0.0, "pair_found": False}
            continue
        best = max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)
        normalized = dexscreener.normalize_pair(best)
        states[address] = {
            "price": normalized.get("price_usd"),
            "liquidity": normalized.get("liquidity") or 0.0,
            "market_cap": normalized.get("market_cap"),
            "pair_found": True,
        }
    return states


def _detect_rug(signal: dict, state: dict) -> str | None:
    """
    Renvoie le motif du rug si le token est mort, sinon None.

    Deux formes de mort à distinguer d'un simple repli de marché :
      - la paire a totalement disparu des indexeurs (liquidité retirée),
      - la liquidité subsiste mais s'est effondrée sous un plancher absolu, ou
        a perdu l'essentiel de sa valeur d'origine.
    """
    if not state.get("pair_found"):
        return "paire introuvable — liquidité retirée"

    liquidity = state.get("liquidity") or 0.0
    if liquidity < cfg.RUG_LIQUIDITY_FLOOR_USD:
        return f"liquidité résiduelle (${liquidity:,.0f})"

    initial_liquidity = signal.get("liquidity") or 0
    if initial_liquidity > 0:
        drop_pct = (1 - liquidity / initial_liquidity) * 100
        if drop_pct >= cfg.RUG_LIQUIDITY_COLLAPSE_PCT:
            return f"liquidité effondrée de {drop_pct:.0f}%"

    return None


def run_check_cycle():
    """
    Vérifie tous les signaux ayant atteint un horizon (1h/6h/24h) sans avoir
    encore été évalués pour cet horizon, et enregistre leur rendement — y
    compris, désormais, les pertes totales.
    """
    for horizon, seconds in cfg.PERFORMANCE_HORIZONS.items():
        pending = db.get_signals_pending_check(horizon, seconds)
        if not pending:
            continue

        # On regroupe par chaîne pour n'émettre qu'une poignée d'appels au lieu
        # d'un par signal.
        by_chain: dict[str, list[str]] = {}
        for signal in pending:
            chain, contract = signal.get("chain"), signal.get("contract")
            if chain and contract:
                by_chain.setdefault(chain, []).append(contract)

        states_by_chain = {
            chain: _fetch_market_states(chain, contracts)
            for chain, contracts in by_chain.items()
        }

        for signal in pending:
            chain = signal.get("chain")
            contract = signal.get("contract")
            entry_price = signal.get("entry_price")
            if not entry_price or entry_price <= 0:
                continue

            state = (states_by_chain.get(chain) or {}).get((contract or "").lower())
            if state is None:
                # Chaîne non interrogeable ou appel réseau échoué : là, et
                # seulement là, il est légitime de réessayer plus tard.
                continue

            rug_reason = _detect_rug(signal, state)
            if rug_reason:
                db.record_performance_check(signal["id"], horizon, entry_price, 0.0, -100.0)
                logger.warning(
                    f"Performance {horizon} — {signal.get('ticker', '?')} : PERTE TOTALE "
                    f"({rug_reason}). Enregistré à -100% pour l'apprentissage."
                )
                continue

            current_price = state.get("price")
            if current_price is None:
                continue

            return_pct = round(((current_price - entry_price) / entry_price) * 100, 2)
            db.record_performance_check(signal["id"], horizon, entry_price, current_price, return_pct)
            logger.info(
                f"Performance {horizon} — {signal.get('ticker', '?')} : "
                f"{return_pct:+.2f}% (entrée ${entry_price} -> ${current_price})"
            )


def run_peak_cycle():
    """
    Relève la capitalisation courante des signaux encore actifs et met à jour
    leur pic (meilleur rendement atteint depuis la détection). Appelé une fois
    par cycle de scan, juste après run_check_cycle().

    C'est ce suivi continu — absent des horizons fixes 1h/6h/24h — qui permet au
    recap narratif d'écrire « peak cap $X atteint le ... , +Y% ». Sans lui, on
    ne connaîtrait que trois points dans le temps et jamais le sommet réel.
    """
    if not getattr(cfg, "RECAP_PEAK_TRACKING", True):
        return

    max_age = getattr(cfg, "RECAP_PEAK_MAX_AGE_SECONDS", 7 * 86400)
    signals = db.get_signals_for_peak_tracking(max_age)
    if not signals:
        return

    by_chain: dict[str, list[str]] = {}
    for signal in signals:
        chain, contract = signal.get("chain"), signal.get("contract")
        if chain and contract:
            by_chain.setdefault(chain, []).append(contract)

    states_by_chain = {
        chain: _fetch_market_states(chain, contracts)
        for chain, contracts in by_chain.items()
    }

    for signal in signals:
        chain = signal.get("chain")
        contract = (signal.get("contract") or "").lower()
        entry_price = signal.get("entry_price")
        if not entry_price or entry_price <= 0:
            continue

        state = (states_by_chain.get(chain) or {}).get(contract)
        if state is None or not state.get("pair_found"):
            continue

        current_price = state.get("price")
        if current_price is None or current_price <= 0:
            continue

        return_pct = round(((current_price - entry_price) / entry_price) * 100, 2)
        market_cap = state.get("market_cap")
        if market_cap is None:
            entry_cap = signal.get("market_cap") or 0
            market_cap = round(entry_cap * (1 + return_pct / 100), 2) if entry_cap else None

        db.update_peak(signal["id"], current_price, market_cap, return_pct)


def get_all_stats() -> dict:
    """Stats {1h, 6h, 24h} au format consommé par le dashboard."""
    stats = {horizon: db.get_performance_stats(horizon) for horizon in cfg.PERFORMANCE_HORIZONS}
    stats["rug_rate"] = db.get_rug_stats()
    return stats
