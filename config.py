"""
config.py — Configuration centrale du Gem Hunter
Destination finale : gem_hunter/config.py

RÉVISION MAJEURE — DOCTRINE « PÉPITE SÛRE ».
La version précédente reposait sur un principe inversé : une donnée de sécurité
absente était notée « neutre » (0.5) au lieu d'être refusée, et la sécurité ne
pesait que 10 points sur 100 alors que le momentum et la pression acheteuse en
pesaient 40 à eux deux. Un pump-and-dump en pleine phase de pompe maximise
exactement ces deux critères : le score récompensait donc mécaniquement la
signature du rug pull.

Nouvelle doctrine, appliquée partout dans ce fichier :
  1. INCONNU = REFUSÉ. Un token qu'on ne peut pas vérifier n'est pas un token
     sûr, c'est un token non vérifié. Il ne devient jamais un SIGNAL.
  2. La sécurité n'est pas un critère parmi d'autres, c'est un veto. Aucun
     score, aussi élevé soit-il, ne peut racheter un veto.
  3. Le momentum ne prouve rien. Il est plafonné et fortement dépondéré.
  4. Deux niveaux d'information au lieu d'un (voir TIERS ci-dessous) : la
     VEILLE donne l'information tôt, le SIGNAL engage du capital. Les deux ne
     sont jamais confondus.
"""
import os
from dotenv import load_dotenv

load_dotenv()  # charge automatiquement le fichier .env s'il existe à la racine du projet

# --- Clés API (à remplir via variables d'environnement de préférence) ---
API_KEYS = {
    "nansen": os.getenv("NANSEN_API_KEY", ""),
    "birdeye": os.getenv("BIRDEYE_API_KEY", ""),
    "goplus_app_key": os.getenv("GOPLUS_APP_KEY", ""),      # optionnel, GoPlus a un tier gratuit sans clé
    "goplus_app_secret": os.getenv("GOPLUS_APP_SECRET", ""),
    "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
    "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID", ""),
    "rpc_robinhood": os.getenv("RPC_ROBINHOOD", ""),  # endpoint RPC Robinhood Chain (Arbitrum Orbit)
    "rpc_bsc": os.getenv("RPC_BSC", "https://bsc-dataseed.binance.org"),
    "rpc_solana": os.getenv("RPC_SOLANA", ""),  # vide = RPC public par défaut (voir data_sources/solana_rpc.py)
}

# --- Chaînes actives au démarrage ---
# Priorité demandée : BNB Chain + Solana, plus Robinhood Chain activée
# automatiquement si elle est réellement indexée (voir chains/robinhood.py,
# détection au démarrage). Ethereum reste disponible dans l'UI mais n'est plus
# scannée par défaut : elle consommait du budget de rate-limit GoPlus au
# détriment des trois chaînes ciblées.
ACTIVE_CHAINS = ["solana", "bsc", "base", "robinhood", "arc"]
AVAILABLE_CHAINS = ["solana", "bsc", "base", "robinhood", "arc", "ethereum"]

# GoPlus chain IDs (nécessaires pour les endpoints token_security)
GOPLUS_CHAIN_IDS = {
    "bsc": "56",
    "ethereum": "1",
    "arbitrum": "42161",
    "base": "8453",
    "polygon": "137",
    "avalanche": "43114",
    "optimism": "10",
    "robinhood": "4663",  # Robinhood Chain (Arbitrum Orbit). Vérifié au démarrage
                          # contre /api/v1/supported_chains : retiré tout seul si
                          # GoPlus ne la couvre pas encore (voir data_sources/goplus.py).
    "arc": "5042",  # Arc Network (Circle), mainnet public depuis le 16/09/2026.
                    # Même vérification automatique que Robinhood Chain — voir
                    # chains/arc.py.
    # Solana est géré via un endpoint dédié GoPlus (solana_token_security)
}

# =============================================================================
# NIVEAUX D'INFORMATION (TIERS)
# =============================================================================
# VEILLE  : détection précoce. Le token est repéré avant tout le monde, mais
#           tout ou partie des vérifications de sécurité est encore
#           impossible (token trop jeune pour être indexé, LP pas encore
#           créée, etc.). Affiché et alerté, explicitement marqué NON VÉRIFIÉ,
#           SANS plan de risque et SANS achat automatique. C'est de
#           l'information, pas une recommandation.
# SIGNAL  : toutes les vérifications de sécurité obligatoires sont passées
#           avec de VRAIES données. Seul niveau qui produit un plan d'entrée.
# Un candidat en VEILLE est automatiquement re-testé aux cycles suivants et
# promu en SIGNAL dès que ses données de sécurité deviennent disponibles et
# propres — c'est le mécanisme qui concilie « tôt » et « sûr ».
TIER_WATCH = "veille"
TIER_SIGNAL = "signal"

# Durée pendant laquelle un candidat en VEILLE est re-testé à chaque cycle
# avant d'être abandonné (il n'est jamais promu s'il ne se vérifie pas).
WATCH_REQUEUE_TTL_SECONDS = 3600      # 1h de suivi actif
WATCH_MAX_TRACKED = 500               # borne mémoire de la file de veille

