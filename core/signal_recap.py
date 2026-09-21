"""
core/signal_recap.py — Recap narratif d'un token détecté, pour les réseaux sociaux
Destination finale : gem_hunter/core/signal_recap.py

L'utilisateur clique sur un token dans le dashboard et récupère un paragraphe
rédigé, prêt à coller, dans la langue d'affichage (fr / en / zh). Le texte
raconte : cap d'entrée + horodatage + fuseau, conviction, nombre de signaux et
de vérifications, confiance X/10, pic atteint (cap + date + %/multiple/durée),
le parcours SCOUT -> MAIN, l'activité smart money connue, la rareté du signal
dans le corpus (« 1 signal validé tous les N cycles »), la moyenne de la
cohorte, et une clause honnête sur ce que le bot ne mesure pas.

Aucune donnée n'est inventée : chaque phrase dont les données manquent est soit
omise, soit remplacée par un constat explicite dans la clause de prudence. Le
suivi du pic est alimenté par core/performance_tracker.run_peak_cycle().
"""
from __future__ import annotations

from datetime import datetime

import config as cfg
from core.telegram_alerts import DISCLAIMER
from storage import db

SUPPORTED_LANGS = ("fr", "en", "zh")
_DEFAULT_LANG = "fr"

# Clés de features produites par core/scoring.py (valeurs 0..1).
_FEATURE_KEYS = (
    "safety", "holder_distribution", "liquidity_score", "liquidity_quality",
    "smart_money", "vol_liq_ratio", "momentum_1h", "buy_pressure", "momentum_5m",
)
_SIGNAL_FIRED_THRESHOLD = 0.6
_SIGNAL_STRONG_THRESHOLD = 0.8

_MONTHS = {
    "fr": ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août",
           "sept.", "oct.", "nov.", "déc."],
    "en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug",
           "Sep", "Oct", "Nov", "Dec"],
    "zh": ["1月", "2月", "3月", "4月", "5月", "6月", "7月", "8月",
           "9月", "10月", "11月", "12月"],
}

_DUR_UNITS = {
    "fr": {"d": " j", "h": " h", "m": " min"},
    "en": {"d": "d", "h": "h", "m": "m"},
    "zh": {"d": "天", "h": "小时", "m": "分钟"},
}

_DUR_SUBMINUTE = {"fr": "moins d'une minute", "en": "under a minute", "zh": "不到一分钟"}

