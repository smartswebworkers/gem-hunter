"""
data_sources/solana_rpc.py — Vérification directe on-chain via RPC Solana
Destination finale : gem_hunter/data_sources/solana_rpc.py

Contrairement aux indexeurs (GoPlus, RugCheck, DexScreener), qui doivent
d'abord indexer un token avant de pouvoir répondre — délai de quelques
secondes à quelques minutes — l'état on-chain est lisible dès le bloc de
création. C'est donc la seule source utilisable sur un token qui vient
d'apparaître, et la seule qui permette de dire quelque chose de vrai sur un
token en bonding curve.

AJOUTS DE CETTE RÉVISION.
Le module ne lisait que mint authority et freeze authority. Il lit désormais
aussi la concentration réelle de l'offre et la part encore détenue par le
créateur, qui sont les deux signaux de rug les plus fiables sur un token
récent — et les seuls disponibles immédiatement :

  - getTokenLargestAccounts : les 20 plus gros comptes de tokens. Si trois
    wallets détiennent 70% de l'offre, le contrat peut être parfait, le rug
    est déjà en place.
  - getTokenAccountsByOwner  : ce que le créateur détient encore de son propre
    token. Un dev qui a gardé 30% de l'offre n'a aucune raison de ne pas la
    vendre sur les premiers acheteurs.

Utilise un RPC public gratuit par défaut (api.mainnet-beta.solana.com,
rate-limité mais suffisant pour ce volume). Un RPC personnel (Helius,
QuickNode...) configuré via RPC_SOLANA dans .env améliore nettement la
fiabilité, et devient nécessaire si le scan tourne en continu.
"""
import logging
import threading
import time

from config import API_KEYS
from data_sources.http_utils import safe_post_json

logger = logging.getLogger("gem_hunter.solana_rpc")

# --- Limiteur de débit adaptatif pour le RPC public ---------------------------
# api.mainnet-beta.solana.com applique une limite AGRESSIVE et non annoncée par
# méthode RPC sur l'endpoint public partagé. Sans espacement côté client, un
# cycle de scan à 25 tokens déclenche ~100 POST en rafale (getAccountInfo,
# getTokenLargestAccounts, getMultipleAccounts, getTokenAccountsByOwner par
# token) et le RPC répond 429 « Too many requests for a specific RPC call » sur
# la quasi-totalité. Les vérifications on-chain échouent alors en masse et des
# tokens propres restent bloqués en VEILLE « non vérifiable », sans qu'aucun
# filtre de sécurité n'ait été assoupli — c'est une panne de débit, pas une
# décision.
#
# On espace donc les appels, et on ajuste l'intervalle empiriquement : on
# ralentit après un échec (429 probable), on réaccélère doucement après une
# série de succès. Un RPC personnel (RPC_SOLANA dans .env) n'a pas cette
# limite : l'espacement est alors désactivé et la rafale passe telle quelle.
_RPC_MIN_INTERVAL = 0.30    # s entre deux appels, plancher (RPC public satisfait)
_RPC_MAX_INTERVAL = 6.0     # s entre deux appels, plafond (RPC public saturé)
_rate_lock = threading.Lock()
_last_call_ts = 0.0
_current_interval = _RPC_MIN_INTERVAL


def _use_public_rpc() -> bool:
    return not API_KEYS.get("rpc_solana")


def _throttle() -> None:
    """Attend le temps nécessaire pour respecter l'intervalle courant entre
    deux appels vers le RPC public. Sans effet si un RPC personnel est
    configuré."""
    if not _use_public_rpc():
        return
    global _last_call_ts
    with _rate_lock:
        wait = _current_interval - (time.monotonic() - _last_call_ts)
        if wait > 0:
            time.sleep(wait)
        _last_call_ts = time.monotonic()


def _note_rpc_result(ok: bool) -> None:
    """Auto-ajuste l'espacement des appels. Le RPC public n'annonce pas sa
    limite ; on la trouve en tâtonnant : décroissance multiplicative après un
    succès, augmentation franche après un échec."""
    if not _use_public_rpc():
        return
    global _current_interval
    with _rate_lock:
        if ok:
            _current_interval = max(_RPC_MIN_INTERVAL, _current_interval * 0.92)
        else:
            _current_interval = min(_RPC_MAX_INTERVAL, _current_interval * 1.6 + 0.1)