# Nombre maximal de candidats réellement VÉRIFIÉS par cycle et par chaîne
# (appels GoPlus, RPC et RugCheck). Les candidats en excès ne sont pas perdus,
# ils sont simplement reportés au cycle suivant. Sans ce plafond, un afflux
# soudain — un cycle Solana peut remonter 200 créations pump.fun — déclenche
# plus d'un millier de requêtes en 45 secondes et fait répondre 429 au RPC
# public, ce qui prive le bot des vérifications anti-rug au moment précis où
# il en a le plus besoin.
MAX_ENRICHMENTS_PER_CYCLE = 25

# =============================================================================
# CRITÈRES DE MARCHÉ
# =============================================================================
MARKET_CAP_MIN = 3_000       # informatif seulement, plus un filtre dur
MARKET_CAP_MAX = 200_000
MIN_LIQUIDITY_USD = 8_000     # relevé : sous ce niveau, sortir de position est illusoire
MIN_VOLUME_1H_USD = 2_000
MAX_PAIR_AGE_HOURS = 6        # fenêtre « early » élargie de 2h à 6h : à 2h, presque
                              # aucun token n'a encore de données GoPlus exploitables,
                              # ce qui poussait mécaniquement tout vers le mode dégradé
MIN_PAIR_AGE_MINUTES = 10     # NOUVEAU : sous 10 minutes, aucune donnée de sécurité
                              # n'existe nulle part. Ces tokens vont en VEILLE, pas en SIGNAL.

# --- Fenêtre « dès la création » -----------------------------------------------
# NOUVEAU. Un token qui vient de naître n'a par construction NI volume, NI
# historique de prix, NI compteur d'achats/ventes — et ce n'est pas un défaut
# de CE token, ça n'existe encore pour personne à cet instant. Le traiter comme
# une donnée « manquante donc suspecte » (note 0,25) écrasait mécaniquement le
# score de tout token frais sous le seuil, si bien que la VEILLE ne voyait
# jamais rien avant que le token n'ait déjà pompé. Dans cette fenêtre :
#   - le scoring EXCLUT les critères de marché absents au lieu de les noter bas
#     (comme il le fait déjà pour le smart money quand Nansen n'est pas
#     configuré), pour que le score early reflète ce qui EST vérifiable :
#     sécurité du contrat, répartition de l'offre, liquidité, détention du dev,
#   - le préfiltre marché assouplit les planchers de liquidité et de volume :
#     un token neuf trop peu liquide part en VEILLE (re-testé à chaque cycle)
#     au lieu d'être rejeté définitivement.
# Les VETOS de sécurité et de marché durs (honeypot, dev qui détient tout, wash
# trading, pompe verticale, coquille vide) restent, eux, entièrement actifs.
EARLY_DETECTION_WINDOW_MINUTES = 60
EARLY_MIN_LIQUIDITY_USD = 2_000   # plancher de liquidité pendant la fenêtre early
                                  # (sous ce niveau, même une VEILLE n'a pas de sens)

# --- Fenêtre « première minute » : détection GARANTIE dès le repérage ---------
# Le flux PumpPortal pousse une création pump.fun en quelques millisecondes ;
# à cet instant la bonding curve n'a souvent aucune réserve, et GeckoTerminal /
# DexScreener n'ont encore rien indexé. Sous FIRST_MINUTE_WINDOW_MINUTES, le
# plancher de liquidité tombe à FIRST_MINUTE_MIN_LIQUIDITY_USD pour que le token
# PARAISSE — en VEILLE, NON VÉRIFIÉ, sans plan d'entrée, sans achat auto.
#
# Ce qui n'est PAS assoupli : tous les vetos anti-rug restent actifs dans la
# fenêtre — honeypot, dev/insiders majoritaires, concentration du top 10,
# autorité de mint/freeze, métadonnées ou frais modifiables, wash trading,
# pompe verticale. Un token dont la STRUCTURE prouve déjà le rug est rejeté,
# pas affiché. Et la promotion en SIGNAL reste soumise à MIN_PAIR_AGE_MINUTES :
# un token de moins de 10 min ne peut jamais devenir un signal validé.
FIRST_MINUTE_WINDOW_MINUTES = 3
FIRST_MINUTE_MIN_LIQUIDITY_USD = 0
# Créneaux de vérification par cycle réservés aux tokens de la fenêtre, les
# plus jeunes servis d'abord — sans quoi le plafond MAX_ENRICHMENTS_PER_CYCLE
# (qui trie par liquidité décroissante) reléguerait systématiquement les
# créations fraîches, quasi sans liquidité, au cycle suivant.
FIRST_MINUTE_ENRICHMENT_RESERVE = 12

MIN_HOLDER_COUNT = 50         # si la source le fournit (0 = ignorer le filtre)
MAX_TOP10_HOLDER_PCT = 25     # abaissé de 60 à 25 : à 60% le top 10 peut vider
                              # le marché à lui seul, c'est la définition d'un rug programmé
MIN_LIQ_TO_MCAP_PCT = 3.0     # NOUVEAU : liquidité minimale rapportée à la capitalisation.
                              # Un token à 200k$ de MC avec 3k$ de liquidité est une coquille
                              # vide : le premier vendeur significatif effondre le prix.
MAX_VOL_TO_LIQ_RATIO = 12.0   # NOUVEAU : au-delà, le « volume » est du wash trading
                              # (bots qui se revendent entre eux pour simuler la traction).
                              # 12x la liquidité échangée en une heure sur un pool de
                              # quelques dizaines de milliers de dollars n'arrive pas
                              # naturellement.
