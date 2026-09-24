"""
core/telegram_alerts.py — Alertes Telegram
Destination finale : gem_hunter/core/telegram_alerts.py

CORRECTIF — LES ALERTES ANGLAISES CONTENAIENT DU FRANÇAIS.

Le gabarit était bien en anglais, mais tout ce qui était injecté dedans (les
raisons, les vetos, les vérifications manquantes, le niveau de risque) était
produit en français par core/security_checks.py et core/scoring.py, puis
retraduit ici par une liste de motifs tenue à la main. Après la réécriture du
moteur de sécurité, cette liste ne connaissait plus aucun des nouveaux
messages : l'alerte anglaise affichait donc « données de sécurité indisponibles
(GoPlus n'a pas encore indexé ce contrat) » au milieu d'un texte anglais.

La liste de motifs est supprimée. Les messages sont désormais déclarés une
seule fois, dans les deux langues, dans core/i18n.py, et cette traduction est
la même dans les deux sens. Un message ajouté au moteur est donc traduit dès sa
déclaration : il n'y a plus de liste à mettre à jour, donc plus de liste à
oublier.

DEUX GABARITS AU LIEU D'UN.
Le scanner produit deux niveaux d'information (voir core/scanner.py). Envoyer
une alerte de VEILLE avec le gabarit d'un SIGNAL reviendrait à présenter un
token non vérifié comme une opportunité validée, prix d'entrée et stop loss à
l'appui. Une alerte de veille a donc son propre gabarit : elle annonce le
token, dit noir sur blanc ce qui n'a PAS pu être vérifié, et ne propose aucun
plan d'entrée.
"""
import logging

import requests

from config import API_KEYS, ALERT_LANGUAGES
from core import i18n

logger = logging.getLogger("gem_hunter.telegram")

AXIOM_REF_HANDLE = "hunter212"
NANSEN_REF = "GloriousKing225"
BINANCE_WEB3_REF = "L3C8VW3Q"

# Mapping de nos noms de chaîne internes vers le code chaîne attendu par Axiom
# "arc" ajouté le 23/09/2026 : Axiom liste désormais Arc Network parmi ses
# chaînes supportées (Solana, BNB Chain, Ethereum, Base, HyperEVM, Robinhood,
# Ink, Arc), au même titre que les autres chaînes ci-dessous.
AXIOM_CHAIN_MAP = {
    "solana": "sol",
    "bsc": "bnb",
    "ethereum": "eth",
    "robinhood": "robinhood",
    "arc": "arc",
}

# Nansen Token God Mode : le paramètre `chain` attend le code de l'API Nansen
# (BNB Chain = "bnb"). Aligné sur data_sources/nansen.NANSEN_SCREENER_CHAIN_MAP.
# "arc" ajouté le 23/09/2026, confirmé en direct via l'API Nansen (voir
# data_sources/nansen.NANSEN_CHAIN_MAP).
NANSEN_LINK_CHAIN_MAP = {
    "solana": "solana",
    "bsc": "bnb",
    "base": "base",
    "ethereum": "ethereum",
    "robinhood": "robinhood",
    "arc": "arc",
}

# Binance Web3 : pages /en/token/<chaîne>/<contrat>. Robinhood Chain n'y est
# pas listée — on n'ajoute alors pas le lien plutôt que d'en produire un mort.
# "arc" ajouté le 24/09/2026, vérifié en ouvrant réellement une page Arc
# (/en/token/arc/<contrat>, sur ARGUS) : Binance Web3 y affiche de vraies
# données de marché (prix, liquidité, holders, audit) et promeut même le
# trading sur Arc en direct sur la page — couverture confirmée, pas supposée.
# (« ARC-20 » que Binance a intégré par ailleurs est un standard de tokens
# Bitcoin — Atomicals Protocol — sans rapport avec Arc Network ; à ne pas
# confondre, mais ce n'est plus la question ici : la page Arc existe bel et
# bien sous ce code chaîne.)
BINANCE_WEB3_CHAIN_MAP = {
    "solana": "solana",
    "bsc": "bsc",
    "base": "base",
    "ethereum": "ethereum",
    "arc": "arc",
}


