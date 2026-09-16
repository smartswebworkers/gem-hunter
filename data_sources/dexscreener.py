"""
data_sources/dexscreener.py — Client DexScreener (gratuit, sans clé API)
Destination finale : gem_hunter/data_sources/dexscreener.py

Docs : https://docs.dexscreener.com/api/reference

BUG CORRIGÉ — L'APPEL DE DÉCOUVERTE SECONDAIRE NE CHERCHAIT RIEN.
chains/bsc.py appelait `dexscreener.search_pairs("bsc")` et chains/ethereum.py
`search_pairs("ethereum")`. Or l'endpoint /latest/dex/search est une recherche
TEXTUELLE sur les noms et symboles de tokens, pas un filtre de chaîne : la
requête « bsc » renvoyait les tokens dont le nom contient « bsc », sur
n'importe quelle chaîne, puis le code les filtrait sur chain == "bsc" et n'en
gardait quasiment aucun. La source secondaire des deux chaînes EVM était donc
décorative.

Elle est remplacée par une découverte réelle, basée sur les flux de nouveaux
tokens de DexScreener (profils et boosts), filtrés par chaîne, puis résolus en
paires. On garde search_pairs() comme recherche textuelle, à son vrai usage.
"""
import time

from data_sources.http_utils import safe_get_json

BASE_URL = "https://api.dexscreener.com"

# Identifiants de chaîne utilisés par DexScreener. Les chaînes absentes sont
# résolues dynamiquement (voir resolve_chain_id), ce qui permet à Robinhood
# Chain de s'activer toute seule dès qu'elle est indexée.
CHAIN_ID_MAP = {
    "solana": "solana",
    "bsc": "bsc",
    "base": "base",
    "ethereum": "ethereum",
}

# Identifiants candidats testés pour les chaînes pas encore cartographiées.
CHAIN_ID_CANDIDATES = {
    "robinhood": ("robinhood", "robinhoodchain", "rhc", "robinhood-chain"),
}

# Token de référence servant à tester si une chaîne est réellement indexée
# (le wrapped natif est toujours la paire la plus liquide d'une chaîne).
_PROBE_QUERIES = {
    "robinhood": "WETH",
}

_resolved_chain_ids: dict[str, str | None] = {}


def resolve_chain_id(chain: str) -> str | None:
    """
    Identifiant DexScreener de la chaîne, ou None si elle n'est pas indexée.
    Pour les chaînes récentes, on teste les identifiants plausibles contre une
    vraie recherche plutôt que de supposer.
    """
    if chain in CHAIN_ID_MAP:
        return CHAIN_ID_MAP[chain]
    if chain in _resolved_chain_ids:
        return _resolved_chain_ids[chain]

    candidates = CHAIN_ID_CANDIDATES.get(chain, (chain,))
    query = _PROBE_QUERIES.get(chain, chain)
    pairs = search_pairs(query)
    found = None
    seen_chain_ids = {str(p.get("chainId", "")).lower() for p in pairs if isinstance(p, dict)}
    for candidate in candidates:
        if candidate.lower() in seen_chain_ids:
            found = candidate
            break

    _resolved_chain_ids[chain] = found
    return found


def search_pairs(query: str) -> list[dict]:
    """
    Recherche TEXTUELLE de paires, par nom ou symbole de token.
    À ne pas utiliser comme filtre de chaîne (voir l'en-tête du fichier).
    """
    url = f"{BASE_URL}/latest/dex/search"
    data = safe_get_json(url, {"q": query})
    return (data or {}).get("pairs", []) or []