MAX_PRICE_CHANGE_1H_PCT = 300  # NOUVEAU : au-delà, on n'achète plus un token, on devient
                               # la liquidité de sortie de ceux qui sont entrés avant.
                               # Ce n'est pas un critère de sécurité du contrat, mais le
                               # résultat pratique est le même : la perte est quasi certaine.

# --- « A déjà plongé » / « est sur le point de plonger » ----------------------
# Le pendant du veto de pompe verticale, dans l'autre sens. Entrer sur un token
# qui a déjà fait son mouvement et s'est effondré, ou dont le retournement est
# déjà amorcé, donne pour l'acheteur le MÊME résultat qu'un rug : perte quasi
# certaine. Ce ne sont pas des critères de sécurité du contrat, mais ils sont
# traités comme des vetos — et surtout le motif est journalisé, pour répondre à
# « le bot doit dire si le token a déjà plongé ou est sur le point de plonger ».
#
# 1. A DÉJÀ PLONGÉ : baisse déjà consommée sur une fenêtre large.
DUMP_PRICE_CHANGE_24H_PCT = -60.0   # variation 24h en dessous de laquelle le token est « déjà plongé »
DUMP_PRICE_CHANGE_6H_PCT  = -50.0   # idem sur 6h (effondrement récent, la 24h peut encore être verte)
#
# 2. EST SUR LE POINT DE PLONGER : le retournement est visible dans les données.
DUMP_ROLLOVER_PUMP_1H_PCT = 40.0    # une pompe 1h d'au moins ce niveau...
DUMP_ROLLOVER_DROP_5M_PCT = -12.0   # ...suivie d'une chute 5 min d'au moins ce niveau = sommet passé
DUMP_SELL_DOMINANCE_RATIO = 2.0     # ventes >= ce multiple des achats sur 1h + prix en baisse = distribution active

# =============================================================================
# PROFIL DE SCAN — « quality » (défaut) vs « degen »
# =============================================================================
# Le bot visait jusqu'ici la création pure : flux temps réel pump.fun, market
# cap de quelques milliers de dollars, tokens de cinq minutes. C'est le meilleur
# moyen de « voir avant tout le monde »… et de ne voir que des rugs, parce que
# c'est presque tout ce qui naît sur ce segment.
#
# « Un token qui a le potentiel d'être listé sur Binance Alpha » est une bête
# différente : équipe réelle, narratif, plusieurs jours d'historique SANS rug,
# des centaines de porteurs, une liquidité à cinq/six chiffres, un contrat
# vérifiable et propre, et de préférence des wallets « smart money » déjà
# positionnés. Aucun bot sur API gratuites ne PRÉDIT une liste Alpha — mais il
# peut ne remonter QUE des tokens qui cochent les traits que les tokens Alpha
# partagent. C'est ce que fait le profil « quality ».
#
#   quality : pas de flux pump.fun, planchers hauts, âge minimum en heures,
#             découverte par les pools qui MONTENT (trending), et un token
#             n'est montré/alerté QUE s'il est entièrement vérifié (niveau
#             SIGNAL) et qu'au moins un signal de qualité externe est présent
#             (smart money OU distribution + traction). Beaucoup moins de
#             résultats, mais pas un mur de memecoins « NON VÉRIFIÉ ».
#   degen   : comportement précédent, pour qui veut le firehose et la VEILLE.
#
# Réglable via la variable d'environnement SCAN_PROFILE ou le bouton PROFIL de
# l'interface.
SCAN_PROFILE = os.getenv("SCAN_PROFILE", "degen").strip().lower()