def _build_links(signal: dict) -> str:
    """
    Liens d'affiliation ajoutés en bas de chaque alerte (une ligne chacun) :
    Axiom, Nansen Token God Mode et Binance Web3. Chaque lien n'est ajouté que
    pour les chaînes où la plateforme correspondante sait ouvrir la bonne page.
    Le format Axiom (/t/{contrat}/@handle?chain=...) n'est confirmé que pour
    Solana ; les autres chaînes suivent le même schéma par extrapolation.
    """
    chain = signal.get("chain", "")
    contract = signal.get("contract", "")
    if not contract:
        return ""

    lines: list[str] = []

    axiom_chain = AXIOM_CHAIN_MAP.get(chain)
    if axiom_chain:
        lines.append(
            f"🔫 Axiom : https://axiom.trade/t/{contract}/@{AXIOM_REF_HANDLE}?chain={axiom_chain}"
        )

    nansen_chain = NANSEN_LINK_CHAIN_MAP.get(chain)
    if nansen_chain:
        lines.append(
            f"🔎 Nansen : https://app.nansen.ai/token-god-mode"
            f"?tokenAddress={contract}&chain={nansen_chain}&tab=transactions&ref={NANSEN_REF}"
        )

    binance_chain = BINANCE_WEB3_CHAIN_MAP.get(chain)
    if binance_chain:
        lines.append(
            f"🟡 Binance Web3 : https://web3.binance.com/en/token/{binance_chain}/{contract}?ref={BINANCE_WEB3_REF}"
        )

    return "\n".join(lines)


TEMPLATES = {
    "fr": """🚨 NOUVEAU SIGNAL — {name} ({ticker})
Chaîne : {chain}
Contrat : {contract}
Market Cap : ${market_cap:,.0f}
Liquidité : ${liquidity:,.0f}
Holders : {holders}
Score IA : {score}/100
Niveau de risque : {risk_level}

Raisons du signal :
{reasons}

📍 Entrée : ${entry_price}
🛑 Stop Loss : ${stop_loss}
🎯 TP1 : ${tp1} | TP2 : ${tp2} | TP3 : ${tp3}
{links}""",

    "en": """🚨 NEW SIGNAL — {name} ({ticker})
Chain: {chain}
Contract: {contract}
Market Cap: ${market_cap:,.0f}
Liquidity: ${liquidity:,.0f}
Holders: {holders}
AI Score: {score}/100
Risk level: {risk_level}

Signal reasons:
{reasons}

📍 Entry: ${entry_price}
🛑 Stop Loss: ${stop_loss}
🎯 TP1: ${tp1} | TP2: ${tp2} | TP3: ${tp3}
{links}""",
}


# --- Gabarits du niveau VEILLE (détection précoce, NON vérifiée) -------------
# Volontairement dépourvus de prix d'entrée, de stop loss et d'objectifs : il
# n'y a rien à recommander sur un token dont la sécurité n'est pas vérifiable.
WATCH_TEMPLATES = {
    "fr": """👁 VEILLE PRÉCOCE — {name} ({ticker})
⚠️ NON VÉRIFIÉ — ceci n'est PAS un signal d'achat.

Chaîne : {chain}
Contrat : {contract}
Market Cap : ${market_cap:,.0f}
Liquidité : ${liquidity:,.0f}
Score provisoire : {score}/100

Ce qui n'a pas pu être vérifié :
{missing}

Observations :
{reasons}

Le bot continue de surveiller ce token et enverra un signal validé
uniquement si toutes les vérifications de sécurité aboutissent.
{links}""",

    "en": """👁 EARLY WATCH — {name} ({ticker})
⚠️ UNVERIFIED — this is NOT a buy signal.

Chain: {chain}
Contract: {contract}
Market Cap: ${market_cap:,.0f}
Liquidity: ${liquidity:,.0f}
Provisional score: {score}/100

Could not be verified:
{missing}

Observations:
{reasons}

The bot keeps monitoring this token and will only send a validated
signal once every security check passes.
{links}""",
}

# Repli affiché quand la liste correspondante est vide.
_EMPTY_MISSING = {"fr": "- (non précisé)", "en": "- (not specified)"}

