"""
core/security_checks.py — Moteur de veto anti-rug
Destination finale : gem_hunter/core/security_checks.py

RÉÉCRITURE COMPLÈTE.

Le module précédent partait d'un principe explicite et faux :
« ne bloquer automatiquement que les dangers CONFIRMÉS par une donnée
réellement disponible ; une donnée simplement absente n'est plus un motif de
rejet automatique ».

C'est la raison numéro un pour laquelle des rug pulls passaient le filtre. Un
token fraîchement déployé n'a, par construction, presque aucune donnée
disponible : ni LP lock indexée, ni liste de porteurs, ni simulation de vente.
L'ancienne règle lui accordait donc un laissez-passer sur tout ce qu'on ne
pouvait pas vérifier, puis lui donnait 0,5 sur 1 en score de sécurité — la
même note qu'un token effectivement vérifié et propre. Le rug n'avait pas
besoin de tromper le filtre, il lui suffisait d'être trop jeune pour être
vérifiable.

Nouvelle règle, en trois lignes :
  - danger confirmé            -> REJET définitif, aucun score ne le rachète
  - donnée obligatoire absente -> pas de rejet, mais pas de SIGNAL non plus :
                                  le candidat part en VEILLE et sera re-testé
                                  aux cycles suivants (voir config.TIER_WATCH)
  - tout vérifié et propre     -> SIGNAL possible, sous réserve du score

Le verdict et le score de sécurité sont deux choses distinctes et le restent :
le verdict décide si le token a le droit d'exister comme signal, le score
sert seulement à classer entre eux les tokens qui ont déjà passé le verdict.
"""
import time

import config as cfg
from core.i18n import t

# Verdicts possibles
VERDICT_REJECT = "reject"   # danger confirmé
VERDICT_WATCH = "watch"     # rien de confirmé, mais pas assez vérifiable pour engager
VERDICT_PASS = "pass"       # entièrement vérifié et propre


# =============================================================================
# Catalogue des vetos
# =============================================================================
# Chaque entrée : clé du champ de sécurité -> code de message rendu quand le
# drapeau est CONFIRMÉ à True. Les textes eux-mêmes vivent dans core/i18n.py,
# en français ET en anglais, pour qu'une alerte anglaise ne puisse plus se
# retrouver à afficher une phrase française faute de traduction. L'activation
# individuelle se règle dans config.SECURITY_VETOES.
BOOLEAN_VETOES = {
    # --- Impossible ou coûteux de revendre ---
    "honeypot": "veto.honeypot",
    "cannot_sell_all": "veto.cannot_sell_all",
    "cannot_buy": "veto.cannot_buy",
    "transfer_pausable": "veto.transfer_pausable",
    "trading_cooldown": "veto.trading_cooldown",
    "is_blacklisted": "veto.is_blacklisted",
    "non_transferable": "veto.non_transferable",
    "transfer_hook": "veto.transfer_hook",
    "transfer_hook_upgradable": "veto.transfer_hook_upgradable",
    # --- Le dev peut changer les règles après ton entrée ---
    "is_proxy": "veto.is_proxy",
    "hidden_owner": "veto.hidden_owner",
    "can_take_back_ownership": "veto.can_take_back_ownership",
    "owner_change_balance": "veto.owner_change_balance",
    "selfdestruct": "veto.selfdestruct",
    "slippage_modifiable": "veto.slippage_modifiable",
    "personal_slippage_modifiable": "veto.personal_slippage_modifiable",
    "anti_whale_modifiable": "veto.anti_whale_modifiable",
    "external_call": "veto.external_call",
    "gas_abuse": "veto.gas_abuse",
    "transfer_fee_upgradable": "veto.transfer_fee_upgradable",
    "balance_mutable_authority": "veto.balance_mutable_authority",
    "closable": "veto.closable",
    "default_account_state_upgradable": "veto.default_account_state_upgradable",
    "metadata_mutable": "veto.metadata_mutable",
    # --- Le dev peut encore créer ou geler des tokens ---
    "mint_authority_active": "veto.mint_authority_active",
    "freeze_authority_active": "veto.freeze_authority_active",
    "is_mintable": "veto.is_mintable",
    # --- Réputation ---
    "honeypot_with_same_creator": "veto.honeypot_with_same_creator",
    "fake_token": "veto.fake_token",
    "is_airdrop_scam": "veto.is_airdrop_scam",
}

# Le champ est_open_source fonctionne à l'envers : c'est le False qui est
# dangereux, pas le True.
INVERTED_VETOES = {
    "is_open_source": ("not_open_source", "veto.not_open_source"),
}