PROFILES = {
    "quality": {
        # --- Marché : on ne regarde plus la création, on regarde la traction ---
        "MARKET_CAP_MIN": 150_000,
        "MARKET_CAP_MAX": 50_000_000,      # les tokens Alpha montent haut ; on ne
                                           # se coupe pas d'un x10 déjà entamé
        "MIN_LIQUIDITY_USD": 40_000,
        "MIN_VOLUME_1H_USD": 15_000,
        "MIN_PAIR_AGE_MINUTES": 12 * 60,   # 12 h : le temps que les rugs éclair
                                           # se soient déjà effondrés
        "MAX_PAIR_AGE_HOURS": 45 * 24,     # 45 jours : encore « tôt » à l'échelle
                                           # d'un vrai projet (21 j était trop
                                           # serré — voir journaux « trop ancienne »)
        "MIN_HOLDER_COUNT": 300,
        "MAX_TOP10_HOLDER_PCT": 20,
        # --- Pas de traitement « dès la création » en quality ---
        "EARLY_DETECTION_WINDOW_MINUTES": 0,
        "EARLY_MIN_LIQUIDITY_USD": 40_000,
        "FIRST_MINUTE_WINDOW_MINUTES": 0,      # quality ne fait pas de détection fraîche
        "FIRST_MINUTE_ENRICHMENT_RESERVE": 0,
        # --- Découverte ---
        "ENABLE_PUMPPORTAL_FIREHOSE": False,   # coupe la source pump.fun bonding curve
        "USE_TRENDING_DISCOVERY": True,        # pools qui montent, pas « les plus récentes »
        "USE_NANSEN_DISCOVERY": True,          # Token Screener Nansen : seule source qui
                                               # couvre réellement BNB Chain et Robinhood
                                               # Chain (voir data_sources/nansen.py)
        # --- Publication ---
        # La VEILLE est publiée MÊME en quality : sur ce profil elle ne contient
        # que des quasi-signaux (score au niveau du curseur, tous vetos passés,
        # marqués NON VÉRIFIÉ, sans plan d'entrée). En voir un ou deux par heure
        # au lieu d'un écran vide permet de comprendre ce qui bloque le passage
        # en SIGNAL (souvent : pas de smart money, ou distribution limite).
        "PUBLISH_WATCH_ALERTS": True,
        # --- Exigences de qualité supplémentaires pour atteindre le SIGNAL ---
        "REQUIRE_SMART_MONEY": True,           # au moins un wallet smart money (Nansen)
        "MIN_SMART_MONEY_FEATURE": 0.45,
        "REQUIRE_HOLDER_COUNT": True,          # le nombre de porteurs DOIT être connu
    },
    "degen": {
        "MARKET_CAP_MIN": 3_000,
        "MARKET_CAP_MAX": 2_000_000,  # relevé de 200k : un pump.fun jeune peut
                                      # déjà valoir ~1M$ en moins de 20 min (cas
                                      # « LEGO » qui a motivé ce changement) —
                                      # 200k excluait justement ce type de cas.
        "MIN_LIQUIDITY_USD": 8_000,
        "MIN_VOLUME_1H_USD": 2_000,
        "MIN_PAIR_AGE_MINUTES": 10,
        "MAX_PAIR_AGE_HOURS": 6,
        "MIN_HOLDER_COUNT": 50,
        "MAX_TOP10_HOLDER_PCT": 20,   # durci de 25 : sur un token de quelques
                                      # minutes, top 10 > 20% = le rug est déjà
                                      # en place dans la structure de détention.
        "EARLY_DETECTION_WINDOW_MINUTES": 60,
        "EARLY_MIN_LIQUIDITY_USD": 2_000,
        "FIRST_MINUTE_WINDOW_MINUTES": 3,
        "FIRST_MINUTE_MIN_LIQUIDITY_USD": 0,
        "FIRST_MINUTE_ENRICHMENT_RESERVE": 12,
        "ENABLE_PUMPPORTAL_FIREHOSE": True,
        "USE_TRENDING_DISCOVERY": False,
        "USE_NANSEN_DISCOVERY": False,   # degen garde le firehose gratuit
        "PUBLISH_WATCH_ALERTS": True,
        "REQUIRE_SMART_MONEY": False,
        "MIN_SMART_MONEY_FEATURE": 0.45,
        "REQUIRE_HOLDER_COUNT": False,
    },
}


def apply_profile(name: str) -> str:
    """
    Écrase les réglages pilotés par le profil. Appelable à chaud (bouton PROFIL
    de l'interface) : le scanner relit ces constantes à chaque cycle.
    """
    name = (name or "quality").strip().lower()
    prof = PROFILES.get(name) or PROFILES["quality"]
    g = globals()
    for key, value in prof.items():
        g[key] = value
    g["SCAN_PROFILE"] = name
    return name

# NB : l'application effective du profil est faite en TOUT DERNIER dans ce
# fichier (voir apply_profile(SCAN_PROFILE) en fin de module), pour qu'elle
# gagne sur toute constante réassignée plus bas, MIN_SCORE_TO_BUY notamment.

# =============================================================================
# ADAPTATION PAR CHAÎNE
# =============================================================================
# BNB Chain et surtout Robinhood Chain n'ont pas le même « fond de marché » que
# Solana. Avec les seuils quality calibrés sur Solana, ces deux chaînes ne
# remontaient RIEN : tout tombait au préfiltre (liquidité < 40 k$, volume 1h
# < 15 k$). Sur Robinhood, un pool à 40 k$ est déjà gros, et Nansen n'indexe pas
# la chaîne (exiger du smart money y rendrait le SIGNAL inatteignable).
#
# Ces surcharges s'appliquent PAR-DESSUS le profil actif, mais uniquement dans
# le sens de l'assouplissement : un seuil MIN_* ne peut qu'être abaissé, un
# indicateur REQUIRE_* ne peut qu'être désactivé. En profil degen (seuils déjà
# bas) elles n'ont donc quasi aucun effet.
CHAIN_MARKET_OVERRIDES = {
    "robinhood": {
        "MIN_LIQUIDITY_USD": 8_000,
        "MIN_VOLUME_1H_USD": 2_500,
        "MIN_LIQ_TO_MCAP_PCT": 1.0,
        "MIN_HOLDER_COUNT": 0,
        "REQUIRE_HOLDER_COUNT": False,
    },
    "bsc": {
        "MIN_LIQUIDITY_USD": 18_000,
        "MIN_VOLUME_1H_USD": 5_000,
        "MIN_HOLDER_COUNT": 100,
    },
    # Base a un fond de marché proche de BNB Chain : pools plus profondes que
    # Robinhood, contrats presque toujours vérifiés sur Basescan (pas de
    # rejet de masse pour code source non vérifié), couverture GoPlus et
    # smart-money Nansen complètes. Ces planchers ne s'appliquent que dans le
    # sens de l'assouplissement par-dessus le profil actif.
    "base": {
        "MIN_LIQUIDITY_USD": 12_000,
        "MIN_VOLUME_1H_USD": 4_000,
        "MIN_HOLDER_COUNT": 80,
    },
}


