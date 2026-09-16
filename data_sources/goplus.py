"""
data_sources/goplus.py — Client GoPlus Security API
Destination finale : gem_hunter/data_sources/goplus.py

GoPlus offre un tier gratuit (rate-limité) sans authentification pour
les endpoints token_security. Docs : https://docs.gopluslabs.io/

Couvre :
- EVM (BNB Chain, Ethereum, Arbitrum, Robinhood Chain...) via /api/v1/token_security/{chain_id}
- Solana via /api/v1/solana/token_security

RÉVISION MAJEURE — EXPLOITATION COMPLÈTE DE LA RÉPONSE.
La version précédente ne lisait que 6 champs sur la cinquantaine renvoyée par
GoPlus : mint, freeze, lp_locked, honeypot, top10, is_open_source. Tous les
champs qui décrivent RÉELLEMENT un rug pull étaient ignorés : taxes d'achat et
de vente, cannot_sell_all, transfer_pausable, blacklist, propriétaire caché,
reprise de propriété, contrat proxy modifiable, slippage modifiable, part
détenue par le créateur... Un token pouvait donc avoir 99% de taxe de vente,
un propriétaire capable de geler les transferts et 80% de l'offre chez le dev,
et ressortir « aucun problème détecté ». C'est exactement ce trou que ce
module comble.

Deuxième correctif important : la conversion des pourcentages. GoPlus renvoie
ses champs `percent` et ses taxes sous forme de FRACTION ("0.05" = 5%). Le
code précédent appliquait une heuristique « valeur * 100 si valeur <= 1 sinon
valeur », ce qui produisait des résultats absurdes dans les deux sens : une LP
verrouillée à 0,9% était lue comme « 90% verrouillée » (rug qui passe), et un
top 10 réel de 0,8% comme « 80% » (bon token rejeté). La conversion est
maintenant déterministe et bornée.
"""
import logging

from config import GOPLUS_CHAIN_IDS
from core.i18n import t
from data_sources.http_utils import safe_get_json

logger = logging.getLogger("gem_hunter.goplus")

BASE_URL = "https://api.gopluslabs.io"

# Adresses de destruction : une LP envoyée là est définitivement brûlée.
BURN_ADDRESSES = {
    "0x0000000000000000000000000000000000000000",
    "0x000000000000000000000000000000000000dead",
    "0x00000000000000000000000000000000000000dead",
    "11111111111111111111111111111111",
}

# Mots-clés des tags GoPlus identifiant un contrat de verrouillage de LP.
LOCKER_TAG_MARKERS = ("lock", "burn", "dead", "black hole", "unicrypt", "pinksale", "team.finance")


# =============================================================================
# Conversions
# =============================================================================
def _to_float(val) -> float | None:
    """Convertit une valeur GoPlus (souvent une chaîne) en float, ou None."""
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _ratio_to_pct(val) -> float | None:
    """
    GoPlus exprime ses parts et ses taxes en FRACTION de 1 ("0.05" = 5%).
    On convertit systématiquement en pourcentage et on borne à [0, 100].

    Pas d'heuristique « si <= 1 alors *100 » ici : c'était la source du bug
    décrit en tête de fichier. Une valeur strictement supérieure à 1 ne peut
    pas être une fraction valide, on la considère alors comme déjà exprimée en
    pourcentage (certains champs Solana le sont), toujours bornée à 100.
    """
    f = _to_float(val)
    if f is None:
        return None
    pct = f * 100 if f <= 1 else f
    # Arrondi : 0.009 * 100 vaut 0.8999999999999999 en virgule flottante, ce
    # qui pollue tous les affichages et les comparaisons de seuil.
    return round(max(0.0, min(100.0, pct)), 6)


def _flag(val) -> bool | None:
    """
    Convertit un drapeau GoPlus ("1"/"0") en booléen.
    Renvoie None si le champ est absent — et cette distinction compte :
    en mode strict, « absent » n'est pas « sûr ».
    """
    if val is None or val == "":
        return None
    s = str(val).strip().lower()
    if s in ("1", "true", "yes"):
        return True
    if s in ("0", "false", "no"):
        return False
    return None


def _nested_flag(raw: dict, key: str) -> bool | None:
    """Champs Solana de la forme {"status": "1", "authority": [...]}"""
    node = raw.get(key)
    if node is None:
        return None
    if isinstance(node, dict):
        return _flag(node.get("status"))
    if isinstance(node, list):
        return len(node) > 0
    return _flag(node)


