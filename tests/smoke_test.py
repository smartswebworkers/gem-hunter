"""
tests/smoke_test.py — Tests de non-régression du cœur logique du bot

Ne nécessite PAS PyQt6 ni le réseau : uniquement du calcul local sur des
candidats fabriqués.

Lancement, depuis la racine du projet gem_hunter/ :
    python tests/smoke_test.py

La section 1 est la plus importante : c'est la batterie anti-rug. Chaque cas
correspond à un schéma de rug pull réel qui PASSAIT le filtre dans la version
précédente du bot. Si l'un de ces tests échoue un jour, c'est qu'une
régression a rouvert un trou par lequel un rug peut passer.
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
import config
config.DB_PATH = tmp_db
# La batterie anti-rug historique (sections 1 à 12) est calibrée sur les seuils
# permissifs. On la fait tourner en profil « degen ». La section 13 teste
# explicitement le profil « quality » (potentiel liste Alpha).
config.apply_profile("degen")

from storage import db
db.init_db()
print("[OK] init_db (WAL + migrations) sur", tmp_db)

# Ces tests doivent tourner hors ligne et donner le même résultat partout. On
# coupe donc explicitement les sources qui feraient un appel réseau, sans quoi
# une clé Nansen présente dans .env fait partir des requêtes réelles pendant
# le test (lent, et résultat dépendant de la connexion du moment).
from data_sources import nansen, rugcheck
nansen.set_enabled(False)
rugcheck.set_enabled(False)
print("[OK] Sources réseau désactivées — tests entièrement hors ligne")

from core.security_checks import (
    evaluate_security, evaluate_rejection, compute_safety_score,
    creator_reputation_veto,
    VERDICT_REJECT, VERDICT_WATCH, VERDICT_PASS,
)
from data_sources import goplus
from core.scoring import compute_score, extract_features

PASSED, FAILED = 0, 0


def check(condition, label, detail=""):
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"[OK] {label}")
    else:
        FAILED += 1
        print(f"[ÉCHEC] {label} {detail}")


# =============================================================================
# Socle : un token BNB Chain irréprochable, qui DOIT passer.
# Les cas de rug ci-dessous ne changent qu'un seul champ à la fois par rapport
# à ce socle, ce qui isole précisément ce que chaque veto détecte.
# =============================================================================
def clean_bsc_token(**overrides) -> dict:
    candidate = {
        "chain": "bsc",
        "contract": "0xCLEAN",
        "ticker": "CLEAN",
        "name": "Clean Token",
        "liquidity": 60_000,
        "market_cap": 250_000,
        "volume_1h": 25_000,
        "price_usd": 0.0012,
        "price_change_5m": 4,
        "price_change_1h": 22,
        "buys_h1": 140,
        "sells_h1": 90,
        "pair_created_at": time.time() * 1000 - (2 * 3_600_000),  # 2h
        "is_pump_bonding_curve": False,
        "dex_paid": False,
        "security": {
            "data_available": True, "source": "goplus",
            "mint_authority_active": False, "freeze_authority_active": None, "is_mintable": False,
            "honeypot": False, "cannot_sell_all": False, "cannot_buy": False,
            "buy_tax": 0.0, "sell_tax": 0.0,
            "transfer_pausable": False, "trading_cooldown": False,
            "is_blacklisted": False, "is_whitelisted": False,
            "is_open_source": True, "is_proxy": False, "hidden_owner": False,
            "can_take_back_ownership": False, "owner_change_balance": False,
            "selfdestruct": False, "slippage_modifiable": False,
            "personal_slippage_modifiable": False, "anti_whale_modifiable": False,
            "external_call": False, "gas_abuse": False,
            "lp_locked_pct": 100.0, "lp_data_available": True,
            "top10_holder_pct": 12.0, "holder_count": 850,
            "creator_pct": 0.5, "owner_pct": 0.0,
            "honeypot_with_same_creator": False, "fake_token": False, "is_airdrop_scam": False,
        },
    }
    security_overrides = overrides.pop("security", {})
    candidate.update(overrides)
    candidate["security"] = {**candidate["security"], **security_overrides}
    return candidate


print("\n=== 1. BATTERIE ANTI-RUG (chaque cas passait le filtre avant) ===")

base = clean_bsc_token()
verdict = evaluate_security(base)
check(verdict["verdict"] == VERDICT_PASS,
      "Un token BNB Chain vérifié et propre est bien accepté",
      f"-> {verdict['verdict']} : {verdict['reasons'][:2]}")

RUG_CASES = [
    ("Honeypot confirmé", {"security": {"honeypot": True}}),
    ("Vente totale impossible", {"security": {"cannot_sell_all": True}}),
    ("Taxe de vente à 40%", {"security": {"sell_tax": 40.0}}),
    ("Transferts suspendables par le dev", {"security": {"transfer_pausable": True}}),
    ("Blacklist activable par le dev", {"security": {"is_blacklisted": True}}),
    ("Contrat proxy modifiable après coup", {"security": {"is_proxy": True}}),
    ("Propriétaire dissimulé", {"security": {"hidden_owner": True}}),
    ("Propriété reprenable après renonciation", {"security": {"can_take_back_ownership": True}}),
    ("Le dev peut réécrire les soldes", {"security": {"owner_change_balance": True}}),
    ("Slippage modifiable (taxe pouvant passer à 100%)", {"security": {"slippage_modifiable": True}}),
    ("Code source non vérifié", {"security": {"is_open_source": False}}),
    ("Mint authority encore active", {"security": {"mint_authority_active": True, "is_mintable": True}}),
    ("Créateur déjà auteur d'un honeypot", {"security": {"honeypot_with_same_creator": True}}),
    ("LP non verrouillée (dev peut retirer le pool)", {"security": {"lp_locked_pct": 5.0}}),
    ("Top 10 détenant 78% de l'offre", {"security": {"top10_holder_pct": 78.0}}),
    ("Créateur détenant 30% de l'offre", {"security": {"creator_pct": 30.0}}),
    ("Un seul wallet détenant 45% de l'offre", {"largest_holder_pct": 45.0}),
    ("Réseau d'insiders détenant 40% de l'offre", {"insider_pct": 40.0}),
    ("Wallet dev détenant encore 25% de l'offre", {"dev_holding_pct": 25.0}),
    ("Coquille vide : 250k$ de MC pour 4k$ de liquidité",
     {"liquidity": 4_000, "market_cap": 250_000, "volume_1h": 2_500}),
    ("Wash trading : volume 1h à 60x la liquidité",
     {"liquidity": 20_000, "volume_1h": 1_200_000}),
    ("Pompe parabolique : +650% en une heure", {"price_change_1h": 650}),
]

for label, overrides in RUG_CASES:
    candidate = clean_bsc_token(**overrides)
    verdict = evaluate_security(candidate)
    scored = compute_score(candidate)
    check(
        verdict["verdict"] == VERDICT_REJECT and scored["rejected"] is True and scored["score"] == 0.0,
        f"REJETÉ — {label}",
        f"-> verdict={verdict['verdict']}, score={scored['score']}",
    )


print("\n=== 1bis. Réputation du wallet déployeur (BSC/EVM) ===")
# GoPlus /address_security réduit à ce qui compte : contrats malveillants déjà
# créés + labels criminels.
rep = goplus.normalize_address_reputation({
    "number_of_malicious_contracts_created": "3",
    "honeypot_related_address": "0",
    "phishing_activities": "1",
    "cybercrime": "0",
})
check(rep["available"] and rep["malicious_contracts_created"] == 3
      and rep["criminal_flags"] == ["phishing_activities"],
      "normalize_address_reputation extrait le compte de contrats malveillants et les labels",
      f"-> {rep}")

check(goplus.normalize_address_reputation(None) == {"available": False},
      "Adresse inconnue de GoPlus = available False (pas un feu vert, mais pas un veto)")

check(creator_reputation_veto({"creator_reputation": rep}) is not None,
      "creator_reputation_veto : un wallet à 3 contrats malveillants est rejeté")

check(creator_reputation_veto(
        {"creator_reputation": {"available": True, "malicious_contracts_created": 0,
                                "honeypot_related": True, "criminal_flags": []}}) is not None,
      "creator_reputation_veto : un wallet lié à un honeypot est rejeté")

check(creator_reputation_veto(
        {"creator_reputation": {"available": True, "malicious_contracts_created": 0,
                                "honeypot_related": False, "criminal_flags": []}}) is None,
      "creator_reputation_veto : un wallet déployeur propre passe")

check(creator_reputation_veto({}) is None
      and creator_reputation_veto({"creator_reputation": {"available": False}}) is None,
      "creator_reputation_veto : sans donnee de reputation, aucun veto (absence n'est pas danger)")


print("\n=== 2. Le pump-and-dump type ne peut plus obtenir un score élevé ===")
# Signature exacte d'un pump-and-dump : momentum vertical, pression acheteuse
# quasi totale, volume massif, et AUCUNE donnée de sécurité disponible.
# Sous l'ancienne pondération, ce candidat approchait ou dépassait 80/100.
pump_and_dump = {
    "chain": "solana",
    "contract": "PUMPMINT111",
    "ticker": "MOON",
    "name": "Moon Rocket",
    "liquidity": 12_000,
    "market_cap": 90_000,
    "volume_1h": 45_000,
    "price_usd": 0.00004,
    "price_change_5m": 180,
    "price_change_1h": 900,
    "buys_h1": 480,
    "sells_h1": 12,
    "pair_created_at": time.time() * 1000 - (40 * 60_000),  # 40 min
    "is_pump_bonding_curve": False,
    "dex_paid": False,
    "security": {
        "data_available": True, "source": "rpc",
        "mint_authority_active": False, "freeze_authority_active": False,
        "honeypot": None, "buy_tax": None, "sell_tax": None,
        "is_open_source": None, "lp_locked_pct": None, "lp_data_available": False,
        "top10_holder_pct": None, "holder_count": None, "creator_pct": None,
    },
}
scored_extreme = compute_score(pump_and_dump)
check(scored_extreme["rejected"] is True,
      "Une hausse de +900% en 1h est rejetée d'office : entrer là, c'est acheter le sommet",
      f"-> rejected={scored_extreme['rejected']}")

# Même profil, hausse ramenée sous le veto : le token n'est plus rejeté, mais
# son score doit rester loin du seuil d'achat, et il ne doit jamais devenir un
# SIGNAL tant que sa sécurité n'est pas vérifiable.
pump_and_dump = dict(pump_and_dump, price_change_1h=180, price_change_5m=60)
scored = compute_score(pump_and_dump)
check(scored["rejected"] is False, "Le même profil sous le seuil de pompe n'est plus rejeté d'office")
check(scored["tier"] == config.TIER_WATCH,
      "Il est classé en VEILLE, pas en SIGNAL (sécurité non vérifiable)",
      f"-> tier={scored['tier']}")
check(scored["verified"] is False, "Il est explicitement marqué non vérifié")
check(scored["score"] < config.MIN_SCORE_TO_BUY,
      f"Son score reste sous le seuil d'achat ({scored['score']} < {config.MIN_SCORE_TO_BUY})",
      f"-> score={scored['score']}")

features = scored["features"]
check(features["momentum_1h"] < 0.4,
      f"Une hausse de +180% sur 1h est notée comme un risque, pas comme une qualité "
      f"(momentum_1h = {features['momentum_1h']:.2f})")
check(features["buy_pressure"] < 0.6,
      f"97% d'achats et presque aucune vente est pénalisé, pas récompensé "
      f"(buy_pressure = {features['buy_pressure']:.2f})")
check(features["safety"] < 0.5,
      f"Une sécurité non vérifiable note SOUS le neutre, au lieu d'exactement 0,50 "
      f"(safety = {features['safety']:.2f})")


print("\n=== 3. Le bug 'or True' de la bonding curve est corrigé ===")
from chains import solana as solana_chain
from data_sources import pumpportal

# Test comportemental plutôt que textuel : on injecte deux événements
# PumpPortal, l'un encore sur la bonding curve, l'autre déjà migré vers un
# pool Raydium. Avec l'ancien `or True`, les DEUX ressortaient marqués
# bonding curve, et bénéficiaient donc tous les deux du laissez-passer
# complet sur les filtres de marché et sur le score de sécurité.
_original_get_recent = pumpportal.get_recent_tokens
pumpportal.get_recent_tokens = lambda max_age_seconds=600: [
    {"mint": "MINTBOND", "symbol": "BOND", "name": "Bonding", "pool": "pump",
     "marketCapSol": 40, "vSolInBondingCurve": 30, "vTokensInBondingCurve": 1e9,
     "traderPublicKey": "DEV1", "_received_at": time.time()},
    {"mint": "MINTMIGR", "symbol": "MIGR", "name": "Migrated", "pool": "raydium",
     "marketCapSol": 60, "vSolInBondingCurve": 45, "vTokensInBondingCurve": 1e9,
     "traderPublicKey": "DEV2", "_received_at": time.time()},
]
try:
    from_pump = {c["contract"]: c for c in solana_chain._from_pumpportal(sol_price_usd=150.0)}
finally:
    pumpportal.get_recent_tokens = _original_get_recent

check(from_pump["MINTBOND"]["is_pump_bonding_curve"] is True,
      "Un token encore sur la bonding curve est bien marqué comme tel")
check(from_pump["MINTMIGR"]["is_pump_bonding_curve"] is False,
      "Un token déjà migré vers Raydium n'est PLUS marqué bonding curve "
      "(c'est le bug `or True` qui les marquait tous)",
      f"-> {from_pump['MINTMIGR']['is_pump_bonding_curve']}")

bonding = {
    "chain": "solana", "contract": "BONDMINT1", "ticker": "BOND", "name": "Bonding",
    "liquidity": 6_000, "market_cap": 20_000, "price_usd": 0.00001,
    "is_pump_bonding_curve": True, "dex_paid": False,
    "security": {
        "data_available": True, "source": "rpc",
        "mint_authority_active": False, "freeze_authority_active": False,
        "honeypot": None, "lp_locked_pct": None, "top10_holder_pct": 8.0,
    },
}
safety, _ = compute_safety_score(bonding)
check(safety < 0.95,
      f"Un token en bonding curve n'obtient plus 100% de sécurité d'office (safety = {safety:.2f})")

scored_bonding = compute_score(bonding)
check(scored_bonding["tier"] == config.TIER_WATCH,
      "Un token en bonding curve reste en VEILLE et ne devient jamais un SIGNAL",
      f"-> tier={scored_bonding['tier']}")

# Le veto dev fonctionne aussi sur une bonding curve
bonding_dev_rug = dict(bonding, dev_holding_pct=35.0)
check(evaluate_security(bonding_dev_rug)["verdict"] == VERDICT_REJECT,
      "Un dev détenant 35% de sa propre bonding curve fait rejeter le token")


print("\n=== 3bis. Règle du maillon faible : la moyenne ne masque plus le risque ===")
# Ces deux tokens passent tous les vetos et affichent un score global élevé.
# Sans la règle du maillon faible, ils devenaient des SIGNAUX avec plan d'entrée.
weak_taxes = clean_bsc_token(security={"buy_tax": 4.0, "sell_tax": 4.0})
r = compute_score(weak_taxes)
check(r["tier"] == config.TIER_WATCH,
      f"Des taxes de 4% (sous le veto) déclassent en VEILLE malgré un score de {r['score']}/100",
      f"-> tier={r['tier']}, safety={r['features']['safety']:.2f}")

weak_distribution = clean_bsc_token(security={"top10_holder_pct": config.MAX_TOP10_HOLDER_PCT - 1})
r = compute_score(weak_distribution)
check(r["tier"] == config.TIER_WATCH,
      f"Un top 10 juste sous le veto ({config.MAX_TOP10_HOLDER_PCT - 1}%) déclasse en VEILLE malgré un score de {r['score']}/100",
      f"-> tier={r['tier']}, distribution={r['features']['holder_distribution']:.2f}")

r = compute_score(clean_bsc_token())
check(r["tier"] == config.TIER_SIGNAL,
      f"Un token sans maillon faible reste bien un SIGNAL ({r['score']}/100)")


print("\n=== 4. Une donnée manquante n'est plus traitée comme rassurante ===")
unverifiable = clean_bsc_token(security={
    "honeypot": None, "buy_tax": None, "sell_tax": None,
    "is_open_source": None, "lp_locked_pct": None, "top10_holder_pct": None,
})
verdict = evaluate_security(unverifiable)
check(verdict["verdict"] == VERDICT_WATCH,
      "Sans données de sécurité, le token part en VEILLE et pas en SIGNAL",
      f"-> {verdict['verdict']}")
check(len(verdict["missing"]) >= 5,
      f"Les vérifications manquantes sont listées explicitement ({len(verdict['missing'])} éléments)")

unreachable = clean_bsc_token(security={"data_available": False,
                                        "unavailable_reason": "GoPlus injoignable"})
verdict = evaluate_security(unreachable)
check(verdict["verdict"] == VERDICT_WATCH and "données de sécurité indisponibles" in verdict["missing"][0],
      "Une source injoignable produit une VEILLE explicite, pas un signal silencieux")

clean_features, _ = extract_features(clean_bsc_token())
unverified_features, _ = extract_features(unverifiable)
check(clean_features["safety"] > unverified_features["safety"] + 0.3,
      f"Un token vérifié se détache nettement d'un token non vérifié "
      f"({clean_features['safety']:.2f} contre {unverified_features['safety']:.2f})")


print("\n=== 5. GoPlus : conversion des pourcentages et lecture des porteurs ===")
from data_sources.goplus import _ratio_to_pct, _sum_locked_lp, _sum_top_holders, normalize_security

check(_ratio_to_pct("0.009") == 0.9,
      "Une LP verrouillée à 0,9% n'est plus lue comme 90% (ancien bug d'heuristique)",
      f"-> {_ratio_to_pct('0.009')}")
check(_ratio_to_pct("0.05") == 5.0, "Une taxe de 0.05 est bien lue comme 5%")
check(_ratio_to_pct("1") == 100.0, "Une part de 1 est bien lue comme 100%")

lp = [
    {"address": "0xdev", "percent": "0.6", "is_locked": "0"},
    {"address": "0x000000000000000000000000000000000000dead", "percent": "0.3", "is_locked": "0"},
    {"address": "0xlocker", "percent": "0.1", "is_locked": "1", "tag": "Unicrypt"},
]
locked = _sum_locked_lp(lp)
check(abs(locked - 40.0) < 0.01,
      f"La LP brûlée et verrouillée est comptée, celle du dev ne l'est pas ({locked:.0f}%)")

holders = [
    {"address": "0xpool", "percent": "0.7", "is_contract": "1"},
    {"address": "0xwhale1", "percent": "0.15", "is_contract": "0"},
    {"address": "0xwhale2", "percent": "0.10", "is_contract": "0"},
]
top = _sum_top_holders(holders)
check(abs(top - 25.0) < 0.01,
      f"Le pool DEX est exclu du calcul de concentration, les baleines non ({top:.0f}%)")

only_contracts = _sum_top_holders([{"address": "0xpool", "percent": "1", "is_contract": "1"}])
check(only_contracts is None,
      "Quand tous les porteurs sont des contrats, la concentration est INCONNUE et non 0%")

empty = normalize_security("bsc", None)
check(empty["data_available"] is False and empty["honeypot"] is None,
      "Une réponse GoPlus vide ne fabrique aucune valeur rassurante par défaut")


print("\n=== 6. GeckoTerminal : l'adresse renvoyée est celle du TOKEN ===")
from data_sources.geckoterminal import normalize_pool, _parse_iso_to_epoch_ms

now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3 * 3600))
pool = {
    "attributes": {
        "address": "0xPOOLADDRESS",
        "name": "GEM / WBNB 0.3%",
        "market_cap_usd": "100000",
        "reserve_in_usd": "10000",
        "base_token_price_usd": "0.004",
        "volume_usd": {"h24": "20000", "h1": "3000"},
        "price_change_percentage": {"m5": "2", "h1": "18"},
        "transactions": {"h1": {"buys": 40, "sells": 25}},
        "pool_created_at": now_iso,
    },
    "relationships": {"base_token": {"data": {"id": "bsc_0xTOKENADDRESS"}}},
}
normalized = normalize_pool(pool, {"bsc_0xTOKENADDRESS": {"symbol": "GEM", "name": "Gem", "address": "0xTOKENADDRESS"}})
check(normalized["contract"] == "0xTOKENADDRESS",
      "Le champ contract porte l'adresse du token, plus celle du pool (bug qui cassait tout BNB Chain)",
      f"-> {normalized['contract']}")
check(normalized["pool_address"] == "0xPOOLADDRESS", "L'adresse du pool est conservée à part")
check(normalized["ticker"] == "GEM", "Le symbole du token est renseigné")
check(normalized["volume_1h"] == 3000.0, "Le volume 1h est renseigné (il manquait entièrement)")
check(normalized["price_change_1h"] == 18.0, "La variation de prix 1h est renseignée")
check(normalized["buys_h1"] == 40 and normalized["sells_h1"] == 25,
      "Les compteurs d'achats et de ventes sont renseignés")

age_h = (time.time() * 1000 - normalized["pair_created_at"]) / 3_600_000
check(2.9 < age_h < 3.1, f"L'âge de la pool est correctement converti ({age_h:.2f}h)")

# Repli quand le bloc `included` est absent
fallback = normalize_pool(pool)
check(fallback["contract"] == "0xTOKENADDRESS" and fallback["ticker"] == "GEM",
      "Sans bloc included, l'adresse et le symbole sont quand même déduits")


print("\n=== 7. Le filtre d'âge et les filtres de marché s'appliquent ===")
old_pair = clean_bsc_token(pair_created_at=time.time() * 1000 - (100 * 3_600_000))
rejected, reasons = evaluate_rejection(old_pair)
check(rejected and any("trop ancienne" in r for r in reasons),
      "Une paire de 100h est rejetée par MAX_PAIR_AGE_HOURS")

too_young = clean_bsc_token(pair_created_at=time.time() * 1000 - (3 * 60_000))
verdict = evaluate_security(too_young)
check(verdict["verdict"] == VERDICT_WATCH,
      "Une paire de 3 minutes part en VEILLE : ses données ne sont pas encore fiables",
      f"-> {verdict['verdict']}")

# CORRECTIF — la fenêtre élargie (96 h) pour BNB/Base/Robinhood ne s'applique
# maintenant QU'EN PROFIL « quality ». Elle s'appliquait aussi en « degen »,
# ce qui contredisait sa promesse de détection précoce : mesuré en direct, un
# token BSC pouvait remonter avec jusqu'à 96 h (4 jours) d'âge réel en degen
# (ex. un token affiché "4 min" sur le dashboard avait en réalité 12,8 h — voir
# Mise à jour 33 : le badge montrait l'heure de NOTRE détection, pas l'âge
# réel). Signalé par l'utilisateur : « je veux des signaux pour être early dès
# la création, pas des tokens vieux de plusieurs jours ».
config.apply_profile("degen")
check(config.chain_max_pair_age_hours("solana") == config.MAX_PAIR_AGE_HOURS
      and config.chain_max_pair_age_hours("bsc") == config.MAX_PAIR_AGE_HOURS
      and config.chain_max_pair_age_hours("base") == config.MAX_PAIR_AGE_HOURS
      and config.chain_max_pair_age_hours("robinhood") == config.MAX_PAIR_AGE_HOURS,
      "En degen, TOUTES les chaines partagent le meme plafond strict (plus de surcharge EVM)",
      f"-> sol={config.chain_max_pair_age_hours('solana')} bsc={config.chain_max_pair_age_hours('bsc')}")

_evm_50h = clean_bsc_token(pair_created_at=time.time() * 1000 - (50 * 3_600_000))
_rej_evm, _rs_evm = evaluate_rejection(_evm_50h)
check(any("trop ancienne" in r for r in _rs_evm),
      "En degen, une paire BNB de 50 h est desormais ecartee pour l'age, comme sur Solana",
      f"-> {_rs_evm}")
_sol_50h = clean_bsc_token(chain="solana",
                           pair_created_at=time.time() * 1000 - (50 * 3_600_000))
_rej_sol, _rs_sol = evaluate_rejection(_sol_50h)
check(any("trop ancienne" in r for r in _rs_sol),
      "La MEME paire de 50 h sur Solana reste ecartee pour l'age (fenetre 6 h intacte)",
      f"-> {_rs_sol}")

# En profil « quality » (qui vise justement des tokens installes depuis
# plusieurs jours), la surcharge EVM s'applique toujours : une paire BNB de
# 50 h n'y est PAS ecartee.
config.apply_profile("quality")
check(config.chain_max_pair_age_hours("bsc") >= 96
      and config.chain_max_pair_age_hours("base") >= 96
      and config.chain_max_pair_age_hours("robinhood") >= 96,
      "En quality, la surcharge EVM (>= 96h) s'applique toujours sur BNB/Base/Robinhood",
      f"-> bsc={config.chain_max_pair_age_hours('bsc')}")
_evm_50h_q = clean_bsc_token(pair_created_at=time.time() * 1000 - (50 * 3_600_000))
_rej_evm_q, _rs_evm_q = evaluate_rejection(_evm_50h_q)
check(not any("trop ancienne" in r for r in _rs_evm_q),
      "En quality, une paire BNB de 50 h n'est pas ecartee pour l'age (fenetre EVM elargie)",
      f"-> {_rs_evm_q}")

# La surcharge n'abaisse jamais sous le plafond du profil, meme en quality
# avec un plafond global artificiellement etroit.
_prev = config.MAX_PAIR_AGE_HOURS
config.MAX_PAIR_AGE_HOURS = 45 * 24
try:
    check(config.chain_max_pair_age_hours("bsc") == 45 * 24,
          "chain_max_pair_age_hours n'abaisse jamais sous le plafond du profil (cas quality)")
finally:
    config.MAX_PAIR_AGE_HOURS = _prev
config.apply_profile("degen")   # restaure le profil attendu par la suite des tests


print("\n=== 8. L'auto-correction ne peut plus désarmer la sécurité ===")
from core import self_tuning

hostile_weights = dict(config.DEFAULT_SCORE_WEIGHTS)
hostile_weights["safety"] = 2
hostile_weights["holder_distribution"] = 2
db.set_state("score_weights", hostile_weights)
db.set_state("score_weights_version", config.SCORE_WEIGHTS_VERSION)
loaded = self_tuning.load_weights()
check(loaded["safety"] == config.DEFAULT_SCORE_WEIGHTS["safety"],
      "Un poids de sécurité écrasé en base est restauré au chargement",
      f"-> {loaded['safety']}")
check(loaded["holder_distribution"] == config.DEFAULT_SCORE_WEIGHTS["holder_distribution"],
      "Idem pour le poids de distribution de l'offre")

# Poids appris effondrés (tous les apprenables au plancher sauf un collé à
# son plafond — même schéma que l'effondrement « liquidity_quality=43.1 » vu
# dans les journaux, transposé au socle apprenable actuel) => réinitialisation.
collapsed = dict(config.DEFAULT_SCORE_WEIGHTS)
for k in collapsed:
    if k not in config.PINNED_SCORE_WEIGHTS:
        collapsed[k] = config.WEIGHT_MIN_BOUND
collapsed["momentum_1h"] = config.WEIGHT_MAX_BOUND
db.set_state("score_weights", collapsed)
db.set_state("score_weights_version", config.SCORE_WEIGHTS_VERSION)
loaded_collapsed = self_tuning.load_weights()
check(loaded_collapsed == dict(config.DEFAULT_SCORE_WEIGHTS),
      "Des poids appris effondrés (un seul critère domine) sont détectés et réinitialisés",
      f"-> {sorted(loaded_collapsed.items(), key=lambda kv: -kv[1])[:3]}")

# Poids d'une structure obsolète (version différente) => réinitialisation.
db.set_state("score_weights", dict(config.DEFAULT_SCORE_WEIGHTS))
db.set_state("score_weights_version", config.SCORE_WEIGHTS_VERSION - 1)
check(self_tuning.load_weights() == dict(config.DEFAULT_SCORE_WEIGHTS),
      "Des poids sauvegardés sous une version antérieure sont réinitialisés")
db.set_state("score_weights", None)
db.set_state("score_weights_version", None)

check("liquidity_quality" in config.PINNED_SCORE_WEIGHTS
      and "smart_money" in config.PINNED_SCORE_WEIGHTS
      and config.WEIGHT_MAX_BOUND <= 12,
      "Le socle qualité (liquidité, smart money) est désormais épinglé et le "
      "plafond d'un poids apprenable est abaissé")

normalized_weights = self_tuning._normalize({**config.DEFAULT_SCORE_WEIGHTS, "momentum_1h": 40})
check(abs(sum(normalized_weights.values()) - 100) < 0.01,
      f"La somme des poids est renormalisée à 100 ({sum(normalized_weights.values()):.1f})")
check(normalized_weights["safety"] == config.DEFAULT_SCORE_WEIGHTS["safety"],
      "La renormalisation ne contourne pas l'épinglage de la sécurité")


print("\n=== 9. Biais de survie : un rug est enregistré comme perte totale ===")
from core.performance_tracker import _detect_rug

signal = {"liquidity": 50_000, "ticker": "RUG"}
check(_detect_rug(signal, {"pair_found": False, "liquidity": 0}) is not None,
      "Une paire disparue des indexeurs est identifiée comme un rug")
check(_detect_rug(signal, {"pair_found": True, "liquidity": 120}) is not None,
      "Une liquidité résiduelle de 120$ est identifiée comme un rug")
check(_detect_rug(signal, {"pair_found": True, "liquidity": 5_000}) is not None,
      "Une liquidité effondrée de 90% est identifiée comme un rug")
check(_detect_rug(signal, {"pair_found": True, "liquidity": 45_000}) is None,
      "Une simple baisse de 10% n'est PAS traitée comme un rug")

sig_id = db.insert_signal({
    "chain": "bsc", "contract": "0xRUGGED", "ticker": "RUG",
    "score": 85, "entry_price": 0.01, "features": {"safety": 0.3},
    "tier": config.TIER_SIGNAL, "verified": True,
})
db.record_performance_check(sig_id, "6h", 0.01, 0.0, -100.0)
training = db.get_training_data(horizon="6h")
check(any(r == -100.0 for _, r in training),
      "Le rug entre bien dans les données d'apprentissage à -100%")
rug_stats = db.get_rug_stats()
check(rug_stats["rug_count"] == 1,
      f"Le taux de rug est mesurable ({rug_stats['rug_count']} sur {rug_stats['checked']})")


print("\n=== 10. Deux niveaux distincts en base et dans les alertes ===")
watch_id = db.insert_signal({
    "chain": "solana", "contract": "WATCHMINT", "ticker": "WATCH",
    "score": 62, "tier": config.TIER_WATCH, "verified": False,
    "missing_checks": ["verrouillage de la liquidité"], "features": {},
})
signal_id = db.insert_signal({
    "chain": "solana", "contract": "SIGNALMINT", "ticker": "SIG",
    "score": 88, "tier": config.TIER_SIGNAL, "verified": True,
    "entry_price": 0.02, "features": {},
})
watch_rows = db.get_active_signals(tier=config.TIER_WATCH)
signal_rows = db.get_active_signals(tier=config.TIER_SIGNAL)
check(any(r["contract"] == "WATCHMINT" for r in watch_rows) and
      not any(r["contract"] == "WATCHMINT" for r in signal_rows),
      "Une veille et un signal sont stockés et filtrables séparément")
check(watch_rows[0]["verified"] is False, "Le drapeau verified est bien persisté")

check(db.has_recent_signal("solana", "WATCHMINT", tier=config.TIER_SIGNAL) is False,
      "Un token en veille reste candidat à la promotion en signal (pas bloqué par le dédoublonnage)")

# CORRECTIF — l'âge réel du token (pair_created_at, epoch ms) doit survivre à
# l'aller-retour en base, sinon l'interface n'a que created_at (l'heure de NOTRE
# détection) pour afficher un âge, ce qui a fait apparaître un token de 20+ min
# comme vieux de 2 min à l'écran (confondu avec created_at).
_pair_ms_test = time.time() * 1000 - 27 * 60_000   # 27 min avant maintenant
_age_persist_id = db.insert_signal({
    "chain": "bsc", "contract": "0xAGEPERSIST", "ticker": "AGEP",
    "score": 70, "tier": config.TIER_WATCH, "verified": False,
    "missing_checks": [], "features": {}, "pair_created_at": _pair_ms_test,
})
_age_persist_row = db.get_signal(_age_persist_id)
check(_age_persist_row is not None and _age_persist_row.get("pair_created_at") == _pair_ms_test,
      "pair_created_at (âge réel du token) est bien persisté et relu depuis la base",
      f"-> {_age_persist_row.get('pair_created_at') if _age_persist_row else None}")
check(any(r["contract"] == "0xAGEPERSIST" and r.get("pair_created_at") == _pair_ms_test
          for r in db.get_active_signals(tier=config.TIER_WATCH)),
      "pair_created_at est aussi présent dans get_active_signals (liste chargée au démarrage de l'interface)")

# Absence de pair_created_at (ex. bonding curve sans pool) : la colonne reste
# NULL plutôt que de fabriquer une valeur — l'interface sait alors qu'elle doit
# afficher « âge non confirmé » plutôt qu'un faux âge précis.
_no_pair_id = db.insert_signal({
    "chain": "solana", "contract": "0xNOPAIRAGE", "ticker": "NOPAIR",
    "score": 40, "tier": config.TIER_WATCH, "verified": False,
    "missing_checks": [], "features": {},
})
check(db.get_signal(_no_pair_id).get("pair_created_at") is None,
      "Sans pair_created_at fourni, la colonne reste NULL (pas de faux âge inventé)")

from core.telegram_alerts import format_alert
watch_alert = format_alert({
    "chain": "solana", "contract": "WATCHMINT", "ticker": "WATCH", "name": "Watch",
    "tier": config.TIER_WATCH, "verified": False, "score": 62,
    "market_cap": 20000, "liquidity": 5000,
    "missing_checks": ["verrouillage de la liquidité"], "reasons": ["Liquidité solide."],
}, "fr")
check("NON VÉRIFIÉ" in watch_alert and "Stop Loss" not in watch_alert,
      "L'alerte de veille est marquée NON VÉRIFIÉ et ne propose aucun plan d'entrée")

signal_alert = format_alert({
    "chain": "bsc", "contract": "0xSIG", "ticker": "SIG", "name": "Signal",
    "tier": config.TIER_SIGNAL, "verified": True, "score": 88,
    "market_cap": 250000, "liquidity": 60000, "risk_level": "Modéré",
    "entry_price": 0.0012, "stop_loss": 0.001, "tp1": 0.0015, "tp2": 0.002, "tp3": 0.003,
    "reasons": ["Liquidité solide."],
}, "fr")
check("Stop Loss" in signal_alert and "NON VÉRIFIÉ" not in signal_alert,
      "L'alerte de signal validé garde bien son plan d'entrée complet")

# Avertissement « pas un conseil en investissement » : en anglais, en bas de
# CHAQUE alerte (veille + signal, toutes langues), après les liens.
from core.telegram_alerts import DISCLAIMER
check(DISCLAIMER in watch_alert and DISCLAIMER in signal_alert,
      "Le disclaimer (anglais) figure dans l'alerte de veille ET dans l'alerte de signal")
check(watch_alert.rstrip().endswith(DISCLAIMER) and signal_alert.rstrip().endswith(DISCLAIMER),
      "Le disclaimer est le tout dernier bloc de l'alerte (après les liens)")
check("not financial advice" in DISCLAIMER and "informational purposes" in DISCLAIMER
      and "afford to lose" in DISCLAIMER and "responsibility" in DISCLAIMER,
      "Le disclaimer couvre : pas un conseil, informatif, responsabilité, risque, perte acceptable")
check(format_alert({"chain": "solana", "contract": "X", "tier": config.TIER_WATCH, "verified": False},
                   "en").count(DISCLAIMER) == 1,
      "Le disclaimer n'apparaît qu'une seule fois par alerte")

# Liens d'affiliation Nansen + Binance Web3 dans les alertes Telegram.
from core.telegram_alerts import _build_links
_lk_bsc = _build_links({"chain": "bsc", "contract": "0xABC"})
check("app.nansen.ai/token-god-mode?tokenAddress=0xABC&chain=bnb" in _lk_bsc
      and "ref=GloriousKing225" in _lk_bsc
      and "web3.binance.com/en/token/bsc/0xABC?ref=L3C8VW3Q" in _lk_bsc,
      "Telegram : lien Nansen (chain=bnb) + Binance Web3 avec les refs, pour BSC",
      f"-> {_lk_bsc!r}")
_lk_sol = _build_links({"chain": "solana", "contract": "SoLmint"})
check("chain=solana&tab=transactions&ref=GloriousKing225" in _lk_sol
      and "web3.binance.com/en/token/solana/SoLmint" in _lk_sol
      and "axiom.trade/t/SoLmint/@hunter212" in _lk_sol,
      "Telegram : Solana cumule Axiom + Nansen + Binance Web3")
_lk_rh = _build_links({"chain": "robinhood", "contract": "0xRH"})
check("app.nansen.ai" in _lk_rh and "chain=robinhood" in _lk_rh
      and "web3.binance.com" not in _lk_rh,
      "Telegram : Robinhood a le lien Nansen mais PAS Binance Web3 (chaine non listee)",
      f"-> {_lk_rh!r}")
_lk_arc = _build_links({"chain": "arc", "contract": "0xARC"})
check("axiom.trade/t/0xARC/@hunter212?chain=arc" in _lk_arc
      and "app.nansen.ai/token-god-mode?tokenAddress=0xARC&chain=arc" in _lk_arc
      and "web3.binance.com/en/token/arc/0xARC" in _lk_arc,
      "Telegram : Arc cumule les 3 liens (Axiom + Nansen + Binance Web3, "
      "couverture confirmee en direct pour les trois)",
      f"-> {_lk_arc!r}")
check("0xABC" in _build_links({"chain": "base", "contract": "0xABC"})
      and _build_links({"chain": "bsc", "contract": ""}) == "",
      "Telegram : Base couverte ; contrat vide => aucun lien")


print("\n=== 10bis. Préfiltre marché et budget d'appels réseau ===")
from core.security_checks import evaluate_market_prefilter

# Le préfiltre doit écarter, SANS aucun appel réseau, ce qui sera de toute
# façon rejeté ensuite. C'est lui qui empêche un cycle Solana à 200 candidats
# de déclencher un millier de requêtes.
sans_securite = {k: v for k, v in clean_bsc_token(liquidity=900, volume_1h=50).items()
                 if k != "security"}
check(len(evaluate_market_prefilter(sans_securite)) > 0,
      "Un token trop peu liquide est écarté avant la moindre vérification réseau")
check(len(evaluate_market_prefilter({k: v for k, v in clean_bsc_token().items() if k != "security"})) == 0,
      "Un token sain passe le préfiltre et sera bien vérifié")
check(len(evaluate_market_prefilter({"chain": "bsc"})) > 0,
      "Un candidat sans donnée de marché est écarté au préfiltre")

# --- 10bis-b. « A déjà plongé » / « est sur le point de plonger » ---------
def _pf_reasons(cand):
    return " | ".join(evaluate_market_prefilter(cand))

dumped_24h = clean_bsc_token(contract="0xDUMP24", price_change_24h=-90.0)
check("plong" in _pf_reasons(dumped_24h).lower(),
      "Un token en -90% sur 24h est signalé « a déjà plongé » au préfiltre",
      f"-> {_pf_reasons(dumped_24h)}")

dumped_6h = clean_bsc_token(contract="0xDUMP6", price_change_24h=15.0,
                            price_change_6h=-55.0)
check("plong" in _pf_reasons(dumped_6h).lower(),
      "Un token vert sur 24h mais -55% sur 6h est signalé « a déjà plongé »",
      f"-> {_pf_reasons(dumped_6h)}")

rollover = clean_bsc_token(contract="0xROLL", price_change_1h=60.0,
                           price_change_5m=-20.0)
check("retournement" in _pf_reasons(rollover).lower()
      or "plonger" in _pf_reasons(rollover).lower(),
      "Pompe 1h +60% puis -20% en 5 min => « sur le point de plonger »",
      f"-> {_pf_reasons(rollover)}")

distrib = clean_bsc_token(contract="0xDISTR", buys_h1=40, sells_h1=200,
                          price_change_1h=-8.0)
check("distribution" in _pf_reasons(distrib).lower(),
      "Ventes 5x les achats sur 1h + prix en baisse => distribution active",
      f"-> {_pf_reasons(distrib)}")

check(_pf_reasons(clean_bsc_token(contract="0xNODUMP")) == "",
      "Un token sain (1h +22%, 5m +4%, pas de baisse large) ne déclenche aucun signal de plongée")

# Fenêtre « première minute » : aucune donnée multi-fenêtres -> pas de test
# de plongée, et surtout aucun plantage.
config.apply_profile("degen")
fm_nodata = {"chain": "solana", "contract": "FMND", "ticker": "FMND",
             "is_pump_bonding_curve": True,
             "pair_created_at": time.time() * 1000, "liquidity": 0,
             "market_cap": 5_000}
check(len(evaluate_market_prefilter(fm_nodata)) == 0,
      "Un token 1re minute sans variations n'est pas faussement marqué « a plongé »")

# Le paramètre RPC corrigé : getTokenAccountsByOwner n'accepte qu'une clé.
from data_sources import solana_rpc
rpc_calls = []
solana_rpc.safe_post_json = lambda url, body, headers=None, timeout=8: (
    rpc_calls.append(body), None)[1]
solana_rpc._mint_cache.clear()
solana_rpc.get_creator_holding_pct("MINT1", "DEV1")
filtre = next((c["params"][1] for c in rpc_calls if c["method"] == "getTokenAccountsByOwner"), None)
check(filtre is not None and len(filtre) == 1 and "mint" in filtre,
      "getTokenAccountsByOwner reçoit une seule clé de filtre (le bug renvoyait une erreur RPC à chaque token)",
      f"-> {filtre}")

# Le cache de mint évite de relire trois fois le même compte
solana_rpc._mint_cache.clear()
solana_rpc.safe_post_json = lambda url, body, headers=None, timeout=8: {
    "result": {"value": {"data": {"parsed": {"info": {
        "mintAuthority": None, "freezeAuthority": None,
        "supply": "1000000000000", "decimals": 6}}}}}}
rpc_calls.clear()
_orig_post = solana_rpc.safe_post_json
def _counting(url, body, headers=None, timeout=8):
    rpc_calls.append(body)
    return _orig_post(url, body, headers, timeout)
solana_rpc.safe_post_json = _counting
for _ in range(3):
    solana_rpc.get_mint_authorities("MINTCACHE")
check(len(rpc_calls) == 1,
      f"Trois lectures du même mint ne font qu'un seul appel RPC ({len(rpc_calls)})")

# Pré-chargement groupé (prefetch_mint_authorities) : plusieurs mints en un
# seul appel getMultipleAccounts, au lieu d'un getAccountInfo par mint.
solana_rpc._mint_cache.clear()
rpc_calls.clear()
def _multi_accounts(url, body, headers=None, timeout=8):
    rpc_calls.append(body)
    if body["method"] == "getMultipleAccounts":
        value = []
        for addr in body["params"][0]:
            if addr == "MINTMISSING":
                value.append(None)
            else:
                value.append({"data": {"parsed": {"info": {
                    "mintAuthority": None, "freezeAuthority": None,
                    "supply": "500000000000", "decimals": 6}}}})
        return {"result": {"value": value}}
    return None
solana_rpc.safe_post_json = _multi_accounts
solana_rpc.prefetch_mint_authorities(["MINTA", "MINTMISSING", "MINTB", "MINTA"])
check(len(rpc_calls) == 1,
      f"prefetch_mint_authorities regroupe plusieurs mints (avec doublon) en un seul appel getMultipleAccounts ({len(rpc_calls)})")
check("MINTA" in solana_rpc._mint_cache and "MINTB" in solana_rpc._mint_cache,
      "Les mints valides du lot sont mis en cache après le pré-chargement groupé")
check("MINTMISSING" not in solana_rpc._mint_cache,
      "Un compte absent de la réponse groupée (null) n'est jamais mis en cache")

rpc_calls.clear()
info_a = solana_rpc.get_mint_authorities("MINTA")
check(info_a is not None and info_a["supply"] == 500000.0 and len(rpc_calls) == 0,
      "Un mint pré-chargé est lu depuis le cache, sans appel RPC supplémentaire",
      f"-> info={info_a}, appels={len(rpc_calls)}")

rpc_calls.clear()
solana_rpc.prefetch_mint_authorities(["MINTA", "MINTB"])
check(len(rpc_calls) == 0,
      "Un lot déjà entièrement en cache et frais ne déclenche aucun appel au pré-chargement")

# enrich_batch (chains/solana.py) pré-charge tout le lot avant d'enrichir
# candidat par candidat — c'est le vrai point d'entrée en production.
# enrich_with_security est neutralisé ici : seul le pré-chargement groupé nous
# intéresse, pas le reste du pipeline (GoPlus/RugCheck, hors périmètre et non
# simulés dans ce test).
from chains import solana as _sol_batch
_prefetch_calls = []
_orig_prefetch = solana_rpc.prefetch_mint_authorities
_orig_enrich_one = _sol_batch.enrich_with_security
solana_rpc.prefetch_mint_authorities = lambda mints: _prefetch_calls.append(list(mints))
_sol_batch.enrich_with_security = lambda c: c
_batch_candidates = [
    {"chain": "solana", "contract": "MINTA", "ticker": "A"},
    {"chain": "solana", "contract": "MINTB", "ticker": "B"},
]
_sol_batch.enrich_batch(_batch_candidates)
solana_rpc.prefetch_mint_authorities = _orig_prefetch
_sol_batch.enrich_with_security = _orig_enrich_one
check(_prefetch_calls == [["MINTA", "MINTB"]],
      "chains.solana.enrich_batch pré-charge tout le lot d'un coup avant d'enrichir candidat par candidat",
      f"-> appels de prefetch : {_prefetch_calls}")

# CORRECTIF — concentration : la bonding curve pump.fun n'est PAS un porteur.
# Le propriétaire du compte de token est un PDA propre à chaque token, dont le
# PROGRAMME propriétaire est pump.fun : il faut regarder deux niveaux. Mesuré sur
# des tokens vivants de 22 s : la curve (50 à 99 % de l'offre) était comptée comme
# un « gros porteur », d'où « un seul wallet détient 100 % » sur presque tout.
PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
SYSTEM_PROGRAM = "11111111111111111111111111111111"
_conc_fail_level2 = {"on": False}

def _fake_rpc(method, params):
    if method == "getTokenLargestAccounts":
        return {"value": [{"address": "CURVEACC", "uiAmount": 800000},
                          {"address": "DEVACC", "uiAmount": 30000},
                          {"address": "WHALEACC", "uiAmount": 20000}]}, True
    if method == "getMultipleAccounts":
        wanted = params[0]
        if "CURVEACC" in wanted:   # niveau 1 : propriétaire (wallet) de chaque compte de token
            owner_of = {"CURVEACC": "CURVEPDA", "DEVACC": "DEVWALLET", "WHALEACC": "WHALEWALLET"}
            return {"value": [{"data": {"parsed": {"info": {"owner": owner_of[a]}}}} for a in wanted]}, True
        if _conc_fail_level2["on"]:   # niveau 2 en échec
            return None, False
        prog_of = {"CURVEPDA": PUMP_PROGRAM, "DEVWALLET": SYSTEM_PROGRAM, "WHALEWALLET": SYSTEM_PROGRAM}
        return {"value": [{"owner": prog_of[a]} for a in wanted]}, True
    return None, False

_orig_call, _orig_gma = solana_rpc._call, solana_rpc.get_mint_authorities
solana_rpc._call = _fake_rpc
solana_rpc.get_mint_authorities = lambda mint: {"supply": 1_000_000.0}
try:
    solana_rpc._program_cache.clear()
    _conc = solana_rpc.get_holder_concentration("MINTCONC")
    check(_conc is not None and abs(_conc["top10_holder_pct"] - 5.0) < 1e-9
          and abs(_conc["largest_holder_pct"] - 3.0) < 1e-9 and _conc["accounts_counted"] == 2,
          "Concentration : la bonding curve pump.fun (PDA possédé par le programme) est exclue",
          f"-> {_conc}")

    # Échec du 2e niveau : jamais d'exclusion à tort, on retombe sur l'ancien comportement.
    solana_rpc._program_cache.clear()
    _conc_fail_level2["on"] = True
    _conc_fb = solana_rpc.get_holder_concentration("MINTCONC")
    check(_conc_fb is not None and abs(_conc_fb["top10_holder_pct"] - 85.0) < 1e-9,
          "Concentration : si le 2e niveau échoue, rien n'est exclu à tort (pas d'assouplissement silencieux)",
          f"-> {_conc_fb}")
    check("CURVEPDA" not in solana_rpc._program_cache,
          "Un échec RPC n'est jamais mis en cache comme un verdict")

    # Curve dont le PDA est possédé par le programme SYSTÈME (mesuré sur des tokens
    # vivants : exactement 793 100 000 jetons = réserves initiales d'une curve) :
    # la détection par programme est impuissante, la clé fournie par PumpPortal
    # (`bondingCurveKey`) l'identifie exactement.
    _conc_fail_level2["on"] = False
    def _fake_rpc_sys(method, params):
        if method == "getTokenLargestAccounts":
            return {"value": [{"address": "CURVEACC", "uiAmount": 793_100},
                              {"address": "DEVACC", "uiAmount": 30_000},
                              {"address": "WHALEACC", "uiAmount": 20_000}]}, True
        if method == "getMultipleAccounts":
            wanted = params[0]
            if "CURVEACC" in wanted:
                owner_of = {"CURVEACC": "CURVESYS", "DEVACC": "DEVWALLET", "WHALEACC": "WHALEWALLET"}
                return {"value": [{"data": {"parsed": {"info": {"owner": owner_of[a]}}}} for a in wanted]}, True
            return {"value": [{"owner": SYSTEM_PROGRAM} for _ in wanted]}, True
        return None, False
    solana_rpc._call = _fake_rpc_sys
    solana_rpc._program_cache.clear()
    _no_key = solana_rpc.get_holder_concentration("MINTSYS")
    _with_key = solana_rpc.get_holder_concentration("MINTSYS", exclude_owners={"CURVESYS"})
    check(_no_key is not None and _no_key["largest_holder_pct"] > 79.0,
          "Concentration : sans la clé de la curve, un PDA système est (à tort) vu comme un porteur",
          f"-> {_no_key}")
    check(_with_key is not None and abs(_with_key["top10_holder_pct"] - 5.0) < 1e-9
          and abs(_with_key["largest_holder_pct"] - 3.0) < 1e-9,
          "Concentration : la clé bondingCurveKey de PumpPortal exclut exactement la curve",
          f"-> {_with_key}")
    # Un vrai développeur qui achète sa propre curve reste rejeté : la clé n'exclut QUE la curve.
    _dev_key = solana_rpc.get_holder_concentration("MINTSYS", exclude_owners={"DEVWALLET"})
    check(_dev_key is not None and _dev_key["largest_holder_pct"] > 79.0,
          "Concentration : exclure la curve n'exclut pas un vrai gros porteur (le dev qui achète 79 % reste vu)")

    # Le candidat PumpPortal porte la clé, et enrich_with_security la transmet au RPC.
    from chains import solana as _sol_curve
    _cand_curve = _sol_curve.candidates_from_pump_tokens(
        [{"mint": "M", "symbol": "S", "name": "N", "marketCapSol": 30, "vSolInBondingCurve": 30,
          "vTokensInBondingCurve": 1e9, "pool": "pump", "traderPublicKey": "C",
          "bondingCurveKey": "CURVEKEY123", "_received_at": time.time()}], 150.0)
    check(_cand_curve and _cand_curve[0].get("bonding_curve") == "CURVEKEY123",
          "Le candidat PumpPortal porte la clé de sa bonding curve")
    _seen_excl = {}
    _o = (solana_rpc.get_holder_concentration, solana_rpc.get_mint_authorities,
          solana_rpc.get_creator_holding_pct, _sol_curve.goplus.check_token,
          _sol_curve.rugcheck.get_report)
    solana_rpc.get_holder_concentration = lambda mint, top_n=10, exclude_owners=None: (
        _seen_excl.update(x=exclude_owners) or None)
    solana_rpc.get_mint_authorities = lambda mint: None
    solana_rpc.get_creator_holding_pct = lambda mint, creator: None
    _sol_curve.goplus.check_token = lambda chain, contract: None
    _sol_curve.rugcheck.get_report = lambda contract: {"available": False}
    try:
        _sol_curve.enrich_with_security(dict(_cand_curve[0]))
    finally:
        (solana_rpc.get_holder_concentration, solana_rpc.get_mint_authorities,
         solana_rpc.get_creator_holding_pct, _sol_curve.goplus.check_token,
         _sol_curve.rugcheck.get_report) = _o
    check(_seen_excl.get("x") == {"CURVEKEY123"},
          "enrich_with_security transmet la clé de la curve au calcul de concentration",
          f"-> {_seen_excl}")

    # RugCheck compte la curve parmi ses « top holders » (vérifié sur des rapports
    # réels : 62-98 %). Fusionné par max() avec la mesure RPC qui l'exclut, il
    # écrasait un 1 % réel par un faux 100 %.
    from data_sources import rugcheck as _rc_curve
    _rc_raw = {"topHolders": [
        {"owner": "CURVEK", "pct": 98.9, "insider": False},
        {"owner": "W1", "pct": 1.05, "insider": False},
        {"owner": "W2", "pct": 0.05, "insider": False}]}
    _rc_norm = _rc_curve._normalize(_rc_raw)
    check(_rc_norm["top_holder_pct"] > 99.9 and len(_rc_norm["holders"]) == 3,
          "RugCheck : le chiffre brut compte la curve, mais la liste des porteurs est conservée")
    check(abs(_rc_curve.top_holder_pct_excluding(_rc_norm["holders"], {"CURVEK"}) - 1.10) < 1e-9
          and _rc_curve.top_holder_pct_excluding([{"owner": "CURVEK", "pct": 98.9}], {"CURVEK"}) is None,
          "RugCheck : top_holder_pct_excluding retire la curve (None s'il ne reste aucun porteur)")

    def _enrich_conc(rpc_result, curve_key):
        _o2 = (solana_rpc.get_holder_concentration, solana_rpc.get_mint_authorities,
               solana_rpc.get_creator_holding_pct, _sol_curve.goplus.check_token,
               _sol_curve.rugcheck.get_report)
        solana_rpc.get_holder_concentration = lambda mint, top_n=10, exclude_owners=None: rpc_result
        solana_rpc.get_mint_authorities = lambda mint: None
        solana_rpc.get_creator_holding_pct = lambda mint, creator: None
        _sol_curve.goplus.check_token = lambda chain, contract: None
        _sol_curve.rugcheck.get_report = lambda contract: dict(_rc_norm, available=True)
        try:
            cand = {"contract": "M", "ticker": "T", "is_pump_bonding_curve": True}
            if curve_key:
                cand["bonding_curve"] = curve_key
            return _sol_curve.enrich_with_security(cand)["security"].get("top10_holder_pct")
        finally:
            (solana_rpc.get_holder_concentration, solana_rpc.get_mint_authorities,
             solana_rpc.get_creator_holding_pct, _sol_curve.goplus.check_token,
             _sol_curve.rugcheck.get_report) = _o2

    _rpc_ok = {"top10_holder_pct": 1.0, "largest_holder_pct": 1.0, "accounts_counted": 2}
    check(abs(_enrich_conc(_rpc_ok, "CURVEK") - 1.10) < 1e-9,
          "Curve connue : le faux 100 % de RugCheck n'écrase plus la mesure RPC (max des deux, curve exclue)")
    check(_enrich_conc(None, "CURVEK") is not None and abs(_enrich_conc(None, "CURVEK") - 1.10) < 1e-9,
          "Curve connue et RPC muet : on utilise le chiffre RugCheck recalculé sans la curve")
    check(_enrich_conc(_rpc_ok, None) > 99.9,
          "Sans clé de curve (token migré/DexScreener) : comportement d'origine inchangé (max, mesure la plus défavorable)")
    # Un vrai porteur concentré reste vu : le dev détient 79 %, hors curve.
    _rc_norm["holders"] = [{"owner": "CURVEK", "pct": 20.0}, {"owner": "DEV", "pct": 79.0}]
    check(abs(_enrich_conc({"top10_holder_pct": 79.0, "largest_holder_pct": 79.0}, "CURVEK") - 79.0) < 1e-9,
          "Un vrai gros porteur (dev à 79 %) reste mesuré à 79 % : seule la curve est retirée")
finally:
    _conc_fail_level2["on"] = False
    solana_rpc._call, solana_rpc.get_mint_authorities = _orig_call, _orig_gma
    solana_rpc._program_cache.clear()


print("\n=== 11. Robustesse générale ===")
from data_sources import http_utils

domain = "test.invalid.example"
for _ in range(10):
    http_utils._record(domain, False)
check(http_utils._is_tripped(domain) is True, "Le disjoncteur réseau s'arme après des échecs répétés")
check(http_utils.get_source_health()[domain]["circuit_open"] is True,
      "get_source_health() reflète le disjoncteur ouvert")

# Les clés d'API ne doivent JAMAIS apparaître dans les journaux : l'URL Helius porte
# la clé dans la requête, et l'exception réseau la recopie. Constaté dans un vrai
# journal (console + panneau LOG du dashboard + journaux collés pour de l'aide).
_helius = "https://mainnet.helius-rpc.com/?api-key=00000000-0000-0000-0000-SECRETVALUE1"
check("SECRETVALUE1" not in http_utils._redact(_helius) and "api-key=***" in http_utils._redact(_helius),
      "Journaux : la clé Helius d'une URL est masquée (api-key=***)")
_exc = "HTTPSConnectionPool(host='x'): Max retries exceeded with url: /?api-key=SECRETVALUE2 (Caused by E)"
check("SECRETVALUE2" not in http_utils._redact(_exc),
      "Journaux : la clé recopiée dans le texte d'une exception réseau est masquée aussi")
check(http_utils._redact("https://api.geckoterminal.com/api/v2/networks/arc/new_pools?include=base_token")
      == "https://api.geckoterminal.com/api/v2/networks/arc/new_pools?include=base_token",
      "Journaux : une URL sans secret n'est pas modifiée")

from core.risk_management import compute_risk_plan
plan = compute_risk_plan({"price_usd": 0.001}, mode="safe")
check("effective_risk_pct" in plan and "capped_by_max_position" in plan,
      "compute_risk_plan() expose le risque réellement pris")

from core import self_upgrade
for _ in range(20):
    self_upgrade.record_cycle_candidates("bsc", 0)
report = self_upgrade.get_health_report()
check(report["dry_streaks"]["bsc"] >= 15 and
      any(e["type"] == "dry_source_suspected" for e in report["recent_events"]),
      "self_upgrade détecte une source de découverte silencieusement cassée")
self_upgrade.run_supervision_cycle()

import core.config as core_cfg
check(core_cfg.MIN_LIQUIDITY_USD == config.MIN_LIQUIDITY_USD,
      "core/config.py ré-exporte bien la config racine")
import core.solana as core_solana
check(core_solana.discover_candidates is not None, "core/solana.py ré-exporte bien chains/solana.py")

# --- Priorite des chaines EVM dans un cycle -------------------------------------
# active_chains est un set : l'ordre de traitement doit etre rendu deterministe
# et placer BNB / Base / Robinhood AVANT Solana, sinon le volume Solana (300+
# candidats, RPC throttle) consomme le temps du cycle avant l'EVM.
_prio = config.CHAIN_SCAN_PRIORITY
_active_set = {"solana", "bsc", "base", "robinhood"}
_cycle_order = sorted(_active_set,
                      key=lambda c: (_prio.index(c) if c in _prio else len(_prio), c))
check(_cycle_order == ["bsc", "base", "robinhood", "solana"],
      "Ordre de cycle deterministe : BNB, Base, Robinhood traitees avant Solana",
      f"-> {_cycle_order}")
check(config.CHAIN_SCAN_MULTIPLIER["robinhood"] == 1,
      "Robinhood Chain est scannee a chaque cycle (multiplicateur ramene a 1)")
check(config.CHAIN_MAX_ENRICHMENTS_PER_CYCLE.get("robinhood", 0) > config.MAX_ENRICHMENTS_PER_CYCLE
      and config.CHAIN_MAX_ENRICHMENTS_PER_CYCLE.get("bsc", 0) > config.MAX_ENRICHMENTS_PER_CYCLE
      and config.CHAIN_MAX_ENRICHMENTS_PER_CYCLE.get("base", 0) > config.MAX_ENRICHMENTS_PER_CYCLE,
      "Les chaines EVM ciblees ont un plafond de verifications/cycle plus large que Solana",
      f"-> {config.CHAIN_MAX_ENRICHMENTS_PER_CYCLE}")
check("solana" not in config.CHAIN_MAX_ENRICHMENTS_PER_CYCLE,
      "Solana garde le plafond global (RPC public throttle)")

# --- Ordre de verification : les tokens d'1 minute passent en tete ------------
from core.scanner import order_for_verification
_n = time.time() * 1000
def _fm(sec):   # token de la fenetre premiere minute, age = sec secondes
    return {"contract": f"fm{sec}", "is_pump_bonding_curve": True,
            "pair_created_at": _n - sec * 1000, "liquidity": 0}
def _old(h, liq):
    return {"contract": f"old{h}", "is_pump_bonding_curve": False,
            "pair_created_at": _n - h * 3_600_000, "liquidity": liq}

_ordered, _dropped = order_for_verification(
    [_old(3, 900_000), _old(1, 500_000), _fm(120), _fm(20), _fm(90)], cap=10)
check([c["contract"] for c in _ordered] == ["fm20", "fm90", "fm120", "old3", "old1"]
      and _dropped == 0,
      "Sous le plafond : tokens < 1 min d'abord (plus jeune en tete), puis le reste par liquidite",
      f"-> {[c['contract'] for c in _ordered]}")

_big = [_fm(10 + i) for i in range(20)] + [_old(2, 100_000 + i) for i in range(20)]
_kept, _drop = order_for_verification(_big, cap=25)
check(all(c["contract"].startswith("fm")
          for c in _kept[:config.FIRST_MINUTE_ENRICHMENT_RESERVE])
      and _kept[0]["contract"] == "fm10" and _drop == 15,
      "Au plafond : les creneaux reserves vont aux tokens d'1 min les plus jeunes",
      f"-> tete={[c['contract'] for c in _kept[:3]]} dropped={_drop}")

check(order_for_verification([], 25) == ([], 0),
      "order_for_verification : liste vide => rien a verifier, rien de reporte")

# CORRECTIF — palier « early » (< EARLY_DETECTION_WINDOW_MINUTES, hors 1re
# minute) desormais priorise sur le reste, meme avec moins de liquidite. Avant
# ce correctif, un token de 45 min pouvait passer APRES un token de 5h juste
# parce que ce dernier avait plus de liquidite — contraire a la demande de
# priorite aux tokens detectes des la creation.
def _early(min_, liq):   # token "early" (< 60 min), hors fenetre 1re minute
    return {"contract": f"early{min_}", "is_pump_bonding_curve": False,
            "pair_created_at": _n - min_ * 60_000, "liquidity": liq}

_ordered2, _dropped2 = order_for_verification(
    [_old(5, 900_000), _early(45, 1_000), _early(10, 500), _fm(30)], cap=10)
check([c["contract"] for c in _ordered2] == ["fm30", "early10", "early45", "old5"]
      and _dropped2 == 0,
      "Palier 'early' (< 60 min) priorise sur le reste (liquidite), meme moins liquide",
      f"-> {[c['contract'] for c in _ordered2]}")

# --- 10b-bis. Selection des chaines pour les alertes (toggle_chain) --------
from core.scanner import Scanner as _Sc, ScannerState as _St
_sc_ch = _Sc(_St())
check(_sc_ch.state.active_chains == set(config.ACTIVE_CHAINS),
      "Au demarrage, toutes les chaines de ACTIVE_CHAINS sont actives")

_sc_ch.toggle_chain("bsc", False)
_sc_ch.toggle_chain("base", False)
_sc_ch.toggle_chain("arc", False)
check(_sc_ch.state.active_chains == {"solana", "robinhood"},
      "toggle_chain(False) retire une chaine du scan (donc des alertes)",
      f"-> {sorted(_sc_ch.state.active_chains)}")

_sc_ch.toggle_chain("bsc", True)
check("bsc" in _sc_ch.state.active_chains,
      "toggle_chain(True) reactive la chaine")

# Garde-fou : on ne peut pas tout desactiver.
_sc_ch.toggle_chain("solana", False)
_sc_ch.toggle_chain("bsc", False)
_sc_ch.toggle_chain("arc", False)
_sc_ch.toggle_chain("robinhood", False)   # tentative de retirer la derniere
check(len(_sc_ch.state.active_chains) == 1 and "robinhood" in _sc_ch.state.active_chains,
      "toggle_chain refuse de retirer la derniere chaine active",
      f"-> {sorted(_sc_ch.state.active_chains)}")

# Chaine inconnue : ignoree sans planter.
_before = set(_sc_ch.state.active_chains)
_sc_ch.toggle_chain("dogecoin", True)
check(_sc_ch.state.active_chains == _before,
      "toggle_chain ignore une chaine hors de AVAILABLE_CHAINS")

# Aucun candidat ne doit faire planter le scoring, même vide ou incohérent
for broken in ({}, {"chain": "bsc"}, {"chain": "solana", "security": {}},
               {"chain": "bsc", "liquidity": None, "market_cap": None, "security": None}):
    try:
        compute_score(broken)
        ok = True
    except Exception as exc:
        ok = False
        print("   ", type(exc).__name__, exc)
    check(ok, f"compute_score() survit à un candidat incomplet : {str(broken)[:45]}")


print("\n=== 11bis. Détection DÈS LA CRÉATION : la VEILLE voit enfin les tokens neufs ===")
from core.security_checks import is_early_candidate, evaluate_market_prefilter as _mpf

_now_ms = time.time() * 1000

# --- Un token FRAÎCHEMENT créé, contrat propre, offre saine, mais AUCUN
#     historique de marché (normal : il vient de naître). Avant, ses features
#     de momentum/volume/pression le tiraient à ~0,25 chacune et son score
#     tombait à ~55 : condamné, même en abaissant le curseur. Il remonte
#     maintenant à ~70 (ce qui EST vérifiable dès la création), donc devient
#     réellement « catchable » en réglant le curseur autour de 0,65-0,70.
#     Le curseur reste souverain : à 0,80 il ne s'affiche pas, et c'est voulu.
fresh_gem = {
    "chain": "solana", "contract": "FRESHMINT111", "ticker": "FRESH", "name": "Fresh Gem",
    "liquidity": 18_000, "market_cap": 45_000, "price_usd": 0.0004,
    "pair_created_at": _now_ms - 5 * 60_000,   # 5 minutes
    "is_pump_bonding_curve": False, "dex_paid": False,
    "volume_1h": None, "volume_24h": None,
    "price_change_5m": None, "price_change_1h": None,
    "buys_h1": None, "sells_h1": None,
    "security": {
        "data_available": True, "source": "rpc",
        "mint_authority_active": False, "freeze_authority_active": False,
        "honeypot": None, "lp_locked_pct": None,
        "top10_holder_pct": 14.0, "creator_pct": 1.0, "holder_count": None,
        "metadata_mutable": False,
    },
}
check(is_early_candidate(fresh_gem) is True,
      "Un token de 5 minutes est dans la fenêtre de détection précoce")

fresh_scored = compute_score(fresh_gem)
check(fresh_scored["rejected"] is False,
      "Un token neuf propre n'est pas rejeté d'office")
check(fresh_scored["tier"] == config.TIER_WATCH and fresh_scored["verified"] is False,
      "Il part en VEILLE, explicitement non vérifié (LP et âge pas encore vérifiables)",
      f"-> tier={fresh_scored['tier']}, verified={fresh_scored['verified']}")
check(fresh_scored["features"]["momentum_1h"] is None
      and fresh_scored["features"]["vol_liq_ratio"] is None
      and fresh_scored["features"]["buy_pressure"] is None,
      "Les critères de marché encore inexistants sont EXCLUS du calcul, pas notés 0,25",
      f"-> {[fresh_scored['features'][k] for k in ('momentum_1h','vol_liq_ratio','buy_pressure')]}")
check(fresh_scored["score"] >= 65,
      f"Son score early ({fresh_scored['score']}/100) reflète ce qui EST vérifiable "
      f"dès la création — il n'est plus condamné à ~55, il devient catchable en "
      f"réglant le curseur",
      f"-> score={fresh_scored['score']}")

# --- Le MÊME token, mais âgé de 3h : sans historique de marché à cet âge,
#     l'absence redevient suspecte (features à 0,25) et le score baisse.
mature_same = dict(fresh_gem, pair_created_at=_now_ms - 3 * 3_600_000)
mature_scored = compute_score(mature_same)
check(is_early_candidate(mature_same) is False,
      "À 3h, le token est sorti de la fenêtre de détection précoce")
check(mature_scored["features"]["momentum_1h"] == 0.25,
      "Hors fenêtre, un momentum toujours absent redevient « non vérifié » (0,25)",
      f"-> {mature_scored['features']['momentum_1h']}")
check(mature_scored["score"] < fresh_scored["score"],
      f"Le même token sans données à 3h score plus bas qu'à 5 min "
      f"({mature_scored['score']} < {fresh_scored['score']})")

# --- Le préfiltre marché laisse ENTRER un token neuf peu liquide (il ira en
#     VEILLE, re-testé), mais rejette toujours un token mûr aussi peu liquide.
young_thin = {"chain": "bsc", "contract": "0xYOUNGTHIN", "ticker": "YT",
              "liquidity": 3_000, "market_cap": 40_000, "volume_1h": 120,
              "pair_created_at": _now_ms - 4 * 60_000, "is_pump_bonding_curve": False}
mature_thin = dict(young_thin, contract="0xMATURETHIN",
                   pair_created_at=_now_ms - 5 * 3_600_000)
check(len(_mpf(young_thin)) == 0,
      "Un token de 4 min avec 3k$ de liquidité passe le préfiltre (ira en VEILLE)")
check(len(_mpf(mature_thin)) > 0,
      "Le même profil à 5h est rejeté : à cet âge, 3k$ de liquidité est un pool mort")

# --- Les vetos DURS restent actifs dans la fenêtre early : une pompe verticale
#     ou un dev majoritaire sur un token de 5 minutes est toujours rejeté.
early_vertical = dict(fresh_gem, contract="EARLYPUMP", price_change_1h=650)
check(compute_score(early_vertical)["rejected"] is True,
      "Une pompe de +650% en 1h reste rejetée même sur un token tout neuf")
early_dev_rug = dict(fresh_gem, contract="EARLYDEVRUG", dev_holding_pct=30.0)
check(compute_score(early_dev_rug)["rejected"] is True,
      "Un dev détenant 30% de l'offre fait rejeter le token même à 5 minutes")

# --- Fenetre PREMIERE MINUTE : detection garantie des le reperage -----------
from core.security_checks import is_first_minute_candidate

# Token pump.fun vu il y a 30 s : bonding curve, AUCUNE reserve encore.
fm_tok = {
    "chain": "solana", "contract": "FIRSTMIN30S", "ticker": "FM", "name": "First Minute",
    "liquidity": 0, "market_cap": 5_000, "price_usd": 0.0000004,
    "pair_created_at": _now_ms - 30_000,        # 30 secondes
    "is_pump_bonding_curve": True, "dex_paid": False,
    "volume_1h": None, "volume_24h": None,
    "price_change_5m": None, "price_change_1h": None, "buys_h1": None, "sells_h1": None,
    "security": {
        "data_available": True, "source": "rpc",
        "mint_authority_active": False, "freeze_authority_active": False,
        "honeypot": None, "lp_locked_pct": None,
        "top10_holder_pct": 12.0, "creator_pct": 1.0, "holder_count": None,
        "metadata_mutable": False,
    },
}
check(is_first_minute_candidate(fm_tok) is True,
      "Un token pump.fun repere il y a 30 s est dans la fenetre premiere minute")
check(len(_mpf(fm_tok)) == 0,
      "Le prefiltre marche LAISSE PASSER un token de 30 s a liquidite nulle (ira en VEILLE)",
      f"-> {_mpf(fm_tok)}")
fm_scored = compute_score(fm_tok)
check(fm_scored["rejected"] is False and fm_scored["tier"] == config.TIER_WATCH,
      "Il n'est pas rejete et part en VEILLE (non verifie, pas de plan d'entree)",
      f"-> rejected={fm_scored['rejected']} tier={fm_scored['tier']}")
check(fm_scored["features"]["liquidity_score"] is None,
      "Sa liquidite nulle est EXCLUE du score (pas notee 0,25) tant qu'il est dans la fenetre",
      f"-> {fm_scored['features']['liquidity_score']}")
check(fm_scored["score"] >= config.MIN_SCORE_TO_WATCH,
      f"Son score ({fm_scored['score']}) depasse le seuil de veille : il s'affichera",
      f"-> score={fm_scored['score']} seuil={config.MIN_SCORE_TO_WATCH}")

# Le MEME token 10 min plus tard : hors fenetre, une reserve toujours nulle
# redevient un veto (bonding curve trop faible).
fm_old = dict(fm_tok, contract="FIRSTMIN10M", pair_created_at=_now_ms - 10 * 60_000)
check(is_first_minute_candidate(fm_old) is False and len(_mpf(fm_old)) > 0,
      "Hors fenetre (10 min), une reserve nulle redevient un veto",
      f"-> {_mpf(fm_old)}")

# Les vetos DURS restent actifs dans la fenetre premiere minute.
fm_honeypot = dict(fm_tok, contract="FMHONEY",
                   security=dict(fm_tok["security"], honeypot=True))
check(compute_score(fm_honeypot)["rejected"] is True,
      "Un honeypot avere sur un token de 30 s est REJETE, pas affiche")
fm_concentr = dict(fm_tok, contract="FMCONC",
                   security=dict(fm_tok["security"], top10_holder_pct=95.0))
check(compute_score(fm_concentr)["rejected"] is True,
      "Un top 10 a 95% sur un token de 30 s est REJETE meme dans la fenetre premiere minute")

# MIN_PAIR_AGE_MINUTES protege toujours le palier SIGNAL : un token de 30 s ne
# peut jamais etre verifie, quel que soit son score.
check(fm_scored["verified"] is False,
      "Un token de 30 s ne peut JAMAIS atteindre le palier SIGNAL (MIN_PAIR_AGE_MINUTES intact)")

# Le plafond de cycle reserve des creneaux a la fenetre premiere minute.
_reserve_tokens = [dict(fm_tok, contract=f"FMRES{i}",
                        pair_created_at=_now_ms - (10 + i) * 1000) for i in range(20)]
_liquid_tokens = [{"chain": "solana", "contract": f"LIQ{i}", "liquidity": 500_000,
                   "market_cap": 900_000, "is_pump_bonding_curve": False,
                   "pair_created_at": _now_ms - 3 * 3_600_000} for i in range(20)]
_pool = _reserve_tokens + _liquid_tokens
_kept_fm = [c for c in _pool if is_first_minute_candidate(c)]
check(len(_kept_fm) == 20 and config.FIRST_MINUTE_ENRICHMENT_RESERVE >= 1,
      "Les tokens de la fenetre sont bien identifies pour le creneau reserve du plafond",
      f"-> {len(_kept_fm)} fenetre, reserve={config.FIRST_MINUTE_ENRICHMENT_RESERVE}")


print("\n=== 12. Alertes 100% anglaises et seuil de veille aligné sur le curseur ===")
from core import i18n

# --- 12a. Le catalogue est complet et réversible ---------------------------
# Chaque message doit exister dans les deux langues, et la traduction inverse
# (utilisée pour les textes relus depuis la base) doit retomber sur le bon
# code. C'est ce test qui empêche le retour du bug : ajouter un message sans
# sa version anglaise, ou en formuler un qui ne se reconnaît plus, échoue ici.
manquants = [code for code, entry in i18n.MESSAGES.items()
             if not entry.get("fr") or not entry.get("en") or not entry.get("zh")]
check(not manquants,
      f"Tous les messages ont une version FR, EN et ZH ({len(i18n.MESSAGES)} messages)",
      f"-> manquants : {manquants[:5]}")

echantillon = {"value": "12.3", "max": "25", "min": "80", "mcap": "150,000",
               "risk": "Top holder", "items": "critere-test-abc", "reason": "test",
               "minutes": "3", "buy": "1.0", "sell": "2.0", "count": "2",
               "wallets": "0xAB (3%)", "flags": "phishing_activities"}
non_reversibles = []
non_reversibles_zh = []
for code, entry in i18n.MESSAGES.items():
    rendu_fr = i18n.t(code, "fr", **echantillon)
    attendu_en = i18n.t(code, "en", **echantillon)
    if i18n.translate(rendu_fr, "en") != attendu_en:
        non_reversibles.append(code)
    attendu_zh = i18n.t(code, "zh", **echantillon)
    if i18n.translate(rendu_fr, "zh") != attendu_zh:
        non_reversibles_zh.append(code)
check(not non_reversibles,
      f"Chaque message français est retraduisible en anglais sans ambiguïté "
      f"(échecs : {non_reversibles[:3]})")
check(not non_reversibles_zh,
      f"Chaque message français est retraduisible en chinois sans ambiguïté "
      f"(échecs : {non_reversibles_zh[:3]})")
check(i18n.SUPPORTED_LANGS == ("fr", "en", "zh"),
      "Le chinois est déclaré comme langue supportée par le catalogue")

# --- 12b. Une alerte anglaise ne contient plus un mot de français -----------
# On fabrique un candidat qui déclenche un maximum de messages, on récupère
# ses vraies raisons produites par le moteur, et on vérifie l'alerte rendue.
from core.telegram_alerts import format_alert, resolve_languages

rug_reel = clean_bsc_token()
rug_reel["security"]["honeypot"] = True
rug_reel["security"]["top10_holder_pct"] = 71.0
resultat_rug = compute_score(rug_reel)

veille_reelle = clean_bsc_token()
veille_reelle["security"] = {"data_available": False,
                             "unavailable_reason": i18n.t("unavailable.goplus_not_indexed")}
resultat_veille = compute_score(veille_reelle)

# Marqueurs sans ambiguïté : aucun d'eux ne peut apparaître dans un texte
# anglais correct (« taxe » a été écarté, il est contenu dans « Taxes »).
MOTS_FRANCAIS = ("vérifi", "sécurité", "liquidité", "détenteur", "détient",
                 "d'achat", "aucune", "indisponible", "insuffisant",
                 "verrouill", "seulement", "peut ", "offre", "créateur",
                 "trop ", "élevé", "hausse", "achats")

def contient_du_francais(texte: str) -> list[str]:
    minuscule = texte.lower()
    return [mot for mot in MOTS_FRANCAIS if mot in minuscule]

alerte_veille_en = format_alert({**resultat_veille, "name": "Watch", "ticker": "WTC"}, "en")
fuites = contient_du_francais(alerte_veille_en)
if fuites:
    print("    fuite(s) :", fuites)
    print(alerte_veille_en)
check(not fuites, "L'alerte de VEILLE en anglais ne contient aucun texte français")

alerte_rejet_en = format_alert({**resultat_rug, "name": "Rug", "ticker": "RUG",
                                "tier": config.TIER_SIGNAL, "verified": True,
                                "risk_level": "Élevé"}, "en")
fuites = contient_du_francais(alerte_rejet_en)
if fuites:
    print("    fuite(s) :", fuites)
    print(alerte_rejet_en)
check(not fuites, "Les motifs de veto rendus en anglais ne contiennent aucun texte français")
check("Risk level: High" in alerte_rejet_en,
      "Le niveau de risque est traduit lui aussi (Élevé -> High)")

# Le français reste disponible pour l'interface, il n'est pas cassé au passage.
alerte_veille_fr = format_alert({**resultat_veille, "name": "Watch", "ticker": "WTC"}, "fr")
check("NON VÉRIFIÉ" in alerte_veille_fr, "Le gabarit français reste fonctionnel pour l'interface")

check(resolve_languages() == ["en"],
      "Les alertes ne partent que dans la langue configurée (ALERT_LANGUAGES)")
check(resolve_languages(["fr", "zh", "fr"]) == ["fr"],
      "Une langue sans gabarit est écartée au lieu de dupliquer l'anglais")

# --- 12c. Le curseur SCORE MIN est un plancher STRICT -----------------------
# L'utilisateur règle 0.80 et voit s'afficher des VEILLE à 0.61 : c'est le bug.
# Par défaut (WATCH_SCORE_MARGIN = 0), le seuil de veille est EXACTEMENT le
# curseur — rien en dessous ne s'affiche ni ne part en alerte.
from core.scanner import Scanner, ScannerState

etat = ScannerState()
etat.running = True            # ces tests unitaires ne passent pas par run()
etat.telegram_enabled = False  # aucun appel réseau Telegram pendant les tests
scanner_test = Scanner(etat)

for cursor in (80, 90, 72, 65):
    scanner_test.set_min_score(cursor)
    expected = cursor if config.WATCH_SCORE_MARGIN == 0 else max(
        config.MIN_SCORE_TO_WATCH, cursor - config.WATCH_SCORE_MARGIN)
    check(scanner_test.watch_threshold() == expected,
          f"Curseur à {cursor/100:.2f} => seuil de veille {expected}/100 "
          f"(marge {config.WATCH_SCORE_MARGIN})",
          f"-> {scanner_test.watch_threshold()}")

# Reproduction exacte du bug signalé : curseur 0.80, un token VEILLE à 61/100.
stats_test = {"below": 0, "watch": 0}
scanner_test.set_min_score(80)
faible = {**resultat_veille, "score": 61.0, "contract": "0xSCORE61", "ticker": "M61"}
scanner_test._handle_watch("bsc", faible, faible, stats_test)
check(stats_test["below"] == 1 and stats_test["watch"] == 0,
      "Curseur à 0.80 : un memecoin à 0.61 n'est NI affiché NI alerté en veille",
      f"-> below={stats_test['below']}, watch={stats_test['watch']}")

# Le même token repasse en veille dès que le curseur descend à 0.60.
stats_test = {"below": 0, "watch": 0}
scanner_test.set_min_score(60)
scanner_test._handle_watch("bsc", dict(faible, contract="0xSCORE61B"), faible, stats_test)
check(stats_test["watch"] == 1,
      "Curseur abaissé à 0.60 : le même token à 0.61 devient visible en veille")

# --- 12c-bis. Le curseur fait autorité SANS exception ---------------------
# Même un token « première minute » (bonding curve pump.fun, capté à l'instant)
# est masqué s'il est sous le curseur : plus de dérogation. Pour le voir, on
# baisse le curseur (il descend maintenant jusqu'à 30/100).
config.apply_profile("degen")
fm_watch = {
    "chain": "solana", "contract": "FMWATCH1", "ticker": "FM1", "name": "FirstMin",
    "is_pump_bonding_curve": True,
    "pair_created_at": time.time() * 1000,   # capté à l'instant -> < 3 min
    "tier": config.TIER_WATCH, "verified": False, "score": 32.0,
    "missing_checks": ["bonding curve"], "reasons": [], "features": {},
}
check(is_first_minute_candidate(fm_watch) is True,
      "Un token bonding curve capté à l'instant est bien dans la fenêtre 1re minute")

stats_fm = {"below": 0, "watch": 0}
scanner_test.set_min_score(80)
scanner_test._handle_watch("solana", fm_watch, fm_watch, stats_fm)
check(stats_fm["below"] == 1 and stats_fm["watch"] == 0,
      "Curseur à 0.80 : une VEILLE 1re minute à 0.32 est masquée comme les autres",
      f"-> below={stats_fm['below']}, watch={stats_fm['watch']}")

# Curseur abaissé à 0.30 (nouveau plancher) : la même VEILLE devient visible.
stats_fm2 = {"below": 0, "watch": 0}
scanner_test.set_min_score(30)
check(scanner_test.state.min_score == 30 and config.MIN_SCORE_RANGE[0] == 30,
      "Le curseur descend jusqu'à 30/100 (MIN_SCORE_RANGE)")
scanner_test._handle_watch("solana", dict(fm_watch, contract="FMWATCH1B"),
                           fm_watch, stats_fm2)
check(stats_fm2["watch"] == 1 and stats_fm2["below"] == 0,
      "Curseur à 0.30 : la VEILLE 1re minute à 0.32 s'affiche")

# Une VEILLE à 0.28 reste sous le plancher, même 1re minute.
stats_fm3 = {"below": 0, "watch": 0}
scanner_test._handle_watch("solana", dict(fm_watch, contract="FMWATCH1C", score=28.0),
                           dict(fm_watch, score=28.0), stats_fm3)
check(stats_fm3["below"] == 1 and stats_fm3["watch"] == 0,
      "Curseur à 0.30 : une VEILLE à 0.28 est sous le plancher")

# --- 12d. STOP coupe réellement les alertes du cycle en cours -------------
# Avant : cliquer STOP au milieu d'un cycle laissait le bot finir d'alerter
# tous les candidats déjà découverts. Le cycle teste désormais _is_stopping()
# à chaque point d'émission.
emitted = []
etat2 = ScannerState()
etat2.on_new_signal = lambda sig: emitted.append(sig)
etat2.telegram_enabled = False
scanner_stop = Scanner(etat2)
scanner_stop.set_min_score(60)

bon_watch = {
    "chain": "solana", "contract": "STOPMINT", "ticker": "STOP", "name": "Stop",
    "tier": config.TIER_WATCH, "verified": False, "score": 95.0,
    "missing_checks": ["âge"], "reasons": [], "features": {},
}
etat2.running = True   # simule un scan en cours
scanner_stop._stop_event.clear()
check(scanner_stop._is_stopping() is False, "Scan en cours : _is_stopping() est False")
scanner_stop.stop()
check(scanner_stop._is_stopping() is True, "Après stop(), _is_stopping() passe à True immédiatement")
scanner_stop._handle_watch("solana", bon_watch, bon_watch, {"below": 0, "watch": 0})
check(emitted == [],
      "Après STOP, un candidat même excellent ne déclenche plus aucune alerte de veille")

# --- 12e. L'entrée VEILLE émise ne porte plus de drapeau de contournement --
# Le dashboard applique strictement le curseur ; aucune carte ne le contourne.
config.apply_profile("degen")
emis_fm = []
etat_fm = ScannerState()
etat_fm.on_new_signal = lambda s: emis_fm.append(s)
etat_fm.telegram_enabled = False
etat_fm.running = True
sc_fm = Scanner(etat_fm)
sc_fm._stop_event.clear()
sc_fm.set_min_score(30)   # curseur au plancher pour que la VEILLE à 0.35 passe
cand_fm = {
    "chain": "solana", "contract": "FMFLAG1", "ticker": "FMF", "name": "FirstMinFlag",
    "is_pump_bonding_curve": True, "pair_created_at": time.time() * 1000,
    "tier": config.TIER_WATCH, "verified": False, "score": 35.0,
    "missing_checks": ["bonding curve"], "reasons": [], "features": {},
}
sc_fm._handle_watch("solana", cand_fm, cand_fm, {"below": 0, "watch": 0})
check(len(emis_fm) == 1 and "first_minute" not in emis_fm[0],
      "VEILLE émise sous curseur 0.30 : plus de clé first_minute, curseur seul juge",
      f"-> emis={len(emis_fm)}, keys_first_minute={'first_minute' in emis_fm[0] if emis_fm else None}")

# --- 12e-bis. Filtre d'âge des alertes (boutons 1/5/10/15/30 min de l'UI) ---
# Filtre d'AFFICHAGE/ALERTE uniquement : ne touche ni au scan, ni au scoring,
# ni à MIN_PAIR_AGE_MINUTES (sécurité). Un candidat hors fenêtre reste
# scanné/vérifié/suivi, il n'est simplement pas affiché ni envoyé sur Telegram
# tant qu'il ne rentre pas dans la fenêtre choisie.
scanner_age = Scanner(ScannerState())
scanner_age.state.running = True
scanner_age.state.telegram_enabled = False

check(scanner_age.state.max_alert_age_minutes is None,
      "Filtre d'âge désactivé par défaut (tous âges affichés)")

scanner_age.set_max_alert_age(15)
check(scanner_age.state.max_alert_age_minutes == 15,
      "set_max_alert_age(15) : filtre réglé à 15 minutes")
scanner_age.set_max_alert_age(0)
check(scanner_age.state.max_alert_age_minutes is None,
      "set_max_alert_age(0) : désactive le filtre, comme le bouton TOUT de l'UI")
scanner_age.set_max_alert_age(-5)
check(scanner_age.state.max_alert_age_minutes is None,
      "set_max_alert_age(-5) : valeur invalide traitée comme désactivée")

_now_ms = time.time() * 1000
_age_jeune = {"pair_created_at": _now_ms - 2 * 60_000}     # 2 min
_age_vieux = {"pair_created_at": _now_ms - 45 * 60_000}    # 45 min
_age_inconnu = {"pair_created_at": None}

scanner_age.state.max_alert_age_minutes = None
check(scanner_age._passes_age_filter(_age_jeune) and scanner_age._passes_age_filter(_age_vieux)
      and scanner_age._passes_age_filter(_age_inconnu),
      "Filtre désactivé : tous les âges passent, y compris inconnu")

scanner_age.state.max_alert_age_minutes = 15
check(scanner_age._passes_age_filter(_age_jeune) is True,
      "Filtre 15 min : un token de 2 min passe")
check(scanner_age._passes_age_filter(_age_vieux) is False,
      "Filtre 15 min : un token de 45 min est exclu")
check(scanner_age._passes_age_filter(_age_inconnu) is True,
      "Filtre 15 min : un âge inconnu passe (on ne présume pas qu'il est hors fenêtre)")

# Intégration VEILLE : hors filtre d'âge, ni affichée ni alertée, mais reste
# suivie en file de promotion (elle pourra quand même devenir un SIGNAL).
scanner_age.set_min_score(30)
scanner_age.set_max_alert_age(15)
_watch_old = {
    "chain": "solana", "contract": "OLDWATCH1", "ticker": "OLDW", "name": "OldWatch",
    "tier": config.TIER_WATCH, "verified": False, "score": 90.0,
    "pair_created_at": _now_ms - 45 * 60_000,
    "missing_checks": ["âge"], "reasons": [], "features": {},
}
_stats_age = {"below": 0, "watch": 0, "filtered_age": 0}
scanner_age._handle_watch("solana", _watch_old, _watch_old, _stats_age)
check(_stats_age["filtered_age"] == 1 and _stats_age["watch"] == 0,
      "VEILLE hors filtre d'âge (45 min > 15 min) : ni affichée ni alertée",
      f"-> {_stats_age}")
check("OLDWATCH1" in scanner_age._watchlist,
      "Mais le token reste suivi malgré le filtre d'affichage (promotion en SIGNAL toujours possible)")

_watch_young = dict(_watch_old, contract="YOUNGWATCH1", pair_created_at=_now_ms - 2 * 60_000)
_stats_age2 = {"below": 0, "watch": 0, "filtered_age": 0}
scanner_age._handle_watch("solana", _watch_young, _watch_young, _stats_age2)
check(_stats_age2["watch"] == 1 and _stats_age2["filtered_age"] == 0,
      "VEILLE dans le filtre d'âge (2 min <= 15 min) : affichée normalement")

# Intégration SIGNAL : même logique, via _process_candidate (compute_score
# monkeypatché pour isoler le test du moteur de scoring réel).
from data_sources import dexscreener as _dsx_age
import core.scanner as _sc_mod

_orig_compute_score = _sc_mod.compute_score
_orig_dex_paid = _dsx_age.check_dex_paid
_dsx_age.check_dex_paid = lambda chain, contract: False
emitted_age: list = []
scanner_age.state.on_new_signal = lambda sig: emitted_age.append(sig)
_stats_sig_age = {"seen": 0, "prefiltered": 0, "rejected": 0, "below": 0,
                   "watch": 0, "signal": 0, "filtered_age": 0}
try:
    _sc_mod.compute_score = lambda candidate: {
        **candidate, "rejected": False, "score": 90.0, "tier": config.TIER_SIGNAL,
        "reasons": [], "missing_checks": [], "features": {},
    }
    _sig_old = {"chain": "bsc", "contract": "OLDSIG1", "ticker": "OLDS",
                "price_usd": 0.001, "pair_created_at": _now_ms - 45 * 60_000}
    scanner_age._process_candidate("bsc", _sig_old, _stats_sig_age)
    _sig_young = {"chain": "bsc", "contract": "YOUNGSIG1", "ticker": "YOUNGS",
                  "price_usd": 0.001, "pair_created_at": _now_ms - 2 * 60_000}
    scanner_age._process_candidate("bsc", _sig_young, _stats_sig_age)
finally:
    _sc_mod.compute_score = _orig_compute_score
    _dsx_age.check_dex_paid = _orig_dex_paid

check(all(e.get("contract") != "OLDSIG1" for e in emitted_age)
      and any(e.get("contract") == "YOUNGSIG1" for e in emitted_age),
      "SIGNAL hors filtre d'âge (45 min) non émis, SIGNAL dans le filtre (2 min) émis normalement",
      f"-> émis : {[e.get('contract') for e in emitted_age]}")
check(_stats_sig_age["filtered_age"] == 1,
      "Le SIGNAL masqué par le filtre d'âge est bien compté à part (pas confondu avec 'sous le seuil')")

# --- 12e-ter. Mémoire des rejets de sécurité (pas de re-vérification à chaque cycle) ---
# Mesuré sur un vrai journal : « .agent », « RWT », « VISE » rejetés à CHAQUE
# cycle, et ces re-vérifications occupaient les créneaux dus aux tokens frais.
_sc_rej = Scanner(ScannerState())
_sc_rej.state.running = True
_prev_ttl = config.REJECTED_RECHECK_SECONDS
config.REJECTED_RECHECK_SECONDS = 600
try:
    check(_sc_rej._is_recently_rejected("solana", "MintAAA") is False,
          "Rejets : un token jamais rejeté n'est pas marqué")
    _sc_rej._remember_rejection("solana", "MintAAA")
    check(_sc_rej._is_recently_rejected("solana", "MintAAA") is True,
          "Rejets : un token rejeté n'est pas re-vérifié pendant le délai")
    check(_sc_rej._is_recently_rejected("solana", "minta aa".replace(" ", "")) is False,
          "Rejets : les adresses Solana restent sensibles à la casse")
    check(_sc_rej._is_recently_rejected("bsc", "MintAAA") is False,
          "Rejets : la mémoire est propre à chaque chaîne")
    _sc_rej._remember_rejection("bsc", "0xAbC")
    check(_sc_rej._is_recently_rejected("bsc", "0xabc") is True,
          "Rejets : les adresses EVM sont comparées sans tenir compte de la casse")

    # Expiration : passé le délai, le token repasse par toute la chaîne de vérification.
    _sc_rej._rejected[("solana", "MintAAA")] = time.time() - 601
    check(_sc_rej._is_recently_rejected("solana", "MintAAA") is False
          and ("solana", "MintAAA") not in _sc_rej._rejected,
          "Rejets : après le délai, le token est re-vérifié (le rejet n'est pas éternel)")

    # Un rejet réel de _process_candidate alimente la mémoire.
    import core.scanner as _sc_rej_mod
    _orig_cs_rej = _sc_rej_mod.compute_score
    try:
        _sc_rej_mod.compute_score = lambda cand: {**cand, "rejected": True, "score": 0,
                                                  "reasons": ["Concentration excessive"], "tier": None}
        _st_rej = {"rejected": 0}
        _sc_rej._process_candidate("solana", {"contract": "MintRug", "ticker": "RUG"}, _st_rej)
    finally:
        _sc_rej_mod.compute_score = _orig_cs_rej
    check(_st_rej["rejected"] == 1 and _sc_rej._is_recently_rejected("solana", "MintRug"),
          "Rejets : un veto de sécurité est mémorisé par _process_candidate")

    # Le cycle saute les tokens récemment rejetés au lieu de les ré-enrichir.
    _enriched_seen: list = []
    class _FakeMod:
        def enrich_batch(self, cands, should_stop=None):
            _enriched_seen.extend(c["contract"] for c in cands)
            return cands
    _orig_cs2 = _sc_rej_mod.compute_score
    _orig_mpf = _sc_rej_mod.evaluate_market_prefilter
    try:
        _sc_rej_mod.compute_score = lambda cand: {**cand, "rejected": True, "score": 0,
                                                  "reasons": ["x"], "tier": None}
        _sc_rej_mod.evaluate_market_prefilter = lambda cand: []
        _sc_rej._process_chain("solana", _FakeMod(),
                               [{"contract": "MintRug", "ticker": "RUG"},
                                {"contract": "MintFresh", "ticker": "NEW"}])
    finally:
        _sc_rej_mod.compute_score = _orig_cs2
        _sc_rej_mod.evaluate_market_prefilter = _orig_mpf
    check(_enriched_seen == ["MintFresh"],
          "Rejets : le token déjà rejeté n'est pas ré-enrichi, le token frais l'est",
          f"-> {_enriched_seen}")

    # Changer de profil invalide les verdicts (les vetos dépendent du profil).
    _sc_rej.set_scan_profile("degen")
    check(_sc_rej._is_recently_rejected("solana", "MintRug") is False,
          "Rejets : un changement de profil vide la mémoire des rejets")

    # 0 = désactivé (comportement d'origine : re-vérification à chaque cycle).
    config.REJECTED_RECHECK_SECONDS = 0
    _sc_rej._remember_rejection("solana", "MintOff")
    check(_sc_rej._is_recently_rejected("solana", "MintOff") is False,
          "Rejets : REJECTED_RECHECK_SECONDS = 0 désactive la mémoire")
finally:
    config.REJECTED_RECHECK_SECONDS = _prev_ttl

# --- 12e-quater. Chemin rapide PumpPortal : alerte dans les premières secondes ---
# Avant : une création pump.fun (poussée en millisecondes) attendait le PROCHAIN
# CYCLE de scan (toutes les chaînes, 25 vérifications à la suite, puis 45 s).
from data_sources import pumpportal as _pp_fast
from data_sources import dexscreener as _dsx_fast
config.apply_profile("degen")
_now_f = time.time()
def _pump_tok(mint, age_s, mcap_sol=30):
    return {"mint": mint, "symbol": mint, "name": mint, "marketCapSol": mcap_sol,
            "vSolInBondingCurve": 30, "vTokensInBondingCurve": 1e9, "pool": "pump",
            "traderPublicKey": "CREATOR", "_received_at": time.time() - age_s}

_pool_f = []
_visible_f = set()   # mints que le « RPC » simulé connaît déjà
_orig_recent, _orig_start, _orig_price, _orig_vis = (
    _pp_fast.get_recent_tokens, _pp_fast.ensure_started,
    _dsx_fast.get_sol_price_usd, solana_rpc.visible_mints)
solana_rpc.visible_mints = lambda mints: {m for m in mints if m in _visible_f}
_pp_fast.get_recent_tokens = lambda max_age_seconds=600: [
    t for t in _pool_f if time.time() - t["_received_at"] <= max_age_seconds]
_pp_fast.ensure_started = lambda: None
_dsx_fast.get_sol_price_usd = lambda: 150.0
try:
    _sc_fast = Scanner(ScannerState())
    _sc_fast.state.running = True
    _proc_calls = []
    _sc_fast._process_chain = lambda chain, module, cands: _proc_calls.append(
        (chain, [c["contract"] for c in cands], cands))

    _pool_f[:] = [_pump_tok("TOO_YOUNG", 0.5)]
    _sc_fast._fast_pump_tick()
    check(_proc_calls == [] and "TOO_YOUNG" not in _sc_fast._fast_seen,
          "Chemin rapide : un token de < 2 s attend (le RPC ne connaît pas encore son mint)")

    # Le RPC ne voit pas encore le mint : on NE vérifie PAS dans le vide (dossier
    # vide = alerte sans aucun contrôle réel). Le token attend, sans rafale d'appels.
    _pool_f[:] = [_pump_tok("FRESH1", 5)]
    _sc_fast._fast_pump_tick()
    check(_proc_calls == [] and "FRESH1" in _sc_fast._fast_pending and "FRESH1" not in _sc_fast._fast_seen,
          "Chemin rapide : mint pas encore visible du RPC => aucune vérification à vide, le token attend")
    _probe_calls = []
    solana_rpc.visible_mints = lambda mints: (_probe_calls.append(list(mints)) or set())
    _sc_fast._fast_pump_tick()
    check(_probe_calls == [],
          "Chemin rapide : pas de nouvelle sonde avant FAST_PUMP_RETRY_SECONDS (pas de martelage du RPC)")
    solana_rpc.visible_mints = lambda mints: {m for m in mints if m in _visible_f}

    # Dès que le RPC voit le mint : vérification COMPLÈTE immédiate (même pipeline).
    _visible_f.add("FRESH1")
    _sc_fast._fast_pending["FRESH1"]["next_at"] = 0.0
    _sc_fast._fast_pump_tick()
    check(len(_proc_calls) == 1 and _proc_calls[0][0] == "solana" and _proc_calls[0][1] == ["FRESH1"]
          and _proc_calls[0][2][0]["is_pump_bonding_curve"] is True
          and "FRESH1" not in _sc_fast._fast_pending,
          "Chemin rapide : dès que le RPC voit le mint, le token est vérifié aussitôt, sans attendre le cycle",
          f"-> {[c[:2] for c in _proc_calls]}")
    _sc_fast._fast_pump_tick()
    check(len(_proc_calls) == 1, "Chemin rapide : un même token n'est jamais traité deux fois")

    _proc_calls.clear()
    _pool_f[:] = [_pump_tok(f"B{i}", 3 + i) for i in range(10)]   # B0 = le plus frais
    _visible_f.update(f"B{i}" for i in range(10))
    _sc_fast._fast_pump_tick()
    _first_batch = _proc_calls[0][1]
    check(len(_first_batch) == config.FAST_PUMP_BATCH and _first_batch[0] == "B0",
          "Chemin rapide : au plus FAST_PUMP_BATCH créations par passage, les plus fraîches d'abord",
          f"-> {_first_batch}")
    _sc_fast._fast_pump_tick()
    check(len(_proc_calls) == 2 and set(_proc_calls[1][1]).isdisjoint(_first_batch),
          "Chemin rapide : le reliquat est repris au passage suivant, rien n'est perdu")

    _proc_calls.clear()
    _pool_f[:] = [_pump_tok("STALE", 200)]
    _visible_f.add("STALE")
    _sc_fast._fast_pump_tick()
    check(_proc_calls == [],
          "Chemin rapide : au-delà de FAST_PUMP_MAX_AGE_SECONDS, c'est le cycle normal qui prend le relais")

    # Un mint qui n'apparaît jamais côté RPC est abandonné au cycle normal, pas gardé indéfiniment.
    _pool_f[:] = [_pump_tok("GHOST", 5)]
    _sc_fast._fast_pump_tick()
    check("GHOST" in _sc_fast._fast_pending, "Chemin rapide : un mint invisible reste en attente au début")
    _sc_fast._fast_pending["GHOST"]["token"]["_received_at"] = time.time() - 200
    _sc_fast._fast_pump_tick()
    check("GHOST" not in _sc_fast._fast_pending and "GHOST" in _sc_fast._fast_seen,
          "Chemin rapide : un mint jamais visible est abandonné au bout de FAST_PUMP_MAX_AGE_SECONDS")

    # La même fenêtre de capitalisation que le cycle normal (aucun contrôle contourné).
    from chains import solana as _sol_fast
    _huge = _sol_fast.candidates_from_pump_tokens([_pump_tok("HUGE", 5, mcap_sol=999_999)], 150.0)
    check(_huge == [], "Chemin rapide : même fenêtre de capitalisation que le cycle normal")

    # Interrupteurs : config, pause, chaîne désactivée.
    check(_sc_fast._fast_pump_enabled() is True, "Chemin rapide actif en degen, scan en cours")
    config.FAST_PUMP_PATH = False
    check(_sc_fast._fast_pump_enabled() is False, "FAST_PUMP_PATH = False coupe le chemin rapide")
    config.FAST_PUMP_PATH = True
    _sc_fast.state.paused = True
    check(_sc_fast._fast_pump_enabled() is False, "Le chemin rapide respecte PAUSE")
    _sc_fast.state.paused = False
    _sc_fast.state.active_chains.discard("solana")
    check(_sc_fast._fast_pump_enabled() is False, "Le chemin rapide respecte le bouton de chaîne Solana")
    check(hasattr(_sc_fast, "_publish_lock"),
          "Publication sérialisée entre le cycle et le chemin rapide (pas de doublon d'alerte)")

    # TTL des rejets : court pour un token early, complet pour un token établi.
    check(Scanner._rejection_ttl({"is_pump_bonding_curve": True,
                                  "pair_created_at": time.time() * 1000}) <= config.REJECTED_RECHECK_EARLY_SECONDS
          and Scanner._rejection_ttl({"pair_created_at": time.time() * 1000 - 3 * 3_600_000})
          == config.REJECTED_RECHECK_SECONDS,
          "Rejets : un token early est re-testé vite, un token établi garde le délai complet")
finally:
    _pp_fast.get_recent_tokens, _pp_fast.ensure_started, _dsx_fast.get_sol_price_usd = (
        _orig_recent, _orig_start, _orig_price)
    solana_rpc.visible_mints = _orig_vis
    config.FAST_PUMP_PATH = True

# --- 12f. STOP interrompt l'enrichissement en cours ----------------------
# Le point de blocage réel : la boucle RPC unitaire de solana.enrich_batch
# (jusqu'à 3 s/appel × 3 appels × 25 tokens sous 429). should_stop l'y coupe.
from chains import solana as _sol_stop, evm_common as _evm_stop
_out_stop = _sol_stop.enrich_batch(
    [{"chain": "solana", "contract": "S1", "ticker": "S1"},
     {"chain": "solana", "contract": "S2", "ticker": "S2"}],
    should_stop=lambda: True,
)
check(_out_stop == [],
      "solana.enrich_batch : should_stop=True interrompt avant le 1er candidat")

_evm_c = [{"chain": "bsc", "contract": "0xEVMSTOP1", "ticker": "E1"}]
_evm_out = _evm_stop.enrich_batch("bsc", _evm_c, should_stop=lambda: True)
check(_evm_out is _evm_c and "security" not in _evm_c[0],
      "evm_common.enrich_batch : should_stop=True renvoie les candidats non enrichis (aucun appel réseau)")


print("\n=== 13. Profil « quality » : viser le potentiel liste Alpha, pas le firehose ===")
# Le profil quality ne remonte QUE des tokens qui cochent les traits des
# tokens Binance Alpha : historique sans rug, centaines de porteurs, liquidité
# à 5 chiffres, contrat vérifié, ET smart money positionné. Beaucoup moins de
# résultats, plus un mur de memecoins « NON VÉRIFIÉ ».
config.apply_profile("quality")
try:
    check(config.ENABLE_PUMPPORTAL_FIREHOSE is False,
          "quality : le flux temps réel pump.fun est coupé")
    check(config.USE_TRENDING_DISCOVERY is True,
          "quality : découverte par les pools qui MONTENT, pas les plus récentes")
    check(config.MIN_LIQUIDITY_USD >= 40_000 and config.MIN_HOLDER_COUNT >= 300
          and config.MIN_PAIR_AGE_MINUTES >= 600,
          "quality : planchers relevés (liquidité, porteurs, âge minimum en heures)")

    _now_ms13 = time.time() * 1000
    established = {
        "chain": "solana", "contract": "ALPHACAND1", "ticker": "ALPHA", "name": "Alpha Candidate",
        "liquidity": 120_000, "market_cap": 2_500_000, "price_usd": 0.03,
        "volume_1h": 60_000, "price_change_1h": 6, "price_change_5m": 1,
        "buys_h1": 220, "sells_h1": 180,
        "pair_created_at": _now_ms13 - 4 * 24 * 3_600_000,   # 4 jours
        "is_pump_bonding_curve": False, "dex_paid": False,
        "security": {
            "data_available": True, "source": "goplus+rpc",
            "mint_authority_active": False, "freeze_authority_active": False,
            "honeypot": False, "lp_locked_pct": 100.0, "lp_data_available": True,
            "top10_holder_pct": 9.0, "creator_pct": 0.4, "holder_count": 1200,
            "metadata_mutable": False,
        },
        "rugcheck": {"available": True, "risks": [], "critical_risks": [], "score": 200},
    }

    import core.scoring as _scoring
    _orig_sm = _scoring.compute_smart_money_signal

    # 13a. Smart money présent => SIGNAL vérifié.
    _scoring.compute_smart_money_signal = lambda chain, contract: {
        "available": True, "holder_count": 5, "combined_ownership_pct": 7.0,
        "net_inflow_positive": True, "buyers": [], "buyers_count": 3,
    }
    with_sm = compute_score(dict(established))
    check(with_sm["rejected"] is False and with_sm["tier"] == config.TIER_SIGNAL
          and with_sm["verified"] is True,
          "quality : un token éprouvé + smart money atteint le SIGNAL vérifié",
          f"-> tier={with_sm['tier']}, verified={with_sm['verified']}, score={with_sm['score']}")

    # 13b. MÊME token, aucun smart money => reste en VEILLE (donc non publié).
    _scoring.compute_smart_money_signal = lambda chain, contract: {
        "available": True, "holder_count": 0, "combined_ownership_pct": 0.0,
        "net_inflow_positive": False, "buyers": [], "buyers_count": 0,
    }
    without_sm = compute_score(dict(established))
    check(without_sm["tier"] == config.TIER_WATCH and without_sm["verified"] is False,
          "quality : sans smart money, le même token n'atteint PAS le SIGNAL",
          f"-> tier={without_sm['tier']}")
    check(any("smart money" in m.lower() for m in without_sm["missing_checks"]),
          "quality : la raison « aucun smart money » est explicitée")

    # 13c. Un token de 5 minutes n'est plus traité comme « early » en quality.
    _scoring.compute_smart_money_signal = _orig_sm
    baby = dict(established, contract="BABY13", pair_created_at=_now_ms13 - 5 * 60_000)
    check(is_early_candidate(baby) is False,
          "quality : la fenêtre de détection précoce est désactivée (aucun traitement de faveur)")
    check(compute_score(baby)["tier"] == config.TIER_WATCH,
          "quality : un token de 5 minutes ne peut pas être un SIGNAL")

    # 13e. BNB Chain / Robinhood : seuils adaptés + smart money non exigé hors
    #      couverture Nansen, sinon ces chaînes ne remontent jamais rien.
    _now_ms13e = time.time() * 1000
    rh_token = {
        "chain": "robinhood", "contract": "0xRHGEM", "ticker": "RHG", "name": "RH Gem",
        "liquidity": 20_000, "market_cap": 300_000, "price_usd": 0.002,
        "volume_1h": 6_000, "price_change_1h": 5, "price_change_5m": 1,
        "buys_h1": 130, "sells_h1": 110,
        "pair_created_at": _now_ms13e - 3 * 24 * 3_600_000,
        "is_pump_bonding_curve": False, "dex_paid": False,
        "security": {
            "data_available": True, "source": "goplus",
            "mint_authority_active": False, "is_mintable": False, "freeze_authority_active": None,
            "honeypot": False, "cannot_sell_all": False, "cannot_buy": False,
            "buy_tax": 0.0, "sell_tax": 0.0,
            "transfer_pausable": False, "is_blacklisted": False,
            "is_open_source": True, "is_proxy": False, "hidden_owner": False,
            "can_take_back_ownership": False, "owner_change_balance": False,
            "lp_locked_pct": 100.0, "lp_data_available": True,
            "top10_holder_pct": 7.0, "holder_count": 450, "creator_pct": 0.4, "owner_pct": 0.0,
        },
    }
    check(len(_mpf(rh_token)) == 0,
          "quality : un token Robinhood à 20k$ de liquidité passe le préfiltre (seuil abaissé)")
    check(len(_mpf(dict(rh_token, chain="solana"))) > 0,
          "quality : le MÊME token sur Solana (aucun assouplissement) est écarté pour liquidité")
    rh_scored = compute_score(rh_token)
    check(rh_scored["rejected"] is False and rh_scored["tier"] == config.TIER_SIGNAL
          and rh_scored["verified"] is True,
          "quality : le token Robinhood atteint le SIGNAL (smart money non exigé — Nansen ne couvre pas la chaîne)",
          f"-> tier={rh_scored['tier']}, verified={rh_scored['verified']}, score={rh_scored['score']}")

    # 13f. Découverte Nansen (Token Screener) : seule source qui couvre à la
    #      fois BNB Chain et Robinhood Chain. GeckoTerminal/DexScreener
    #      n'indexent pas Robinhood ; sans cette source, cette chaîne ne
    #      remonte littéralement RIEN (discover_candidates renvoie []).
    from data_sources import geckoterminal, dexscreener
    check(nansen.screener_supports_chain("robinhood") is True
          and nansen.screener_supports_chain("bsc") is True,
          "Nansen : le Token Screener couvre robinhood ET bsc")
    check(nansen.supports_chain("robinhood") is False,
          "Nansen : robinhood reste hors couverture smart-money "
          "(le SIGNAL n'y exige donc pas de smart money)")

    _now_ms13f = time.time() * 1000
    _screener_calls = []
    _fake_screener = {"data": [
        {"chain": "bnb", "token_address": "0xN4B", "token_symbol": "N4B",
         "token_age_days": 90, "token_age_hours": 90 * 24,
         "market_cap_usd": 13_600_000, "liquidity": 1_600_000, "price_usd": 223.4,
         "price_change": 2.5, "volume": 40_000, "netflow": 716_700,
         "nof_traders": 140, "nof_buys": 300, "nof_sells": 190},
        {"chain": "bnb", "token_address": "0xTHIN", "token_symbol": "THIN",
         "token_age_days": 4, "token_age_hours": 96,
         "market_cap_usd": 28_600_000, "liquidity": 40_000, "price_usd": 0.03,
         "price_change": 12.0, "volume": 9_000, "netflow": -5_000,
         "nof_traders": 40, "nof_buys": 60, "nof_sells": 90},
    ]}
    _nansen_orig_post = nansen.safe_post_json
    nansen._screener_cache.clear()
    nansen.set_enabled(True)
    nansen.safe_post_json = lambda url, body, headers=None, timeout=8: (
        _screener_calls.append((url, body)), _fake_screener)[1]
    try:
        rows = nansen.screen_tokens("bsc")
    finally:
        nansen.safe_post_json = _nansen_orig_post
        nansen.set_enabled(False)
        nansen._screener_cache.clear()

    check(len(_screener_calls) == 1
          and _screener_calls[0][0].endswith("/token-screener")
          and _screener_calls[0][1]["chains"] == ["bnb"],
          'Nansen screen_tokens("bsc") appelle /token-screener avec chains=["bnb"]',
          f"-> {_screener_calls[:1]}")
    _n4b = next((r for r in rows if r["contract"] == "0xN4B"), None)
    check(_n4b is not None and _n4b["source"] == "nansen" and _n4b["chain"] == "bsc"
          and _n4b["ticker"] == "N4B"
          and abs((_n4b["liquidity"] or 0) - 1_600_000) < 1
          and abs((_n4b["volume_1h"] or 0) - 40_000) < 1
          and _n4b["pair_created_at"] is not None
          and _n4b["pair_created_at"] < _now_ms13f - 80 * 24 * 3_600_000,
          "Nansen screen_tokens : la ligne screener est normalisée "
          "(contract, volume_1h, âge dérivé de token_age_hours)",
          f"-> {_n4b}")

    # La découverte EVM commune récupère bien les candidats Nansen quand
    # USE_NANSEN_DISCOVERY est actif et que GT/DS ne renvoient rien (cas
    # Robinhood en conditions réelles).
    from chains import evm_common as _evm
    _evm_orig = (geckoterminal.get_new_pools, geckoterminal.get_trending_pools,
                 geckoterminal.resolve_network, dexscreener.discover_token_addresses,
                 dexscreener.get_best_pairs_bulk, nansen.screen_tokens)
    geckoterminal.get_new_pools = lambda chain: ([], {})
    geckoterminal.get_trending_pools = lambda chain: ([], {})
    geckoterminal.resolve_network = lambda chain: None
    dexscreener.discover_token_addresses = lambda chain, limit=40: []
    dexscreener.get_best_pairs_bulk = lambda chain, addresses: []
    nansen.screen_tokens = lambda chain: [{
        "contract": "0xNANSENGEM", "ticker": "NG", "chain": chain,
        "liquidity": 120_000, "market_cap": 900_000, "volume_1h": 30_000,
        "price_change_1h": 8, "buys_h1": 120, "sells_h1": 90,
        "pair_created_at": _now_ms13f - 5 * 24 * 3_600_000, "source": "nansen",
    }]
    try:
        discovered = _evm.discover_candidates("robinhood")
    finally:
        (geckoterminal.get_new_pools, geckoterminal.get_trending_pools,
         geckoterminal.resolve_network, dexscreener.discover_token_addresses,
         dexscreener.get_best_pairs_bulk, nansen.screen_tokens) = _evm_orig
    check(any(c["contract"] == "0xNANSENGEM" for c in discovered),
          'evm_common.discover_candidates("robinhood") remonte les candidats du screener Nansen',
          f"-> {[c.get('contract') for c in discovered]}")

    # NOUVEAU : la découverte EVM utilise le screener Nansen MÊME quand
    # USE_NANSEN_DISCOVERY est coupé (profil degen), du moment qu'une clé est
    # configurée et que l'interrupteur runtime est actif — c'est ce qui
    # débloque BNB / Base / Robinhood sur un compte Nansen Pro. Solana, elle,
    # garde le firehose et n'appelle PAS le screener.
    _evm_orig2 = (geckoterminal.get_new_pools, geckoterminal.get_trending_pools,
                  geckoterminal.resolve_network, dexscreener.discover_token_addresses,
                  dexscreener.get_best_pairs_bulk, nansen.screen_tokens)
    _screener_chains_seen = []
    def _fake_screen(chain):
        _screener_chains_seen.append(chain)
        return [{
            "contract": "0xEVMKEYGEM", "ticker": "EKG", "chain": chain,
            "liquidity": 120_000, "market_cap": 900_000, "volume_1h": 30_000,
            "price_change_1h": 8, "buys_h1": 120, "sells_h1": 90,
            "pair_created_at": _now_ms13f - 5 * 24 * 3_600_000, "source": "nansen",
        }]
    geckoterminal.get_new_pools = lambda chain: ([], {})
    geckoterminal.get_trending_pools = lambda chain: ([], {})
    geckoterminal.resolve_network = lambda chain: None
    dexscreener.discover_token_addresses = lambda chain, limit=40: []
    dexscreener.get_best_pairs_bulk = lambda chain, addresses: []
    nansen.screen_tokens = _fake_screen
    _prev_flag = config.USE_NANSEN_DISCOVERY
    _prev_key = config.API_KEYS.get("nansen")
    config.USE_NANSEN_DISCOVERY = False
    config.API_KEYS["nansen"] = "test-key"
    nansen.set_enabled(True)
    try:
        _base_disc = _evm.discover_candidates("base")
        _sol_seen_before = list(_screener_chains_seen)
        # solana ne passe pas par evm_common, mais on vérifie que le garde-fou
        # de chaîne du screener EVM exclut bien "solana".
        _evm.discover_candidates("solana") if hasattr(_evm, "discover_candidates") else None
    finally:
        (geckoterminal.get_new_pools, geckoterminal.get_trending_pools,
         geckoterminal.resolve_network, dexscreener.discover_token_addresses,
         dexscreener.get_best_pairs_bulk, nansen.screen_tokens) = _evm_orig2
        config.USE_NANSEN_DISCOVERY = _prev_flag
        if _prev_key is None:
            config.API_KEYS.pop("nansen", None)
        else:
            config.API_KEYS["nansen"] = _prev_key
        nansen.set_enabled(False)
    check(any(c["contract"] == "0xEVMKEYGEM" for c in _base_disc)
          and "base" in _screener_chains_seen,
          "evm_common : screener Nansen utilise pour la decouverte EVM meme si USE_NANSEN_DISCOVERY=False (cle presente)",
          f"-> chains vus: {_screener_chains_seen}")
    check("solana" not in _screener_chains_seen,
          "evm_common : le screener EVM n'est jamais appele pour solana",
          f"-> chains vus: {_screener_chains_seen}")

    # Base est cablee de bout en bout : chaine active, module de scan, maps
    # GoPlus / Nansen / GeckoTerminal / DexScreener.
    from core.scanner import CHAIN_MODULES as _CM
    check("base" in config.ACTIVE_CHAINS and "base" in _CM
          and nansen.screener_supports_chain("base") and nansen.supports_chain("base")
          and geckoterminal.NETWORK_MAP.get("base") == "base"
          and dexscreener.CHAIN_ID_MAP.get("base") == "base"
          and config.GOPLUS_CHAIN_IDS.get("base") == "8453",
          "Base : chaine active + module + maps GoPlus/Nansen/GeckoTerminal/DexScreener cables")

    # robinhood._probe : la chaîne s'active pour la découverte sur la seule
    # foi du screener Nansen (GeckoTerminal + DexScreener absents), en niveau
    # VEILLE puisque GoPlus ne couvre pas le chain ID 4663.
    from chains import robinhood as _rh
    _rh_orig = (geckoterminal.resolve_network, dexscreener.resolve_chain_id,
                nansen.screener_supports_chain, _rh.goplus.get_supported_chains,
                _rh.goplus.supports_chain)
    geckoterminal.resolve_network = lambda chain: None
    dexscreener.resolve_chain_id = lambda chain: None
    nansen.screener_supports_chain = lambda chain: chain == "robinhood"
    _rh.goplus.get_supported_chains = lambda: []
    _rh.goplus.supports_chain = lambda chain: False
    try:
        _st = _rh._probe(force=True)
    finally:
        (geckoterminal.resolve_network, dexscreener.resolve_chain_id,
         nansen.screener_supports_chain, _rh.goplus.get_supported_chains,
         _rh.goplus.supports_chain) = _rh_orig
    check(_st["discovery_available"] is True and _st["nansen_screener"] is True
          and _st["security_available"] is False,
          "robinhood._probe : découverte via Nansen seul => chaîne active en VEILLE",
          f"-> {_st['detail']}")

    # Arc Network (Circle, chain ID 5042) : câblée comme Robinhood. La
    # couverture Nansen, confirmée en direct le 23/09/2026 (screener ET
    # smart-money holders renvoient de vrais résultats sur Arc), est
    # maintenant câblée elle aussi — au même titre que Base/BSC/Ethereum/Solana.
    from chains import arc as _arc
    check("arc" in config.ACTIVE_CHAINS and "arc" in _CM
          and config.GOPLUS_CHAIN_IDS.get("arc") == "5042"
          and nansen.supports_chain("arc")
          and nansen.screener_supports_chain("arc"),
          "Arc : chaîne active + module câblés, chain ID GoPlus correct, "
          "couverture Nansen (screener + smart money) confirmée")

    _arc_orig = (geckoterminal.resolve_network, dexscreener.resolve_chain_id,
                 _arc.goplus.get_supported_chains, _arc.goplus.supports_chain)

    # Cas 1 : aucun indexeur de découverte ne couvre encore la chaîne =>
    # inactive, aucun candidat, aucun créneau de scan gaspillé.
    geckoterminal.resolve_network = lambda chain: None
    dexscreener.resolve_chain_id = lambda chain: None
    _arc.goplus.get_supported_chains = lambda: []
    _arc.goplus.supports_chain = lambda chain: False
    try:
        _st_arc_none = _arc._probe(force=True)
        _disc_arc_none = _arc.discover_candidates()
    finally:
        (geckoterminal.resolve_network, dexscreener.resolve_chain_id,
         _arc.goplus.get_supported_chains, _arc.goplus.supports_chain) = _arc_orig
    check(_st_arc_none["discovery_available"] is False and _disc_arc_none == [],
          "arc._probe : aucun indexeur ne couvre encore la chaîne => aucun candidat",
          f"-> {_st_arc_none['detail']}")

    # Cas 2 : GeckoTerminal indexe déjà le réseau mais GoPlus ne couvre pas
    # encore le chain ID 5042 => découverte active, niveau VEILLE seulement.
    geckoterminal.resolve_network = lambda chain: "arc" if chain == "arc" else None
    dexscreener.resolve_chain_id = lambda chain: None
    _arc.goplus.get_supported_chains = lambda: ["1", "56", "8453"]  # 5042 absent
    _arc.goplus.supports_chain = lambda chain: False
    try:
        _st_arc_watch = _arc._probe(force=True)
    finally:
        (geckoterminal.resolve_network, dexscreener.resolve_chain_id,
         _arc.goplus.get_supported_chains, _arc.goplus.supports_chain) = _arc_orig
    check(_st_arc_watch["discovery_available"] is True
          and _st_arc_watch["security_available"] is False,
          "arc._probe : découverte GeckoTerminal seule, GoPlus absent => VEILLE uniquement",
          f"-> {_st_arc_watch['detail']}")

    _arc_cand = [{"chain": "arc", "contract": "0xARC1", "ticker": "A1"}]
    _arc_enriched = _arc.enrich_with_security(dict(_arc_cand[0]))
    check(_arc_enriched["security"]["data_available"] is False,
          "arc.enrich_with_security : sécurité non vérifiable tant que GoPlus ne couvre pas 5042")

    # 13d. La découverte GeckoTerminal est mise en cache : un second appel
    #      immédiat ne refait PAS de requête réseau (c'est ce qui faisait
    #      répondre 429 et vidait la source « trending » du profil quality).
    from data_sources import geckoterminal as _gt
    _gt._pools_cache.clear()
    _gt_calls = []
    _gt_orig = _gt.safe_get_json
    _gt.safe_get_json = lambda url, params=None, headers=None: (
        _gt_calls.append(url),
        {"data": [{"id": "p1", "attributes": {}, "relationships": {}}], "included": []},
    )[1]
    try:
        r1 = _gt.get_trending_pools("solana")
        r2 = _gt.get_trending_pools("solana")
    finally:
        _gt.safe_get_json = _gt_orig
    check(len(_gt_calls) == 1 and r1[0] and r2[0] == r1[0],
          "GeckoTerminal : deux découvertes rapprochées ne font qu'un appel réseau (cache 90 s)",
          f"-> {len(_gt_calls)} appel(s)")
finally:
    _scoring.compute_smart_money_signal = _orig_sm
    config.apply_profile("degen")

# =============================================================================
print("\n=== 14. Recap narratif pour les reseaux (core/signal_recap.py) ===")
from core import signal_recap as _recap

_rid = db.insert_signal({
    "chain": "bsc", "contract": "0xRECAP0000000000000000000000000000000001",
    "ticker": "RCAP", "name": "Recap Token", "market_cap": 120_000.0,
    "liquidity": 45_000.0, "holders": 320, "score": 82.0,
    "reasons": ["raison A", "raison B"],
    "features": {"safety": 0.95, "holder_distribution": 0.8, "liquidity_score": 0.7,
                 "liquidity_quality": 0.65, "smart_money": 0.42, "vol_liq_ratio": 0.9,
                 "momentum_1h": 0.55, "buy_pressure": 0.61, "momentum_5m": 0.3},
    "risk_level": "Modéré", "entry_price": 0.0012, "stop_loss": 0.0009,
    "tp1": 0.002, "tp2": 0.003, "tp3": 0.005,
    "status": "active", "tier": "signal", "verified": True,
})
check(isinstance(_rid, int) and _rid > 0, "recap : signal de test insere", f"-> id {_rid}")

_sig = db.get_signal(_rid)
check(_sig is not None and _sig["ticker"] == "RCAP",
      "db.get_signal renvoie bien la ligne", f"-> {_sig and _sig.get('ticker')}")
check(db.get_signal(999_999) is None,
      "db.get_signal sur un id inexistant renvoie None")

# --- Suivi du pic : nouveau max enregistre, valeur plus basse ignoree --------
db.update_peak(_rid, price=0.0024, market_cap=240_000.0, return_pct=100.0)
_sig = db.get_signal(_rid)
check(_sig["peak_return_pct"] == 100.0 and _sig["peak_checks"] == 1,
      "update_peak : premier releve fixe le pic", f"-> {_sig['peak_return_pct']}% / {_sig['peak_checks']} releve(s)")

db.update_peak(_rid, price=0.0018, market_cap=180_000.0, return_pct=50.0)
_sig = db.get_signal(_rid)
check(_sig["peak_return_pct"] == 100.0 and _sig["peak_checks"] == 2,
      "update_peak : un releve plus bas ne remplace pas le pic mais compte",
      f"-> {_sig['peak_return_pct']}% / {_sig['peak_checks']} releve(s)")

db.update_peak(_rid, price=0.006, market_cap=600_000.0, return_pct=400.0)
_sig = db.get_signal(_rid)
check(_sig["peak_return_pct"] == 400.0 and _sig["peak_market_cap"] == 600_000.0,
      "update_peak : un nouveau max ecrase l'ancien", f"-> {_sig['peak_return_pct']}%")

# --- Compteurs de corpus ---------------------------------------------------
_c1 = db.bump_cycle_counter("recap_cycle_count")
_c2 = db.bump_cycle_counter("recap_cycle_count")
check(_c2 == _c1 + 1, "bump_cycle_counter incremente et renvoie la nouvelle valeur",
      f"-> {_c1} puis {_c2}")
db.bump_cycle_counter("recap_signals_emitted")
_stats = db.get_cycle_stats()
check(_stats["scan_cycles"] >= 2 and _stats["signals_emitted"] >= 1,
      "get_cycle_stats agrege les compteurs", f"-> {_stats}")

# --- Cohorte -------------------------------------------------------------
_cohort = db.get_cohort_stats(tier="signal", risk_level="Modéré")
check(_cohort["count"] >= 1 and _cohort["avg_peak_return_pct"] is not None,
      "get_cohort_stats : moyenne calculee sur les signaux comparables", f"-> {_cohort}")
_empty_cohort = db.get_cohort_stats(tier="signal", risk_level="RisqueInexistant")
check(_empty_cohort == {"count": 0, "avg_peak_return_pct": None, "best_peak_return_pct": None},
      "get_cohort_stats : cohorte vide renvoie des None sans planter")

# --- Generation du texte dans les 3 langues -----------------------------
_fr = _recap.build_recap(_rid, "fr")
_en = _recap.build_recap(_rid, "en")
_zh = _recap.build_recap(_rid, "zh")
check(bool(_fr) and bool(_en) and bool(_zh),
      "build_recap produit un texte non vide en fr / en / zh",
      f"-> longueurs {len(_fr)}/{len(_en)}/{len(_zh)}")
check("RCAP" in _fr and "RCAP" in _en and "RCAP" in _zh,
      "build_recap : le ticker apparait dans les 3 versions")
check(_fr != _en and _en != _zh,
      "build_recap : les 3 langues donnent des textes distincts")
check("conviction" in _fr.lower() and "conviction:" in _en.lower(),
      "build_recap : la ligne de conviction est presente")
check("—" not in _fr and "—" not in _en and "—" not in _zh,
      "build_recap : plus aucun tiret cadratin dans le texte genere (virgules a la place)")
check(_fr.startswith(_recap._TX["opener"]["fr"]),
      "build_recap : accroche humaine en tete du texte")
check(all(t.rstrip().endswith(DISCLAIMER) and t.count(DISCLAIMER) == 1 for t in (_fr, _en, _zh)),
      "build_recap : le disclaimer (anglais) termine le texte, une seule fois, dans les 3 langues")
check("400%" in _fr,
      "build_recap : le pic suivi (+400%) apparait dans le texte")
check(_recap.build_recap(999_999, "fr") == "",
      "build_recap sur un id inexistant renvoie une chaine vide")

# Un token en VEILLE : pas de plan d'entree, clause de prudence adaptee
_wid = db.insert_signal({
    "chain": "solana", "contract": "SoRECAPwatch1111111111111111111111111111111",
    "ticker": "WCAP", "name": "Watch Token", "market_cap": 30_000.0,
    "liquidity": 8_000.0, "holders": 90, "score": 71.0,
    "reasons": ["veille A"], "features": {"safety": 0.6, "smart_money": 0.0},
    "risk_level": "Non vérifié", "entry_price": None,
    "status": "active", "tier": "veille", "verified": False,
    "missing_checks": ["LP lock", "audit"],
})
_wfr = _recap.build_recap(_wid, "fr").lower()
check(bool(_wfr) and ("surveillance" in _wfr or "précoce" in _wfr or "precoce" in _wfr),
      "build_recap : un token VEILLE est decrit comme detection precoce")
_wen = _recap.build_recap(_wid, "en")
check(bool(_wen) and "not a buy signal" in _wen,
      "build_recap VEILLE (en) : mentionne explicitement 'not a buy signal'")

# =============================================================================
os.unlink(tmp_db)
for ext in ("-wal", "-shm"):
    p = tmp_db + ext
    if os.path.exists(p):
        os.unlink(p)

print(f"\n=== RÉSULTAT : {PASSED} test(s) réussi(s), {FAILED} échec(s) ===")
sys.exit(1 if FAILED else 0)