def _veto_enabled(name: str) -> bool:
    return bool(cfg.SECURITY_VETOES.get(name, False))


def is_early_candidate(candidate: dict) -> bool:
    """
    True quand le token est trop récent (ou encore en bonding curve) pour qu'on
    puisse LUI reprocher l'absence d'historique de marché.

    Dans cette fenêtre, un volume, un momentum ou une pression acheteuse
    manquants ne sont pas un signal négatif : ils n'existent encore pour
    personne. Sert à deux endroits :
      - core/scoring.py exclut alors ces critères du calcul (au lieu de les
        noter 0,25), pour que le score early reflète uniquement ce qui EST
        vérifiable dès la création (sécurité du contrat, distribution de
        l'offre, liquidité, détention du dev) ;
      - le préfiltre marché ci-dessous assouplit les planchers de liquidité et
        de volume, pour qu'un token neuf parte en VEILLE plutôt qu'au rebut.

    Les vetos DURS (honeypot, dev majoritaire, wash trading, pompe verticale,
    coquille vide face à la capitalisation) restent actifs même dans la fenêtre.
    """
    if candidate.get("is_pump_bonding_curve"):
        return True
    created_ms = candidate.get("pair_created_at")
    if not created_ms:
        return False
    age_min = (time.time() * 1000 - created_ms) / 60_000
    return age_min < cfg.EARLY_DETECTION_WINDOW_MINUTES


def is_first_minute_candidate(candidate: dict) -> bool:
    """
    True quand le token vient tout juste d'être repéré : moins de
    FIRST_MINUTE_WINDOW_MINUTES, ou bonding curve pump.fun sans horodatage
    fiable (le flux temps réel pousse la création avant toute indexation).

    Dans cette fenêtre, le préfiltre marché lève le plancher de liquidité pour
    GARANTIR la détection en VEILLE. Les vetos anti-rug — honeypot, dev
    majoritaire, concentration, autorité de mint, wash trading, pompe verticale
    — restent tous actifs, et la promotion en SIGNAL reste soumise à
    MIN_PAIR_AGE_MINUTES.
    """
    if getattr(cfg, "FIRST_MINUTE_WINDOW_MINUTES", 0) <= 0:
        return False
    created_ms = candidate.get("pair_created_at")
    if not created_ms:
        return bool(candidate.get("is_pump_bonding_curve"))
    age_min = (time.time() * 1000 - created_ms) / 60_000
    return age_min < cfg.FIRST_MINUTE_WINDOW_MINUTES