def chain_threshold(name: str, chain: str | None):
    """
    Valeur effective d'un seuil pour une chaîne donnée : la surcharge de
    CHAIN_MARKET_OVERRIDES si elle assouplit, sinon la valeur globale (issue du
    profil). Une surcharge ne peut jamais rendre le filtre plus strict.
    """
    base = globals().get(name)
    override = CHAIN_MARKET_OVERRIDES.get(chain or "", {})
    if name not in override:
        return base
    value = override[name]
    if isinstance(base, bool):
        return base and bool(value)          # peut désactiver, pas activer
    if isinstance(base, (int, float)) and isinstance(value, (int, float)):
        return min(base, value)              # peut abaisser, pas relever
    return value


# Fenêtre de fraîcheur PAR CHAÎNE, distincte des seuils MIN_* de marché.
# Sur Solana, le firehose pump.fun impose une fenêtre serrée : au-delà de
# MAX_PAIR_AGE_HOURS un token n'est plus « tôt ». Sur BNB Chain / Base /
# Robinhood il n'y a pas de bonding-curve firehose, et le smart-money (Nansen)
# n'entre qu'une fois le token installé — de l'ordre de la journée, jamais 6 h.
# Une fenêtre de 6 h y rend la découverte Nansen littéralement vide (mesuré :
# 0 résultat sur les trois chaînes). On élargit donc à 96 h pour ces chaînes.
#
# L'âge d'une paire n'est PAS un filtre anti-rug : une paire plus ancienne a
# MOINS de risque de rug éclair — le profil « quality » impose d'ailleurs 12 h
# d'âge MINIMUM pour cette raison. Le moteur de veto (honeypot, contrat proxy,
# mint/freeze authority, LP lock, concentration des holders, part du créateur)
# est strictement inchangé.
CHAIN_MAX_PAIR_AGE_HOURS = {
    "bsc": 96,
    "base": 96,
    "robinhood": 96,
}


def chain_max_pair_age_hours(chain: str | None) -> float:
    """
    Plafond d'âge d'une paire pour cette chaîne (heures). Solana et les chaînes
    non listées gardent le plafond global du profil. Ne peut qu'élargir : en
    profil « quality » (fenêtre déjà de 45 jours) la surcharge EVM n'a aucun
    effet.
    """
    override = CHAIN_MAX_PAIR_AGE_HOURS.get(chain or "")
    if override is None:
        return MAX_PAIR_AGE_HOURS
    return max(override, MAX_PAIR_AGE_HOURS)

# =============================================================================
# VETOS DE SÉCURITÉ — aucun score ne peut les racheter
# =============================================================================
# Chaque entrée à True est un motif de rejet immédiat dès que la donnée
# correspondante est CONFIRMÉE dangereuse. Voir core/security_checks.py.
SECURITY_VETOES = {
    # --- Impossible ou coûteux de revendre ---
    "honeypot": True,                       # achat possible, vente impossible
    "cannot_sell_all": True,                # vente partielle seulement (piège classique)
    "transfer_pausable": True,              # le dev peut geler les transferts
    "trading_cooldown": True,               # délai imposé entre transactions
    "is_blacklisted": True,                 # le dev peut blacklister un acheteur
    "non_transferable": True,               # Solana : token non transférable
    "transfer_hook": True,                  # Solana Token-2022 : code arbitraire à chaque transfert
    "transfer_hook_upgradable": True,
    # --- Le dev peut changer les règles après coup ---
    "is_proxy": True,                       # contrat modifiable après déploiement
    "hidden_owner": True,                   # propriétaire dissimulé
    "can_take_back_ownership": True,        # renonciation réversible = renonciation factice
    "owner_change_balance": True,           # le dev peut réécrire les soldes
    "selfdestruct": True,
    "slippage_modifiable": True,            # les taxes peuvent passer à 100% après coup
    "personal_slippage_modifiable": True,   # taxe ciblée sur une adresse précise
    "anti_whale_modifiable": True,
    "external_call": True,                  # logique déportée dans un contrat tiers modifiable
    "gas_abuse": True,
    "transfer_fee_upgradable": True,        # Solana Token-2022 : frais modifiables après coup
    "balance_mutable_authority": True,      # Solana : soldes modifiables par une autorité
    "closable": True,                       # Solana : le compte mint peut être fermé
    "default_account_state_upgradable": True,
    # --- Le dev peut encore créer ou geler des tokens ---
    "mint_authority_active": True,
    "freeze_authority_active": True,
    "is_mintable": True,
    # --- Réputation du créateur et du token ---
    "honeypot_with_same_creator": True,     # la même adresse a déjà déployé un honeypot
    "fake_token": True,                     # contrefaçon d'un projet existant
    "is_airdrop_scam": True,
    "cannot_buy": True,
    # --- Contrat opaque ---
    "not_open_source": True,                # code non vérifié = audit impossible
    "metadata_mutable": True,               # Solana : nom/logo modifiables après coup
                                            # (recyclage d'un token mort en « nouveau » projet)
}

# Taxes d'achat/vente maximales tolérées, en pourcentage.
MAX_BUY_TAX_PCT = 5.0
MAX_SELL_TAX_PCT = 5.0

