"""
data_sources/nansen.py — Client Nansen API (Smart Money / Token God Mode)
Destination finale : gem_hunter/data_sources/nansen.py

Nécessite NANSEN_API_KEY dans .env. Sans clé, ce module renvoie
systématiquement des résultats vides et le scoring retombe sur le
comportement neutre existant (aucun crash).

Endpoint utilisé : POST /api/v1/tgm/holders, filtré sur les labels
Smart Money. Voir https://docs.nansen.ai/api/token-god-mode/holders

IMPORTANT — cache anti-gaspillage : un même token candidat reste visible
plusieurs cycles de suite (jusqu'à 15 min sur PumpPortal) tant qu'il n'a
pas encore généré de signal ou été rejeté. Sans cache, chaque cycle de
scan (toutes les 45s) relance un appel Nansen pour ce même token, même si
rien n'a changé — ce qui consomme des crédits Nansen inutilement. Le
résultat est donc mis en cache par (chaîne, contrat) pendant NANSEN_CACHE_TTL
secondes avant de renvoyer un nouvel appel.
"""
import logging
import time
from datetime import datetime, timezone

import config as cfg
from config import API_KEYS, NANSEN_CACHE_TTL_SECONDS
from data_sources.http_utils import safe_post_json

logger = logging.getLogger("gem_hunter.nansen")

BASE_URL = "https://api.nansen.ai/api/v1"

# Nansen nomme la BNB Smart Chain "bnb" (pas "bsc")
NANSEN_CHAIN_MAP = {
    "solana": "solana",
    "bsc": "bnb",
    "ethereum": "ethereum",
    "base": "base",
}

# Chaînes couvertes par le Token Screener Nansen (découverte). C'est une carte
# DISTINCTE de NANSEN_CHAIN_MAP à dessein : le screener couvre Robinhood Chain,
# alors que l'endpoint smart-money (/tgm/holders) ne la couvre pas. Garder les
# deux séparées permet de découvrir des tokens Robinhood via Nansen SANS que
# « smart money exigé » (core/scoring.py, gated sur supports_chain) ne bloque
# leur passage — il ne peut pas être satisfait sur une chaîne que /tgm/holders
# n'indexe pas.
NANSEN_SCREENER_CHAIN_MAP = {
    "solana": "solana",
    "bsc": "bnb",
    "ethereum": "ethereum",
    "base": "base",
    "robinhood": "robinhood",
}
_SCREENER_URL = f"{BASE_URL}/token-screener"

# Résultat du screener mis en cache quelques secondes par chaîne : le scanner
# peut réinterroger la même chaîne plusieurs fois dans un même cycle (sources
# multiples, déduplication), inutile de repayer 5 crédits à chaque fois. Un
# résultat vide n'est jamais mis en cache (on retente au cycle suivant).
_screener_cache: dict[str, tuple[float, list[dict]]] = {}

# Labels Smart Money reconnus par Nansen (traders/funds à forte conviction)
SMART_MONEY_LABELS = ["Smart Trader", "30D Smart Trader", "90D Smart Trader", "Fund"]

# Cache (chain, contract) -> (timestamp, holders_list)
_cache: dict[tuple[str, str], tuple[float, list]] = {}
_MAX_CACHE_ENTRIES = 5000  # sécurité anti-fuite mémoire sur une session longue

# Interrupteur runtime : permet de couper les appels Nansen (économie de
# crédits) sans retirer la clé API du .env. Activé par défaut si une clé
# est configurée.
_enabled = True


def supports_chain(chain: str | None) -> bool:
    """
    Nansen indexe-t-il cette chaîne ? (Robinhood Chain, par exemple, n'est pas
    couverte.) Sur une chaîne non couverte, « smart money exigé » ne peut pas
    être satisfait et ne doit donc pas bloquer le passage en SIGNAL.
    """
    return bool(chain) and chain in NANSEN_CHAIN_MAP


def screener_supports_chain(chain: str | None) -> bool:
    """Le Token Screener Nansen couvre-t-il cette chaîne ? (découverte)."""
    return bool(chain) and chain in NANSEN_SCREENER_CHAIN_MAP


def _sf(val):
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _iso_to_epoch_ms(value) -> float | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp() * 1000
    except (TypeError, ValueError):
        return None