# Avertissement ajouté à la fin de CHAQUE alerte (signal validé et veille), quelle
# que soit la langue : volontairement en anglais uniquement, à la demande de
# l'exploitant, pour un texte juridique unique et identique partout.
DISCLAIMER = (
    "⚠️ Disclaimer: This is not financial advice. Signals are provided for "
    "informational purposes only. Memecoin trading is highly risky, so do your own "
    "research, take full responsibility for your own decisions, and only invest "
    "what you can afford to lose."
)


def _with_disclaimer(text: str) -> str:
    return f"{text.rstrip()}\n\n{DISCLAIMER}"


def _localize(items, lang: str) -> list[str]:
    """Traduit une liste de messages produits par le moteur vers `lang`."""
    return [i18n.translate_joined(str(item), lang) for item in (items or [])]


def format_watch_alert(signal: dict, lang: str) -> str:
    template = WATCH_TEMPLATES.get(lang, WATCH_TEMPLATES["en"])
    reasons = _localize(signal.get("reasons"), lang)
    missing = _localize(signal.get("missing_checks"), lang)

    return _with_disclaimer(template.format(
        name=signal.get("name", "?"),
        ticker=signal.get("ticker", "?"),
        chain=signal.get("chain", "?"),
        contract=signal.get("contract", "?"),
        market_cap=signal.get("market_cap") or 0,
        liquidity=signal.get("liquidity") or 0,
        score=signal.get("score", 0),
        missing="\n".join(f"- {m}" for m in missing) or _EMPTY_MISSING.get(lang, _EMPTY_MISSING["en"]),
        reasons="\n".join(f"- {r}" for r in reasons[:6]) or "-",
        links=_build_links(signal),
    ))


def format_alert(signal: dict, lang: str) -> str:
    # Un token non vérifié ne doit jamais emprunter le gabarit d'un signal
    # validé : c'est le gabarit, autant que le contenu, qui dit au lecteur
    # quel niveau de confiance accorder au message.
    if signal.get("tier") == "veille" or signal.get("verified") is False:
        return format_watch_alert(signal, lang)

    template = TEMPLATES.get(lang, TEMPLATES["en"])
    reasons = _localize(signal.get("reasons"), lang)
    risk_level = i18n.translate(signal.get("risk_level", "N/A"), lang)

    return _with_disclaimer(template.format(
        name=signal.get("name", "?"),
        ticker=signal.get("ticker", "?"),
        chain=signal.get("chain", "?"),
        contract=signal.get("contract", "?"),
        market_cap=signal.get("market_cap") or 0,
        liquidity=signal.get("liquidity") or 0,
        holders=signal.get("holders") or "N/A",
        score=signal.get("score", 0),
        risk_level=risk_level,
        reasons="\n".join(f"- {r}" for r in reasons) or "-",
        entry_price=signal.get("entry_price", "N/A"),
        stop_loss=signal.get("stop_loss", "N/A"),
        tp1=signal.get("tp1", "N/A"),
        tp2=signal.get("tp2", "N/A"),
        tp3=signal.get("tp3", "N/A"),
        links=_build_links(signal),
    ))


def resolve_languages(languages: list[str] | None = None) -> list[str]:
    """
    Langues effectivement envoyées. Une langue sans gabarit est écartée plutôt
    que de retomber silencieusement sur l'anglais en double : recevoir deux
    fois la même alerte est plus déroutant que de n'en recevoir qu'une.
    """
    requested = languages or ALERT_LANGUAGES or ["en"]
    seen, resolved = set(), []
    for lang in requested:
        if lang in TEMPLATES and lang not in seen:
            seen.add(lang)
            resolved.append(lang)
    return resolved or ["en"]


def send_alert(signal: dict, languages: list[str] | None = None) -> list[str]:
    """
    Envoie l'alerte dans toutes les langues configurées (config.ALERT_LANGUAGES).
    Retourne la liste des erreurs rencontrées (vide si tout est OK).
    """
    token = API_KEYS.get("telegram_bot_token")
    chat_id = API_KEYS.get("telegram_chat_id")
    if not token or not chat_id:
        return ["TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID non configurés."]

    errors = []
    for lang in resolve_languages(languages):
        text = format_alert(signal, lang)
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            resp = requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=10)
            if resp.status_code != 200:
                errors.append(f"[{lang}] Erreur Telegram: {resp.text}")
        except requests.RequestException as e:
            errors.append(f"[{lang}] Exception: {e}")
    return errors