# =============================================================================
# Évaluation principale
# =============================================================================
def evaluate_security(candidate: dict) -> dict:
    """
    Verdict de sécurité complet pour un candidat.

    Retourne :
      {
        "verdict": "reject" | "watch" | "pass",
        "vetoes":  [str]  dangers confirmés (non négociables),
        "missing": [str]  champs obligatoires non vérifiables,
        "reasons": [str]  explications lisibles, pour l'UI et Telegram,
        "safety_score": float 0-1,
        "distribution_score": float 0-1,
      }
    """
    sec = candidate.get("security") or {}
    chain = candidate.get("chain", "")
    is_bonding_curve = bool(candidate.get("is_pump_bonding_curve"))

    vetoes: list[str] = []
    missing: list[str] = []
    reasons: list[str] = []

    # -- 1. Vetos booléens sur les drapeaux de contrat --------------------
    for field, code in BOOLEAN_VETOES.items():
        if not _veto_enabled(field):
            continue
        if sec.get(field) is True:
            vetoes.append(t(code))

    for field, (veto_name, code) in INVERTED_VETOES.items():
        if not _veto_enabled(veto_name):
            continue
        if sec.get(field) is False:
            vetoes.append(t(code))

    # -- 2. Taxes -----------------------------------------------------------
    buy_tax = sec.get("buy_tax")
    sell_tax = sec.get("sell_tax")
    transfer_fee = sec.get("transfer_fee_pct")
    if buy_tax is not None and buy_tax > cfg.MAX_BUY_TAX_PCT:
        vetoes.append(t("veto.buy_tax", value=f"{buy_tax:.1f}", max=f"{cfg.MAX_BUY_TAX_PCT:.0f}"))
    if sell_tax is not None and sell_tax > cfg.MAX_SELL_TAX_PCT:
        vetoes.append(t("veto.sell_tax", value=f"{sell_tax:.1f}", max=f"{cfg.MAX_SELL_TAX_PCT:.0f}"))
    if transfer_fee is not None and transfer_fee > cfg.MAX_SELL_TAX_PCT:
        vetoes.append(t("veto.transfer_fee", value=f"{transfer_fee:.1f}"))

    # -- 3. Concentration de l'offre ---------------------------------------
    top10 = sec.get("top10_holder_pct")
    if top10 is not None and top10 > cfg.MAX_TOP10_HOLDER_PCT:
        vetoes.append(t("veto.top10", value=f"{top10:.1f}", max=f"{cfg.MAX_TOP10_HOLDER_PCT:.0f}"))

    creator_pct = sec.get("creator_pct")
    if creator_pct is not None and creator_pct > cfg.MAX_CREATOR_PCT:
        vetoes.append(t("veto.creator_pct", value=f"{creator_pct:.1f}", max=f"{cfg.MAX_CREATOR_PCT:.0f}"))

    owner_pct = sec.get("owner_pct")
    if owner_pct is not None and owner_pct > cfg.MAX_OWNER_PCT:
        vetoes.append(t("veto.owner_pct", value=f"{owner_pct:.1f}"))

    dev_pct = candidate.get("dev_holding_pct")
    if dev_pct is not None and dev_pct > cfg.MAX_CREATOR_PCT:
        vetoes.append(t("veto.dev_pct", value=f"{dev_pct:.1f}"))

    largest = candidate.get("largest_holder_pct")
    if largest is not None and largest > cfg.MAX_SINGLE_HOLDER_PCT:
        vetoes.append(t("veto.single_holder", value=f"{largest:.1f}",
                        max=f"{cfg.MAX_SINGLE_HOLDER_PCT:.0f}"))

    insider_pct = candidate.get("insider_pct")
    if insider_pct is not None and insider_pct > cfg.MAX_INSIDER_PCT:
        vetoes.append(t("veto.insider_network", value=f"{insider_pct:.1f}",
                        max=f"{cfg.MAX_INSIDER_PCT:.0f}"))

    # Traction faite par trop peu de wallets : quelques SOL d'achat concentrés sur
    # un ou deux comptes, c'est du wash/bundle, pas de l'intérêt. Ne s'applique
    # qu'aux créations PumpPortal (seules à avoir ce compteur mesuré).
    holder_accounts = candidate.get("holder_accounts")
    if (candidate.get("source") == "pumpportal" and holder_accounts is not None
            and holder_accounts < cfg.EARLY_MIN_HOLDER_ACCOUNTS):
        vetoes.append(t("veto.few_wallets", value=f"{holder_accounts}",
                        min=f"{cfg.EARLY_MIN_HOLDER_ACCOUNTS}"))

    holder_count = sec.get("holder_count")
    min_holders = cfg.chain_threshold("MIN_HOLDER_COUNT", chain)
    if min_holders and holder_count is not None and holder_count < min_holders:
        vetoes.append(t("veto.holder_count", value=f"{holder_count}", min=f"{min_holders}"))

    # -- 4. Verrouillage de la liquidité -----------------------------------
    # Hors bonding curve : une LP non verrouillée est le rug pull le plus
    # simple qui existe, le dev retire la liquidité et le prix va à zéro.
    if not is_bonding_curve:
        lp_locked = sec.get("lp_locked_pct")
        if lp_locked is not None and lp_locked < cfg.MIN_LP_LOCKED_PCT:
            vetoes.append(t("veto.lp_lock", value=f"{lp_locked:.0f}",
                            min=f"{cfg.MIN_LP_LOCKED_PCT:.0f}"))

    # -- 5. Verdict RugCheck (Solana) --------------------------------------
    rug = candidate.get("rugcheck") or {}
    if rug.get("available"):
        for risk in rug.get("critical_risks", []):
            # Le libellé du risque vient de RugCheck, déjà en anglais.
            vetoes.append(t("veto.rugcheck_risk", risk=risk))
        rug_score = rug.get("score")
        if rug_score is not None and rug_score > cfg.MAX_RUGCHECK_SCORE:
            vetoes.append(t("veto.rugcheck_score", value=f"{rug_score}",
                            max=f"{cfg.MAX_RUGCHECK_SCORE}"))
        if rug.get("lp_locked_pct") is not None and not is_bonding_curve:
            if rug["lp_locked_pct"] < cfg.MIN_LP_LOCKED_PCT:
                vetoes.append(t("veto.rugcheck_lp", value=f"{rug['lp_locked_pct']:.0f}"))

    # -- 6. Filtres de marché ----------------------------------------------
    vetoes.extend(_market_vetoes(candidate, is_bonding_curve,
                                 early=is_early_candidate(candidate),
                                 first_minute=is_first_minute_candidate(candidate)))

    # -- 7. Filtre « pas encore DEX Paid » (choix de stratégie) ------------
    if candidate.get("dex_paid") is True:
        vetoes.append(t("veto.dex_paid"))

    if vetoes:
        return {
            "verdict": VERDICT_REJECT,
            "vetoes": vetoes,
            "missing": missing,
            "reasons": vetoes,
            "safety_score": 0.0,
            "distribution_score": 0.0,
        }

    # -- 8. Complétude des vérifications -----------------------------------
    # Aucun danger confirmé. Reste à savoir si l'absence de danger vient d'une
    # vérification réussie, ou simplement du fait qu'on n'a rien pu vérifier.
    missing = _missing_required_fields(candidate, sec, chain, is_bonding_curve)

    safety_score, safety_reasons = compute_safety_score(candidate)
    distribution_score, distribution_reasons = compute_distribution_score(candidate)
    reasons = safety_reasons + distribution_reasons

    if missing and cfg.SECURITY_STRICT_MODE:
        reasons.insert(0, t("missing.header", items=", ".join(missing)))
        return {
            "verdict": VERDICT_WATCH,
            "vetoes": [],
            "missing": missing,
            "reasons": reasons,
            "safety_score": safety_score,
            "distribution_score": distribution_score,
        }

    return {
        "verdict": VERDICT_PASS,
        "vetoes": [],
        "missing": missing,
        "reasons": reasons,
        "safety_score": safety_score,
        "distribution_score": distribution_score,
    }