# Cache court des infos de mint. get_holder_concentration() et
# get_creator_holding_pct() ont toutes deux besoin de l'offre totale, et
# appelaient donc get_mint_authorities() chacune de leur côté : trois lectures
# du même compte par token. Sur un cycle à 200 candidats Solana, cela suffisait
# à faire répondre 429 au RPC public. Le TTL est court parce que les autorités
# d'un mint peuvent être révoquées d'une minute à l'autre, et que c'est
# précisément ce qu'on surveille.
_mint_cache: dict[str, tuple[float, dict | None]] = {}
_MINT_CACHE_TTL = 60
_MINT_CACHE_MAX = 2000

DEFAULT_RPC = "https://api.mainnet-beta.solana.com"
TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
TOKEN_2022_PROGRAM_ID = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"

# Comptes qui détiennent légitimement une grosse part de l'offre sans que ce
# soit un risque de vente : bonding curve pump.fun, pools AMM, programmes.
_KNOWN_PROGRAM_OWNERS = {
    "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P",   # pump.fun
    "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA",   # PumpSwap AMM
    "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8",  # Raydium AMM v4
    "CAMMCzo5YL8w4VFF8KVHrK22GGUsp5VTaW7grrKgrWqK",  # Raydium CLMM
}


def _rpc_url() -> str:
    return API_KEYS.get("rpc_solana") or DEFAULT_RPC


def _call(method: str, params: list) -> tuple[dict | list | None, bool]:
    """
    Renvoie (résultat, appel_réussi).

    Le second élément est indispensable : sur Solana, un appel réussi qui ne
    renvoie aucun compte veut dire « le créateur ne détient rien », alors qu'un
    appel échoué veut dire « on ne sait pas ». Les confondre reviendrait à
    conclure « le dev ne détient rien » chaque fois que le RPC est saturé, ce
    qui est exactement le genre de faux positif rassurant qui laisse passer un rug.
    """
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    _throttle()
    data = safe_post_json(_rpc_url(), body, headers={"Content-Type": "application/json"})
    if not data:
        # safe_post_json a déjà renvoyé None sur un statut HTTP 429 : on le
        # compte comme une saturation de débit pour élargir l'intervalle.
        _note_rpc_result(False)
        return None, False
    if "error" in data:
        err = data["error"]
        code = err.get("code") if isinstance(err, dict) else None
        msg = str(err.get("message", "")).lower() if isinstance(err, dict) else ""
        if code == 429 or "many requests" in msg or "rate limit" in msg:
            # Certains fournisseurs renvoient 200 + erreur JSON pour un
            # rate-limit : même traitement que le 429 HTTP, sans polluer le log.
            _note_rpc_result(False)
            return None, False
        logger.warning(f"Erreur RPC Solana ({method}) : {err}")
        _note_rpc_result(False)
        return None, False
    _note_rpc_result(True)
    return data.get("result"), True


def get_mint_authorities(mint_address: str) -> dict | None:
    """
    Renvoie {mint_authority_active, freeze_authority_active, supply, decimals}
    lu directement depuis le compte mint on-chain, ou None si indisponible
    (RPC injoignable, adresse invalide, compte inexistant).

    Résultat mis en cache une minute : plusieurs vérifications du même cycle ont
    besoin de l'offre totale du même token. Un lot entier de candidats peut
    être pré-chargé d'un coup avec prefetch_mint_authorities() ci-dessous — le
    cache y est alors déjà chaud et cet appel unitaire ne coûte plus rien.
    """
    cached = _mint_cache.get(mint_address)
    if cached and (time.time() - cached[0]) < _MINT_CACHE_TTL:
        return cached[1]

    info = _fetch_mint_authorities(mint_address)
    if info is not None:  # on ne met jamais un échec en cache
        if len(_mint_cache) > _MINT_CACHE_MAX:
            _mint_cache.clear()
        _mint_cache[mint_address] = (time.time(), info)
    return info


_GET_MULTIPLE_ACCOUNTS_MAX = 100  # limite imposée par le RPC Solana lui-même