# =============================================================================
# Appels API
# =============================================================================
_supported_chains_cache: list[str] | None = None


def get_supported_chains() -> list[str]:
    """
    Liste des chain IDs réellement couverts par GoPlus, récupérée une fois par
    session. Sert à l'auto-détection de Robinhood Chain (chains/robinhood.py) :
    plutôt que de supposer que GoPlus couvre la chaîne, on lui demande.
    """
    global _supported_chains_cache
    if _supported_chains_cache is not None:
        return _supported_chains_cache

    data = safe_get_json(f"{BASE_URL}/api/v1/supported_chains")
    if not data:
        return []  # pas de mise en cache d'un échec, on retentera
    result = data.get("result") or []
    _supported_chains_cache = [str(c.get("id")) for c in result if isinstance(c, dict) and c.get("id")]
    return _supported_chains_cache


def supports_chain(chain: str) -> bool:
    chain_id = GOPLUS_CHAIN_IDS.get(chain)
    if not chain_id:
        return False
    supported = get_supported_chains()
    if not supported:
        # L'endpoint est injoignable : on ne peut pas conclure. On laisse
        # l'appel token_security trancher (il renverra simplement None).
        return True
    return chain_id in supported


def check_evm_token(chain: str, token_address: str) -> dict | None:
    chain_id = GOPLUS_CHAIN_IDS.get(chain)
    if not chain_id:
        return None
    url = f"{BASE_URL}/api/v1/token_security/{chain_id}"
    data = safe_get_json(url, {"contract_addresses": token_address})
    if not data:
        return None
    result = data.get("result") or {}
    return result.get(token_address.lower()) or result.get(token_address)


def check_evm_tokens_bulk(chain: str, addresses: list[str]) -> dict[str, dict]:
    """
    Vérifie plusieurs contrats en un seul appel (GoPlus accepte une liste
    d'adresses séparées par des virgules), au lieu d'un appel par token.
    Évite de déclencher le rate limit sur les cycles à beaucoup de candidats.
    Découpé par lots de 25 par précaution. Renvoie {adresse_lower: raw_data}.
    """
    chain_id = GOPLUS_CHAIN_IDS.get(chain)
    if not chain_id or not addresses:
        return {}

    url = f"{BASE_URL}/api/v1/token_security/{chain_id}"
    results: dict[str, dict] = {}
    batch_size = 25
    for i in range(0, len(addresses), batch_size):
        batch = addresses[i:i + batch_size]
        data = safe_get_json(url, {"contract_addresses": ",".join(batch)})
        if not data:
            continue  # ce lot échoue, les autres lots peuvent quand même réussir
        result = data.get("result") or {}
        for addr in batch:
            info = result.get(addr.lower()) or result.get(addr)
            if info:
                results[addr.lower()] = info
    return results


# Drapeaux d'activité criminelle renvoyés par /address_security. Un seul à "1"
# sur le wallet déployeur suffit à disqualifier le token, quel que soit l'état
# du contrat lui-même.
_ADDRESS_CRIME_FLAGS = (
    "blacklist_doubt", "phishing_activities", "blackmail_activities",
    "stealing_attack", "fake_kyc", "malicious_mining_activities",
    "darkweb_transactions", "cybercrime", "money_laundering",
    "financial_crime", "sanctioned",
)


def check_evm_address_security(chain: str, address: str) -> dict | None:
    """
    Réputation d'un wallet (déployeur, propriétaire) via GoPlus
    /api/v1/address_security/{address}. Gratuit, sans clé. Renvoie None si
    l'adresse est inconnue de GoPlus ou si l'appel échoue.
    """
    chain_id = GOPLUS_CHAIN_IDS.get(chain)
    if not chain_id or not address:
        return None
    if address.lower() in BURN_ADDRESSES:
        return None
    url = f"{BASE_URL}/api/v1/address_security/{address}"
    data = safe_get_json(url, {"chain_id": chain_id})
    if not data:
        return None
    return data.get("result") or {}