def _normalize_screener_row(row: dict, chain: str) -> dict:
    """
    Convertit une ligne du Token Screener Nansen en dict candidat interne,
    aligné sur data_sources/geckoterminal.normalize_pool et dexscreener.
    Le screener est interrogé sur une fenêtre courte (NANSEN_SCREENER_TIMEFRAME,
    « 1h » par défaut) : `volume`, `price_change` et `netflow` portent donc sur
    cette fenêtre — `volume` est mappé sur volume_1h, la variation sur
    price_change_1h.
    """
    created_ms = _iso_to_epoch_ms(row.get("token_deployment_date"))
    if created_ms is None:
        age_hours = _sf(row.get("token_age_hours"))
        age_days = _sf(row.get("token_age_days"))
        age_s = (age_hours * 3600) if age_hours is not None else (
            age_days * 86400 if age_days is not None else None
        )
        if age_s is not None:
            created_ms = (time.time() - age_s) * 1000

    return {
        "contract": row.get("token_address"),
        "pool_address": None,
        "ticker": row.get("token_symbol"),
        "name": row.get("token_symbol"),
        "chain": chain,
        "market_cap": _sf(row.get("market_cap_usd")),
        "liquidity": _sf(row.get("liquidity")),
        "volume_1h": _sf(row.get("volume")),
        "volume_24h": None,
        "price_usd": _sf(row.get("price_usd")),
        "price_change_5m": None,
        "price_change_1h": _sf(row.get("price_change")),
        "price_change_24h": None,
        "buys_h1": int(row.get("nof_buys") or 0),
        "sells_h1": int(row.get("nof_sells") or 0),
        "pair_created_at": created_ms,
        "nansen_netflow": _sf(row.get("netflow")),
        "nansen_traders": row.get("nof_traders"),
        "source": "nansen",
    }