def creator_reputation_veto(candidate: dict) -> str | None:
    """
    Veto fondé sur le PASSÉ du wallet déployeur (BSC / EVM), pas sur le contrat
    du jour. Renseigné par core/scanner.py juste avant la promotion en SIGNAL, à
    partir de goplus.normalize_address_reputation(). Renvoie le motif de rejet,
    ou None si rien à signaler (y compris quand GoPlus ne connaît pas l'adresse :
    absence d'information n'est pas un feu vert, mais ce n'est pas un veto).
    """
    rep = candidate.get("creator_reputation") or {}
    if not rep.get("available"):
        return None
    n = rep.get("malicious_contracts_created", 0)
    if n > cfg.MAX_CREATOR_MALICIOUS_CONTRACTS:
        return t("veto.creator_known_rugger", count=str(n))
    flags = list(rep.get("criminal_flags") or [])
    if rep.get("honeypot_related"):
        flags.append("honeypot_related_address")
    if flags:
        return t("veto.creator_wallet_flagged", flags=", ".join(flags))
    return None


def early_traction_verdict(candidate: dict) -> tuple[str, str | None]:
    """
    "Ce lancement mérite-t-il une alerte ?" : porte d'IMPORTANCE des créations
    pump.fun, sans aucun appel réseau (l'état de la curve est lu en amont par
    chains.solana.annotate_traction).

    Mesuré en direct : à 30 s, la MÉDIANE d'achat organique (SOL de la curve moins
    l'achat initial du créateur) est de 0,00 SOL ; ~7 % seulement des lancements
    dépassent 3 SOL. Alerter tous ceux dont le contrat est "propre" noyait
    l'utilisateur sous des lancements morts.

    Renvoie ("ok", None), ("wait", motif) : pas assez de traction (pour l'instant)
    ou curve pas encore lisible, ou ("reject", motif) : signature de scam avérée.
    Ne s'applique qu'aux candidats PumpPortal encore en bonding curve.
    """
    if not getattr(cfg, "EARLY_TRACTION_GATE", False):
        return "ok", None
    if candidate.get("source") != "pumpportal" or not candidate.get("is_pump_bonding_curve"):
        return "ok", None

    real_sol = candidate.get("curve_real_sol")
    if real_sol is None:
        return "wait", t("veto.curve_unreadable")
    if candidate.get("curve_complete"):
        return "ok", None   # a déjà migré : la traction est faite

    dev_sol = candidate.get("dev_buy_sol") or 0.0
    # Le créateur a retiré ce qu'il avait mis : la curve contient nettement moins de
    # SOL que son propre achat. Signature de sortie, pas d'un lancement faible.
    if (dev_sol >= cfg.EARLY_DEV_EXIT_MIN_DEV_SOL
            and real_sol < dev_sol * cfg.EARLY_DEV_EXIT_RATIO):
        return "reject", t("veto.dev_exited", dev=f"{dev_sol:.2f}", now=f"{real_sol:.2f}")

    organic = real_sol - dev_sol
    if organic < cfg.EARLY_MIN_ORGANIC_SOL:
        return "wait", t("veto.no_traction", value=f"{max(organic, 0):.2f}",
                         min=f"{cfg.EARLY_MIN_ORGANIC_SOL:.1f}")
    return "ok", None


