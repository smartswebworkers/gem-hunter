"""
core/scoring.py — Calcul du score de confiance (0-100)
Destination finale : gem_hunter/core/scoring.py

RÉÉCRITURE — LE SCORE RÉCOMPENSAIT LA SIGNATURE DU RUG PULL.

Ancienne pondération : momentum 5m (10) + momentum 1h (15) + pression
acheteuse (15) + ratio volume/liquidité (20) = 60 points sur 100 pour des
critères qu'un pump-and-dump maximise par construction. Sécurité : 10 points.
Un token en pleine pompe artificielle, poussé par des bots qui se le revendent
entre eux, cochait donc les 60 points, et l'absence de toute donnée de sécurité
lui rapportait encore 5 points sur 10 au titre du « neutre ». Le bot ne se
faisait pas tromper par les rug pulls, il les cherchait.

Trois changements de fond :

1. La sécurité et la distribution de l'offre pèsent 44 points sur 100, contre
   10 auparavant, et l'auto-correction n'a pas le droit d'y toucher
   (config.PINNED_SCORE_WEIGHTS).

2. Le momentum est plafonné dans les deux sens. Au-delà d'un certain seuil,
   une hausse n'est plus un signal de qualité mais un signal de risque : un
   token qui fait +900% en cinq minutes n'est pas neuf fois meilleur qu'un
   token qui fait +100%, il est surtout beaucoup plus près de son sommet.
   La courbe est donc en cloche, pas croissante.

3. Une donnée manquante ne vaut plus 0,5 (« neutre ») mais 0,25 (« non
   vérifié »). C'est le point qui, à lui seul, permettait à un token dont on
   ne savait rien d'obtenir le même score qu'un token vérifié et propre.
"""
import math

import config as cfg
from core.i18n import t
from core.security_checks import (
    evaluate_security, is_early_candidate, is_first_minute_candidate,
    VERDICT_REJECT, VERDICT_WATCH,
)
from data_sources.nansen import compute_smart_money_signal, supports_chain as _nansen_supports_chain

UNKNOWN = 0.25  # note attribuée à un critère non vérifiable


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _bell_momentum(change_pct: float | None, peak_pct: float, tolerance: float) -> float:
    """
    Note de momentum en cloche asymétrique, culminant à `peak_pct`.

    Trois régimes :
      - baisse            : note décroissante, -100% donne 0,
      - hausse mesurée    : montée linéaire de 0,5 (stable) vers 1,0 (au pic),
      - hausse verticale  : décroissance rapide vers un plancher de 0,15.

    C'est le point de fond : au-delà d'un certain seuil, une hausse n'est plus
    un signal de qualité mais un signal de risque. Un token à +900% en une
    heure n'est pas neuf fois meilleur qu'un token à +100%, il est surtout
    beaucoup plus près de son sommet, et l'acheteur qui entre là est celui sur
    qui les premiers vendent. L'ancienne formule (0.5 + variation) donnait au
    contraire la note maximale à toute hausse supérieure à 50%, ce qui revenait
    à noter la pompe elle-même comme un gage de qualité.
    """
    if change_pct is None:
        return UNKNOWN
    if change_pct <= 0:
        return _clamp01(0.5 + change_pct / 200)
    if change_pct < peak_pct:
        return _clamp01(0.5 + 0.5 * (change_pct / peak_pct))
    distance = (change_pct - peak_pct) / tolerance
    return _clamp01(max(0.15, math.exp(-distance * distance)))


