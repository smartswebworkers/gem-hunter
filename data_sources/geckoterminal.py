"""
data_sources/geckoterminal.py — Client GeckoTerminal (gratuit, sans clé API)
Destination finale : gem_hunter/data_sources/geckoterminal.py

Docs : https://api.geckoterminal.com/docs/index.html

BUG MAJEUR CORRIGÉ — LA SOURCE PRIMAIRE BNB CHAIN ÉTAIT ENTIÈREMENT CASSÉE.
normalize_pool() renvoyait `attributes.address`, c'est-à-dire l'adresse du
POOL, dans le champ `contract`. Or tout le reste du bot traite `contract`
comme l'adresse du TOKEN : c'est cette valeur qui était envoyée à GoPlus, à
Nansen et à DexScreener. GoPlus, interrogé sur une adresse de pool, ne renvoie
évidemment rien, donc `data_available` valait False, donc chaque candidat BNB
Chain issu de GeckoTerminal — la source primaire de cette chaîne — était
rejeté pour « données de sécurité indisponibles ». Le bot ne scannait pas
vraiment BNB Chain, il rejetait BNB Chain en boucle.

L'adresse du token se trouve dans relationships.base_token.data.id, sous la
forme "<réseau>_<adresse>". Elle est maintenant extraite correctement, et
l'appel demande explicitement les tokens inclus pour récupérer aussi le
symbole et le nom.

Autres correctifs de cette révision :
  - le pool renvoyait uniquement volume_24h : ni volume 1h, ni variations de
    prix, ni compte d'achats/ventes. Toutes les features de momentum et de
    pression acheteuse valaient donc leur valeur par défaut sur chaque
    candidat BNB Chain. Ces champs sont désormais remplis.
  - NETWORK_MAP ne contenait ni ethereum ni robinhood : get_new_pools() y
    renvoyait systématiquement une liste vide. Le mapping est maintenant
    résolu dynamiquement contre la liste réelle des réseaux de l'API.
"""
import logging
import time
from datetime import datetime, timezone

from data_sources.http_utils import safe_get_json

logger = logging.getLogger("gem_hunter.geckoterminal")

BASE_URL = "https://api.geckoterminal.com/api/v2"

# Correspondances connues. Les réseaux absents d'ici sont résolus
# dynamiquement par resolve_network() contre /networks.
NETWORK_MAP = {
    "solana": "solana",
    "bsc": "bsc",
    "base": "base",
    "ethereum": "eth",
}

# Termes utilisés pour reconnaître un réseau dont on ne connaît pas encore
# l'identifiant exact chez GeckoTerminal (voir chains/robinhood.py).
NETWORK_SEARCH_HINTS = {
    "robinhood": ("robinhood", "rhc", "robinhood-chain"),
    "arc": ("arc", "circle-arc", "arc-network"),
}

_GT_HEADERS = {"Accept": "application/json;version=20230302"}

_networks_cache: list[dict] | None = None
_resolved_networks: dict[str, str | None] = {}

# GeckoTerminal (tier gratuit) plafonne à ~30 requêtes/minute. Le scanner
# interroge new_pools + trending_pools pour 3 chaînes toutes les 45 s, ce qui
# suffit à faire répondre 429 en rafale puis à armer le disjoncteur — la
# découverte « trending » du profil quality tombe alors à zéro (visible dans les
# journaux : « Trending: 0 »). Ces listes ne bougent pas d'un cycle à l'autre :
# on les met en cache 90 s. Un résultat vide n'est jamais mis en cache (on
# retente au cycle suivant).
_POOLS_CACHE_TTL_SECONDS = 90
_pools_cache: dict[tuple[str, str], tuple[float, tuple[list, dict]]] = {}


def _fetch_pools(chain: str, kind: str, params: dict) -> tuple[list[dict], dict]:
    cache_key = (kind, chain)
    cached = _pools_cache.get(cache_key)
    now = time.time()
    if cached and (now - cached[0]) < _POOLS_CACHE_TTL_SECONDS:
        return cached[1]

    network = resolve_network(chain)
    if not network:
        return [], {}
    url = f"{BASE_URL}/networks/{network}/{kind}"
    data = safe_get_json(url, params, headers=_GT_HEADERS)
    if not data:
        return [], {}
    result = ((data.get("data") or []), _index_included(data.get("included") or []))
    if result[0]:
        _pools_cache[cache_key] = (now, result)
    return result


def get_networks() -> list[dict]:
    """
    Liste des réseaux réellement servis par GeckoTerminal, récupérée une fois
    par session (deux pages suffisent largement, l'API en pagine environ 50 par
    page). Sert à l'auto-détection des chaînes récentes.
    """
    global _networks_cache
    if _networks_cache is not None:
        return _networks_cache

    networks = []
    for page in (1, 2, 3):
        data = safe_get_json(f"{BASE_URL}/networks", {"page": page}, headers=_GT_HEADERS)
        items = (data or {}).get("data") or []
        if not items:
            break
        networks.extend(items)
    if networks:
        _networks_cache = networks
    return networks


def resolve_network(chain: str) -> str | None:
    """
    Identifiant GeckoTerminal du réseau, ou None si l'API ne le sert pas.
    Les correspondances connues sont renvoyées directement ; les autres sont
    cherchées dans la liste réelle des réseaux, par identifiant et par nom.
    """
    if chain in NETWORK_MAP:
        return NETWORK_MAP[chain]
    if chain in _resolved_networks:
        return _resolved_networks[chain]

    hints = NETWORK_SEARCH_HINTS.get(chain, (chain,))
    resolved = None
    for net in get_networks():
        net_id = str(net.get("id", "")).lower()
        net_name = str((net.get("attributes") or {}).get("name", "")).lower()
        if any(h in net_id or h in net_name for h in hints):
            resolved = net.get("id")
            break

    _resolved_networks[chain] = resolved
    if resolved:
        logger.info(f"GeckoTerminal : réseau « {chain} » résolu en « {resolved} ».")
    return resolved