def evaluate_market_prefilter(candidate: dict) -> list[str]:
    """
    Vetos de marché uniquement, calculables SANS le moindre appel réseau à
    partir des données de découverte (liquidité, volume, âge, capitalisation,
    variation de prix).

    Sert de préfiltre : sur un cycle réel, l'écrasante majorité des candidats
    tombe ici. Les passer d'abord par l'enrichissement de sécurité revenait à
    dépenser un appel GoPlus, trois appels RPC et un appel RugCheck sur des
    tokens éliminés ensuite pour 600 dollars de volume horaire. C'est ce qui
    saturait le RPC public Solana dès qu'un cycle remontait 200 candidats.
    """
    return _market_vetoes(candidate, bool(candidate.get("is_pump_bonding_curve")),
                          early=is_early_candidate(candidate),
                          first_minute=is_first_minute_candidate(candidate))


def _market_vetoes(candidate: dict, is_bonding_curve: bool, early: bool = False,
                   first_minute: bool = False) -> list[str]:
    """
    Filtres de maturité et de crédibilité du marché. Ce ne sont pas des
    vérifications de contrat, mais ils éliminent aussi sûrement : un pool sans
    profondeur réelle se vide au premier ordre, et un volume sans rapport avec
    la liquidité est du wash trading destiné à attirer exactement les bots
    comme celui-ci.
    """
    vetoes = []
    liquidity = candidate.get("liquidity") or 0
    market_cap = candidate.get("market_cap") or 0

    # Seuils adaptés à la chaîne : BNB Chain et Robinhood ont un « fond de
    # marché » bien plus léger que Solana ; sans assouplissement, elles ne
    # remontent jamais rien (voir config.CHAIN_MARKET_OVERRIDES).
    chain = candidate.get("chain")

    def _thr(name):
        return cfg.chain_threshold(name, chain)

    if is_bonding_curve:
        # Sur une bonding curve pump.fun, la « liquidité » est constituée des
        # réserves virtuelles, l'échelle n'est pas comparable à un pool réel.
        # Dans la fenêtre « première minute », le plancher tombe à
        # FIRST_MINUTE_MIN_LIQUIDITY_USD : à la seconde 30 la courbe n'a encore
        # aucune réserve, ce n'est pas un défaut du token, c'est son âge.
        reserves_floor = (cfg.FIRST_MINUTE_MIN_LIQUIDITY_USD
                          if first_minute else 1_500)
        if liquidity < reserves_floor:
            vetoes.append(t("veto.bonding_reserves", value=f"{liquidity:,.0f}"))
        return vetoes

    # Pendant la fenêtre « dès la création », le plancher de liquidité est
    # assoupli : un token neuf mais trop léger part en VEILLE et sera re-testé
    # aux cycles suivants, au lieu d'être rejeté définitivement avant même
    # d'avoir eu le temps d'attirer de la liquidité.
    if first_minute:
        min_liq = cfg.FIRST_MINUTE_MIN_LIQUIDITY_USD
    elif early:
        min_liq = _thr("EARLY_MIN_LIQUIDITY_USD")
    else:
        min_liq = _thr("MIN_LIQUIDITY_USD")
    if liquidity < min_liq:
        vetoes.append(t("veto.liquidity", value=f"{liquidity:,.0f}",
                        min=f"{min_liq:,.0f}"))

    # Profondeur réelle : une capitalisation de 200k$ adossée à 4k$ de
    # liquidité n'a pas de marché, elle a une vitrine.
    if market_cap and liquidity:
        liq_ratio = (liquidity / market_cap) * 100
        min_ratio = _thr("MIN_LIQ_TO_MCAP_PCT")
        if liq_ratio < min_ratio:
            vetoes.append(t("veto.liq_to_mcap", value=f"{liq_ratio:.1f}",
                            mcap=f"{market_cap:,.0f}",
                            min=f"{min_ratio:.0f}"))

    volume_1h = candidate.get("volume_1h")
    if volume_1h is None:
        volume_24h = candidate.get("volume_24h")
        volume_1h = (volume_24h / 24) if volume_24h is not None else None

    if volume_1h is not None:
        # Le plancher de volume ne s'applique pas dans la fenêtre early : un
        # token de dix minutes n'a pas encore de volume, ça ne dit rien de sa
        # qualité. Le veto wash trading, lui, reste actif — un volume ÉNORME
        # sur un pool minuscule dès la première heure est au contraire un
        # signal fort, et négatif.
        min_vol = _thr("MIN_VOLUME_1H_USD")
        if not early and volume_1h < min_vol:
            vetoes.append(t("veto.volume_1h", value=f"{volume_1h:,.0f}",
                            min=f"{min_vol:,.0f}"))
        elif liquidity > 0:
            vol_liq = volume_1h / liquidity
            if vol_liq > cfg.MAX_VOL_TO_LIQ_RATIO:
                vetoes.append(t("veto.wash_trading", value=f"{vol_liq:.0f}"))

    # Pompe parabolique : entrer après une hausse verticale, c'est acheter
    # la position de sortie de ceux qui sont entrés avant. Le contrat peut
    # être irréprochable, le résultat pour l'acheteur tardif est le même
    # qu'un rug. L'ancien scoring, lui, notait cette hausse au maximum.
    change_1h = candidate.get("price_change_1h")
    if change_1h is not None and change_1h > cfg.MAX_PRICE_CHANGE_1H_PCT:
        vetoes.append(t("veto.vertical_pump", value=f"{change_1h:+.0f}",
                        max=f"{cfg.MAX_PRICE_CHANGE_1H_PCT:.0f}"))

    # « A déjà plongé » / « est sur le point de plonger ». Pendant la fenêtre
    # « première minute », le token n'a par construction aucune variation multi-
    # fenêtres exploitable : on ne teste ces motifs qu'ensuite (données réelles).
    if not first_minute:
        vetoes.extend(_dump_vetoes(candidate))

    created_ms = candidate.get("pair_created_at")
    if created_ms:
        age_h = (time.time() * 1000 - created_ms) / 3_600_000
        max_age_h = cfg.chain_max_pair_age_hours(chain)
        if age_h > max_age_h:
            # Une décimale sous 10 h : « 6h > 6h » (âge réel 6,3 h arrondi) se
            # lisait comme un rejet absurde dans les journaux.
            vetoes.append(t("veto.pair_too_old",
                            value=f"{age_h:.1f}" if age_h < 10 else f"{age_h:.0f}",
                            max=f"{max_age_h:.0f}"))

    return vetoes