def extract_features(candidate: dict, security: dict | None = None) -> tuple[dict, list[str]]:
    """
    Construit les features continues 0-1 du candidat et les explications
    associées. Retourne (features, raisons).

    `security` peut être fourni par l'appelant pour éviter de refaire
    l'évaluation complète (compute_score l'a déjà calculée).
    """
    security = security or evaluate_security(candidate)

    # Dès la création, volume / momentum / pression acheteuse n'existent pas
    # encore — et pas seulement pour CE token. Les noter 0,25 (« non vérifié »)
    # revenait à condamner tout token frais à un score sous le seuil, donc à
    # rendre la VEILLE aveugle à la fenêtre qu'elle est censée couvrir. Dans
    # cette fenêtre, un critère de marché ABSENT est donc exclu du calcul (mis
    # à None, exactement comme le smart money quand Nansen n'est pas branché),
    # pas noté bas. Un critère PRÉSENT est évalué normalement, y compris à la
    # baisse : une pompe verticale reste une pompe verticale.
    early = is_early_candidate(candidate)
    first_minute = is_first_minute_candidate(candidate)

    liquidity = candidate.get("liquidity") or 0
    market_cap = candidate.get("market_cap") or 0

    volume_1h = candidate.get("volume_1h")
    volume_24h = candidate.get("volume_24h")
    volume = volume_1h if volume_1h is not None else (volume_24h / 24 if volume_24h is not None else None)

    reasons: list[str] = []

    # --- Liquidité absolue ---
    if liquidity:
        liquidity_score = _clamp01(math.log10(max(liquidity, 1)) / 6)
    elif first_minute:
        # À la seconde 30, la bonding curve n'a de réserve pour PERSONNE : la
        # noter 0,25 reviendrait à pénaliser le token pour son âge. Exclue du
        # calcul (comme volume / momentum en fenêtre early), son poids est
        # réparti sur les critères réellement vérifiables (sécurité,
        # distribution). Hors fenêtre, une liquidité nulle reste « non vérifié ».
        liquidity_score = None
    else:
        liquidity_score = UNKNOWN
    if liquidity >= 50_000:
        reasons.append(t("reason.solid_liquidity", value=f"{liquidity:,.0f}"))

    # --- Profondeur réelle : liquidité rapportée à la capitalisation ---
    if market_cap and liquidity:
        liq_ratio_pct = (liquidity / market_cap) * 100
        # 20% de la capitalisation en liquidité est excellent, le plancher
        # dépend de la chaîne (assoupli sur Robinhood, voir config).
        min_liq_ratio = cfg.chain_threshold("MIN_LIQ_TO_MCAP_PCT", candidate.get("chain"))
        liquidity_quality = _clamp01((liq_ratio_pct - min_liq_ratio) / 17)
        if liq_ratio_pct >= 15:
            reasons.append(t("reason.deep_pool", value=f"{liq_ratio_pct:.0f}"))
    else:
        liquidity_quality = UNKNOWN

    # --- Traction réelle, désormais pénalisée dans les DEUX sens ---
    # L'ancienne formule était monotone croissante : plus le volume dépassait
    # la liquidité, meilleure était la note, plafonnée à 5x. Or un volume
    # horaire à 20 fois la liquidité n'est pas vingt fois plus convaincant
    # qu'un volume à 1 fois, c'est le contraire : personne n'échange autant sur
    # un pool aussi mince sans que ce soit des bots qui se revendent le token
    # entre eux. La note est donc en cloche, sur une échelle logarithmique
    # parce que ce ratio s'étale sur plusieurs ordres de grandeur.
    if volume is not None and liquidity > 0:
        ratio = volume / liquidity
        if ratio > cfg.MAX_VOL_TO_LIQ_RATIO:
            vol_liq_ratio = 0.0
            reasons.append(t("reason.wash_trading", value=f"{ratio:.0f}"))
        elif ratio <= 0:
            vol_liq_ratio = 0.0
            reasons.append(t("reason.no_volume"))
        else:
            # Optimum autour de 0,8x de la liquidité échangée en une heure :
            # assez pour prouver un intérêt réel, pas assez pour ressembler à
            # du volume fabriqué.
            d = (math.log10(ratio) - math.log10(0.8)) / 0.7
            vol_liq_ratio = _clamp01(math.exp(-d * d))
            if 0.3 <= ratio <= 3:
                reasons.append(t("reason.healthy_vol_liq", value=f"{ratio:.2f}"))
            elif ratio < 0.1:
                reasons.append(t("reason.very_low_volume", value=f"{ratio:.2f}"))
    else:
        # Volume/liquidité pas encore calculable : exclu du score dans la
        # fenêtre early, sinon noté « non vérifié ».
        vol_liq_ratio = None if early else UNKNOWN

    # --- Momentum en cloche ---
    # Absent + fenêtre early => exclu (None). Absent hors fenêtre => _bell_momentum
    # renvoie UNKNOWN. Présent => évalué, dans les deux sens.
    raw_5m = candidate.get("price_change_5m")
    raw_1h = candidate.get("price_change_1h")
    momentum_5m = None if (raw_5m is None and early) else _bell_momentum(raw_5m, peak_pct=15, tolerance=25)
    momentum_1h = None if (raw_1h is None and early) else _bell_momentum(raw_1h, peak_pct=40, tolerance=60)
    h1 = candidate.get("price_change_1h")
    if h1 is not None:
        if h1 > 300:
            reasons.append(t("reason.vertical_1h", value=f"{h1:+.0f}"))
        elif h1 > 15:
            reasons.append(t("reason.momentum_positive_1h", value=f"{h1:+.0f}"))
        elif h1 < -15:
            reasons.append(t("reason.momentum_negative_1h", value=f"{h1:+.0f}"))

    # --- Pression acheteuse, plafonnée ---
    buys = candidate.get("buys_h1")
    sells = candidate.get("sells_h1")
    if buys is None and sells is None:
        buy_pressure = None if early else UNKNOWN
    else:
        buys, sells = buys or 0, sells or 0
        total_tx = buys + sells
        if total_tx < 20:
            # Trop peu de transactions pour que le ratio veuille dire quoi que
            # ce soit : trois achats sur trois transactions ne sont pas « 100%
            # de pression acheteuse ». Dès la création, c'est attendu — exclu ;
            # sinon, « non vérifié ».
            buy_pressure = None if early else UNKNOWN
        else:
            raw = buys / total_tx
            # Au-delà de 90% d'achats, ce n'est plus de l'enthousiasme, c'est
            # une absence de vendeurs — typique d'un honeypot ou d'un carnet
            # entièrement tenu par des bots. La pénalité est franche : 97%
            # d'achats doit noter plus bas qu'un marché équilibré, pas presque
            # aussi haut qu'un marché sain.
            buy_pressure = raw if raw <= 0.9 else _clamp01(0.9 - (raw - 0.9) * 8)
            if raw > 0.9:
                reasons.append(t("reason.almost_no_sells", value=f"{raw*100:.0f}"))
            elif raw > 0.65:
                reasons.append(t("reason.strong_buy_pressure", value=f"{raw*100:.0f}"))

    # --- Smart money ---
    smart_money, smart_money_reasons = _smart_money_feature(candidate)
    reasons.extend(smart_money_reasons)

    features = {
        "safety": security["safety_score"],
        "holder_distribution": security["distribution_score"],
        "liquidity_score": liquidity_score,
        "liquidity_quality": liquidity_quality,
        "smart_money": smart_money,
        "vol_liq_ratio": vol_liq_ratio,
        "momentum_1h": momentum_1h,
        "buy_pressure": buy_pressure,
        "momentum_5m": momentum_5m,
    }
    return features, security["reasons"] + reasons