def screen_tokens(chain: str) -> list[dict]:
    """
    Découverte de tokens via le Token Screener Nansen (POST /token-screener,
    ~5 crédits par appel).

    C'est la seule source de la stack qui couvre réellement BNB Chain ET
    Robinhood Chain : GeckoTerminal et DexScreener n'indexent pas Robinhood, et
    le tier gratuit de GeckoTerminal répond 429 en rafale dès qu'on interroge
    plusieurs chaînes toutes les 45 s. Le screener renvoie en UN appel tout ce
    dont le préfiltre marché et le scoring ont besoin : adresse du token, âge,
    capitalisation, liquidité, volume, achats/ventes, variation de prix, flux
    net.

    Renvoie une liste de dicts candidats normalisés, ou [] si la chaîne n'est
    pas couverte, si Nansen est désactivé, si aucune clé n'est configurée, ou
    si l'appel échoue.
    """
    nansen_chain = NANSEN_SCREENER_CHAIN_MAP.get(chain)
    if not nansen_chain or not _enabled or not API_KEYS.get("nansen"):
        return []

    ttl = getattr(cfg, "NANSEN_SCREENER_CACHE_TTL_SECONDS", 40)
    now = time.time()
    cached = _screener_cache.get(chain)
    if cached and (now - cached[0]) < ttl:
        return cached[1]

    timeframe = getattr(cfg, "NANSEN_SCREENER_TIMEFRAME", "1h")
    per_page = int(getattr(cfg, "NANSEN_SCREENER_MAX_ROWS", 100))

    min_liq = cfg.chain_threshold("MIN_LIQUIDITY_USD", chain) or 0
    min_vol = cfg.chain_threshold("MIN_VOLUME_1H_USD", chain) or 0
    age_min_days = max(getattr(cfg, "MIN_PAIR_AGE_MINUTES", 0) / 1440, 0)
    # Fenêtre d'âge PAR CHAÎNE : sur BNB / Base / Robinhood le plafond du profil
    # (6 h en degen) est irréaliste — Nansen n'indexe quasi rien d'aussi jeune
    # sur ces chaînes. cfg.chain_max_pair_age_hours l'élargit à 96 h pour l'EVM
    # et laisse Solana intact. Le préfiltre marché applique la même fenêtre, les
    # candidats ne sont donc pas rejetés ensuite pour « paire trop ancienne ».
    age_max_days = cfg.chain_max_pair_age_hours(chain) / 24

    # Préfiltre volontairement large côté serveur (liquidité au quart du
    # plancher, comme evm_common._prefilter_liquidity) : le filtrage fin
    # appartient au moteur de veto, pas à la découverte.
    filters: dict = {
        "market_cap_usd": {"min": getattr(cfg, "MARKET_CAP_MIN", 0),
                           "max": getattr(cfg, "MARKET_CAP_MAX", 10 ** 12)},
        "liquidity": {"min": max(min_liq / 4, 0)},
        "token_age_days": {"min": age_min_days, "max": age_max_days},
        "nof_traders": {"min": int(getattr(cfg, "NANSEN_SCREENER_MIN_TRADERS", 8))},
        "include_stablecoins": False,
        "include_native_tokens": False,
    }
    if min_vol:
        filters["volume"] = {"min": min_vol}

    body = {
        "chains": [nansen_chain],
        "timeframe": timeframe,
        "filters": filters,
        "pagination": {"page": 1, "per_page": per_page},
        "order_by": [{"field": "netflow", "direction": "DESC"}],
    }
    headers = {
        "apiKey": API_KEYS.get("nansen"),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    timeout = int(getattr(cfg, "NANSEN_SCREENER_TIMEOUT", 30))
    data = safe_post_json(_SCREENER_URL, body, headers=headers, timeout=timeout)
    if not data:
        return []  # pas de mise en cache : on retente au prochain cycle

    rows = data.get("data") or []
    out = [
        _normalize_screener_row(r, chain)
        for r in rows
        if isinstance(r, dict) and r.get("token_address")
    ]
    _screener_cache[chain] = (now, out)
    return out


def set_enabled(enabled: bool):
    global _enabled
    _enabled = enabled


def is_enabled() -> bool:
    return _enabled


def _prune_cache():
    if len(_cache) <= _MAX_CACHE_ENTRIES:
        return
    now = time.time()
    expired = [k for k, (ts, _) in _cache.items() if now - ts > NANSEN_CACHE_TTL_SECONDS]
    for k in expired:
        del _cache[k]


def _post(path: str, body: dict) -> dict | None:
    api_key = API_KEYS.get("nansen")
    if not api_key:
        return None
    url = f"{BASE_URL}{path}"
    headers = {"apiKey": api_key, "Content-Type": "application/json", "Accept": "application/json"}
    return safe_post_json(url, body, headers=headers)


def get_smart_money_holders(chain: str, token_address: str) -> list[dict] | None:
    """
    Renvoie la liste des wallets Smart Money détenant ce token, avec leur
    pourcentage de détention et flux récents. Liste vide si aucune clé
    Nansen configurée, si l'appel échoue, ou si aucun smart money ne
    détient le token. Résultat mis en cache pendant NANSEN_CACHE_TTL_SECONDS
    pour éviter de re-consommer des crédits sur un token déjà vérifié
    récemment (voir note en tête de fichier).
    """
    nansen_chain = NANSEN_CHAIN_MAP.get(chain)
    if not nansen_chain or not _enabled:
        return None  # non interrogeable : « inconnu », pas « aucun smart money »

    cache_key = (chain, token_address)
    cached = _cache.get(cache_key)
    now = time.time()
    if cached and (now - cached[0]) < NANSEN_CACHE_TTL_SECONDS:
        return cached[1]

    body = {
        "chain": nansen_chain,
        "token_address": token_address,
        "aggregate_by_entity": False,
        "label_type": "all_holders",
        "filters": {"include_smart_money_labels": SMART_MONEY_LABELS},
        "pagination": {"page": 1, "per_page": 20},
    }
    data = _post("/tgm/holders", body)
    if data is None:
        # CORRECTIF : cette fonction renvoyait une liste vide aussi bien quand
        # l'appel échouait (réseau coupé, rate limit, clé invalide) que quand
        # Nansen répondait « aucun smart money ne détient ce token ». Comme une
        # clé configurée suffisait à marquer le signal « disponible », un
        # simple incident réseau se transformait en verdict « aucun smart money
        # détecté » — une information négative fabriquée à partir de rien. On
        # renvoie maintenant None, qui veut dire « on ne sait pas ».
        # Pas de mise en cache non plus, pour retenter au prochain cycle.
        return None

    holders = data.get("data", [])
    _cache[cache_key] = (now, holders)
    _prune_cache()
    return holders


def _short_addr(addr: str) -> str:
    if not addr or len(addr) <= 10:
        return addr or "?"
    return f"{addr[:4]}…{addr[-4:]}"


def compute_smart_money_signal(chain: str, token_address: str) -> dict:
    """
    Résume la présence de smart money en un signal exploitable par le scoring :
      - available: bool (clé Nansen configurée et appel réussi)
      - holder_count: nombre de wallets Smart Money détenteurs
      - combined_ownership_pct: somme de leurs % de détention
      - net_inflow_positive: bool (accumulation nette positive récente)
      - buyers: liste des wallets Smart Money en accumulation active (achat
        net sur les dernières 24h, balance_change_24h > 0), avec adresse
        raccourcie, label et % détenu — la vraie preuve de "smart money qui
        achète", pas juste "qui détient".
      - buyers_count: nombre de wallets dans `buyers`
    """
    holders = get_smart_money_holders(chain, token_address)
    api_key_configured = bool(API_KEYS.get("nansen"))

    if holders is None:
        # Appel impossible ou échoué : critère non mesurable, il sera écarté du
        # score par core/scoring.py au lieu de peser négativement.
        return {
            "available": False,
            "holder_count": 0,
            "combined_ownership_pct": 0.0,
            "net_inflow_positive": False,
            "buyers": [],
            "buyers_count": 0,
        }

    if not holders:
        return {
            "available": api_key_configured,  # clé présente et 0 smart money = résultat légitime
            "holder_count": 0,
            "combined_ownership_pct": 0.0,
            "net_inflow_positive": False,
            "buyers": [],
            "buyers_count": 0,
        }

    combined_pct = sum(h.get("ownership_percentage", 0) or 0 for h in holders)
    net_flow = sum(
        (h.get("total_inflow", 0) or 0) - (h.get("total_outflow", 0) or 0) for h in holders
    )

    buyers = sorted(
        (h for h in holders if (h.get("balance_change_24h") or 0) > 0),
        key=lambda h: h.get("ownership_percentage", 0) or 0,
        reverse=True,
    )
    buyer_summaries = [
        {
            "address": _short_addr(h.get("address", "")),
            "label": h.get("address_label") or "Smart Money",
            "ownership_pct": round(h.get("ownership_percentage", 0) or 0, 2),
        }
        for h in buyers[:5]
    ]

    return {
        "available": True,
        "holder_count": len(holders),
        "combined_ownership_pct": round(combined_pct, 2),
        "net_inflow_positive": net_flow > 0,
        "buyers": buyer_summaries,
        "buyers_count": len(buyers),
    }
