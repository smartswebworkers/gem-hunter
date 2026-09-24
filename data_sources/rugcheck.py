"""
data_sources/rugcheck.py — Second avis anti-rug sur Solana (NOUVEAU MODULE)
Destination finale : gem_hunter/data_sources/rugcheck.py

Pourquoi ce module existe.
Sur Solana, GoPlus ne fait AUCUNE simulation d'achat/vente : son champ
honeypot est structurellement à None. Le bot n'avait donc, sur sa chaîne
prioritaire, aucune détection de piège à la vente — et c'est précisément la
forme la plus courante de rug pull sur cette chaîne. RugCheck comble ce trou :
il analyse le mint, les autorités, la répartition de l'offre, le verrouillage
de la liquidité et les réseaux de wallets liés au créateur (les fameux
« insiders » qui achètent au bloc zéro pour revendre sur les acheteurs
suivants).

Endpoint public, sans clé pour le rapport de base :
    GET https://api.rugcheck.xyz/v1/tokens/{mint}/report
    GET https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary

Le module est volontairement tolérant à la panne : si l'API est injoignable ou
change de format, il renvoie {"available": False}. En mode strict, un candidat
Solana sans second avis n'est pas pour autant accepté à l'aveugle — il reste
soumis aux vérifications GoPlus et RPC obligatoires (voir
core/security_checks.py). L'indisponibilité de RugCheck dégrade la profondeur
d'analyse, elle n'ouvre jamais une porte.
"""
import logging
import time

from config import RUGCHECK_CACHE_TTL_SECONDS, RUGCHECK_CRITICAL_LEVELS
from data_sources.http_utils import safe_get_json

logger = logging.getLogger("gem_hunter.rugcheck")

BASE_URL = "https://api.rugcheck.xyz/v1"

# Motifs de risque qu'on traite comme rédhibitoires quel que soit le niveau
# annoncé par l'API. Ce sont des mécanismes de rug, pas des « points
# d'attention » : les laisser passer parce que RugCheck les classe en warning
# reviendrait à refaire l'erreur de l'ancien moteur de sécurité.
ALWAYS_CRITICAL_PATTERNS = (
    "mint authority",
    "freeze authority",
    "honeypot",
    "cannot sell",
    "transfer fee",
    "transfer hook",
    "mutable metadata",
    "copycat",
    "rugged",
    "creator history",
    "single holder ownership",
    "high ownership",
    "low liquidity",
    "liquidity unlocked",
)

_cache: dict[str, tuple[float, dict]] = {}
_MAX_CACHE_ENTRIES = 2000
_enabled = True


def set_enabled(enabled: bool):
    global _enabled
    _enabled = enabled


def is_enabled() -> bool:
    return _enabled


def _prune_cache():
    if len(_cache) <= _MAX_CACHE_ENTRIES:
        return
    now = time.time()
    for key in [k for k, (ts, _) in _cache.items() if now - ts > RUGCHECK_CACHE_TTL_SECONDS]:
        del _cache[key]


def _to_float(val):
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def get_report(mint: str) -> dict:
    """
    Rapport RugCheck normalisé pour un mint Solana.

    Retourne toujours un dict :
      {
        "available": bool,
        "score": int | None,             # échelle RugCheck, monte avec le danger
        "risks": [str],                  # tous les risques signalés
        "critical_risks": [str],         # ceux qui justifient un veto
        "lp_locked_pct": float | None,
        "top_holder_pct": float | None,
        "insider_pct": float | None,     # part détenue par les wallets liés au créateur
        "rugged": bool | None,           # le token a DÉJÀ été rug
      }
    """
    empty = {
        "available": False, "score": None, "risks": [], "critical_risks": [],
        "lp_locked_pct": None, "top_holder_pct": None, "insider_pct": None, "rugged": None,
        "holders": [],
    }
    if not mint or not _enabled:
        return empty

    cached = _cache.get(mint)
    now = time.time()
    if cached and (now - cached[0]) < RUGCHECK_CACHE_TTL_SECONDS:
        return cached[1]

    data = safe_get_json(f"{BASE_URL}/tokens/{mint}/report")
    if not isinstance(data, dict):
        # Pas de mise en cache d'un échec : on retentera au cycle suivant
        # plutôt que de figer une indisponibilité transitoire.
        return empty

    report = _normalize(data)
    _cache[mint] = (now, report)
    _prune_cache()
    return report