def _dump_vetoes(candidate: dict) -> list[str]:
    """
    Répond à la question « le token a-t-il déjà plongé, ou est-il sur le point
    de plonger ? ». Trois signatures, toutes traitées comme des vetos (et
    surtout journalisées) :

      1. A DÉJÀ PLONGÉ  — variation 24h ou 6h fortement négative : le mouvement
         est fait, on n'entre pas sur un cadavre.
      2. RETOURNEMENT AMORCÉ — pompe 1h nette SUIVIE d'une chute 5 min nette :
         le sommet est passé, la descente commence.
      3. DISTRIBUTION ACTIVE — ventes très majoritaires sur 1h AVEC prix en
         baisse : les premiers entrants sortent sur les suivants.
    """
    vetoes: list[str] = []
    ch_24h = candidate.get("price_change_24h")
    ch_6h = candidate.get("price_change_6h")
    ch_1h = candidate.get("price_change_1h")
    ch_5m = candidate.get("price_change_5m")

    # 1. A déjà plongé.
    if ch_24h is not None and ch_24h <= cfg.DUMP_PRICE_CHANGE_24H_PCT:
        vetoes.append(t("veto.already_dumped", window="24h",
                        value=f"{ch_24h:+.0f}"))
    elif ch_6h is not None and ch_6h <= cfg.DUMP_PRICE_CHANGE_6H_PCT:
        vetoes.append(t("veto.already_dumped", window="6h",
                        value=f"{ch_6h:+.0f}"))

    # 2. Retournement amorcé (sommet passé).
    if (ch_1h is not None and ch_5m is not None
            and ch_1h >= cfg.DUMP_ROLLOVER_PUMP_1H_PCT
            and ch_5m <= cfg.DUMP_ROLLOVER_DROP_5M_PCT):
        vetoes.append(t("veto.dump_rollover", pump=f"{ch_1h:.0f}",
                        drop=f"{ch_5m:+.0f}"))

    # 3. Distribution active : ventes >> achats ET prix qui baisse.
    buys = candidate.get("buys_h1")
    sells = candidate.get("sells_h1")
    if (buys is not None and sells is not None and buys >= 0 and sells > 0
            and sells >= max(1, buys) * cfg.DUMP_SELL_DOMINANCE_RATIO
            and ch_1h is not None and ch_1h < 0):
        vetoes.append(t("veto.dump_distribution", sells=str(sells),
                        buys=str(buys), change=f"{ch_1h:+.0f}"))

    return vetoes