def get_pairs_for_tokens_bulk(chain: str, token_addresses: list[str]) -> dict[str, list[dict]]:
    """
    Paires de PLUSIEURS tokens en un seul appel, via /tokens/v1/{chaîne}/{adresses}
    qui accepte jusqu'à 30 adresses séparées par des virgules.

    CORRECTIF DE PERFORMANCE. La découverte interrogeait DexScreener une fois
    par adresse : jusqu'à 40 appels par chaîne et par cycle, sur un cycle censé
    durer 45 secondes. Avec un timeout de 15s et des tentatives successives,
    une seule adresse lente suffisait à faire déborder tout le cycle, et 40
    adresses lentes bloquaient le scanner pendant plusieurs minutes — ce que
    montrait la rafale de ReadTimeoutError au démarrage. On passe de 40 appels
    à 2.

    Renvoie {adresse_en_minuscules: [paires]}.
    """
    dex_chain = resolve_chain_id(chain)
    if not dex_chain or not token_addresses:
        return {}

    results: dict[str, list[dict]] = {}
    batch_size = 30
    for i in range(0, len(token_addresses), batch_size):
        batch = token_addresses[i:i + batch_size]
        url = f"{BASE_URL}/tokens/v1/{dex_chain}/{','.join(batch)}"
        data = safe_get_json(url)
        if not isinstance(data, list):
            continue  # ce lot échoue, les autres peuvent réussir
        # Les adresses du lot qui ont bien été interrogées sont marquées, même
        # sans paire : « interrogé, aucune paire » et « pas interrogé » sont
        # deux choses différentes. La première signale une liquidité retirée
        # (voir core/performance_tracker.py), la seconde un incident réseau.
        for address in batch:
            results.setdefault(address.lower(), [])
        for pair in data:
            if not isinstance(pair, dict):
                continue
            address = ((pair.get("baseToken") or {}).get("address") or "").lower()
            if address:
                results.setdefault(address, []).append(pair)
    return results


def get_best_pairs_bulk(chain: str, token_addresses: list[str]) -> list[dict]:
    """
    Paire la plus liquide de chaque token, normalisée, en un minimum d'appels.
    C'est la fonction que doivent utiliser les modules de découverte.
    """
    bulk = get_pairs_for_tokens_bulk(chain, token_addresses)
    out = []
    for pairs in bulk.values():
        if not pairs:
            continue
        best = max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)
        out.append(normalize_pair(best))
    return out


def get_pairs_for_token(chain: str, token_address: str) -> list[dict]:
    """Toutes les paires DEX d'un contrat de token donné."""
    dex_chain = resolve_chain_id(chain)
    if not dex_chain:
        return []
    url = f"{BASE_URL}/token-pairs/v1/{dex_chain}/{token_address}"
    data = safe_get_json(url)
    return data if isinstance(data, list) else []


def get_latest_token_profiles() -> list[dict]:
    """Flux des derniers tokens listés, toutes chaînes confondues."""
    url = f"{BASE_URL}/token-profiles/latest/v1"
    data = safe_get_json(url)
    return data if isinstance(data, list) else []


def get_latest_boosted_tokens() -> list[dict]:
    """
    Flux des tokens récemment boostés. Complète les profils : un projet qui
    paie un boost est visible avant d'avoir un profil complet, ce qui en fait
    une source de fraîcheur utile — sans rien dire de sa qualité, que le
    moteur de sécurité tranchera de toute façon.
    """
    url = f"{BASE_URL}/token-boosts/latest/v1"
    data = safe_get_json(url)
    return data if isinstance(data, list) else []


def discover_token_addresses(chain: str, limit: int = 40) -> list[str]:
    """
    Adresses de tokens récemment apparus sur une chaîne donnée, dédoublonnées.
    Remplace l'ancien `search_pairs(nom_de_la_chaîne)`, qui ne renvoyait rien
    d'exploitable.
    """
    dex_chain = resolve_chain_id(chain)
    if not dex_chain:
        return []

    addresses: list[str] = []
    seen: set[str] = set()
    for entry in list(get_latest_token_profiles()) + list(get_latest_boosted_tokens()):
        if not isinstance(entry, dict):
            continue
        if str(entry.get("chainId", "")).lower() != dex_chain.lower():
            continue
        address = entry.get("tokenAddress")
        if address and address not in seen:
            seen.add(address)
            addresses.append(address)
        if len(addresses) >= limit:
            break
    return addresses


_dex_paid_cache: dict[tuple[str, str], tuple[float, bool]] = {}
_DEX_PAID_CACHE_TTL = 300  # 5 min : un token « pas encore payé » le reste rarement longtemps