def _normalize(data: dict) -> dict:
    risks_raw = data.get("risks") or []
    risks: list[str] = []
    critical: list[str] = []

    for r in risks_raw:
        if not isinstance(r, dict):
            continue
        name = (r.get("name") or "").strip()
        description = (r.get("description") or "").strip()
        level = (r.get("level") or "").strip().lower()
        label = f"{name} — {description}" if description else name
        if not label:
            continue
        risks.append(label)

        haystack = f"{name} {description}".lower()
        if level in RUGCHECK_CRITICAL_LEVELS:
            critical.append(label)
        elif any(pattern in haystack for pattern in ALWAYS_CRITICAL_PATTERNS):
            critical.append(label)

    rugged = data.get("rugged")
    if rugged is True:
        critical.append("Token déjà identifié comme rug pull par RugCheck.")

    return {
        "available": True,
        "score": _to_float(data.get("score_normalised") if data.get("score_normalised") is not None
                           else data.get("score")),
        "risks": risks,
        "critical_risks": critical,
        "lp_locked_pct": _extract_lp_locked(data),
        "top_holder_pct": _extract_top_holder(data),
        # Porteurs bruts (propriétaire, %), pour qu'un appelant qui connaît la
        # bonding curve puisse la retirer du calcul : top_holder_pct ci-dessus la
        # COMPTE (vérifié sur des rapports réels : la curve y figure à 62-98 %).
        "holders": _extract_holders(data),
        "insider_pct": _extract_insider_pct(data),
        "rugged": rugged if isinstance(rugged, bool) else None,
    }


def _extract_lp_locked(data: dict) -> float | None:
    """
    RugCheck expose le verrouillage de la LP par marché. On prend le marché le
    plus profond, celui qui compte réellement pour sortir d'une position.
    """
    markets = data.get("markets")
    if not isinstance(markets, list) or not markets:
        return None

    best_pct = None
    best_liquidity = -1.0
    for m in markets:
        if not isinstance(m, dict):
            continue
        lp = m.get("lp") or {}
        liquidity = _to_float(lp.get("lpLockedUSD")) or _to_float(lp.get("baseUSD")) or 0.0
        pct = _to_float(lp.get("lpLockedPct"))
        if pct is None:
            continue
        if liquidity > best_liquidity:
            best_liquidity = liquidity
            best_pct = pct
    return None if best_pct is None else max(0.0, min(100.0, best_pct))


def _extract_top_holder(data: dict) -> float | None:
    holders = data.get("topHolders")
    if not isinstance(holders, list) or not holders:
        return None
    pcts = []
    for h in holders:
        if not isinstance(h, dict):
            continue
        # On ignore les comptes déjà identifiés comme pool ou verrouillés.
        if h.get("insider") is None and (h.get("owner") in (None, "")):
            continue
        pct = _to_float(h.get("pct"))
        if pct is not None:
            pcts.append(pct)
    if not pcts:
        return None
    pcts.sort(reverse=True)
    return max(0.0, min(100.0, sum(pcts[:10])))


def _extract_holders(data: dict) -> list[dict]:
    holders = data.get("topHolders")
    out = []
    if not isinstance(holders, list):
        return out
    for h in holders:
        if not isinstance(h, dict) or not h.get("owner"):
            continue
        pct = _to_float(h.get("pct"))
        if pct is not None:
            out.append({"owner": h.get("owner"), "pct": pct})
    return out


def top_holder_pct_excluding(holders: list[dict] | None, excluded_owners: set[str]) -> float | None:
    """
    Part cumulée des 10 plus gros porteurs SANS les propriétaires exclus (en
    pratique la bonding curve). None s'il ne reste aucun porteur à mesurer.
    """
    pcts = sorted(
        (h["pct"] for h in (holders or []) if h.get("owner") not in excluded_owners),
        reverse=True,
    )
    if not pcts:
        return None
    return max(0.0, min(100.0, sum(pcts[:10])))


def _extract_insider_pct(data: dict) -> float | None:
    """
    Part de l'offre détenue par les wallets que RugCheck relie au créateur.
    C'est le signal « bundle » : plusieurs wallets financés par la même source
    achètent au premier bloc, puis revendent ensemble sur les acheteurs
    suivants. Un token peut avoir un contrat parfaitement propre et être un rug
    programmé uniquement par cette structure de détention.
    """
    networks = data.get("insiderNetworks")
    if not isinstance(networks, list) or not networks:
        return None
    total_supply = _to_float(data.get("token", {}).get("supply") if isinstance(data.get("token"), dict) else None)
    total = 0.0
    for n in networks:
        if not isinstance(n, dict):
            continue
        pct = _to_float(n.get("pct"))
        if pct is not None:
            total += pct
            continue
        tokens = _to_float(n.get("tokenAmount"))
        if tokens and total_supply:
            total += (tokens / total_supply) * 100
    return max(0.0, min(100.0, total)) if total else None
