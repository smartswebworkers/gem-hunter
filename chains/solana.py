"""
chains/solana.py — Découverte et vérification des tokens sur Solana
Destination finale : gem_hunter/chains/solana.py

BUG CRITIQUE CORRIGÉ — LE DRAPEAU BONDING CURVE ÉTAIT TOUJOURS VRAI.
La ligne était :

    "is_pump_bonding_curve": (token.get("pool") in BONDING_CURVE_POOL_MARKERS) or True

Le `or True` final rendait la condition inutile : le drapeau valait True pour
tous les candidats PumpPortal, sans exception. Or ce drapeau commande deux
comportements dans l'ancien code :
  - core/security_checks.py sautait TOUS les filtres de liquidité, de volume
    et d'âge, et se contentait d'exiger 300 dollars de liquidité,
  - compute_safety_score() attribuait d'office la note maximale de sécurité,
    avec le motif « fonds verrouillés par le programme pump.fun ».

Autrement dit, chaque token issu de PumpPortal recevait automatiquement 100%
de sécurité et échappait à tous les filtres de marché. C'est le chemin le plus
court par lequel un rug pull traversait le bot.

Le drapeau est maintenant calculé pour de vrai, et surtout le raisonnement qui
en découlait a été corrigé : sur pump.fun, la liquidité est effectivement hors
d'atteinte du développeur, mais ce n'est pas la liquidité qu'il vend. Il vend
son allocation. Un token en bonding curve n'est donc jamais « sûr » par
construction, il est simplement non vérifiable — d'où son classement en
niveau VEILLE, avec les vérifications développeur ci-dessous pour éliminer
malgré tout les cas les plus flagrants.
"""
import logging

from core.i18n import t
from data_sources import dexscreener, geckoterminal, goplus, pumpportal, rugcheck, solana_rpc
import config as cfg

logger = logging.getLogger("gem_hunter.chains.solana")

CHAIN = "solana"

# Le champ « pool » du flux PumpPortal vaut « pump » tant que le token n'a pas
# migré vers un pool classique (PumpSwap, Raydium).
BONDING_CURVE_POOL_MARKERS = {"pump", "pump-amm", "pumpswap", "bonding-curve"}


def _prefilter_liquidity() -> float:
    return cfg.MIN_LIQUIDITY_USD / 4


def _from_pumpportal(sol_price_usd: float) -> list[dict]:
    candidates = []
    window = getattr(cfg, "PUMP_RECENT_WINDOW_SECONDS", 900)
    for token in pumpportal.get_recent_tokens(max_age_seconds=window):
        market_cap_sol = token.get("marketCapSol")
        if market_cap_sol is None:
            continue
        market_cap_usd = market_cap_sol * sol_price_usd
        if not (cfg.MARKET_CAP_MIN <= market_cap_usd <= cfg.MARKET_CAP_MAX):
            continue

        v_sol_reserves = token.get("vSolInBondingCurve") or 0
        v_token_reserves = token.get("vTokensInBondingCurve") or 0
        # Prix spot réel de la bonding curve = ratio des réserves virtuelles.
        price_usd = (v_sol_reserves / v_token_reserves) * sol_price_usd if v_token_reserves else None

        pool = str(token.get("pool") or "").lower()
        candidates.append({
            "chain": CHAIN,
            "contract": token.get("mint"),
            "ticker": token.get("symbol"),
            "name": token.get("name"),
            "market_cap": market_cap_usd,
            "liquidity": v_sol_reserves * sol_price_usd,
            "volume_24h": None,
            "volume_1h": None,
            "price_usd": price_usd,
            "creator": token.get("traderPublicKey"),
            # Correctif : plus de `or True`. Un token dont le champ pool est
            # absent ou inconnu est traité comme une bonding curve par défaut,
            # ce qui est le choix prudent puisque PumpPortal ne diffuse que des
            # créations pump.fun — mais c'est maintenant une décision explicite,
            # pas un accident d'écriture.
            "is_pump_bonding_curve": pool in BONDING_CURVE_POOL_MARKERS or not pool,
            "pair_created_at": (token.get("_received_at") or 0) * 1000 or None,
            "source": "pumpportal",
        })
    return candidates


def _from_dexscreener() -> list[dict]:
    candidates = []
    prefilter = _prefilter_liquidity()
    addresses = dexscreener.discover_token_addresses(CHAIN)
    for normalized in dexscreener.get_best_pairs_bulk(CHAIN, addresses):
        if not normalized.get("contract"):
            continue
        if (normalized.get("liquidity") or 0) < prefilter:
            continue
        normalized["chain"] = CHAIN
        normalized["is_pump_bonding_curve"] = False
        candidates.append(normalized)
    return candidates