def prefetch_mint_authorities(mint_addresses: list[str]) -> None:
    """
    Pré-charge en un minimum d'appels RPC les informations de mint de PLUSIEURS
    tokens à la fois, via getMultipleAccounts (jusqu'à 100 adresses par appel),
    au lieu d'un getAccountInfo séparé par token dans get_mint_authorities().

    C'est le principal levier pour desserrer la limite de débit du RPC public
    partagé : un cycle à 25 candidats Solana déclenchait jusqu'à 25 appels
    getAccountInfo rien que pour les autorités de mint (avant même
    getTokenLargestAccounts et getTokenAccountsByOwner, qui restent unitaires
    faute d'équivalent groupé côté RPC) ; ce pré-chargement les remplace par un
    seul appel getMultipleAccounts pour tout le lot.

    Les adresses déjà en cache et encore fraîches ne sont jamais reversées :
    sans effet sur les tokens déjà vus il y a moins de _MINT_CACHE_TTL secondes.
    Un échec réseau sur un lot laisse simplement ces adresses non pré-chargées
    — get_mint_authorities() retentera l'appel unitaire comme avant, sans
    jamais confondre « pas encore pré-chargé » et « compte inexistant ».
    """
    now = time.time()
    seen: set[str] = set()
    to_fetch: list[str] = []
    for addr in mint_addresses:
        if not addr or addr in seen:
            continue
        seen.add(addr)
        cached = _mint_cache.get(addr)
        if cached and (now - cached[0]) < _MINT_CACHE_TTL:
            continue
        to_fetch.append(addr)
    if not to_fetch:
        return

    for i in range(0, len(to_fetch), _GET_MULTIPLE_ACCOUNTS_MAX):
        batch = to_fetch[i:i + _GET_MULTIPLE_ACCOUNTS_MAX]
        result, ok = _call("getMultipleAccounts", [batch, {"encoding": "jsonParsed"}])
        if not ok or not result or not isinstance(result.get("value"), list):
            continue
        fetch_time = time.time()
        if len(_mint_cache) > _MINT_CACHE_MAX:
            _mint_cache.clear()
        for address, account in zip(batch, result["value"]):
            info = _parse_mint_account(account)
            if info is not None:
                _mint_cache[address] = (fetch_time, info)


def _fetch_mint_authorities(mint_address: str) -> dict | None:
    result, _ok = _call("getAccountInfo", [mint_address, {"encoding": "jsonParsed"}])
    if not result or not result.get("value"):
        return None
    info = _parse_mint_account(result["value"])
    if info is None:
        logger.warning(f"Réponse RPC inattendue pour {mint_address} (compte non-SPL ?).")
    return info


def _parse_mint_account(account: dict | None) -> dict | None:
    """
    Extrait {mint_authority_active, freeze_authority_active, supply, decimals}
    d'un compte mint déjà résolu en jsonParsed. Factorisé pour servir aussi
    bien à un getAccountInfo unitaire (_fetch_mint_authorities) qu'à un
    getMultipleAccounts groupé (prefetch_mint_authorities) — même forme de
    réponse par compte dans les deux cas.
    """
    if not account:
        return None
    try:
        info = account["data"]["parsed"]["info"]
    except (KeyError, TypeError):
        return None

    supply_raw = info.get("supply")
    decimals = info.get("decimals")
    try:
        supply = float(supply_raw) / (10 ** int(decimals)) if supply_raw is not None and decimals is not None else None
    except (TypeError, ValueError, OverflowError):
        supply = None

    return {
        "mint_authority_active": info.get("mintAuthority") is not None,
        "freeze_authority_active": info.get("freezeAuthority") is not None,
        "mint_authority": info.get("mintAuthority"),
        "freeze_authority": info.get("freezeAuthority"),
        "supply": supply,
        "supply_raw": supply_raw,
        "decimals": decimals,
    }