# Part maximale de l'offre détenue par le créateur ou le propriétaire du contrat.
MAX_CREATOR_PCT = 5.0
MAX_OWNER_PCT = 5.0

# Part maximale détenue par les wallets liés au créateur (« bundle » d'insiders
# financés par la même source, qui achètent au premier bloc et revendent
# ensemble sur les acheteurs suivants). Un contrat peut être irréprochable et
# le rug déjà entièrement en place uniquement par cette structure de détention.
MAX_INSIDER_PCT = 10.0   # durci de 15 : le « bundle » d'insiders financés par
                         # la même source est la signature de rug la plus
                         # fréquente qui passe TOUS les autres filtres (contrat
                         # propre, LP verrouillée). 10% laisse déjà passer un
                         # airdrop d'équipe normal ; au-delà c'est un cartel de
                         # revente groupée.

# Part maximale détenue par un seul wallet individuel.
MAX_SINGLE_HOLDER_PCT = 10.0

# --- Réputation du wallet déployeur (BSC / EVM) ---------------------------------
# À 10 minutes de vie, un runner et un rug sont identiques on-chain : liquidité
# faible, holders concentrés, aucun historique. La seule chose qui les sépare
# vraiment tôt, c'est le PASSÉ du wallet qui a déployé le contrat. GoPlus
# /address_security expose `number_of_malicious_contracts_created` : un wallet
# qui a déjà sorti des contrats malveillants est une usine à rug, que le contrat
# du jour soit propre ou non. Vérifié UNIQUEMENT sur les candidats en passe de
# devenir un SIGNAL (un appel réseau par token, pas d'endpoint groupé chez
# GoPlus — voir core/scanner.py).
CHECK_CREATOR_REPUTATION = True
MAX_CREATOR_MALICIOUS_CONTRACTS = 0   # tolérance zéro : un seul suffit

# Verrouillage de la liquidité : part minimale de la LP brûlée ou verrouillée.
MIN_LP_LOCKED_PCT = 80.0

# Score RugCheck (Solana) : au-delà de ce niveau de risque, rejet direct.
# L'échelle RugCheck monte avec le danger (0 = propre).
MAX_RUGCHECK_SCORE = 1500
RUGCHECK_CRITICAL_LEVELS = {"danger"}  # tout risque de ce niveau = veto

# --- Mode strict ---
# True  : une donnée de sécurité obligatoire manquante interdit le SIGNAL
#         (le candidat bascule en VEILLE et sera re-testé). C'est le réglage
#         demandé, et le seul cohérent avec « pépite sûre ».
# False : les données manquantes sont seulement pénalisées dans le score.
SECURITY_STRICT_MODE = True

# --- Règle du maillon faible ---
# En plus du score global, un SIGNAL doit atteindre un minimum sur CHACUN des
# deux critères de risque. Sans cette règle, un token peut compenser une
# sécurité médiocre par une excellente liquidité et un bon momentum, et
# franchir le seuil global : c'est exactement la moyenne qui masque le risque.
# Un maillon faible sur la sécurité ne se compense pas, il déclasse en VEILLE.
MIN_SAFETY_FEATURE = 0.85
MIN_DISTRIBUTION_FEATURE = 0.70

# Champs de sécurité qui DOIVENT être présents et propres pour qu'un candidat
# accède au niveau SIGNAL. Absence = pas de signal (VEILLE à la place).
REQUIRED_SECURITY_FIELDS = {
    "evm": ["honeypot", "buy_tax", "sell_tax", "is_open_source", "lp_locked_pct", "top10_holder_pct"],
    "solana": ["mint_authority_active", "freeze_authority_active", "top10_holder_pct"],
}

# =============================================================================
# SCORE DE CONFIANCE
# =============================================================================
MIN_SCORE_TO_BUY = 80  # valeur par défaut au démarrage, ajustable dans l'UI de 30 à 100
MIN_SCORE_RANGE = (30, 100)

# --- Seuil du niveau VEILLE -------------------------------------------------
# CORRECTIF. Le niveau VEILLE avait son propre seuil fixe (55/100), totalement
# indépendant du curseur de l'interface. Régler le curseur sur 80 n'avait donc
# aucun effet sur les alertes de veille : des tokens à 57/100 continuaient de
# s'afficher et de partir sur Telegram, ce qui donnait à juste titre
# l'impression que le seuil n'était pas respecté.
#
# Marge dont le seuil de VEILLE descend SOUS le curseur de l'interface.
#
# Réglé à 0 : le curseur fait autorité. Ce que tu règles à 0,80 est un plancher
# strict — RIEN sous 80/100 n'apparaît, ni en SIGNAL, ni en VEILLE. Si tu veux
# voir des détections plus précoces (un token frais propre score plutôt ~70,
# parce qu'il n'a pas encore d'historique de marché — voir la fenêtre
# EARLY_DETECTION_WINDOW_MINUTES et core/scoring.py), la manœuvre est d'abaisser
# le curseur toi-même, pas de laisser le bot le contourner en douce.
#
# Passer cette marge à 10 ou 20 rouvre une VEILLE sous le curseur, jamais sous
# MIN_SCORE_TO_WATCH. À n'utiliser que si tu acceptes de voir s'afficher des
# scores inférieurs à ton réglage.
WATCH_SCORE_MARGIN = 0