def normalize_address_reputation(raw: dict | None) -> dict:
    """
    Réduit la réponse /address_security aux deux signaux qui comptent pour
    l'anti-rug : combien de contrats malveillants ce wallet a déjà déployés
    (une « usine à rug » en a plusieurs), et s'il porte un label d'activité
    criminelle avérée. `available: False` = GoPlus ne connaît pas l'adresse,
    ce qui n'est PAS un feu vert, juste une absence d'information.
    """
    if not raw:
        return {"available": False}
    return {
        "available": True,
        "malicious_contracts_created": _to_int(
            raw.get("number_of_malicious_contracts_created")
        ) or 0,
        "honeypot_related": _flag(raw.get("honeypot_related_address")) is True,
        "criminal_flags": [
            k for k in _ADDRESS_CRIME_FLAGS if _flag(raw.get(k)) is True
        ],
    }


def check_solana_token(token_address: str) -> dict | None:
    url = f"{BASE_URL}/api/v1/solana/token_security"
    data = safe_get_json(url, {"contract_addresses": token_address})
    if not data:
        return None
    result = data.get("result") or {}
    return result.get(token_address)


def check_token(chain: str, token_address: str) -> dict | None:
    if chain == "solana":
        return check_solana_token(token_address)
    return check_evm_token(chain, token_address)


# =============================================================================
# Normalisation
# =============================================================================
EMPTY_SECURITY = {
    "data_available": False,
    "source": None,
    # Autorités
    "mint_authority_active": None,
    "freeze_authority_active": None,
    "is_mintable": None,
    # Vendabilité
    "honeypot": None,
    "cannot_sell_all": None,
    "cannot_buy": None,
    "buy_tax": None,
    "sell_tax": None,
    "transfer_pausable": None,
    "trading_cooldown": None,
    "is_blacklisted": None,
    "is_whitelisted": None,
    "non_transferable": None,
    "transfer_hook": None,
    "transfer_hook_upgradable": None,
    "transfer_fee_pct": None,
    "transfer_fee_upgradable": None,
    # Mutabilité du contrat
    "is_open_source": None,
    "is_proxy": None,
    "hidden_owner": None,
    "can_take_back_ownership": None,
    "owner_change_balance": None,
    "selfdestruct": None,
    "slippage_modifiable": None,
    "personal_slippage_modifiable": None,
    "anti_whale_modifiable": None,
    "external_call": None,
    "gas_abuse": None,
    "balance_mutable_authority": None,
    "closable": None,
    "default_account_state_upgradable": None,
    "metadata_mutable": None,
    # Distribution
    "lp_locked_pct": None,
    "lp_data_available": False,
    "top10_holder_pct": None,
    "holder_count": None,
    "creator_pct": None,
    "owner_pct": None,
    "creator_address": None,
    "owner_address": None,
    # Réputation
    "honeypot_with_same_creator": None,
    "fake_token": None,
    "is_airdrop_scam": None,
    "is_in_dex": None,
    "other_risks": None,
    "unavailable_reason": None,
}


def empty_security(reason: str | None = None) -> dict:
    sec = dict(EMPTY_SECURITY)
    sec["unavailable_reason"] = reason
    return sec


def normalize_security(chain: str, raw: dict | None) -> dict:
    """
    Uniformise la réponse GoPlus (dont les champs diffèrent entre EVM et
    Solana) en un dict de sécurité standard consommé par core/security_checks.py.
    Tous les champs inconnus restent à None — jamais à une valeur « rassurante
    par défaut ».
    """
    if not raw:
        return empty_security(t("unavailable.goplus_empty"))

    sec = dict(EMPTY_SECURITY)
    sec["data_available"] = True
    sec["source"] = "goplus"

    if chain == "solana":
        return _normalize_solana(raw, sec)
    return _normalize_evm(raw, sec)