def _from_trending() -> list[dict]:
    """Pools Solana qui gagnent de la traction (profil « quality »)."""
    candidates = []
    prefilter = _prefilter_liquidity()
    pools, token_index = geckoterminal.get_trending_pools(CHAIN)
    for pool in pools:
        normalized = geckoterminal.normalize_pool(pool, token_index)
        if not normalized.get("contract"):
            continue
        if (normalized.get("liquidity") or 0) < prefilter:
            continue
        normalized["chain"] = CHAIN
        normalized["is_pump_bonding_curve"] = False
        candidates.append(normalized)
    return candidates


def discover_candidates() -> list[dict]:
    sol_price = dexscreener.get_sol_price_usd()
    candidates = []

    # Le flux temps réel pump.fun n'est branché QU'EN profil « degen » : sur ce
    # segment, l'écrasante majorité des créations sont des rugs, et c'est
    # exactement ce que l'utilisateur ne veut plus voir remonter.
    if getattr(cfg, "ENABLE_PUMPPORTAL_FIREHOSE", True):
        pumpportal.ensure_started()
        if sol_price:
            candidates.extend(_from_pumpportal(sol_price))
        else:
            logger.warning("Prix SOL/USD indisponible — découverte PumpPortal ignorée pour ce cycle.")

    candidates.extend(_from_dexscreener())

    if getattr(cfg, "USE_TRENDING_DISCOVERY", False):
        candidates.extend(_from_trending())

    # Dédoublonnage par contrat. Priorité à la version DexScreener quand elle
    # existe : un token présent des deux côtés a migré, et la version DexScreener
    # porte alors de vraies données de marché et un vrai pool vérifiable.
    by_contract: dict[str, dict] = {}
    for c in candidates:
        addr = c.get("contract")
        if not addr:
            continue
        existing = by_contract.get(addr)
        if existing is None or (
            existing.get("source") == "pumpportal"
            and c.get("source") in ("dexscreener", "geckoterminal")
        ):
            by_contract[addr] = c

    unique = list(by_contract.values())
    logger.info(
        f"Solana [{getattr(cfg, 'SCAN_PROFILE', '?')}] : {len(unique)} candidat(s) présélectionné(s) "
        f"(PumpPortal: {sum(1 for c in unique if c.get('source') == 'pumpportal')}, "
        f"DexScreener: {sum(1 for c in unique if c.get('source') == 'dexscreener')}, "
        f"Trending: {sum(1 for c in unique if c.get('source') == 'geckoterminal')})."
    )
    return unique