# Plancher absolu : même avec une marge non nulle, la veille ne descend jamais
# sous ce score. Aligné sur le bas du curseur de l'interface (0.30).
MIN_SCORE_TO_WATCH = 30

# --- Pondération du scoring (total = 100) ---
# Rééquilibrage complet. Avant : momentum + pression acheteuse = 40 points,
# sécurité = 10. Maintenant l'inverse. Le momentum reste utile pour classer
# des tokens DÉJÀ jugés sûrs, il ne sert plus à les déclarer sûrs.
DEFAULT_SCORE_WEIGHTS = {
    "safety": 30,               # verrouillage LP, autorités révoquées, taxes, contrat vérifié
    "holder_distribution": 14,  # concentration réelle du top 10 et du créateur
    "liquidity_score": 14,      # liquidité absolue (échelle logarithmique)
    "liquidity_quality": 10,    # liquidité rapportée à la capitalisation (profondeur réelle)
    "smart_money": 10,          # wallets Nansen « smart money » détectés
    "vol_liq_ratio": 8,         # traction réelle, désormais pénalisée si absurdement haute
    "momentum_1h": 6,
    "buy_pressure": 5,
    "momentum_5m": 3,
}
SCORE_WEIGHTS = dict(DEFAULT_SCORE_WEIGHTS)

# Poids que l'auto-correction (core/self_tuning.py) n'a PAS le droit de
# toucher. Sans cette liste, la descente de gradient finissait
# mathématiquement par écraser le poids de la sécurité vers sa borne basse :
# sur un échantillon court, les tokens les plus rentables sont souvent aussi
# les plus risqués, et le bot « apprenait » donc à ignorer la sécurité.
#
# ÉLARGI. Avec seulement safety + holder_distribution épinglés, un seul cycle
# d'apprentissage sur des données bruitées (RPC en 429, quelques rugs
# enregistrés) suffisait à écraser TOUS les autres poids à leur plancher et à
# gonfler `liquidity_quality` jusqu'à son plafond — visible dans les journaux :
# « liquidity_quality=43.1, safety=30.0, ... , momentum_5m=2.2 ». Le score se
# résumait alors à la liquidité rapportée à la capitalisation, et plus aucun
# token ne franchissait le seuil (« 24 sous le seuil, 0 signal » à chaque
# cycle). On fige donc tout le socle « qualité » : seuls les critères de pur
# momentum restent apprenables, et ils ne pèsent ensemble que 22 points — pas
# de quoi désarmer quoi que ce soit.
PINNED_SCORE_WEIGHTS = [
    "safety", "holder_distribution", "liquidity_score",
    "liquidity_quality", "smart_money",
]

# Incrémenter invalide les poids appris sauvegardés (storage) et repart des
# valeurs par défaut. À bump à chaque changement de la structure des poids ou
# de PINNED_SCORE_WEIGHTS.
SCORE_WEIGHTS_VERSION = 2

# --- Seuils de rejet automatique (conservés pour compatibilité ascendante) ---
REJECTION_THRESHOLDS = {
    "top10_holder_pct_max": MAX_TOP10_HOLDER_PCT,
    "honeypot_flag": True,
    "mint_authority_active": True,
    "freeze_authority_active": True,
}

# =============================================================================
# AUTO-CORRECTION ET SUPERVISION
# =============================================================================
LEARNING_RATE = 0.05
LEARN_CYCLE_EVERY_N_SCANS = 20
WEIGHT_MIN_BOUND = 2
WEIGHT_MAX_BOUND = 12   # abaissé de 40 : aucun critère apprenable ne doit
                        # pouvoir dominer le score à lui seul (le socle qualité
                        # est de toute façon épinglé, voir PINNED_SCORE_WEIGHTS)
SELF_UPGRADE_CYCLE_EVERY_N_SCANS = 5

# Un signal dont le pool a disparu ou dont la liquidité s'est effondrée sous ce
# seuil est enregistré comme un rug (-100%) au lieu d'être ignoré. C'était le
# biais de survie le plus grave du bot : les rugs, devenus introuvables sur
# DexScreener, n'entraient jamais dans les données d'apprentissage, si bien que
# l'auto-correction n'apprenait que sur les survivants.
RUG_LIQUIDITY_FLOOR_USD = 500
RUG_LIQUIDITY_COLLAPSE_PCT = 80.0  # chute de liquidité au-delà = rug avéré

# =============================================================================
# GESTION DU RISQUE
# =============================================================================
RISK_DEFAULTS = {
    "account_size_usd": 1000.0,
    "risk_per_trade_pct": 1.0,
    "stop_loss_pct": 15.0,
    "tp1_pct": 30.0,
    "tp2_pct": 75.0,
    "tp3_pct": 150.0,
    "trailing_stop_pct": 10.0,
}

MODES = {
    "safe": {"max_position_pct": 0.5},
    "aggressive": {"max_position_pct": 2.0},
}

ALERT_LANGUAGES = ["en"]
SCAN_INTERVAL_SECONDS = 45

# Démarrage automatique du scanner à l'ouverture de l'application.
# Laissé à False : le scan ne démarre QUE sur clic du bouton DÉMARRER, et
# s'arrête réellement sur clic du bouton STOP (voir core/scanner.py).
AUTOSTART_SCANNER = False