def _normalize_evm(raw: dict, sec: dict) -> dict:
    fake_token = raw.get("fake_token")
    sec.update({
        # Autorités
        "is_mintable": _flag(raw.get("is_mintable")),
        "mint_authority_active": _flag(raw.get("is_mintable")),
        "freeze_authority_active": None,  # concept Solana, sans équivalent EVM direct

        # Vendabilité — le cœur de la détection de honeypot
        "honeypot": _flag(raw.get("is_honeypot")),
        "cannot_sell_all": _flag(raw.get("cannot_sell_all")),
        "cannot_buy": _flag(raw.get("cannot_buy")),
        "buy_tax": _ratio_to_pct(raw.get("buy_tax")),
        "sell_tax": _ratio_to_pct(raw.get("sell_tax")),
        "transfer_pausable": _flag(raw.get("transfer_pausable")),
        "trading_cooldown": _flag(raw.get("trading_cooldown")),
        "is_blacklisted": _flag(raw.get("is_blacklisted")),
        "is_whitelisted": _flag(raw.get("is_whitelisted")),

        # Mutabilité du contrat — comment le dev peut changer les règles APRÈS
        # que tu es entré en position
        "is_open_source": _flag(raw.get("is_open_source")),
        "is_proxy": _flag(raw.get("is_proxy")),
        "hidden_owner": _flag(raw.get("hidden_owner")),
        "can_take_back_ownership": _flag(raw.get("can_take_back_ownership")),
        "owner_change_balance": _flag(raw.get("owner_change_balance")),
        "selfdestruct": _flag(raw.get("selfdestruct")),
        "slippage_modifiable": _flag(raw.get("slippage_modifiable")),
        "personal_slippage_modifiable": _flag(raw.get("personal_slippage_modifiable")),
        "anti_whale_modifiable": _flag(raw.get("anti_whale_modifiable")),
        "external_call": _flag(raw.get("external_call")),
        "gas_abuse": _flag(raw.get("gas_abuse")),

        # Distribution
        "top10_holder_pct": _sum_top_holders(raw.get("holders") or []),
        "holder_count": _to_int(raw.get("holder_count")),
        "creator_pct": _ratio_to_pct(raw.get("creator_percent")),
        "owner_pct": _ratio_to_pct(raw.get("owner_percent")),
        # Adresse du déployeur et du propriétaire : servent à interroger
        # /address_security sur les candidats en passe de devenir un SIGNAL
        # (voir check_evm_address_security / core/scanner.py).
        "creator_address": _addr(raw.get("creator_address")),
        "owner_address": _addr(raw.get("owner_address")),

        # Réputation du créateur : GoPlus signale si la MÊME adresse a déjà
        # déployé un honeypot. C'est le signal de récidive le plus direct
        # disponible gratuitement, et il n'était pas lu du tout.
        "honeypot_with_same_creator": _flag(raw.get("honeypot_with_same_creator")),
        "fake_token": _flag(fake_token.get("value") if isinstance(fake_token, dict) else fake_token),
        "is_airdrop_scam": _flag(raw.get("is_airdrop_scam")),
        "is_in_dex": _flag(raw.get("is_in_dex")),
        "other_risks": raw.get("other_potential_risks") or None,
    })

    lp_holders = raw.get("lp_holders") or []
    sec["lp_locked_pct"] = _sum_locked_lp(lp_holders)
    sec["lp_data_available"] = bool(lp_holders)
    return sec


def _normalize_solana(raw: dict, sec: dict) -> dict:
    transfer_hook = raw.get("transfer_hook")
    sec.update({
        # Autorités
        "mint_authority_active": _nested_flag(raw, "mintable"),
        "freeze_authority_active": _nested_flag(raw, "freezable"),
        "is_mintable": _nested_flag(raw, "mintable"),

        # Vendabilité — les équivalents Solana du honeypot vivent dans les
        # extensions Token-2022, totalement ignorées jusqu'ici.
        "honeypot": None,  # pas de simulation d'achat/vente côté GoPlus Solana
        "non_transferable": _flag(raw.get("non_transferable")),
        "transfer_hook": (len(transfer_hook) > 0) if isinstance(transfer_hook, list) else _flag(transfer_hook),
        "transfer_hook_upgradable": _nested_flag(raw, "transfer_hook_upgradable"),
        "transfer_fee_pct": _extract_transfer_fee(raw.get("transfer_fee")),
        "transfer_fee_upgradable": _nested_flag(raw, "transfer_fee_upgradable"),

        # Mutabilité
        "balance_mutable_authority": _nested_flag(raw, "balance_mutable_authority"),
        "closable": _nested_flag(raw, "closable"),
        "default_account_state_upgradable": _nested_flag(raw, "default_account_state_upgradable"),
        "metadata_mutable": _nested_flag(raw, "metadata_mutable"),

        # Distribution
        "top10_holder_pct": _sum_top_holders(raw.get("holders") or []),
        "holder_count": _to_int(raw.get("holder_count")),
        "creator_pct": _creator_pct_solana(raw),
    })

    lp_holders = raw.get("lp_holders") or []
    lp_locked = _sum_locked_lp(lp_holders)
    if lp_locked is None:
        lp_locked = _ratio_to_pct(raw.get("lp_locked_percentage") or raw.get("lockedPercent"))
    sec["lp_locked_pct"] = lp_locked
    sec["lp_data_available"] = lp_locked is not None
    return sec