def _missing_required_fields(candidate: dict, sec: dict, chain: str, is_bonding_curve: bool) -> list[str]:
    """
    Liste des vérifications obligatoires qu'on n'a PAS pu effectuer.
    Non vide = le candidat ne peut pas devenir un SIGNAL (il part en VEILLE).
    """
    missing = []

    if not sec.get("data_available"):
        reason = sec.get("unavailable_reason") or t("unavailable.generic")
        return [t("missing.security_data", reason=reason)]

    profile = "solana" if chain == "solana" else "evm"
    required = cfg.REQUIRED_SECURITY_FIELDS.get(profile, [])

    for field in required:
        # La LP n'existe pas encore sur une bonding curve : ce n'est pas une
        # donnée manquante, c'est une donnée sans objet.
        if is_bonding_curve and field == "lp_locked_pct":
            continue
        if sec.get(field) is None:
            missing.append(t(f"missing.{field}"))

    # Profil « quality » : un vrai candidat (potentiel liste Alpha) a des
    # centaines de porteurs, et cette donnée DOIT être connue pour l'affirmer.
    # Une source qui ne renvoie pas le nombre de détenteurs = pas de SIGNAL.
    if cfg.chain_threshold("REQUIRE_HOLDER_COUNT", chain) and sec.get("holder_count") is None:
        missing.append(t("missing.holder_count"))

    # Un token en bonding curve n'a par définition ni LP, ni historique, ni
    # distribution stabilisée. Il ne peut pas être « vérifié » au sens strict :
    # il reste en VEILLE tant qu'il n'a pas migré vers un pool réel. C'est le
    # seul traitement honnête de ce segment, qui concentre l'essentiel des rugs.
    if is_bonding_curve:
        missing.append(t("missing.bonding_curve"))

    # Un token de moins de MIN_PAIR_AGE_MINUTES n'a été indexé nulle part :
    # les réponses « rien à signaler » ne prouvent rien à ce stade.
    created_ms = candidate.get("pair_created_at")
    if created_ms:
        age_min = (time.time() * 1000 - created_ms) / 60_000
        if age_min < cfg.MIN_PAIR_AGE_MINUTES:
            missing.append(t("missing.pair_too_young", minutes=f"{age_min:.0f}"))

    return missing


# =============================================================================
# Scores continus (classement, pas décision)
# =============================================================================
def compute_safety_score(candidate: dict) -> tuple[float, list[str]]:
    """
    Score continu 0-1 de sécurité du CONTRAT.

    Différence de fond avec la version précédente : une donnée manquante ne
    vaut plus 0,5 (« neutre ») mais 0,25 (« non vérifié »). Un token vérifié
    et propre doit se détacher nettement d'un token dont on ne sait rien,
    sinon le score ne mesure plus rien.
    """
    sec = candidate.get("security") or {}
    is_bonding_curve = bool(candidate.get("is_pump_bonding_curve"))
    UNKNOWN = 0.25

    reasons: list[str] = []
    parts: list[float] = []

    # --- Verrouillage de la liquidité ---
    if is_bonding_curve:
        parts.append(0.5)
        reasons.append(t("reason.bonding_curve_reserves"))
    else:
        lp_locked = sec.get("lp_locked_pct")
        if lp_locked is None:
            parts.append(UNKNOWN)
            reasons.append(t("reason.lp_not_verifiable"))
        else:
            parts.append(min(lp_locked / 100, 1.0))
            if lp_locked >= 95:
                reasons.append(t("reason.lp_locked_burned", value=f"{lp_locked:.0f}"))
            else:
                reasons.append(t("reason.lp_locked", value=f"{lp_locked:.0f}"))

    # --- Autorités révoquées ---
    # La freeze authority est un concept Solana : sur une chaîne EVM elle vaut
    # toujours None et ne doit pas compter comme « non vérifiée », sinon tout
    # token EVM était pénalisé pour une donnée qui ne le concerne pas.
    mint = sec.get("mint_authority_active")
    freeze = sec.get("freeze_authority_active")
    authority_values = [mint, freeze] if candidate.get("chain") == "solana" else [mint]
    authority_parts = [1.0 if v is False else (UNKNOWN if v is None else 0.0) for v in authority_values]
    parts.append(sum(authority_parts) / len(authority_parts))
    if mint is False and freeze is False:
        reasons.append(t("reason.mint_freeze_revoked"))
    elif mint is False:
        reasons.append(t("reason.mint_revoked"))

    # --- Taxes ---
    buy_tax, sell_tax = sec.get("buy_tax"), sec.get("sell_tax")
    if buy_tax is not None and sell_tax is not None:
        worst = max(buy_tax, sell_tax)
        parts.append(max(0.0, 1.0 - worst / cfg.MAX_SELL_TAX_PCT))
        if worst == 0:
            reasons.append(t("reason.no_taxes"))
        else:
            reasons.append(t("reason.taxes", buy=f"{buy_tax:.1f}", sell=f"{sell_tax:.1f}"))
    elif candidate.get("chain") != "solana":
        parts.append(UNKNOWN)
        reasons.append(t("reason.taxes_not_verifiable"))

    # --- Code source ---
    is_open = sec.get("is_open_source")
    if is_open is True:
        parts.append(1.0)
        reasons.append(t("reason.source_verified"))
    elif is_open is None and candidate.get("chain") != "solana":
        parts.append(UNKNOWN)

    # --- Vendabilité réellement vérifiée ---
    # Sur Solana, GoPlus ne simule aucun achat/vente : sans second avis
    # RugCheck, la capacité à revendre n'est tout simplement pas vérifiée. Ne
    # rien dire de ce critère reviendrait à l'omettre du score, donc à noter le
    # token comme si la question ne se posait pas.
    rug = candidate.get("rugcheck") or {}
    if candidate.get("chain") == "solana":
        if rug.get("available"):
            parts.append(1.0 if not rug.get("risks") else 0.7)
        else:
            parts.append(UNKNOWN)
            reasons.append(t("reason.sellability_unverified"))

        # Métadonnées modifiables : un token mort peut être renommé et
        # recyclé en « nouveau projet » sans changer de contrat.
        metadata_mutable = sec.get("metadata_mutable")
        if metadata_mutable is False:
            parts.append(1.0)
        elif metadata_mutable is None:
            parts.append(UNKNOWN)

    # --- Verdict externe RugCheck ---
    if rug.get("available") and rug.get("score") is not None:
        parts.append(max(0.0, 1.0 - rug["score"] / cfg.MAX_RUGCHECK_SCORE))
        reasons.append(t("reason.rugcheck_score", value=f"{rug['score']}"))

    score = sum(parts) / len(parts) if parts else UNKNOWN

    if candidate.get("dex_paid") is False:
        reasons.append(t("reason.not_dex_paid"))

    return score, reasons