# Durée pendant laquelle un token capté par le flux temps réel PumpPortal reste
# candidat au scan, même s'il n'a pas encore migré vers un pool. Relevé de 15 à
# 30 min : un token pump.fun met souvent plus d'un quart d'heure à migrer, et
# c'est justement pendant cette période qu'on veut le voir.
PUMP_RECENT_WINDOW_SECONDS = 1800

CHAIN_SCAN_MULTIPLIER = {
    "solana": 1,
    "bsc": 1,       # remonté de 2 à 1 : BNB Chain est une cible prioritaire
    "base": 1,      # cible prioritaire au même titre que BNB Chain
    "robinhood": 1,  # remonté de 2 à 1 : chaîne prioritaire, scannée chaque cycle
    "arc": 1,        # chaîne récente en cours d'activation (voir chains/arc.py)
    "ethereum": 4,
}

# Ordre de traitement des chaînes DANS un cycle. Les chaînes EVM ciblées
# (BNB Chain, Base, Robinhood) passent AVANT Solana : leur découverte et leurs
# vérifications s'exécutent en premier, avant que le volume Solana (300+
# candidats par cycle, RPC public limité) ne consomme le temps et le budget
# d'appels du cycle. Une chaîne absente de cette liste est traitée ensuite.
# Arc est placée après les trois chaînes EVM déjà établies : tant qu'elle
# n'est pas indexée par les sources de découverte (voir chains/arc.py), son
# traitement coûte un appel de sonde négligeable et rien de plus.
CHAIN_SCAN_PRIORITY = ["bsc", "base", "robinhood", "arc", "solana", "ethereum"]

# Plafond de vérifications par cycle, PAR CHAÎNE. Sur les chaînes EVM,
# l'enrichissement sécurité est un appel GoPlus groupé (lots de 25) sans RPC
# par token : on peut en vérifier davantage sans risque de saturation. Solana
# garde le plafond global MAX_ENRICHMENTS_PER_CYCLE (RPC public throttlé).
CHAIN_MAX_ENRICHMENTS_PER_CYCLE = {
    "bsc": 40,
    "base": 40,
    "robinhood": 60,   # ~70 candidats/cycle depuis le Token Screener Nansen
    "arc": 40,         # même plafond que BSC/Base en attendant un volume mesuré
}

PERFORMANCE_HORIZONS = {
    "1h": 3600,
    "6h": 21600,
    "24h": 86400,
}

# --- Recap narratif pour les réseaux sociaux (core/signal_recap.py) --------
# Résumé rédigé, généré à la demande quand l'utilisateur clique sur un token
# dans le dashboard. Copiable dans la langue d'affichage (fr/en/zh).
RECAP_ENABLED = True
# Fuseau affiché dans le recap. Vide = heure locale de la machine. Sinon un nom
# IANA ("Australia/Sydney", "Europe/Paris"...) ; l'abréviation (AEST, CET) est
# calculée automatiquement.
RECAP_TIMEZONE = os.getenv("RECAP_TIMEZONE", "").strip()
# Suivi du pic : à chaque cycle de scan, on relève la capitalisation courante
# des signaux encore actifs et on ne garde que le maximum atteint. C'est ce qui
# permet d'écrire « peak cap $X, +Y% » dans le recap.
RECAP_PEAK_TRACKING = True
RECAP_PEAK_MAX_AGE_SECONDS = 7 * 86400  # au-delà d'une semaine, on arrête de suivre le pic

NANSEN_CACHE_TTL_SECONDS = 600
RUGCHECK_CACHE_TTL_SECONDS = 900

# --- Découverte via le Token Screener Nansen (data_sources/nansen.py) ------
# Activée en profil « quality » (voir PROFILES / USE_NANSEN_DISCOVERY). C'est
# la seule source qui couvre à la fois BNB Chain et Robinhood Chain. Coût
# indicatif : ~5 crédits Nansen par appel, un appel par cycle et par chaîne
# concernée (BNB à chaque cycle, Robinhood un cycle sur deux — voir
# CHAIN_SCAN_MULTIPLIER).
NANSEN_SCREENER_TIMEFRAME = "1h"        # fenêtre des métriques : volume, variation, flux net
NANSEN_SCREENER_MAX_ROWS = 100
NANSEN_SCREENER_MIN_TRADERS = 8        # plancher de traders distincts à la découverte.
                                       # Abaissé de 15 : Base est une chaîne fine,
                                       # à 15 le screener y renvoyait 0 (mesuré).
                                       # Le tri qualité appartient au veto + scoring,
                                       # pas à la découverte — ceci n'écarte que les
                                       # tokens quasi morts.
NANSEN_SCREENER_CACHE_TTL_SECONDS = 40  # < SCAN_INTERVAL_SECONDS : au plus un appel réseau
                                        # par cycle et par chaîne
NANSEN_SCREENER_TIMEOUT = 30            # le screener balaie des milliers de tokens côté
                                        # serveur : le timeout de 8 s par défaut de
                                        # safe_post_json le fait échouer à tous les coups
                                        # (« Read timed out » dans les journaux)

DB_PATH = os.path.join(os.path.dirname(__file__), "storage", "gemhunter.db")

# --- Application du profil de scan, en dernier : gagne sur tout ce qui précède.
apply_profile(SCAN_PROFILE)