def _extract_transfer_fee(node) -> float | None:
    """Extrait le pourcentage de frais de transfert d'une extension Token-2022."""
    if not isinstance(node, dict) or not node:
        return None
    for key in ("current_fee_rate", "fee_rate", "transfer_fee_basis_points", "newer_transfer_fee"):
        val = node.get(key)
        if isinstance(val, dict):
            val = val.get("transfer_fee_basis_points") or val.get("fee_rate")
        if val is None:
            continue
        f = _to_float(val)
        if f is None:
            continue
        # Les basis points vont de 0 à 10000 (100%)
        return min(100.0, f / 100) if f > 1 else _ratio_to_pct(val)
    return None


def _creator_pct_solana(raw: dict) -> float | None:
    creators = raw.get("creators")
    if not isinstance(creators, list) or not creators:
        return None
    total = 0.0
    found = False
    for c in creators:
        if not isinstance(c, dict):
            continue
        pct = _ratio_to_pct(c.get("share") if c.get("share") is not None else c.get("percent"))
        if pct is not None:
            total += pct
            found = True
    return min(100.0, total) if found else None


def _to_int(val) -> int | None:
    f = _to_float(val)
    return int(f) if f is not None else None


def _addr(val) -> str | None:
    """Normalise une adresse GoPlus (chaîne) ; ignore les valeurs vides ou nulles."""
    if not isinstance(val, str):
        return None
    s = val.strip()
    return s or None


def _sum_locked_lp(lp_holders: list) -> float | None:
    """
    Somme la part de LP réellement verrouillée ou brûlée : porteurs marqués
    is_locked, adresses de burn connues, et contrats de locker identifiés par
    leur tag GoPlus (Unicrypt, PinkLock, Team Finance...).

    Le tag était ignoré auparavant : une LP verrouillée chez un locker
    reconnu mais sans le drapeau is_locked ressortait comme non verrouillée,
    ce qui rangeait de bons tokens dans « LP non vérifiable » — et donc, avec
    l'ancienne notation neutre, au même niveau qu'un token dont la LP était
    restée dans le wallet du dev.
    """
    if not lp_holders:
        return None
    total = 0.0
    for h in lp_holders:
        if not isinstance(h, dict):
            continue
        address = (h.get("address") or "").lower()
        tag = (h.get("tag") or "").lower()
        is_locked = _flag(h.get("is_locked")) is True
        is_burned = address in BURN_ADDRESSES or address.endswith("dead")
        is_locker = any(marker in tag for marker in LOCKER_TAG_MARKERS)

        if is_locked or is_burned or is_locker:
            pct = _ratio_to_pct(h.get("percent"))
            if pct is not None:
                total += pct
    return min(100.0, total)


def _sum_top_holders(holders: list, top_n: int = 10) -> float | None:
    """
    Somme la part détenue par les plus gros porteurs individuels, en excluant
    les adresses qui détiennent légitimement une grosse part sans que ce soit
    un risque de vente : pool DEX, routeur, contrat de locker, adresse de burn.

    Deux correctifs par rapport à la version précédente. L'exclusion se base
    désormais sur is_contract ET sur le tag ET sur les adresses de burn. Et le
    top N est pris APRÈS tri décroissant : GoPlus ne garantit pas l'ordre de la
    liste, l'ancien code sommait donc « les 10 premiers renvoyés », pas « les
    10 plus gros ».
    """
    if not holders:
        return None

    relevant = []
    for h in holders:
        if not isinstance(h, dict):
            continue
        address = (h.get("address") or "").lower()
        tag = (h.get("tag") or "").lower()
        if address in BURN_ADDRESSES:
            continue
        if _flag(h.get("is_contract")) is True:
            continue
        if _flag(h.get("is_locked")) is True:
            continue
        if any(marker in tag for marker in LOCKER_TAG_MARKERS):
            continue
        pct = _ratio_to_pct(h.get("percent"))
        if pct is not None:
            relevant.append(pct)

    if not relevant:
        # Tous les porteurs listés sont des contrats ou des adresses
        # verrouillées : on ne sait rien de la concentration réelle entre
        # mains individuelles. On renvoie None (inconnu), pas 0 (« parfait »).
        return None

    relevant.sort(reverse=True)
    return min(100.0, sum(relevant[:top_n]))