def compute_distribution_score(candidate: dict) -> tuple[float, list[str]]:
    """
    Score continu 0-1 de la répartition de l'offre. Séparé de la sécurité du
    contrat parce que ce sont deux risques différents : un contrat parfait dont
    trois wallets détiennent 70% de l'offre reste un rug qui n'attend que son
    heure.
    """
    sec = candidate.get("security") or {}
    UNKNOWN = 0.25
    reasons: list[str] = []
    parts: list[float] = []

    top10 = sec.get("top10_holder_pct")
    if top10 is None:
        parts.append(UNKNOWN)
        reasons.append(t("reason.holders_not_verifiable"))
    else:
        parts.append(max(0.0, (cfg.MAX_TOP10_HOLDER_PCT - top10) / cfg.MAX_TOP10_HOLDER_PCT))
        if top10 < 10:
            reasons.append(t("reason.supply_well_spread", value=f"{top10:.1f}"))
        else:
            reasons.append(t("reason.top10", value=f"{top10:.1f}"))

    creator_pct = sec.get("creator_pct") if sec.get("creator_pct") is not None else candidate.get("dev_holding_pct")
    if creator_pct is None:
        parts.append(UNKNOWN)
    else:
        parts.append(max(0.0, (cfg.MAX_CREATOR_PCT - creator_pct) / cfg.MAX_CREATOR_PCT))
        if creator_pct <= 1:
            reasons.append(t("reason.creator_holds_nothing"))
        else:
            reasons.append(t("reason.creator_holds", value=f"{creator_pct:.1f}"))

    holder_count = sec.get("holder_count")
    if holder_count is not None:
        parts.append(min(holder_count / 500, 1.0))
        reasons.append(t("reason.holder_count", value=f"{holder_count}"))

    return (sum(parts) / len(parts) if parts else UNKNOWN), reasons


# =============================================================================
# Compatibilité ascendante
# =============================================================================
def evaluate_rejection(candidate: dict) -> tuple[bool, list[str]]:
    """
    Ancienne interface, conservée pour ne rien casser dans le code appelant et
    dans les tests existants. Un verdict VEILLE n'est PAS un rejet ici : c'est
    core/scoring.py et core/scanner.py qui décident quoi en faire.
    """
    verdict = evaluate_security(candidate)
    return verdict["verdict"] == VERDICT_REJECT, verdict["reasons"]