def _smart_money_feature(candidate: dict) -> tuple[float, list[str]]:
    signal = compute_smart_money_signal(candidate.get("chain"), candidate.get("contract"))
    reasons: list[str] = []

    if not signal["available"]:
        # Pas de clé Nansen configurée : le critère n'est mesurable pour AUCUN
        # token. On le retire du calcul (valeur None) au lieu de le noter bas.
        #
        # La distinction est importante et vaut pour tout le scoring : une
        # donnée manquante POUR CE TOKEN est un signal négatif, parce que les
        # autres tokens, eux, sont vérifiables. Une donnée manquante POUR TOUT
        # LE MONDE n'apprend rien sur ce token en particulier : la pénaliser
        # reviendrait juste à rendre le seuil d'achat inatteignable, ce qui
        # pousserait tôt ou tard à le baisser — et à rouvrir la porte.
        return None, []

    if signal["holder_count"] <= 0:
        # Appel réussi, aucun smart money détecté. Sur un token établi c'est
        # une info (plutôt une absence de validation) : on note 0,35. Sur un
        # token qui vient de naître, aucun smart money n'a encore EU le temps
        # d'entrer : ça n'apprend rien, on exclut le critère.
        return (None if is_early_candidate(candidate) else 0.35), []

    holder_factor = min(signal["holder_count"] / 5, 1.0)
    ownership_factor = min(signal["combined_ownership_pct"] / 10, 1.0)
    inflow_bonus = 0.2 if signal["net_inflow_positive"] else 0.0
    buyers_bonus = min(signal.get("buyers_count", 0) / 3, 1.0) * 0.2
    score = _clamp01(0.4 * holder_factor + 0.2 * ownership_factor + inflow_bonus + buyers_bonus)

    buyers = signal.get("buyers", [])
    if buyers:
        buyers_str = ", ".join(f"{b['address']} ({b['ownership_pct']}%)" for b in buyers)
        reasons.append(t("reason.smart_money_buying",
                         count=f"{signal['buyers_count']}", wallets=buyers_str))
    else:
        reasons.append(t("reason.smart_money_holding",
                         count=f"{signal['holder_count']}",
                         value=f"{signal['combined_ownership_pct']}"))
    return score, reasons


