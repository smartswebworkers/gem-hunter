"""
core/risk_management.py — Position sizing, Stop Loss, TP1-3, Trailing Stop, R/R
Destination finale : gem_hunter/core/risk_management.py
"""
import logging

from config import RISK_DEFAULTS, MODES

logger = logging.getLogger("gem_hunter.risk")


def compute_risk_plan(candidate: dict, mode: str = "safe", overrides: dict | None = None) -> dict:
    """
    Calcule le plan de risque complet pour un signal donné.
    `mode` : "safe" ou "aggressive" (change la taille de position max).
    `overrides` : dict optionnel pour surcharger RISK_DEFAULTS depuis l'UI.
    """
    params = {**RISK_DEFAULTS, **(overrides or {})}
    mode_cfg = MODES.get(mode, MODES["safe"])

    entry_price = candidate.get("price_usd")
    if entry_price is None:
        return {"error": "Prix d'entrée indisponible — plan de risque non calculable."}
    entry_price = float(entry_price)

    account_size = params["account_size_usd"]
    risk_pct = params["risk_per_trade_pct"] / 100
    sl_pct = params["stop_loss_pct"] / 100

    risk_amount_usd = account_size * risk_pct
    stop_loss_price = entry_price * (1 - sl_pct)

    # Position size = montant risqué / distance au stop (en %)
    risk_based_size_usd = risk_amount_usd / sl_pct if sl_pct > 0 else 0
    max_position_usd = account_size * (mode_cfg["max_position_pct"] / 100)
    position_size_usd = min(risk_based_size_usd, max_position_usd)

    # CORRECTIF (transparence, pas de changement de comportement) : avec les
    # valeurs par défaut (risk_per_trade_pct=1%, stop_loss_pct=15%,
    # mode safe max_position_pct=0.5%), le plafond max_position_usd (5$ sur un
    # compte de 1000$) est TOUJOURS plus restrictif que le sizing basé sur le
    # risque (66,7$) — dès que stop_loss_pct dépasse environ 2x
    # max_position_pct. Le réglage "risque X% par trade" choisi par
    # l'utilisateur devient alors décoratif : le risque réellement pris est
    # max_position_usd * sl_pct, pas risk_amount_usd. On expose maintenant le
    # chiffre réel (effective_risk_usd/pct) au lieu de le laisser caché, et on
    # logue un avertissement une fois par appel quand c'est le cas.
    capped_by_max_position = position_size_usd < risk_based_size_usd
    effective_risk_usd = position_size_usd * sl_pct
    effective_risk_pct = (effective_risk_usd / account_size * 100) if account_size > 0 else 0
    if capped_by_max_position:
        logger.debug(
            f"Position plafonnée par max_position_pct du mode '{mode}' : "
            f"risque réellement pris {effective_risk_pct:.3f}% du capital "
            f"(vs {params['risk_per_trade_pct']:.2f}% visé)."
        )

    tp1 = entry_price * (1 + params["tp1_pct"] / 100)
    tp2 = entry_price * (1 + params["tp2_pct"] / 100)
    tp3 = entry_price * (1 + params["tp3_pct"] / 100)
    trailing_stop_pct = params["trailing_stop_pct"]

    reward_tp1 = entry_price * (params["tp1_pct"] / 100)
    risk_per_unit = entry_price * sl_pct
    risk_reward_ratio = round(reward_tp1 / risk_per_unit, 2) if risk_per_unit > 0 else None

    return {
        "entry_price": entry_price,
        "position_size_usd": round(position_size_usd, 2),
        "stop_loss": round(stop_loss_price, 8),
        "tp1": round(tp1, 8),
        "tp2": round(tp2, 8),
        "tp3": round(tp3, 8),
        "trailing_stop_pct": trailing_stop_pct,
        "risk_reward_ratio": risk_reward_ratio,
        "risk_amount_usd": round(risk_amount_usd, 2),
        "effective_risk_usd": round(effective_risk_usd, 2),
        "effective_risk_pct": round(effective_risk_pct, 3),
        "capped_by_max_position": capped_by_max_position,
        "mode": mode,
    }