# Fragments de phrase. {placeholders} remplis via str.format.
# Ton volontairement humain, pas un rapport technique : c'est un texte pensé
# pour être publié tel quel sur les réseaux sociaux. Aucune virgule cadratin
# (—) n'est utilisée nulle part ici, sur demande explicite : la ponctuation
# reste la virgule, plus naturelle à lire dans un post.
_TX = {
    "opener": {
        "fr": "Jetons un coup d'œil sur notre trouvaille du jour !",
        "en": "Let's take a look at today's find!",
        "zh": "一起来看看我们今天的发现吧！",
    },
    "header": {
        "fr": "{ticker} ({name}) sur {chain}, repéré le {detected}, avec une capitalisation d'entrée de ${entry_cap}.",
        "en": "{ticker} ({name}) on {chain}, spotted on {detected}, with an entry cap of ${entry_cap}.",
        "zh": "{ticker}（{name}），{chain} 链，检测时间 {detected}，入场市值 ${entry_cap}。",
    },
    "conviction": {
        "fr": "Conviction : {conviction}, avec un score de confiance de {confidence}/10.",
        "en": "Conviction: {conviction}, with a confidence score of {confidence}/10.",
        "zh": "信念：{conviction}，置信度评分 {confidence}/10。",
    },
    "conv.very_high": {"fr": "très forte", "en": "very high", "zh": "极高"},
    "conv.high": {"fr": "forte", "en": "high", "zh": "高"},
    "conv.moderate": {"fr": "modérée", "en": "moderate", "zh": "中等"},
    "conv.low": {"fr": "faible", "en": "low", "zh": "低"},
    "signals.signal": {
        "fr": "{fired} critères de scoring déclenchés, dont {strong} forts, et toutes les vérifications de sécurité sont passées, aucun veto anti-rug.",
        "en": "{fired} scoring criteria fired, {strong} of them strong, and every security check passed, no anti-rug veto.",
        "zh": "触发了 {fired} 项评分标准，其中 {strong} 项为强信号，并通过全部安全检查，无反 rug 否决。",
    },
    "signals.watch": {
        "fr": "{fired} critères de scoring déclenchés, dont {strong} forts, mais {pending} vérification(s) de sécurité encore impossible(s) : c'est une détection précoce, pas un signal d'achat.",
        "en": "{fired} scoring criteria fired, {strong} of them strong, but {pending} security check(s) still impossible: this is an early detection, not a buy signal.",
        "zh": "触发了 {fired} 项评分标准，其中 {strong} 项为强信号，但仍有 {pending} 项安全检查无法完成：这是早期检测，而非买入信号。",
    },
    "track.signal": {
        "fr": "Il a d'abord été repéré par notre passe SCOUT, puis confirmé par la passe MAIN (sécurité, liquidité, répartition des détenteurs) avant d'être publié comme signal validé.",
        "en": "It was first picked up by our SCOUT pass, then confirmed by the MAIN pass (security, liquidity, holder distribution) before being published as a validated signal.",
        "zh": "先由 SCOUT 扫描发现，随后经 MAIN 扫描（安全性、流动性、持有人分布）确认，才作为已验证信号发布。",
    },
    "track.watch": {
        "fr": "Il a été repéré par notre passe SCOUT comme détection précoce, la passe MAIN de vérification reste à faire, donc à traiter en surveillance uniquement.",
        "en": "It was picked up by our SCOUT pass as an early detection, the MAIN verification pass is still pending, so treat it as watch-only for now.",
        "zh": "由 SCOUT 扫描作为早期检测发现，MAIN 验证扫描尚未完成，因此仅作观察。",
    },
    "peak.have": {
        "fr": "Côté pic, la capitalisation a atteint ${peak_cap} le {peak_at}, soit {gain} (x{mult}) en {duration} après la détection.",
        "en": "On the peak side, the cap reached ${peak_cap} on {peak_at}, that's {gain} (x{mult}) in {duration} after detection.",
        "zh": "峰值方面，市值于 {peak_at} 达到 ${peak_cap}，也就是检测后 {duration} 内 {gain}（x{mult}）。",
    },
    "peak.none": {
        "fr": "Côté pic, pas encore de relevé exploitable, le token est trop récent ou la paire n'est pas encore indexée, donc le sommet réel reste inconnu.",
        "en": "On the peak side, no usable reading yet, the token is too recent or the pair isn't indexed yet, so the real high is unknown.",
        "zh": "峰值方面，暂无可用数据，代币过新或交易对尚未被索引，真实高点尚不清楚。",
    },
    "peak.thin": {
        "fr": "Côté pic, ${peak_cap} le {peak_at} ({gain}), mais sur {checks} relevé(s) seulement, donc le vrai sommet est probablement plus haut.",
        "en": "On the peak side, ${peak_cap} on {peak_at} ({gain}), but based on only {checks} reading(s), so the true high is likely higher.",
        "zh": "峰值方面，{peak_at} 时 ${peak_cap}（{gain}），但仅有 {checks} 次采样，真实高点可能更高。",
    },
    "smart.yes": {
        "fr": "On a aussi détecté de l'activité smart money sur ce token (indice {sm}/1.00).",
        "en": "We also detected smart-money activity on this token (score {sm}/1.00).",
        "zh": "我们还检测到该代币有聪明钱活动（指数 {sm}/1.00）。",
    },
    "smart.no": {
        "fr": "Aucun wallet smart money identifié sur ce token au moment de la détection.",
        "en": "No smart-money wallet identified on this token at the time of detection.",
        "zh": "检测时未发现该代币有聪明钱钱包。",
    },
    "rarity": {
        "fr": "Pour donner une idée de la rareté, on valide environ 1 signal tous les {ratio} cycles de scan ({emitted} signaux sur {cycles} cycles).",
        "en": "To give you a sense of rarity, we validate roughly 1 signal every {ratio} scan cycles ({emitted} signals over {cycles} cycles).",
        "zh": "关于稀有度，大约每 {ratio} 个扫描周期产生 1 个已验证信号（{cycles} 个周期内 {emitted} 个信号）。",
    },
    "cohort": {
        "fr": "En comparaison, sur {count} signaux du même genre (risque {risk}) qu'on a suivis, le pic moyen est de {avg} et le meilleur de {best}.",
        "en": "For comparison, across {count} similar signals (risk {risk}) we've tracked, the average peak is {avg} and the best is {best}.",
        "zh": "作为参考，在已跟踪的 {count} 个同类信号（风险 {risk}）中，平均峰值为 {avg}，最佳为 {best}。",
    },
    "caveats.lead": {"fr": "Quelques précisions honnêtes : ", "en": "A few honest notes: ", "zh": "几点说明："},
    "cav.wallets": {
        "fr": "les montants d'achat/vente par wallet ne sont pas suivis",
        "en": "per-wallet buy/sell amounts are not tracked",
        "zh": "未跟踪每个钱包的买入/卖出金额",
    },
    "cav.entry": {
        "fr": "la cap d'entrée est le premier prix on-chain après détection, pas forcément votre prix d'exécution",
        "en": "the entry cap is the first on-chain price after detection, not necessarily your fill",
        "zh": "入场市值为检测后的首个链上价格，未必是你的成交价",
    },
    "cav.watch": {
        "fr": "ce token est en détection précoce non vérifiée : aucun plan d'entrée",
        "en": "this token is an unverified early detection: no entry plan",
        "zh": "该代币为未验证的早期检测：无入场计划",
    },
    "cav.peak": {
        "fr": "le suivi du pic dépend de l'indexation DexScreener et peut sous-estimer le sommet",
        "en": "peak tracking depends on DexScreener indexing and can understate the high",
        "zh": "峰值跟踪依赖 DexScreener 索引，可能低估高点",
    },
    "risk.Faible": {"fr": "faible", "en": "low", "zh": "低"},
    "risk.Modéré": {"fr": "modéré", "en": "moderate", "zh": "中等"},
    "risk.Élevé": {"fr": "élevé", "en": "high", "zh": "高"},
    "risk.Non vérifié": {"fr": "non vérifié", "en": "unverified", "zh": "未验证"},
}