def enrich_with_security(candidate: dict) -> dict:
    """
    Assemble le dossier de sécurité d'un token Solana à partir de trois sources
    complémentaires, choisies pour leurs délais d'indexation différents :

      - RPC on-chain : disponible immédiatement, donne les autorités, la
        concentration réelle et ce que détient encore le créateur,
      - GoPlus      : quelques minutes de délai, donne les extensions
        Token-2022 et le verrouillage de la liquidité,
      - RugCheck    : second avis, seul à couvrir les réseaux de wallets liés
        au créateur (les « insiders » qui achètent au premier bloc).

    Aucune de ces sources n'est traitée comme facultative-donc-rassurante :
    ce qui reste inconnu reste marqué inconnu, et bloque le passage en SIGNAL.
    """
    contract = candidate["contract"]
    is_bonding = bool(candidate.get("is_pump_bonding_curve"))

    # dex_paid est vérifié plus tard, juste avant la promotion en signal
    # (voir core/scanner.py) : c'est un appel réseau par candidat, inutile de
    # le dépenser sur des tokens qui seront rejetés par la sécurité.

    # --- 1. GoPlus (extensions Token-2022, LP, métadonnées) ---
    raw = goplus.check_token(CHAIN, contract)
    sec = goplus.normalize_security(CHAIN, raw)

    # --- 2. RPC on-chain : la seule source utilisable sur un token récent ---
    mint_info = solana_rpc.get_mint_authorities(contract)
    if mint_info is not None:
        sec["data_available"] = True
        sec["source"] = (sec.get("source") or "") + "+rpc" if sec.get("source") else "rpc"
        # Le RPC fait autorité sur les autorités : il lit l'état réel du compte
        # mint, sans délai d'indexation ni cache.
        sec["mint_authority_active"] = mint_info["mint_authority_active"]
        sec["freeze_authority_active"] = mint_info["freeze_authority_active"]
    elif not sec.get("data_available"):
        sec["unavailable_reason"] = t("unavailable.solana_no_source")

    concentration = solana_rpc.get_holder_concentration(contract)
    if concentration is not None:
        # On garde la mesure la plus défavorable entre l'indexeur et la lecture
        # on-chain : si les deux divergent, c'est presque toujours que l'indexeur
        # a une vue périmée d'un token qui bouge vite.
        rpc_top10 = concentration["top10_holder_pct"]
        existing = sec.get("top10_holder_pct")
        sec["top10_holder_pct"] = rpc_top10 if existing is None else max(existing, rpc_top10)
        candidate["largest_holder_pct"] = concentration["largest_holder_pct"]

    creator = candidate.get("creator")
    if creator:
        dev_pct = solana_rpc.get_creator_holding_pct(contract, creator)
        if dev_pct is not None:
            candidate["dev_holding_pct"] = dev_pct
            if sec.get("creator_pct") is None:
                sec["creator_pct"] = dev_pct

    candidate["security"] = sec

    # --- 3. RugCheck : second avis, indispensable sur Solana ---
    # GoPlus ne simule pas d'achat/vente sur cette chaîne : sans RugCheck, le
    # bot n'a littéralement aucune détection de piège à la vente sur sa chaîne
    # prioritaire.
    candidate["rugcheck"] = rugcheck.get_report(contract)
    rug = candidate["rugcheck"]
    if rug.get("available"):
        if rug.get("lp_locked_pct") is not None and sec.get("lp_locked_pct") is None:
            sec["lp_locked_pct"] = rug["lp_locked_pct"]
            sec["lp_data_available"] = True
        if rug.get("top_holder_pct") is not None:
            existing = sec.get("top10_holder_pct")
            sec["top10_holder_pct"] = rug["top_holder_pct"] if existing is None else max(existing, rug["top_holder_pct"])
        insider_pct = rug.get("insider_pct")
        if insider_pct is not None:
            candidate["insider_pct"] = insider_pct

    if is_bonding:
        # Rappel explicite pour les journaux et l'interface : ce candidat ne
        # peut pas devenir un SIGNAL tant qu'il n'a pas migré. Ce n'est pas une
        # pénalité arbitraire, c'est qu'il n'y a rien à vérifier avant.
        candidate["watch_only_reason"] = (
            "token encore en bonding curve — pool, verrouillage de liquidité et "
            "distribution ne sont pas encore vérifiables"
        )

    return candidate


def enrich_batch(candidates: list[dict], should_stop=None) -> list[dict]:
    """
    Solana n'a pas d'endpoint GoPlus groupé et la plupart des appels RPC sont
    unitaires : on enrichit candidat par candidat, en tolérant l'échec de l'un
    sans perdre tout le cycle.

    Un appel groupé existe cependant pour les autorités de mint
    (getMultipleAccounts) : on pré-charge tout le lot d'un coup avant la
    boucle, ce qui remplace jusqu'à len(candidates) appels getAccountInfo
    séparés par un seul appel RPC. C'est le principal levier pour desserrer la
    limite de débit du RPC public partagé (voir data_sources/solana_rpc.py) —
    getTokenLargestAccounts et getTokenAccountsByOwner restent unitaires,
    faute d'équivalent groupé côté RPC Solana.

    `should_stop` : callable optionnel renvoyant True dès qu'un STOP a été
    demandé. Testé avant CHAQUE candidat — un cycle Solana avec le RPC public
    en 429 peut mettre plusieurs minutes (throttle adaptatif jusqu'à 6 s/appel,
    2 appels RPC restants par token, 25 tokens) : sans ce test, cliquer STOP ne
    faisait effet qu'à la fin de la boucle.
    """
    if should_stop is None or not should_stop():
        # Un STOP déjà demandé avant même de commencer n'a aucune raison de
        # déclencher un appel réseau : le pré-chargement groupé est soumis à
        # la même règle que la boucle qui suit.
        solana_rpc.prefetch_mint_authorities([c["contract"] for c in candidates if c.get("contract")])

    enriched = []
    for candidate in candidates:
        if should_stop is not None and should_stop():
            logger.info(
                f"STOP demandé — enrichissement Solana interrompu "
                f"({len(enriched)}/{len(candidates)} vérifiés)."
            )
            break
        try:
            enriched.append(enrich_with_security(candidate))
        except Exception:
            logger.exception(f"Échec d'enrichissement pour {candidate.get('ticker', '?')}.")
    return enriched