def compute_score(candidate: dict) -> dict:
    """
    Évalue un candidat et retourne un dict enrichi avec :
      - score (float 0-100)
      - rejected (bool)         : veto de sécurité, définitif
      - tier ("signal"|"veille"): niveau d'information atteint
      - verified (bool)         : toutes les vérifications obligatoires ont abouti
      - reasons (list[str])
      - features (dict)         : exposé pour l'auto-correction (self_tuning.py)
    """
    security = evaluate_security(candidate)

    if security["verdict"] == VERDICT_REJECT:
        return {
            **candidate,
            "score": 0.0,
            "rejected": True,
            "tier": None,
            "verified": False,
            "reasons": security["reasons"],
            "vetoes": security["vetoes"],
        }

    features, reasons = extract_features(candidate, security)

    # Une feature à None n'est mesurable pour aucun token (voir
    # _smart_money_feature) : elle est retirée du numérateur ET du
    # dénominateur, ce qui revient à répartir son poids sur les critères
    # réellement disponibles. À ne pas confondre avec une feature à 0,25, qui
    # signifie « mesurable en principe, mais pas vérifiée sur CE token » et
    # doit bien peser négativement.
    weights = cfg.SCORE_WEIGHTS
    applicable = {k: v for k, v in features.items() if v is not None and k in weights}
    total_weight = sum(weights[k] for k in applicable) or 1
    weighted_sum = sum(applicable[k] * weights[k] for k in applicable)
    score = (weighted_sum / total_weight) * 100

    missing = list(security["missing"])
    is_verified = security["verdict"] != VERDICT_WATCH

    # --- Règle du maillon faible ---
    # Un score global élevé peut masquer une faiblesse sur le seul critère qui
    # compte vraiment. On vérifie donc les deux critères de risque
    # individuellement, en plus de la moyenne.
    if features["safety"] < cfg.MIN_SAFETY_FEATURE:
        is_verified = False
        missing.append(t("missing.safety_feature",
                         value=f"{features['safety']:.2f}",
                         min=f"{cfg.MIN_SAFETY_FEATURE}"))
    if features["holder_distribution"] < cfg.MIN_DISTRIBUTION_FEATURE:
        is_verified = False
        missing.append(t("missing.distribution_feature",
                         value=f"{features['holder_distribution']:.2f}",
                         min=f"{cfg.MIN_DISTRIBUTION_FEATURE}"))

    # --- Profil « quality » : présence de smart money exigée ---
    # Le meilleur signal disponible gratuitement pour « ce token a l'attention
    # d'acteurs sérieux » (et donc un potentiel de liste). Absent ou trop
    # faible => le token reste en VEILLE (donc non publié en profil quality).
    if getattr(cfg, "REQUIRE_SMART_MONEY", False) and _nansen_supports_chain(candidate.get("chain")):
        sm = features.get("smart_money")
        nansen_configured = bool(cfg.API_KEYS.get("nansen"))
        if sm is None:
            # Nansen n'a rien renvoyé pour CE token. Si une clé est configurée,
            # c'est une donnée manquante à re-tester => VEILLE. Sinon, on ne
            # peut pas exiger ce qu'on ne peut pas mesurer : ne pas condamner
            # définitivement le tier SIGNAL faute de clé.
            if nansen_configured:
                is_verified = False
                missing.append(t("missing.smart_money"))
        elif sm < getattr(cfg, "MIN_SMART_MONEY_FEATURE", 0.45):
            # Nansen a répondu et ne voit pas de smart money positionné.
            is_verified = False
            missing.append(t("missing.smart_money"))

    tier = cfg.TIER_SIGNAL if is_verified else cfg.TIER_WATCH

    return {
        **candidate,
        "score": round(score, 1),
        "rejected": False,
        "tier": tier,
        "verified": is_verified,
        "missing_checks": missing,
        "reasons": reasons,
        "vetoes": [],
        "features": features,
    }