def get_new_pools(chain: str) -> tuple[list[dict], dict]:
    """
    Nouvelles pools du réseau. Renvoie (pools, index_des_tokens_inclus).

    L'index permet de retrouver le symbole et le nom du token de base sans un
    appel supplémentaire par pool. Résultat mis en cache 90 s (voir _fetch_pools).
    """
    return _fetch_pools(chain, "new_pools", {"include": "base_token"})


def get_trending_pools(chain: str) -> tuple[list[dict], dict]:
    """
    Pools qui GAGNENT de la traction sur le réseau (volume + acheteurs en
    hausse). C'est la source de découverte du profil « quality » : au lieu de
    « les pools les plus récentes » (mine de rugs), on part de « les pools que
    le marché est en train de remarquer », puis on filtre sur l'âge, la
    capitalisation, la distribution et la sécurité.

    Renvoie (pools, index_des_tokens_inclus), même forme que get_new_pools.
    Résultat mis en cache 90 s (voir _fetch_pools) : sans ça, GeckoTerminal
    répond 429 et cette source de découverte disparaît complètement.
    """
    return _fetch_pools(chain, "trending_pools",
                        {"include": "base_token", "duration": "24h"})


def get_pool_info(chain: str, pool_address: str) -> dict | None:
    network = resolve_network(chain)
    if not network:
        return None
    url = f"{BASE_URL}/networks/{network}/pools/{pool_address}"
    data = safe_get_json(url, {"include": "base_token"}, headers=_GT_HEADERS)
    return (data or {}).get("data")


def _index_included(included: list) -> dict:
    """{id_du_token: {symbol, name, address}} à partir du bloc `included`."""
    index = {}
    for item in included:
        if not isinstance(item, dict) or item.get("type") != "token":
            continue
        attrs = item.get("attributes") or {}
        index[item.get("id")] = {
            "symbol": attrs.get("symbol"),
            "name": attrs.get("name"),
            "address": attrs.get("address"),
        }
    return index


def _safe_float(val):
    """L'API GeckoTerminal renvoie souvent ses nombres sous forme de chaîne."""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _parse_iso_to_epoch_ms(value) -> float | None:
    """
    GeckoTerminal renvoie pool_created_at en ISO-8601 (« 2024-01-01T00:00:00Z »),
    pas en epoch millisecondes.
    """
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp() * 1000
    except (TypeError, ValueError):
        return None


def _token_address_from_relationship(pool: dict) -> tuple[str | None, str | None]:
    """
    Extrait (identifiant_du_token, adresse_du_token) depuis
    relationships.base_token.data.id, de la forme « bsc_0xabc... ».
    C'est le correctif central de ce fichier.
    """
    try:
        token_id = pool["relationships"]["base_token"]["data"]["id"]
    except (KeyError, TypeError):
        return None, None
    if not isinstance(token_id, str) or "_" not in token_id:
        return token_id, token_id
    _network, _, address = token_id.partition("_")
    return token_id, address or None


def _symbol_from_pool_name(name: str | None) -> str | None:
    """Repli : le nom d'une pool a la forme « TOKEN / WBNB 0.3% »."""
    if not name:
        return None
    head = str(name).split("/")[0].strip()
    return head or None


def normalize_pool(pool: dict, token_index: dict | None = None) -> dict:
    """
    Convertit une pool GeckoTerminal en dict interne standardisé, aligné sur
    celui produit par data_sources/dexscreener.py.
    """
    attrs = pool.get("attributes", {}) or {}
    token_index = token_index or {}

    token_id, token_address = _token_address_from_relationship(pool)
    token_info = token_index.get(token_id) or {}

    price_change = attrs.get("price_change_percentage") or {}
    volume = attrs.get("volume_usd") or {}
    txns_h1 = (attrs.get("transactions") or {}).get("h1") or {}

    return {
        # `contract` est bien l'adresse du TOKEN, pas celle du pool.
        "contract": token_info.get("address") or token_address,
        "pool_address": attrs.get("address"),
        "ticker": token_info.get("symbol") or _symbol_from_pool_name(attrs.get("name")),
        "name": token_info.get("name") or attrs.get("name"),
        "market_cap": _safe_float(attrs.get("market_cap_usd")) or _safe_float(attrs.get("fdv_usd")),
        "liquidity": _safe_float(attrs.get("reserve_in_usd")),
        "volume_24h": _safe_float(volume.get("h24")),
        "volume_1h": _safe_float(volume.get("h1")),
        "price_usd": _safe_float(attrs.get("base_token_price_usd")),
        "price_change_5m": _safe_float(price_change.get("m5")),
        "price_change_1h": _safe_float(price_change.get("h1")),
        "price_change_6h": _safe_float(price_change.get("h6")),
        "price_change_24h": _safe_float(price_change.get("h24")),
        "buys_h1": int(txns_h1.get("buys") or 0),
        "sells_h1": int(txns_h1.get("sells") or 0),
        "pair_created_at": _parse_iso_to_epoch_ms(attrs.get("pool_created_at")),
        "source": "geckoterminal",
    }