def check_dex_paid(chain: str, token_address: str) -> bool | None:
    """
    Vérifie si le token a payé pour les « Enhanced Token Info » (badge DEX
    Paid) via l'endpoint officiel /orders/v1/. Renvoie True si un ordre de
    type « tokenProfile » est approuvé, False sinon, None si la vérification
    échoue (réseau, rate limit) — à ne jamais traiter comme « payé » par défaut.
    """
    dex_chain = resolve_chain_id(chain)
    if not dex_chain:
        return None

    cache_key = (chain, token_address)
    cached = _dex_paid_cache.get(cache_key)
    now = time.time()
    if cached and (now - cached[0]) < _DEX_PAID_CACHE_TTL:
        return cached[1]

    url = f"{BASE_URL}/orders/v1/{dex_chain}/{token_address}"
    data = safe_get_json(url)
    if data is None:
        return None  # échec réseau : ne pas mettre en cache, ne pas bloquer dessus

    paid = any(
        isinstance(o, dict) and o.get("type") == "tokenProfile" and o.get("status") == "approved"
        for o in data
    ) if isinstance(data, list) else False

    _dex_paid_cache[cache_key] = (now, paid)
    return paid


WSOL_MINT = "So11111111111111111111111111111111111111112"
_sol_price_cache = {"value": None, "ts": 0.0}


def get_sol_price_usd() -> float | None:
    """
    Prix réel du SOL en USD, via la paire wSOL la plus liquide sur DexScreener.
    Mis en cache 60 secondes pour éviter des appels réseau répétés.
    """
    now = time.time()
    if _sol_price_cache["value"] is not None and now - _sol_price_cache["ts"] < 60:
        return _sol_price_cache["value"]

    pairs = get_pairs_for_token("solana", WSOL_MINT)
    if not pairs:
        return _sol_price_cache["value"]  # ancienne valeur en cache plutôt que None

    best = max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)
    price = normalize_pair(best).get("price_usd")
    if price:
        _sol_price_cache["value"] = price
        _sol_price_cache["ts"] = now
    return price


def get_best_pair(chain: str, token_address: str) -> dict | None:
    """Paire la plus liquide d'un token, normalisée. None si aucune paire."""
    pairs = get_pairs_for_token(chain, token_address)
    if not pairs:
        return None
    best = max(pairs, key=lambda p: (p.get("liquidity") or {}).get("usd") or 0)
    return normalize_pair(best)


def _safe_float(val):
    """L'API renvoie parfois ces champs en chaîne ou en None."""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def normalize_pair(pair: dict) -> dict:
    """Convertit une réponse DexScreener en dict interne standardisé."""
    base = pair.get("baseToken", {}) or {}
    liquidity = pair.get("liquidity", {}) or {}
    price_change = pair.get("priceChange", {}) or {}
    txns_h1 = (pair.get("txns", {}) or {}).get("h1", {}) or {}
    buys = txns_h1.get("buys", 0) or 0
    sells = txns_h1.get("sells", 0) or 0

    volume = pair.get("volume") or {}
    return {
        "chain": pair.get("chainId"),
        "contract": base.get("address"),
        "pool_address": pair.get("pairAddress"),
        "ticker": base.get("symbol"),
        "name": base.get("name"),
        "market_cap": _safe_float(pair.get("marketCap")) or _safe_float(pair.get("fdv")),
        "liquidity": _safe_float(liquidity.get("usd")),
        "volume_24h": _safe_float(volume.get("h24")),
        "volume_1h": _safe_float(volume.get("h1")),
        "price_usd": _safe_float(pair.get("priceUsd")),
        "price_change_24h": _safe_float(price_change.get("h24")),
        "price_change_6h": _safe_float(price_change.get("h6")),
        "price_change_5m": _safe_float(price_change.get("m5")),
        "price_change_1h": _safe_float(price_change.get("h1")),
        "buys_h1": buys,
        "sells_h1": sells,
        "pair_created_at": pair.get("pairCreatedAt"),
        "dex_url": pair.get("url"),
        "source": "dexscreener",
    }