def _tx(key: str, lang: str, **params) -> str:
    entry = _TX.get(key, {})
    template = entry.get(lang) or entry.get(_DEFAULT_LANG) or key
    if params:
        try:
            return template.format(**params)
        except (KeyError, IndexError, ValueError):
            return template
    return template


def _norm_lang(lang: str | None) -> str:
    return lang if lang in SUPPORTED_LANGS else _DEFAULT_LANG


def _num(value) -> str:
    try:
        return f"{float(value):,.0f}"
    except (TypeError, ValueError):
        return "?"


def _signed_pct(value) -> str:
    try:
        return f"{float(value):+.0f}%"
    except (TypeError, ValueError):
        return "?"


def _resolve_tz(tz: str | None):
    name = (tz or getattr(cfg, "RECAP_TIMEZONE", "") or "").strip()
    if not name:
        return None  # datetime.fromtimestamp(ts) -> heure locale de la machine
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return None


def _fmt_ts(ts: float | None, lang: str, tzinfo) -> str:
    if not ts:
        return "?"
    dt = datetime.fromtimestamp(float(ts), tzinfo)
    hour12 = dt.hour % 12 or 12
    ampm = "am" if dt.hour < 12 else "pm"
    month = _MONTHS[lang][dt.month - 1]
    abbr = ""
    try:
        abbr = dt.strftime("%Z") or ""
    except Exception:
        abbr = ""
    if lang == "zh":
        base = f"{dt.year}年{month}{dt.day}日 {hour12}:{dt.minute:02d}{ampm}"
    else:
        base = f"{dt.day} {month} {dt.year}, {hour12}:{dt.minute:02d}{ampm}"
    return f"{base} {abbr}".strip()


def _fmt_duration(seconds: float, lang: str) -> str:
    seconds = max(0, int(seconds or 0))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    units = _DUR_UNITS[lang]
    parts: list[str] = []
    if days:
        parts.append(f"{days}{units['d']}")
    if hours:
        parts.append(f"{hours}{units['h']}")
    if minutes and not days:
        parts.append(f"{minutes}{units['m']}")
    return " ".join(parts) or _DUR_SUBMINUTE[lang]


def _conviction_key(score) -> str:
    try:
        s = float(score)
    except (TypeError, ValueError):
        return "conv.moderate"
    if s >= 85:
        return "conv.very_high"
    if s >= 75:
        return "conv.high"
    if s >= 65:
        return "conv.moderate"
    return "conv.low"


def _count_signals(features: dict) -> tuple[int, int]:
    fired = strong = 0
    for key in _FEATURE_KEYS:
        val = features.get(key)
        if not isinstance(val, (int, float)):
            continue
        if val >= _SIGNAL_FIRED_THRESHOLD:
            fired += 1
        if val >= _SIGNAL_STRONG_THRESHOLD:
            strong += 1
    return fired, strong