def get_holder_concentration(mint_address: str, top_n: int = 10) -> dict | None:
    """
    Concentration réelle de l'offre, lue on-chain via getTokenLargestAccounts.

    Renvoie {top10_holder_pct, largest_holder_pct, accounts_counted} ou None si
    la donnée est inexploitable. Les comptes appartenant à un programme connu
    (bonding curve pump.fun, pools AMM) sont exclus du calcul : ils détiennent
    l'offre au nom du marché, pas au nom d'une personne qui peut la vendre.

    C'est la vérification qui manquait le plus : sur un token de moins d'une
    heure, aucun indexeur ne connaît encore la liste des porteurs, alors que
    le RPC la donne immédiatement.
    """
    result, _ok = _call("getTokenLargestAccounts", [mint_address, {"commitment": "confirmed"}])
    if not result or not isinstance(result.get("value"), list):
        return None

    accounts = result["value"]
    if not accounts:
        return None

    total = 0.0
    amounts: list[tuple[str, float]] = []
    for acc in accounts:
        try:
            amount = float(acc.get("uiAmount") or 0)
        except (TypeError, ValueError):
            continue
        if amount <= 0:
            continue
        amounts.append((acc.get("address", ""), amount))
        total += amount

    if total <= 0 or not amounts:
        return None

    # On a besoin de l'offre totale pour rapporter ces montants à quelque
    # chose de significatif. getTokenLargestAccounts ne renvoie que les 20 plus
    # gros comptes : leur somme n'est pas l'offre totale.
    mint_info = get_mint_authorities(mint_address)
    supply = (mint_info or {}).get("supply")
    denominator = supply if supply and supply > 0 else total

    owners = _resolve_account_owners([a for a, _ in amounts])

    individual = []
    for address, amount in amounts:
        owner = owners.get(address)
        if owner and owner in _KNOWN_PROGRAM_OWNERS:
            continue  # bonding curve ou pool AMM, pas un porteur individuel
        individual.append(amount)

    if not individual:
        return None

    individual.sort(reverse=True)
    top_sum = sum(individual[:top_n])

    return {
        "top10_holder_pct": max(0.0, min(100.0, (top_sum / denominator) * 100)),
        "largest_holder_pct": max(0.0, min(100.0, (individual[0] / denominator) * 100)),
        "accounts_counted": len(individual),
        "supply_known": supply is not None,
    }


def _resolve_account_owners(addresses: list[str]) -> dict[str, str]:
    """
    Résout le propriétaire (wallet) de chaque compte de token, en un seul
    appel groupé getMultipleAccounts. Sans cette résolution, impossible de
    distinguer « une baleine détient 60% » de « la bonding curve détient 60% »,
    qui n'ont rien à voir.
    """
    if not addresses:
        return {}
    result, _ok = _call("getMultipleAccounts", [addresses[:100], {"encoding": "jsonParsed"}])
    if not result or not isinstance(result.get("value"), list):
        return {}

    owners = {}
    for address, account in zip(addresses, result["value"]):
        if not account:
            continue
        try:
            owners[address] = account["data"]["parsed"]["info"]["owner"]
        except (KeyError, TypeError):
            continue
    return owners


def get_creator_holding_pct(mint_address: str, creator_address: str) -> float | None:
    """
    Part de l'offre encore détenue par le créateur du token.

    Sur pump.fun, le schéma de rug le plus fréquent est le plus simple : le dev
    achète une grosse part de sa propre bonding curve à la création, laisse le
    prix monter sur les acheteurs suivants, puis vend tout d'un coup. Aucun
    drapeau de contrat ne le signale, aucun indexeur ne le voit assez tôt.
    Cette lecture on-chain, elle, est disponible immédiatement.
    """
    if not creator_address or not mint_address:
        return None

    # BUG CORRIGÉ : le filtre passait à la fois "mint" et "programId".
    # getTokenAccountsByOwner n'accepte QU'UNE seule clé de filtre, d'où
    # l'erreur RPC « invalid value: map, expected map with a single key » sur
    # chaque token. Le contrôle de la part détenue par le développeur — l'une
    # des vérifications anti-rug les plus importantes sur pump.fun — échouait
    # donc systématiquement, et renvoyait « inconnu » à chaque fois.
    # Filtrer sur le mint seul suffit et couvre les deux programmes de token.
    balances = []
    result, any_call_succeeded = _call("getTokenAccountsByOwner", [
        creator_address,
        {"mint": mint_address},
        {"encoding": "jsonParsed", "commitment": "confirmed"},
    ])
    if result and isinstance(result.get("value"), list):
        for acc in result["value"]:
            try:
                amount = float(acc["account"]["data"]["parsed"]["info"]["tokenAmount"]["uiAmount"] or 0)
            except (KeyError, TypeError, ValueError):
                continue
            balances.append(amount)

    if not balances:
        # Aucun compte trouvé : soit le créateur ne détient réellement rien
        # (bon signe), soit le RPC a échoué (on ne peut rien conclure). Les
        # confondre reviendrait à déclarer « dev à 0% » chaque fois que le RPC
        # sature. Seul un appel réussi autorise à conclure 0.
        return 0.0 if any_call_succeeded else None

    mint_info = get_mint_authorities(mint_address)
    supply = (mint_info or {}).get("supply")
    if not supply or supply <= 0:
        return None

    return max(0.0, min(100.0, (sum(balances) / supply) * 100))