def build_recap(signal_id: int, lang: str = "fr", tz: str | None = None) -> str:
    """
    Paragraphe rédigé et prêt à coller pour le token `signal_id`, dans `lang`
    (fr/en/zh). Renvoie une chaîne vide si le signal n'existe plus.
    """
    lang = _norm_lang(lang)
    signal = db.get_signal(signal_id)
    if not signal:
        return ""

    tzinfo = _resolve_tz(tz)
    features = signal.get("features") or {}
    is_watch = (signal.get("tier") == getattr(cfg, "TIER_WATCH", "veille")) or (
        signal.get("verified") is False
    )

    lines: list[str] = []

    # 0. Accroche humaine, pour que le texte se lise comme un post plutôt
    # qu'un rapport (demandé explicitement, avec des virgules à la place des
    # tirets cadratins partout dans ce fichier).
    lines.append(_tx("opener", lang))

    # 1. En-tête : ticker, chaîne, horodatage, cap d'entrée.
    lines.append(_tx(
        "header", lang,
        ticker=signal.get("ticker") or "?",
        name=(signal.get("name") or "").strip() or (signal.get("ticker") or "?"),
        chain=(signal.get("chain") or "?").upper(),
        detected=_fmt_ts(signal.get("created_at"), lang, tzinfo),
        entry_cap=_num(signal.get("market_cap")),
    ))

    # 2. Conviction + confiance X/10.
    score = signal.get("score")
    try:
        confidence = f"{float(score) / 10:.1f}"
    except (TypeError, ValueError):
        confidence = "?"
    lines.append(_tx(
        "conviction", lang,
        conviction=_tx(_conviction_key(score), lang),
        confidence=confidence,
    ))

    # 3. Décompte des signaux / vérifications.
    fired, strong = _count_signals(features)
    if is_watch:
        lines.append(_tx(
            "signals.watch", lang,
            fired=fired, strong=strong,
            pending=len(signal.get("missing_checks") or []) or "?",
        ))
    else:
        lines.append(_tx("signals.signal", lang, fired=fired, strong=strong))

    # 4. Parcours SCOUT -> MAIN.
    lines.append(_tx("track.watch" if is_watch else "track.signal", lang))

    # 5. Pic atteint.
    peak_return = signal.get("peak_return_pct")
    peak_checks = signal.get("peak_checks") or 0
    peak_at = signal.get("peak_at")
    if peak_return is not None and peak_checks > 0 and peak_at:
        try:
            mult = f"{1 + float(peak_return) / 100:.1f}"
        except (TypeError, ValueError):
            mult = "?"
        peak_cap = signal.get("peak_market_cap")
        if peak_cap is None:
            entry_cap = signal.get("market_cap") or 0
            try:
                peak_cap = entry_cap * (1 + float(peak_return) / 100)
            except (TypeError, ValueError):
                peak_cap = None
        duration = _fmt_duration((peak_at or 0) - (signal.get("created_at") or 0), lang)
        key = "peak.thin" if peak_checks < 3 else "peak.have"
        lines.append(_tx(
            key, lang,
            peak_cap=_num(peak_cap),
            peak_at=_fmt_ts(peak_at, lang, tzinfo),
            gain=_signed_pct(peak_return),
            mult=mult,
            duration=duration,
            checks=peak_checks,
        ))
    else:
        lines.append(_tx("peak.none", lang))

    # 6. Smart money (à partir des données déjà stockées, pas d'appel réseau).
    sm = features.get("smart_money")
    if isinstance(sm, (int, float)) and sm > 0:
        lines.append(_tx("smart.yes", lang, sm=f"{sm:.2f}"))
    else:
        lines.append(_tx("smart.no", lang))

    # 7. Rareté dans le corpus.
    cycle_stats = db.get_cycle_stats()
    cycles = cycle_stats.get("scan_cycles") or 0
    emitted = cycle_stats.get("signals_emitted") or 0
    if emitted > 0 and cycles > 0:
        lines.append(_tx(
            "rarity", lang,
            ratio=f"{cycles / emitted:.0f}",
            emitted=emitted,
            cycles=cycles,
        ))

    # 8. Moyenne de la cohorte (même niveau + même risque).
    cohort = db.get_cohort_stats(
        tier=signal.get("tier"), risk_level=signal.get("risk_level")
    )
    if cohort.get("count", 0) > 0 and cohort.get("avg_peak_return_pct") is not None:
        raw_risk = signal.get("risk_level") or ""
        risk_key = f"risk.{raw_risk}"
        risk_label = _tx(risk_key, lang) if risk_key in _TX else (raw_risk or "?")
        lines.append(_tx(
            "cohort", lang,
            count=cohort["count"],
            risk=risk_label,
            avg=_signed_pct(cohort["avg_peak_return_pct"]),
            best=_signed_pct(cohort.get("best_peak_return_pct")),
        ))

    # 9. Clause de prudence honnête.
    caveats = [_tx("cav.entry", lang), _tx("cav.wallets", lang), _tx("cav.peak", lang)]
    if is_watch:
        caveats.insert(0, _tx("cav.watch", lang))
    sep = "；" if lang == "zh" else "; "
    tail = "。" if lang == "zh" else "."
    lines.append(_tx("caveats.lead", lang) + sep.join(caveats) + tail)

    # 10. Avertissement « pas un conseil en investissement » : même texte
    # anglais que dans les alertes Telegram (source unique), quelle que soit la
    # langue du récap, puisque ce texte est destiné à être publié tel quel.
    lines.append(DISCLAIMER)

    return "\n\n".join(lines)
