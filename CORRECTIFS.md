# Gem Hunter — correctifs anti-rug

Ce document liste ce qui a été corrigé, pourquoi, et ce que ça change concrètement
quand tu relances le bot.

## Le problème de fond

Le bot ne se faisait pas tromper par les rug pulls. Il les cherchait.

Trois mécanismes se renforçaient les uns les autres :

**1. Une donnée absente était traitée comme rassurante.** Le moteur de sécurité
était explicitement construit pour « ne bloquer automatiquement que les dangers
confirmés par une donnée réellement disponible ». Or un token qui vient d'être
déployé n'a, par construction, presque aucune donnée disponible. Le rug n'avait
donc pas besoin de tromper le filtre, il lui suffisait d'être trop jeune pour
être vérifiable. Pire, ces tokens recevaient 0,5 sur 1 en score de sécurité, la
même note qu'un token effectivement vérifié et propre.

**2. Le score récompensait la signature exacte du pump-and-dump.** Momentum 5m
(10 points) + momentum 1h (15) + pression acheteuse (15) + ratio volume sur
liquidité (20) faisaient 60 points sur 100 pour des critères qu'une pompe
artificielle maximise par définition. La sécurité en pesait 10.

**3. Le bot n'apprenait jamais de ses rugs.** Le suivi de performance
abandonnait un signal quand il ne trouvait plus de prix. Or un token rug pull
n'a plus de paire indexée, justement. Ces signaux restaient éternellement « en
attente » et n'entraient jamais dans les données d'apprentissage. Le module
d'auto-correction ne voyait donc que des survivants, et poussait tranquillement
le poids de la sécurité vers sa borne basse, puisque sur un échantillon court
les tokens les plus rentables sont aussi les plus risqués.

## Bugs de code, chacun suffisant à laisser passer un rug

| Fichier | Bug | Conséquence |
|---|---|---|
| `chains/solana.py` | `"is_pump_bonding_curve": (...) or True` | Le `or True` rendait la condition inutile. TOUS les candidats PumpPortal étaient marqués bonding curve, ce qui leur donnait 100% de sécurité d'office et leur faisait sauter tous les filtres de liquidité, de volume et d'âge. |
| `data_sources/geckoterminal.py` | `contract` recevait l'adresse du POOL, pas du TOKEN | GoPlus interrogé sur une adresse de pool ne répond jamais. Toute la source primaire BNB Chain était rejetée en boucle pour « données indisponibles ». BNB Chain n'était pas scannée. |
| `data_sources/geckoterminal.py` | `NETWORK_MAP` sans `ethereum` | `get_new_pools("ethereum")` renvoyait toujours une liste vide. |
| `data_sources/geckoterminal.py` | Ni volume 1h, ni variations de prix, ni compteurs d'achats/ventes | Toutes les features de momentum valaient leur valeur par défaut sur chaque candidat BNB Chain. |
| `chains/bsc.py`, `chains/ethereum.py` | `search_pairs("bsc")` | L'endpoint de recherche DexScreener cherche du TEXTE, pas une chaîne. La source secondaire des deux chaînes EVM était décorative. |
| `data_sources/goplus.py` | `valeur * 100 si valeur <= 1 sinon valeur` | Une LP verrouillée à 0,9% était lue comme « 90% verrouillée » (rug qui passe), un top 10 réel de 0,8% comme « 80% » (bon token rejeté). |
| `data_sources/goplus.py` | Top 10 pris sans tri | GoPlus ne garantit pas l'ordre : le code sommait « les 10 premiers renvoyés », pas « les 10 plus gros ». |
| `core/security_checks.py` | `MIN_HOLDER_COUNT` lisait `holder_count` | Ce champ n'était jamais rempli par `normalize_security`. Le filtre ne s'est jamais déclenché. |
| `core/performance_tracker.py` | `if current_price is None: continue` | Biais de survie complet (voir plus haut). |
| `core/self_tuning.py` | Gradient appliqué à tous les poids | Le bot apprenait activement à ignorer la sécurité. |
| `chains/robinhood.py` | `AUTO_SCAN_ENABLED = False` en dur | La chaîne ne pouvait jamais s'activer, même une fois indexée. |

## Ce qui a été ajouté

### Un vrai moteur de veto

GoPlus renvoie une cinquantaine de champs. Le bot en lisait six. Il lit
maintenant tout ce qui décrit réellement un rug pull, et chacun est un veto
qu'aucun score ne rachète : `cannot_sell_all`, `transfer_pausable`,
`is_blacklisted`, `is_proxy`, `hidden_owner`, `can_take_back_ownership`,
`owner_change_balance`, `selfdestruct`, `slippage_modifiable`,
`personal_slippage_modifiable`, `anti_whale_modifiable`, `external_call`,
`gas_abuse`, `honeypot_with_same_creator`, `fake_token`, taxes d'achat et de
vente, part détenue par le créateur et par le propriétaire, et côté Solana les
extensions Token-2022 (`transfer_hook`, `transfer_fee_upgradable`,
`non_transferable`, `balance_mutable_authority`, `closable`,
`metadata_mutable`).

S'y ajoutent des vetos de marché : coquille vide (liquidité dérisoire face à la
capitalisation), wash trading (volume horaire supérieur à 12 fois la
liquidité), et pompe parabolique (plus de 300% en une heure — le contrat peut
être irréprochable, entrer là revient à acheter le sommet).

### Un second avis sur Solana

`data_sources/rugcheck.py` est un nouveau module. Sur Solana, GoPlus ne fait
aucune simulation d'achat ou de vente : sans ce module, le bot n'avait
littéralement aucune détection de piège à la vente sur sa chaîne prioritaire.
RugCheck apporte en plus les réseaux de wallets liés au créateur, ces
« bundles » d'insiders qui achètent au premier bloc et revendent ensemble sur
les acheteurs suivants. Un token peut avoir un contrat parfait et être un rug
déjà entièrement en place uniquement par cette structure de détention.

### Des vérifications on-chain immédiates

`data_sources/solana_rpc.py` lit maintenant la concentration réelle de l'offre
(`getTokenLargestAccounts`, en excluant les comptes de programme comme la
bonding curve et les pools) et la part encore détenue par le créateur
(`getTokenAccountsByOwner`). Ce sont les deux seuls signaux disponibles
immédiatement sur un token récent, et les plus fiables. Le schéma de rug le plus
fréquent sur pump.fun est aussi le plus simple : le dev achète une grosse part
de sa propre bonding curve, laisse monter, et vend tout d'un coup. Aucun drapeau
de contrat ne le signale.

### La règle du maillon faible

Un score global élevé peut masquer une faiblesse sur le seul critère qui compte.
Un SIGNAL doit désormais atteindre un minimum sur chacun des deux critères de
risque pris séparément, en plus de la moyenne. Un token avec 4% de taxe (sous le
veto) et un excellent profil par ailleurs ne peut plus compenser : il est
déclassé en veille.

## Les deux niveaux : VEILLE et SIGNAL

C'est le changement qui concilie « avoir l'information avant tout le monde » et
« ne pas acheter de rug ». L'ancien scanner n'avait qu'une sortie, et c'est
précisément ce qui obligeait le moteur de sécurité à être laxiste : refuser les
tokens non vérifiables aurait vidé l'écran.

**VEILLE** — le token est repéré tôt, affiché et alerté, marqué NON VÉRIFIÉ en
jaune sur le dashboard et en toutes lettres sur Telegram. Aucun prix d'entrée,
aucun stop loss, aucun objectif, pas d'achat automatique. C'est de
l'information, pas une recommandation.

**SIGNAL** — toutes les vérifications obligatoires ont abouti avec de vraies
données. Seul niveau qui produit un plan d'entrée.

Un candidat en veille reste dans une file de suivi et est re-testé à chaque
cycle. Dès que ses données deviennent disponibles et propres, il est promu en
signal, avec le plan d'entrée. Tu vois donc les tokens aussi tôt qu'avant, tu
n'engages du capital que sur ce qui a été vérifié.

Les tokens pump.fun en bonding curve sont toujours scannés, avec les
vérifications développeur, mais restent en veille jusqu'à leur migration vers un
pool réel. C'est le seul traitement honnête d'un segment où il n'y a rien à
vérifier avant.

## Robinhood Chain

Le mode dégradé écrit en dur est remplacé par une détection réelle, refaite
toutes les 30 minutes tant que la chaîne n'est pas pleinement disponible :

1. GeckoTerminal indexe-t-il le réseau ?
2. DexScreener indexe-t-il la chaîne ?
3. GoPlus couvre-t-il le chain ID 4663 ?

Si la découverte fonctionne mais pas GoPlus, la chaîne est scannée en veille
uniquement. Elle bascule d'elle-même en mode complet le jour où GoPlus l'ajoute,
sans intervention de ta part. L'état de détection est consultable depuis
l'interface (`getChainAvailability`).

Ethereum n'est plus scannée par défaut, pour concentrer le budget de rate-limit
sur BNB Chain, Solana et Robinhood. Le module reste fonctionnel et la chaîne
réactivable.

## Ce que ça change en pratique

**Tu auras beaucoup moins de signaux validés.** C'est le résultat attendu, pas
un dysfonctionnement. En mode strict, la grande majorité des tokens récents sont
soit dangereux, soit encore invérifiables. Le journal l'explique explicitement
quand aucun signal n'est apparu depuis dix minutes.

**Le seuil par défaut est passé de 90 à 80.** Ce n'est pas un assouplissement :
l'ancien 90 s'appliquait à un score où la sécurité pesait 10 points, le nouveau
80 s'applique à un score où elle en pèse 44 avec un plancher par critère. Un 80
d'aujourd'hui est bien plus exigeant qu'un 90 d'hier.

**Un taux de rug est enfin mesurable** (`get_rug_stats`). Un bon taux de
réussite accompagné d'un taux de rug élevé est le signe d'un filtre qui laisse
passer des pièges. C'est la métrique à surveiller.

## Correctifs de performance (après le premier lancement réel)

Le premier démarrage a fait remonter une rafale de `ReadTimeoutError` sur
DexScreener. Ce n'était pas un problème de réseau, c'était une régression que
j'avais introduite en remplaçant l'ancienne découverte cassée.

**Le scanner faisait jusqu'à 80 appels réseau par cycle et par chaîne.** La
découverte résolvait chaque adresse de token individuellement (jusqu'à 40
appels), et l'enrichissement vérifiait le badge DEX Paid une fois par candidat
(40 de plus). Sur un cycle censé durer 45 secondes, avec un timeout de 15
secondes et trois tentatives par requête, une seule API lente suffisait à
bloquer le scanner plusieurs minutes.

Trois corrections :

1. **Appels groupés.** DexScreener expose `/tokens/v1/{chaîne}/{adresses}` qui
   accepte 30 adresses par requête. La résolution de 40 adresses passe de 40
   appels à 2.

2. **Vérification DEX Paid paresseuse.** Elle n'a pas d'endpoint groupé et ne
   concerne que la poignée de candidats sur le point de devenir un signal. Elle
   est donc faite juste avant la promotion, plus à l'enrichissement de tous les
   candidats.

3. **Réseau moins patient.** Timeout de 15 à 8 secondes, trois tentatives
   ramenées à une, et le disjoncteur s'arme après 4 appels au lieu de 8. Le
   scanner tourne en boucle : insister sur une API lente coûte plus cher que
   d'abandonner et de réessayer au cycle suivant. Le bruit d'urllib3, qui
   journalisait chaque tentative en WARNING, est aussi coupé — nos propres
   messages disent la même chose en une ligne.

Au passage, le curseur de score du dashboard envoyait une commande au backend
à chaque pixel de déplacement, d'où la cascade de « Seuil de score minimum
réglé à... » au démarrage. Il n'envoie plus qu'au relâchement.

## Correctifs du second lancement réel

Le bot tourne, le filtre fait son travail (BNB Chain et Robinhood Chain
scannent, les rejets sont tous justifiés, les rugs sont enregistrés à -100%).
Trois problèmes restaient.

**Une erreur RPC sur chaque token Solana.** `getTokenAccountsByOwner` recevait
un filtre à deux clés (`mint` et `programId`), alors qu'il n'en accepte qu'une,
d'où l'erreur « invalid value: map, expected map with a single key » répétée
dans le journal. Le contrôle de la part détenue par le développeur — l'une des
vérifications anti-rug les plus importantes sur pump.fun — échouait donc
systématiquement et renvoyait « inconnu » à chaque fois. Filtrer sur le mint
seul suffit et couvre les deux programmes de token.

**Le RPC public Solana répondait 429.** Un cycle Solana peut remonter 200
créations pump.fun. Les vérifier toutes représentait environ 1400 requêtes en
45 secondes. Deux corrections :

- **Un préfiltre marché avant toute vérification.** Liquidité, volume, âge,
  capitalisation et pompe parabolique sont calculables à partir des données
  déjà en main, sans le moindre appel réseau. Sur un cycle réaliste, la moitié
  des candidats tombe là. Les faire d'abord passer par GoPlus, le RPC et
  RugCheck revenait à dépenser sept requêtes sur un token éliminé ensuite pour
  600 dollars de volume horaire.
- **Un plafond de 25 vérifications par cycle et par chaîne**, les plus liquides
  d'abord. Les candidats en excès ne sont pas perdus, ils passent au cycle
  suivant.

S'y ajoute un cache d'une minute sur les lectures de compte mint : la
concentration des porteurs et la part du développeur avaient toutes deux besoin
de l'offre totale et la relisaient chacune de leur côté, soit trois lectures du
même compte par token.

Au total, environ 1400 appels par cycle ramenés à 125.

**Le suivi de performance interrogeait DexScreener une fois par signal.** Sur
une vingtaine de signaux en attente, cela faisait vingt requêtes séquentielles
à chaque cycle, en plus de la découverte — de quoi expliquer une bonne part des
timeouts en cascade. Il utilise maintenant l'endpoint groupé.

Dernier point, mineur : Robinhood Chain annonçait « vérification de sécurité
disponible » alors que le test de couverture GoPlus avait échoué et renvoyait
True faute de pouvoir conclure. Le message distingue maintenant « confirmé
couvert » de « supposé couvert ».

## À faire de ton côté

1. Lance `python tests/smoke_test.py` depuis la racine du projet. 85 tests
   doivent passer. La première section est la batterie anti-rug : chaque cas
   correspond à un schéma qui passait le filtre avant.

2. Configure un RPC Solana personnel dans `.env` (`RPC_SOLANA=`). Les
   vérifications de concentration et de détention du dev font plusieurs appels
   RPC par token : le RPC public gratuit va saturer si le scan tourne en
   continu. Helius ou QuickNode en tier gratuit suffisent largement.

3. Supprime le dossier parasite `gem_hunter/{storage,data_sources,chains,core,gui}`,
   créé par un `mkdir` avec des accolades sous Windows. Il est vide et sans
   effet, mais il prête à confusion.

4. Ton curseur de score est resté sur 74 après ton dernier réglage. Avec le
   nouveau système de poids, 74 est nettement plus permissif que ce que tu
   cherches — remonte-le à 80 au moins, ou plus haut le temps de voir passer
   les premiers signaux.

5. La base de données existante est migrée automatiquement au démarrage
   (colonnes `tier`, `verified`, `vetoes`, `missing_checks` ajoutées sans
   toucher aux données). Les anciens poids de scoring seront ignorés puisque les
   clés ont changé, et les poids par défaut réappliqués.

---

# Mise à jour 2 — seuil de veille et alertes anglaises

Deux problèmes signalés après le second run réel.

## 1. Des tokens à 0.50 passaient alors que le seuil était réglé à 0.80

**Ce que tu voyais dans le journal :**

```
[bsc] 👁 VEILLE BEPE — score 57.2/100, NON VÉRIFIÉ (...)
[solana] 👁 VEILLE CASHCAT — score 61.4/100, NON VÉRIFIÉ (...)
```

**Cause.** Le niveau VEILLE avait son propre seuil, écrit en dur dans
`config.py` :

```python
MIN_SCORE_TO_WATCH = 55
```

et `core/scanner.py` l'utilisait tel quel, sans jamais regarder le curseur :

```python
if score < cfg.MIN_SCORE_TO_WATCH:   # 55, quoi que tu règles dans l'interface
```

Le curseur ne pilotait donc que le niveau SIGNAL. Régler 0.80 n'avait aucun
effet sur les alertes de veille, qui continuaient à sortir dès 55/100. Ce
n'était pas un bug de calcul de score : c'était deux seuils indépendants, dont
un seul t'était présenté.

**Correctif.** Le seuil de veille suit maintenant le curseur :

```python
def watch_threshold(self) -> int:
    margin = max(0, getattr(cfg, "WATCH_SCORE_MARGIN", 0))
    if margin == 0:
        return self.state.min_score
    return max(cfg.MIN_SCORE_TO_WATCH, self.state.min_score - margin)
```

`WATCH_SCORE_MARGIN` vaut **0** par défaut : rien en dessous du score que tu
règles n'apparaît nulle part, ni en signal, ni en veille. Si un jour tu veux
revoir la détection précoce un peu plus bas que ton seuil d'achat, mets cette
marge à 10 ou 15 dans `config.py` — la veille descendra alors jusqu'à
`seuil - marge`, sans jamais passer sous `MIN_SCORE_TO_WATCH`.

**En plus :** un bouton **VEILLE: ON/OFF** a été ajouté à la barre de commandes
du dashboard. Il coupe complètement les alertes de veille (le suivi interne
continue : un token coupé à l'écran peut toujours être promu en SIGNAL vérifié
plus tard).

## 2. Les alertes Telegram anglaises contenaient du français

**Cause.** Le gabarit était bien en anglais, mais tout ce qui était injecté
dedans — raisons, vetos, vérifications manquantes, niveau de risque — était
produit en français dans `core/security_checks.py` et `core/scoring.py`, puis
retraduit par une liste de motifs tenue à la main dans
`core/telegram_alerts.py`. Après la réécriture du moteur de sécurité, cette
liste ne connaissait plus aucun des nouveaux messages. Résultat : un texte
anglais parsemé de phrases françaises.

**Correctif — la duplication est supprimée, pas rallongée.** Nouveau module
`core/i18n.py` : chaque message est déclaré **une seule fois, dans les deux
langues**, avec ses paramètres nommés.

```python
"veto.top10": {
    "fr": "Concentration excessive — le top 10 détient {value}% (maximum toléré {max}%).",
    "en": "Excessive concentration — the top 10 hold {value}% (maximum allowed {max}%).",
},
```

Le moteur produit ses messages via `t("veto.top10", value=..., max=...)`. La
version anglaise ne peut donc plus être oubliée : elle est écrite au même
endroit et au même moment que la française. Pour les textes relus depuis la
base de données, la traduction inverse est **dérivée automatiquement** des
gabarits — il n'y a plus de seconde liste à maintenir.

108 messages sont couverts : les 31 vetos de contrat, les taxes, la
concentration, le LP lock, les verdicts RugCheck, les 7 vetos de marché, les
12 libellés de vérification manquante, les 4 raisons d'indisponibilité des
sources, les 30 observations de score, et les niveaux de risque.

`ALERT_LANGUAGES = ["en"]` reste le réglage : **tu ne reçois que l'anglais.**
Une langue sans gabarit est désormais écartée au lieu de retomber
silencieusement sur un doublon anglais.

## Tests

`python tests/smoke_test.py` → **101 tests**, dont 11 nouveaux :

- chaque message a bien une version FR et EN (les 108) ;
- chaque message français est retraduisible en anglais sans ambiguïté ;
- une alerte de VEILLE anglaise, construite à partir des vraies sorties du
  moteur, ne contient **aucun** marqueur de texte français ;
- idem pour les motifs de veto ;
- `Élevé` devient bien `High` ;
- le gabarit français reste fonctionnel pour l'interface ;
- avec un seuil réglé à 0.80, `watch_threshold()` vaut 80 ;
- un token à 57/100 n'est plus publié en veille quand le seuil est à 80.

---

# Mise à jour 3 — un vrai détecteur *dès la création*

Constat après lecture complète : le bot ne détectait **rien** au moment de la
création d'un token. Pas par excès de prudence sur les rugs — ce volet est
solide — mais parce que la détection précoce (VEILLE) était étranglée par deux
mécanismes qui se cumulaient.

## 1. Le curseur SCORE MIN n'était pas un plancher strict

> **Note.** Une version intermédiaire de ce correctif avait mis
> `WATCH_SCORE_MARGIN` à 20 pour que la VEILLE descende automatiquement sous le
> curseur. À l'usage, ça affichait des cartes à 0,61 alors que le curseur était
> réglé à 0,80 — exactement la plainte de la Mise à jour 2. **Marge remise à
> 0** : le curseur fait autorité, point. Pour de la détection plus précoce,
> l'utilisateur abaisse le curseur lui-même (le point 2 ci-dessous fait que ça
> marche : un token frais propre score désormais ~70 au lieu de ~55, donc il
> est réellement rattrapable en réglant le curseur vers 0,65-0,70).

Deux endroits appliquent maintenant ce plancher :

- **backend** — `watch_threshold()` renvoie exactement le curseur (marge 0) ;
  `_handle_watch` écarte tout token sous ce seuil, sans log INFO ni alerte ;
- **interface** — `addSignal` / `reconcileCardsWithScore` dans `index.html` :
  une carte dont le score est sous le curseur n'est pas rendue, et déplacer le
  curseur retire (ou réaffiche) les cartes en conséquence. Ça couvre les
  détections déjà en base au moment où on change le réglage. Le curseur pousse
  aussi sa valeur au backend dès l'ouverture, pour que « SEUIL ACTIF » dans
  l'en-tête et le seuil réellement appliqué coïncident.

## 2. Le score punissait l'absence d'un marché qui n'existe pas encore

Sur un token de cinq minutes, volume, momentum et pression acheteuse n'existent
pour **personne**. Le scoring les notait quand même 0,25 (« non vérifié »),
soit quatre critères qui tiraient mécaniquement tout token frais sous le seuil.

Nouvelle règle, dans une fenêtre `EARLY_DETECTION_WINDOW_MINUTES` (60 min, ou
tant que le token est en bonding curve) : un critère de marché **absent** est
**exclu** du calcul — exactement comme le smart money quand Nansen n'est pas
branché — au lieu d'être noté bas. Le score early ne reflète alors que ce qui
est réellement vérifiable dès la création : sécurité du contrat, répartition de
l'offre, liquidité, part détenue par le dev. Un critère **présent** reste
évalué normalement, à la hausse comme à la baisse. Hors fenêtre, l'absence
redevient suspecte (0,25) : à trois heures, un token sans le moindre volume
n'est pas « jeune », il est mort.

Effet mesuré (test 11bis) : un token neuf, contrat propre, offre saine, sans
historique, passe de ~55/100 (condamné) à ~70/100 (rattrapable en réglant le
curseur vers 0,65-0,70).

## 3. Le préfiltre marché rejetait les tokens neufs peu liquides

`evaluate_market_prefilter` appliquait les mêmes planchers de liquidité (8 000 $)
et de volume (2 000 $/h) à un token de quatre minutes qu'à un token de six
heures. Un token frais sous ces seuils était **rejeté définitivement**, et
re-rejeté à chaque cycle : il ne pouvait jamais entrer dans la file de VEILLE
où il aurait été re-testé.

Dans la fenêtre early : plancher de liquidité abaissé à `EARLY_MIN_LIQUIDITY_USD`
(2 000 $), plancher de volume **ignoré** (déféré). Le token part en VEILLE et
sera revérifié tant qu'il reste dans la file. Les vetos **durs** — wash trading,
pompe verticale (+300 %/h), coquille vide face à la capitalisation, honeypot,
dev majoritaire — restent actifs **dans** la fenêtre : un tout jeune token qui
coche un de ces schémas est toujours rejeté (tests 11bis).

## 4. STOP ne stoppait pas — et démarrage sur clic uniquement

Deux corrections liées :

**Le bouton STOP n'interrompait que le cycle SUIVANT.** `stop()` posait bien le
drapeau d'arrêt, mais le cycle en cours continuait jusqu'au bout : découverte,
enrichissement et **alertes** de tous les candidats déjà trouvés. Sur un cycle
chargé, ça faisait des dizaines de secondes d'alertes après le clic. Le cycle
teste maintenant `_is_stopping()` à chaque étape — boucle des chaînes, avant
enrichissement, boucle des candidats, et juste avant chaque émission de signal
ou d'alerte (SIGNAL comme VEILLE). Un STOP coupe le cycle en cours dans la
seconde, sans plus rien émettre. `run()` attend en plus qu'un éventuel thread
d'un STOP précédent soit terminé avant d'en lancer un neuf, pour éviter deux
boucles de scan en parallèle.

**Démarrage manuel.** `AUTOSTART_SCANNER = False` : le scan ne part QUE sur clic
du bouton DÉMARRER (réglage dans `config.py` si un jour tu veux l'inverse).

## 5. Points annexes

- `PUMP_RECENT_WINDOW_SECONDS` : fenêtre de suivi des créations PumpPortal
  portée de 15 à 30 min (un token pump.fun met souvent plus d'un quart d'heure
  à migrer, et c'est pendant ce temps qu'on veut le voir). Tampon interne du
  listener relevé de 1 000 à 4 000 entrées pour ne rien évincer sur un pic.
- Curseur de score du dashboard : la position de départ affichait 0,90 alors
  que le backend utilisait 0,80. Aligné sur 0,80.

## Tests

`python tests/smoke_test.py` → **119 tests** (101 + 18). Section **11bis**
dédiée à la détection dès la création : score early relevé (~70 au lieu de
~55), critères de marché absents exclus du score, même token noté plus bas à
3h, préfiltre qui laisse entrer le jeune token peu liquide mais rejette le
token mûr équivalent, vetos durs (pompe verticale, dev majoritaire) toujours
actifs sur un token de cinq minutes. Section **12c** : curseur à 0,80 => un
memecoin à 0,61 n'est ni affiché ni alerté, et redevient visible dès qu'on
abaisse le curseur à 0,60. Section **12d** : après un STOP, un candidat même
excellent ne déclenche plus aucune alerte.

---

# Mise à jour 4 — profil « quality » : viser le potentiel liste Alpha

Constat de l'utilisateur : **tous** les tokens remontés finissaient en rug.
Normal — le bot était branché sur la création pure : flux temps réel pump.fun,
market cap de quelques milliers de dollars, tokens de cinq minutes. Sur ce
segment, l'écrasante majorité de ce qui naît EST un rug. « Détecter dès la
création » et « trouver un token qui a le potentiel d'être listé sur Binance
Alpha » sont deux objectifs opposés.

Aucun bot sur API gratuites ne **prédit** une liste Alpha. Mais il peut ne
remonter **que** des tokens qui cochent les traits que les tokens Alpha
partagent : plusieurs jours d'historique sans rug, des centaines de porteurs,
une liquidité à cinq/six chiffres, un contrat vérifié et propre, et des wallets
« smart money » déjà positionnés.

## Deux profils, réglables (bouton PROFIL, ou `SCAN_PROFILE` dans `.env`)

**`quality`** (défaut) :

| | quality | degen (ancien) |
|---|---|---|
| Flux temps réel pump.fun | **coupé** | actif |
| Découverte | pools qui **montent** (GeckoTerminal trending) | pools les plus récentes |
| Market cap | 150 k – 50 M $ | 3 k – 200 k $ |
| Liquidité min | 40 k $ | 8 k $ |
| Volume 1h min | 15 k $ | 2 k $ |
| Âge min avant SIGNAL | **12 h** (le temps que les rugs éclair s'effondrent) | 10 min |
| Âge max | 21 jours | 6 h |
| Porteurs min | **300, et le nombre DOIT être connu** | 50 si fourni |
| Top 10 max | 20 % | 25 % |
| Smart money | **au moins un wallet requis** (Nansen) | non requis |
| VEILLE (« NON VÉRIFIÉ ») | **suivie en interne, jamais affichée ni alertée** | affichée |
| Fenêtre « dès la création » du scoring | désactivée | 60 min |

**`degen`** : comportement des Mises à jour 1-3 à l'identique, pour qui veut le
firehose et la détection la plus précoce possible.

## Ce qui a changé dans le code

- `config.py` : `PROFILES` + `apply_profile()` (applicable à chaud). Toutes les
  constantes pilotées par le profil sont écrites en dernier dans le fichier.
- `data_sources/geckoterminal.py` : `get_trending_pools()`.
- `chains/solana.py`, `chains/evm_common.py` : le flux pump.fun est branché
  uniquement si `ENABLE_PUMPPORTAL_FIREHOSE`, et la découverte part des pools
  trending si `USE_TRENDING_DISCOVERY`.
- `core/security_checks.py` : `REQUIRE_HOLDER_COUNT` — un candidat dont le
  nombre de porteurs est inconnu ne peut pas devenir un SIGNAL.
- `core/scoring.py` : `REQUIRE_SMART_MONEY` — pas de smart money positionné
  (feature < `MIN_SMART_MONEY_FEATURE`) => reste en VEILLE.
- `core/scanner.py` : `PUBLISH_WATCH_ALERTS` — en quality, la VEILLE est suivie
  pour la promotion mais jamais publiée ; `set_scan_profile()` + slot bridge
  `setScanProfile` / `getScanProfile` ; bouton **PROFIL: QUALITY/DEGEN** dans
  l'interface.

## Attente réaliste

En profil quality tu verras **très peu** de SIGNAUX — parfois zéro sur une
journée calme. C'est le résultat recherché : le bot ne remonte plus que des
tokens qui ont déjà prouvé quelque chose. Un flux nourri de « signaux » sur ce
créneau serait le symptôme d'un filtre trop laxiste, pas d'un bon bot.

## Tests

`python tests/smoke_test.py` → **128 tests** (119 + 9). Les sections 1-12
tournent en profil `degen` (batterie anti-rug historique inchangée). Section
**13** : profil `quality` — flux pump.fun coupé, VEILLE non publiée, planchers
relevés ; un token éprouvé + smart money atteint le SIGNAL, le même token sans
smart money reste en VEILLE, un token de cinq minutes ne peut pas être un
SIGNAL.

---

# Mise à jour 5 — pourquoi le profil quality ne sortait rien

Journal d'un run réel : `0 signal / 0 veille` cycle après cycle, meilleur
candidat (DONK) coincé à ~80/100. Trois causes, dont deux d'infrastructure.

## 1. Le RPC Solana public était noyé sous les 429 (À FAIRE DE TON CÔTÉ)

`api.mainnet-beta.solana.com` renvoyait `429 Too many requests` en rafale à
chaque cycle. La lecture on-chain de la concentration des porteurs et de la
part détenue par le dev — deux vérifications anti-rug importantes — échouait
donc systématiquement sur Solana. RugCheck couvre une partie (c'est lui qui
produit les rejets « top 10 détient X% »), mais pas tout.

**Configure `RPC_SOLANA` dans `.env`** avec un endpoint perso (Helius ou
QuickNode en tier gratuit suffisent). Sans ça, le volet Solana tourne à
moitié aveugle et le journal est illisible.

## 2. GeckoTerminal rate-limité → la découverte « trending » disparaissait

Le tier gratuit GeckoTerminal plafonne à ~30 req/min. Le scanner interrogeait
`new_pools` + `trending_pools` pour trois chaînes toutes les 45 s → 429 en
rafale → disjoncteur armé → `Trending: 0` dans les journaux, donc en profil
quality plus aucune source de découverte pertinente.

**Correctif :** `data_sources/geckoterminal.py` met ces listes en cache **90 s**
(`_fetch_pools`). Elles ne bougent pas d'un cycle à l'autre. Un résultat vide
n'est jamais mis en cache. La découverte trending reste maintenant alimentée.

## 3. Les quasi-signaux étaient invisibles

En quality, `PUBLISH_WATCH_ALERTS` était à `False` : un token comme DONK, à
80/100, tous vetos passés, mais sans smart money positionné, restait en VEILLE
**et n'était jamais affiché**. Écran vide, aucune idée de ce qui bloquait.

**Correctif :** `PUBLISH_WATCH_ALERTS = True` aussi en quality. Sur ce profil
la VEILLE ne contient que des quasi-signaux (score au niveau du curseur, marqués
NON VÉRIFIÉ, sans plan d'entrée) et leur `missing_checks` dit pourquoi ils ne
passent pas en SIGNAL — le plus souvent « aucun wallet smart money positionné ».

## Points annexes

- `MAX_PAIR_AGE_HOURS` du profil quality : 21 j → **45 j**. À 21 j, beaucoup de
  projets encore dans leur fenêtre Alpha étaient rejetés « paire trop ancienne ».
- `core/scoring.py` : `REQUIRE_SMART_MONEY` ne condamne plus définitivement le
  tier SIGNAL quand Nansen est indisponible pour tout le monde (pas de clé).
  Avec une clé configurée, un token sans smart money reste en VEILLE (inchangé).

## Ce qui reste vrai

Le profil quality sort **peu** de SIGNAUX vérifiés — c'est voulu. La nouveauté
est que tu vois désormais les quasi-signaux en VEILLE et la raison du blocage,
au lieu d'un écran vide. Si tu veux plus de volume, bouton **PROFIL: DEGEN**.

## Tests

`python tests/smoke_test.py` → **128 tests**. Section 13 mise à jour : profil
quality publie la VEILLE, `REQUIRE_SMART_MONEY` avec dégradation gracieuse,
et section **13d** : deux découvertes GeckoTerminal rapprochées ne font qu'un
seul appel réseau (cache 90 s).

---

# Mise à jour 6 — BNB Chain et Robinhood Chain remontent enfin quelque chose

En profil quality, ces deux chaînes produisaient `0` : **tout tombait au
préfiltre**. Les seuils quality (liquidité ≥ 40 k$, volume 1h ≥ 15 k$) sont
calibrés pour Solana/ETH. Sur Robinhood Chain, un pool à 40 k$ est déjà gros ;
sur BNB Chain, la majorité des tokens frais sont des memecoins à 10-30 k$ de
liquidité. Et Nansen n'indexe pas Robinhood, donc « smart money exigé » y
rendait le SIGNAL structurellement inatteignable.

## Ce qui change

**`config.CHAIN_MARKET_OVERRIDES`** — des seuils par chaîne, appliqués
par-dessus le profil, **uniquement dans le sens de l'assouplissement** (un
`MIN_*` ne peut qu'être abaissé, un `REQUIRE_*` que désactivé) :

| | Robinhood | BNB Chain | Solana (inchangé) |
|---|---|---|---|
| Liquidité min | 8 k$ | 18 k$ | 40 k$ |
| Volume 1h min | 2,5 k$ | 5 k$ | 15 k$ |
| Liq/MC min | 1 % | 3 % | 3 % |
| Porteurs min | non requis | 100 | 300, requis |

`core/security_checks.py` et `core/scoring.py` lisent désormais ces seuils via
`config.chain_threshold(nom, chaîne)`.

**Smart money** (`core/scoring.py`) : `REQUIRE_SMART_MONEY` ne s'applique plus
sur une chaîne que Nansen n'indexe pas (`data_sources/nansen.supports_chain`).
Robinhood peut donc atteindre le SIGNAL sur la seule foi de la sécurité du
contrat, de la distribution et de la liquidité. BNB Chain, elle, reste soumise
à l'exigence smart money (Nansen couvre « bnb »).

En degen, les seuils de base étant déjà bas, ces surcharges n'ont aucun effet.

## Tests

`python tests/smoke_test.py` → **131 tests**. Section **13e** : un token
Robinhood à 20 k$ de liquidité passe le préfiltre quality et atteint le SIGNAL,
alors que le même token sur Solana est écarté pour liquidité insuffisante.

---

## Mise à jour 7 — BNB Chain et Robinhood : découverte via le Token Screener Nansen

### Le vrai blocage n'était pas les seuils, c'était la découverte

La Mise à jour 6 avait assoupli les seuils par chaîne, mais BNB Chain et
Robinhood restaient muettes parce qu'aucun candidat n'arrivait jusqu'au
préfiltre :

- **Robinhood Chain** : `chains/robinhood.py` n'active la découverte que si
  GeckoTerminal **ou** DexScreener indexe le réseau. Ni l'un ni l'autre ne le
  fait (L3 Arbitrum Orbit, trop récente) → `discover_candidates()` renvoyait
  `[]` à chaque cycle. `RPC_ROBINHOOD` est vide également.
- **BNB Chain** : découverte via GeckoTerminal, mais le tier gratuit (~30
  req/min) répond `429` en rafale dès qu'on interroge trending + new pools pour
  3 chaînes toutes les 45 s. Disjoncteur armé → source vide. Symptôme déjà
  visible dans les journaux : `Trending: 0`, `429` en boucle.

### Correctif : le Token Screener Nansen comme source de découverte

`POST https://api.nansen.ai/api/v1/token-screener` (~5 crédits/appel, mêmes
en-têtes que l'endpoint smart-money déjà utilisé). Un appel groupé par chaîne
renvoie tout ce dont le préfiltre et le scoring ont besoin — adresse, âge,
capitalisation, liquidité, volume, achats/ventes, variation de prix, flux net —
et Nansen couvre réellement les deux chaînes (vérifié en direct : HTTP 200 sur
`bnb` **et** `robinhood`).

- `data_sources/nansen.py` : `screen_tokens(chain)`, `screener_supports_chain()`,
  `NANSEN_SCREENER_CHAIN_MAP` (inclut `robinhood`, contrairement à
  `NANSEN_CHAIN_MAP` — carte **distincte** à dessein : le screener couvre
  Robinhood, l'endpoint `/tgm/holders` non, donc « smart money exigé » ne
  bloque pas le passage sur Robinhood). Fenêtre `1h` par défaut, résultat mis
  en cache 40 s (< intervalle de scan → au plus un appel réseau par cycle et
  par chaîne).
- `config.py` : `USE_NANSEN_DISCOVERY` (True en `quality`, False en `degen` qui
  garde le firehose gratuit) ; constantes `NANSEN_SCREENER_TIMEFRAME` /
  `_MAX_ROWS` / `_CACHE_TTL_SECONDS`.
- `chains/evm_common.py` : nouvelle source Nansen dans `discover_candidates()` ;
  GeckoTerminal n'est plus interrogé quand le réseau n'y est pas servi (fin du
  gaspillage de budget rate-limit pour Robinhood).
- `chains/robinhood.py` : `_probe()` accepte « le screener Nansen couvre la
  chaîne » comme voie de découverte valide (`_status["nansen_screener"]`).

### Ce que ça donne concrètement

- **BNB Chain** : GoPlus couvre le chain ID 56 → les candidats Nansen sont
  enrichis en sécurité de contrat réelle → **SIGNAL vérifié possible** (sous
  réserve de smart money, `bnb` étant couvert par `/tgm/holders`).
- **Robinhood Chain** : aucune API d'audit de contrat n'existe pour le chain ID
  4663 (GoPlus ne le couvre pas, RugCheck est Solana). Les tokens sont donc
  **découverts et affichés en VEILLE (NON VÉRIFIÉ)**, jamais en SIGNAL, tant
  que GoPlus n'ajoute pas la chaîne. Sans données de contrat, leur score
  plafonne autour de 40-45/100 : **il faut baisser le curseur** pour les voir.
  C'est une limite du paysage de données, pas un bug — le bot ne fabrique pas
  un « rien à signaler » qu'il ne peut pas vérifier.

### Tests

`python tests/smoke_test.py` → **137 tests, 0 échec** (+6). Section 13f :
couverture screener robinhood/bsc, `supports_chain("robinhood")` reste False,
normalisation d'une ligne screener, `evm_common.discover_candidates("robinhood")`
remonte les candidats Nansen, `robinhood._probe` s'active sur la seule foi du
screener. Appel REST réel vérifié hors tests : HTTP 200 sur `bnb` et
`robinhood` avec le corps exact que `screen_tokens` construit.

---

## Mise à jour 8 — Pourquoi « 24 sous le seuil, 0 signal » à chaque cycle

Les journaux fournis montrent trois causes distinctes, deux corrigées ici.

### 1. L'auto-correction avait effondré les poids du score (corrigé)

Journal : `Nouveaux poids : liquidity_quality=43.1, safety=30.0,
holder_distribution=14.0, liquidity_score=2.2, smart_money=2.2,
vol_liq_ratio=2.2, momentum_1h=2.2, buy_pressure=2.2, momentum_5m=2.2`.

Seuls `safety` et `holder_distribution` étaient épinglés. Un seul cycle
d'apprentissage sur des données bruitées (RPC Solana en `429`, quelques rugs
enregistrés) a suffi à coller **tous** les autres poids apprenables à leur
plancher et à gonfler `liquidity_quality` jusqu'à son plafond. Le score se
résumait alors à `0,30·sécurité + 0,14·distribution + 0,43·(liq/mcap)` : un
token propre avec 5 % de liq/mcap plafonnait vers 46/100, loin d'un curseur à
70-80. D'où « 24 sous le seuil, 0 signal » sur **les trois** chaînes.

- `config.PINNED_SCORE_WEIGHTS` : ajoute `liquidity_score`, `liquidity_quality`,
  `smart_money`. Seuls les 4 critères de pur momentum restent apprenables
  (22 points au total — pas de quoi désarmer le socle).
- `config.WEIGHT_MAX_BOUND` : 40 → 12 (aucun critère apprenable ne peut plus
  dominer).
- `config.SCORE_WEIGHTS_VERSION = 2` + `self_tuning.load_weights()` :
  réinitialise automatiquement les poids sauvegardés d'une structure
  antérieure, **et** détecte un effondrement (`_is_degenerate` : tous les
  apprenables au plancher sauf un au plafond) pour repartir des valeurs par
  défaut. Les poids corrompus déjà en base sont donc neutralisés au prochain
  lancement.

### 2. Le Token Screener Nansen expirait à tous les coups (corrigé)

Journal : `Requête réseau échouée (…/token-screener) : Read timed out.
(read timeout=8)` en boucle. Le screener balaie des milliers de tokens côté
serveur ; 8 s ne suffisent pas. `config.NANSEN_SCREENER_TIMEOUT = 30`, passé
explicitement par `nansen.screen_tokens`. Sans ça, BSC retombait sur la seule
découverte GeckoTerminal (elle-même en `429`) et Robinhood sur rien.

### 3. `RPC_SOLANA` toujours absent (action requise, non corrigeable ici)

Le mur de `429` de `api.mainnet-beta.solana.com` persiste dans ces journaux,
et fait maintenant aussi expirer GoPlus Solana et RugCheck. Tant que
`RPC_SOLANA=` n'est pas rempli dans `.env` (Helius/QuickNode, gratuit), toute
la vérification Solana reste dégradée.

### Sur le token entouré en rouge (LEGO, ~19 min, ~1 M$ de MC)

C'est un token Solana pump.fun (contrat en `…pump`). Le bot **le détecte** :
`[solana] LEGO REJETÉ — Réseau d'insiders liés au créateur détenant 95,4 %
de l'offre — schéma de bundle avant revente groupée`. 95 % de l'offre dans des
wallets liés au créateur, c'est le rug entièrement en place, pas une pépite —
le rejet est correct. Le détecter plus tôt n'y changerait rien. En profil
`quality`, il est de toute façon écarté sur l'âge (< 12 h). Viser des tokens à
ce stade (quelques minutes, ~1 M$ de MC) est le rôle du profil `degen`, où la
VEILLE précoce est déjà affichée (`👁 VEILLE …` dans les mêmes journaux).

### Tests

`python tests/smoke_test.py` → **140 tests, 0 échec** (+3 : effondrement des
poids détecté et réinitialisé, poids d'une version antérieure réinitialisés,
socle qualité épinglé).

---

## Mise à jour 9 — Dashboard trilingue (FR / EN / 中文)

Demande : pouvoir choisir la langue de toute l'interface du dashboard, pas
seulement des alertes Telegram (déjà bilingues FR/EN depuis la Mise à jour…
concernant `core/i18n.py`).

### Catalogue backend étendu au chinois

`core/i18n.py` : chaque message (vetos, vérifications manquantes, raisons,
niveaux de risque — 110 entrées) a désormais une clé `"zh"` en plus de `"fr"`
et `"en"`. `SUPPORTED_LANGS = ("fr", "en", "zh")`. La traduction inverse
(`translate()`, utilisée pour les textes déjà stockés en base) fonctionne sans
changement : elle part toujours du gabarit français, `zh` n'est qu'une cible
de rendu supplémentaire.

Les alertes Telegram (`core/telegram_alerts.py`, `config.ALERT_LANGUAGES`)
restent volontairement FR/EN uniquement — c'est un réglage séparé, non
demandé ici, et `resolve_languages()` continue de filtrer sur ses propres
gabarits (`TEMPLATES`), indépendants de `i18n.SUPPORTED_LANGS`.

### Nouveau réglage : langue d'affichage du dashboard

`gui/bridge.py` :
- `getUiLanguage()` / `setUiLanguage(lang)` — persistée en base
  (`storage.db`, clé `ui_language`), défaut `"fr"` (comportement inchangé pour
  qui ne touche pas au sélecteur).
- `_localize_signal()` : traduit `reasons` et `missing_checks` d'un signal
  dans la langue actuellement choisie AVANT de l'envoyer au JS (`newSignal`,
  `getExistingSignals`, `getVerifiedSignals`, `getWatchSignals`). Le moteur
  continue de produire ces textes en français en interne (aucun changement à
  `core/security_checks.py` / `core/scoring.py`) ; la traduction n'a lieu
  qu'au moment de l'affichage, jamais en base — Telegram et le dashboard
  peuvent donc afficher des langues différentes sans interférence.
- `risk_level` n'est PAS traduit côté backend (il reste une des 4 valeurs
  françaises fixes) : c'est le dashboard qui le mappe à l'affichage via une
  petite table de correspondance, plus simple qu'un aller-retour serveur pour
  4 valeurs connues.

### Dashboard (gui/web/index.html)

- Sélecteur de langue (FR / EN / 中文) dans l'en-tête.
- Dictionnaire `I18N` couvrant tout le texte statique de l'interface : boutons
  (RUN/PAUSE/STOP/AUTO BUY/TELEGRAM/MULTICHAIN/SMART MONEY/VEILLE/PROFIL et
  son infobulle), titres de panneaux, filtres, badges de carte
  (VEILLE/VÉRIFIÉ), unités d'âge (min/h/j), toute la fiche modale (statuts,
  labels MARKET CAP/LIQUIDITÉ/STOP LOSS/TP, texte d'avertissement VEILLE,
  boutons COPIER/COPIÉ), et les noms des 9 critères de scoring.
- Changer de langue : (1) persiste le choix via `bridge.setUiLanguage`, (2)
  ré-applique toutes les traductions statiques, (3) recharge les signaux
  existants (`getExistingSignals`) pour que leurs raisons/vérifications
  manquantes, déjà en cache côté JS dans l'ancienne langue, soient
  ré-affichées traduites.
- La langue choisie est relue au démarrage (`bridge.getUiLanguage`) pour
  refléter le dernier choix, comme le fait déjà le bouton PROFIL avec
  `getScanProfile`.

### Hors périmètre (assumé)

- Le panneau JOURNAL affiche les messages du logger Python tels quels
  (français, produits dans tout `core/scanner.py` et ailleurs) : les traduire
  demanderait de reprendre des centaines de chaînes de log dispersées dans le
  moteur, sans rapport avec « l'interface » au sens strict. Non fait.
- Les alertes Telegram restent FR/EN, réglage indépendant.

### Tests

`python tests/smoke_test.py` → **142 tests, 0 échec** (+2 : couverture FR/EN/ZH
du catalogue, réversibilité de chaque message vers le chinois). La logique de
`gui/bridge.py::_localize_signal` a été vérifiée manuellement (elle dépend de
PyQt6, hors du suite offline par conception) : un signal test traduit
correctement `reasons`/`missing_checks` dans les trois langues, `risk_level`
reste inchangé. Le script inline du dashboard a été validé syntaxiquement
(`node --check`) et chaque clé `data-i18n`/`t("...")` référencée a été
recoupée avec le dictionnaire `I18N` (aucune clé manquante ni orpheline).

---

## Mise à jour 10 — Détection précoce anti-rug (profil degen + réputation du déployeur)

**Constat.** Des heures de scan, zéro SIGNAL sur les trois chaînes. Le bot
tournait en profil `quality` (`SCAN_PROFILE=quality`), conçu pour viser des
tokens *matures* type Binance Alpha : âge minimum 12 h, 300 porteurs exigés,
smart money Nansen obligatoire pour atteindre le SIGNAL. Or Nansen timeoutait
en boucle (`Read timed out`), et un token de 19 minutes comme « LEGO » ou le
prochain MarsCoin est exclu par construction. Le profil `quality` ne peut pas
répondre à « détecter très tôt ».

**1. Profil `degen` par défaut** (`config.py`).
`SCAN_PROFILE` par défaut : `quality` → `degen`. Réactive le flux temps réel
pump.fun, âge minimum 10 min, pas de smart money exigé. `MARKET_CAP_MAX` du
profil degen relevé de 200 k$ → 2 M$ : un pump.fun jeune peut déjà valoir
~1 M$ en < 20 min (le cas « LEGO » qui a motivé le changement), 200 k$
excluait justement ce type de token. Le changement ne s'applique qu'au
prochain démarrage (le profil est en mémoire, pas persisté) ; bascule à chaud
possible via le bouton PROFIL du dashboard.

**2. Vetos anti-rug durcis, sans dépendre de données matures** (`config.py`).
- `MAX_TOP10_HOLDER_PCT` (profil degen) : 25 → 20. Sur un token de quelques
  minutes, top 10 > 20 % = le rug est déjà en place dans la structure de
  détention.
- `MAX_INSIDER_PCT` (global) : 15 → 10. Le « bundle » d'insiders financés par
  la même source est la signature de rug qui passe le plus souvent TOUS les
  autres filtres (contrat propre, LP verrouillée) ; 10 % laisse passer un
  airdrop d'équipe normal, au-delà c'est un cartel de revente groupée.

**3. Réputation du wallet déployeur (BSC/EVM).** À 10 minutes de vie, un runner
et un rug sont identiques on-chain. La seule chose qui les sépare vraiment,
c'est le PASSÉ du wallet qui a déployé le contrat.
- `data_sources/goplus.py` : `_normalize_evm` capture désormais
  `creator_address` / `owner_address` (jusqu'ici ignorés). Nouvelles fonctions
  `check_evm_address_security()` (GoPlus `/api/v1/address_security/{addr}`,
  gratuit, sans clé) et `normalize_address_reputation()` qui réduit la réponse
  à deux signaux : `number_of_malicious_contracts_created` (une usine à rug en
  a plusieurs) et les labels d'activité criminelle avérée
  (`phishing_activities`, `stealing_attack`, `money_laundering`,
  `honeypot_related_address`…).
- `config.py` : `CHECK_CREATOR_REPUTATION = True`,
  `MAX_CREATOR_MALICIOUS_CONTRACTS = 0` (tolérance zéro).
- `core/security_checks.py` : `creator_reputation_veto(candidate)` — motif de
  rejet, ou `None`. Une adresse inconnue de GoPlus (`available: False`) n'est
  PAS un feu vert, mais n'est pas non plus un veto.
- `core/i18n.py` : `veto.creator_known_rugger`, `veto.creator_wallet_flagged`
  (FR/EN/中文).
- `core/scanner.py` : la vérification est faite juste avant la promotion en
  SIGNAL, comme `dex_paid` — un appel réseau par token (GoPlus n'a pas
  d'endpoint groupé pour `/address_security`), donc réservée à la poignée de
  candidats qui ont déjà tout passé. Les adresses de burn/zéro sont ignorées.
  Non appliquée à la VEILLE (budget réseau) ni à Solana (RugCheck couvre déjà
  l'historique du créateur là-bas via « Creator history of rugged tokens »).

**Tension assumée.** « Détecter très tôt » et « zéro rug » ne sont pas
pleinement compatibles avec des API gratuites : à 10 minutes, l'essentiel du
segment memecoin est du rug, et rien on-chain ne le prouve encore. Ces trois
mesures rapprochent le curseur du bon côté (contrat + structure de détention +
passé du déployeur) sans prétendre l'éliminer. Le mode degen reste plus
risqué que quality — c'est le compromis du choix « détection précoce ».

**Tests.** 148 tests smoke (0 échec), +6 : `normalize_address_reputation`
(extraction compte + labels, adresse inconnue), `creator_reputation_veto`
(usine à rug, wallet lié honeypot, wallet propre, absence de donnée). Le
durcissement `MAX_TOP10_HOLDER_PCT` a invalidé un test « maillon faible »
câblé en dur sur 24 % : réécrit relativement à `config.MAX_TOP10_HOLDER_PCT`.

---

## Mise à jour 11 — Recap narratif pour les réseaux sociaux (fr / en / zh)

**Demande.** « Il faut que j'aie un résumé des différents tokens détectés quand je
clique sur le signal du token, c'est pour mon contenu sur mes réseaux » — un
paragraphe rédigé façon post, copiable dans la langue d'affichage du dashboard
(français, anglais, chinois).

### Suivi du pic (nouveau)

Les horizons de performance étaient figés à 1 h / 6 h / 24 h : le bot ne
connaissait jamais le **sommet réel** d'un token. Ajout d'un relevé continu.

- `storage/db.py` : colonnes `signals.peak_market_cap`, `peak_price`,
  `peak_return_pct`, `peak_at`, `peak_checks` (migration non destructive).
- `core/performance_tracker.py` : `run_peak_cycle()` relève la capitalisation
  courante de chaque signal actif (< 7 j, avec plan d'entrée) et ne garde que le
  meilleur rendement atteint. Branché dans `scanner._loop`, juste après
  `run_check_cycle()`. `_fetch_market_states` renvoie désormais aussi `market_cap`.
- `db.update_peak()` : n'écrase le pic que sur un nouveau maximum de rendement,
  incrémente toujours `peak_checks`.

### Corpus & cohorte

- `db.bump_cycle_counter()` / `db.get_cycle_stats()` : compteurs cumulés dans
  `app_state` (`recap_cycle_count`, `recap_signals_emitted`, `recap_watch_emitted`).
  Incrémentés dans `scanner._scan_once` et aux points d'émission SIGNAL / VEILLE.
  Donne la phrase « environ 1 signal validé tous les N cycles ».
- `db.get_cohort_stats(tier, risk_level)` : pic moyen et meilleur pic des signaux
  comparables (même niveau, même risque), calculé sur les entrées ayant au moins
  un relevé.
- `db.get_signal(id)` : lecture d'une ligne unique (n'existait pas).

### Générateur de texte

- `core/signal_recap.py` : `build_recap(signal_id, lang, tz=None)` — module
  autonome, templates trilingues internes (pas passés par le catalogue
  `core/i18n.py` ni son test de réversibilité). Sections : en-tête (ticker,
  chaîne, horodatage + fuseau, cap d'entrée), conviction + confiance X/10,
  décompte des critères déclenchés / vérifications, parcours SCOUT → MAIN, pic
  (cap + date + % + multiple + durée, ou constat d'absence), activité smart money
  (depuis les features stockées, aucun appel réseau), rareté dans le corpus,
  moyenne de la cohorte, clause de prudence honnête (cap d'entrée ≠ prix
  d'exécution, montants par wallet non suivis, dépendance à l'indexation
  DexScreener, VEILLE = pas un signal d'achat).
- Fuseau : `config.RECAP_TIMEZONE` (nom IANA, ex. `Australia/Sydney`) ou heure
  locale de la machine si vide. Abréviation (AEST, CET…) calculée automatiquement.

### Dashboard

- `gui/bridge.py` : `@pyqtSlot(int, result=str) getSignalRecap(signal_id)` —
  rend le recap dans `self._ui_language()` (fr/en/zh), indépendant de
  `config.ALERT_LANGUAGES` (Telegram).
- `gui/web/index.html` : section « RÉCAP (pour les réseaux) » dans la fiche
  détail, `<textarea readonly>` + bouton COPIER (`navigator.clipboard.writeText`).
  Titre et libellés via le dictionnaire i18n JS ; le corps suit la langue
  sélectionnée. Dégradation propre si le pont est absent.

### Tests

`tests/smoke_test.py` section 14 : insertion d'un signal, `get_signal`,
logique de `update_peak` (nouveau max / valeur plus basse / écrasement),
compteurs de cycles, `get_cohort_stats` (peuplée + vide → None sans planter),
`build_recap` non vide et distinct en fr/en/zh, ticker présent, ligne de
conviction, pic reporté, id inexistant → chaîne vide, cas VEILLE.
**166 tests, 0 échec** (148 → 166).

---

## Mise à jour 12 — Limiteur de débit adaptatif sur le RPC Solana public

**Problème.** Sans le RPC public `api.mainnet-beta.solana.com` : aucun
espacement des appels côté client. Un cycle de scan à 25 tokens déclenche
~100 POST en rafale (`getAccountInfo`, `getTokenLargestAccounts`,
`getMultipleAccounts`, `getTokenAccountsByOwner` par token). L'endpoint
répond `429 "Too many requests for a specific RPC call"` sur la quasi-totalité.
Conséquence : les vérifications on-chain (concentration des holders, autorité
de mint, part encore détenue par le créateur) échouent en masse, et des tokens
propres restent bloqués en VEILLE « non vérifiable ». Le disjoncteur
`data_sources/http_utils.py` ne fait qu'osciller : coupe 120 s, se réarme,
reçoit à nouveau des 429.

**Correctif.** `data_sources/solana_rpc.py` :

- `_throttle()` appelé avant chaque `_call` : attend le temps nécessaire pour
  respecter l'intervalle courant entre deux appels (verrou `threading.Lock`,
  horloge `monotonic`).
- `_note_rpc_result(ok)` appelé après chaque `_call` : ajuste l'intervalle
  empiriquement — décroissance `×0.92` après un succès, augmentation
  `×1.6 + 0.1` après un échec. Plancher 0,30 s, plafond 3,0 s. Le RPC public
  n'annonce pas sa limite ; on la trouve en tâtonnant.
- Détection du rate-limit renvoyé en `200` + erreur JSON (`code 429`,
  message « many requests » / « rate limit ») en plus du `429` HTTP déjà géré.
- **Désactivé si `RPC_SOLANA` est renseigné dans `.env`** : un RPC dédié
  (Helius, QuickNode, Alchemy…) encaisse la rafale, l'espacement tombe à zéro.

**Ce que ça ne change pas.** Aucun seuil, aucun véto anti-rug, aucun poids de
score touché. C'est une correction de débit : elle permet aux vérifications
on-chain d'aboutir, donc à moins de tokens de rester coincés en VEILLE faute
de données, et au palier SIGNAL Solana d'être atteignable plus souvent.

**Ce que ça ne débloque pas.** Les signaux BSC / Robinhood : le log montre que
ces candidats tombent en « sous le seuil » (score trop bas) ou sont rejetés par
le moteur anti-rug (proxy, holders = 0, LP non verrouillée), pas par un « plan
pro » manquant. Les faire passer sans affaiblir l'anti-rug n'est pas possible
avec des API gratuites.

**Tests.** `tests/smoke_test.py` : 166 tests, 0 échec (inchangé).

---

## Mise à jour 13 — Débloquer BNB Chain / Base / Robinhood avec Nansen Pro

**Contexte.** Le compte est en Nansen Pro mais le bot tourne en profil `degen`,
qui coupe explicitement la découverte Nansen (`USE_NANSEN_DISCOVERY = False`).
Nansen ne servait donc qu'à noter le smart-money par token sur BNB Chain — pas
à trouver les bons candidats EVM. Et Base n'était pas dans `ACTIVE_CHAINS` :
jamais scannée, alors que tout le plumbing (chain-id, GoPlus, maps Nansen)
était déjà en place.

**Aucun véto anti-rug, seuil de score ou poids n'est modifié.** Ces changements
ne touchent que le *vivier de candidats* et la *liste des chaînes scannées* ;
chaque token passe ensuite le préfiltre marché, l'enrichissement GoPlus, le
moteur de veto et le scoring à l'identique.

### 1. Base activée de bout en bout

- `config.py` : `ACTIVE_CHAINS` et `AVAILABLE_CHAINS` incluent `base` ;
  `CHAIN_MARKET_OVERRIDES["base"]` (planchers proches de BNB : liq 12 k$,
  vol 1h 4 k$, 80 porteurs — assouplissement uniquement) ;
  `CHAIN_SCAN_MULTIPLIER["base"] = 1` (chaque cycle).
- `data_sources/geckoterminal.py` : `NETWORK_MAP["base"] = "base"`.
- `data_sources/dexscreener.py` : `CHAIN_ID_MAP["base"] = "base"`.
- `chains/base.py` : nouveau module (identique à `chains/bsc.py`, fixe
  `CHAIN = "base"`).
- `core/scanner.py` : import `base`, `CHAIN_MODULES["base"] = base`.

Base est la mieux couverte des trois : contrats presque toujours vérifiés sur
Basescan (pas de rejet de masse « code source non vérifié » comme sur
Robinhood), GoPlus et smart-money Nansen complets, indexation GeckoTerminal +
DexScreener.

### 2. Découverte Nansen branchée sur les chaînes EVM quel que soit le profil

`chains/evm_common.py` : le Token Screener Nansen (ordonné par flux net
décroissant, filtré sur ≥ 15 traders) est désormais utilisé pour la découverte
EVM dès qu'une clé Nansen est configurée et que l'interrupteur runtime est
actif — plus seulement en profil `quality`. Sur BNB / Base / Robinhood,
GeckoTerminal « nouvelles pools » ne remonte quasiment que des rugs ; le
screener remonte des tokens avec du flux réel.

- Coût : ~1 appel groupé (~5 crédits) par chaîne et par cycle, mis en cache
  40 s. Solana garde le firehose et n'appelle jamais le screener.
- Coupe-circuit : bouton NANSEN de l'UI (`nansen.set_enabled(False)`) ou
  `NANSEN_SCREENER_CACHE_TTL_SECONDS` pour espacer les appels.

### Limite qui subsiste sur Robinhood

Nansen `/tgm/holders` **ne couvre pas** Robinhood Chain : le critère
smart-money y reste « inconnu » (exclu du score, pas pénalisé). Et beaucoup de
contrats Robinhood ne sont pas vérifiés sur l'explorateur → véto anti-rug
`not_open_source`. Robinhood produira donc moins de signaux que BNB / Base,
pour des raisons qui ne se corrigent pas sans affaiblir l'audit.

**Tests.** `tests/smoke_test.py` : 169 tests, 0 échec (166 → 169). Nouveaux
tests : screener Nansen utilisé pour la découverte EVM même avec
`USE_NANSEN_DISCOVERY = False` (clé présente), jamais pour Solana ; Base câblée
de bout en bout.

---

## Mise à jour 14 — Fenêtre d'âge par chaîne : la découverte Nansen EVM renvoyait 0

**Symptôme.** Après la Mise à jour 13, `[base]` apparaît bien dans les cycles
mais toujours 0 signal, 0 veille sur BNB / Base / Robinhood, et les candidats
EVM ressemblent au firehose GeckoTerminal (pumps verticaux, contrats proxy,
holders = 0).

**Cause, mesurée.** Le Token Screener Nansen répond `200 { data: [] }` pour les
trois chaînes. La requête impose `token_age_days.max = 0.25` (**6 h**, hérité de
`MAX_PAIR_AGE_HOURS` du profil degen). Nansen n'indexe quasi rien d'aussi jeune
sur BNB / Base / Robinhood — le smart-money n'entre sur un token qu'une fois
installé (~1 jour). Bisection des filtres : avec un plafond à 6 h → 0 résultat ;
à 96 h → **20 BNB, 3 Base, 70 Robinhood**. La découverte EVM retombait donc
entièrement sur le firehose gratuit.

**Correctif — aucun véto anti-rug touché.** L'âge d'une paire n'est pas un
filtre de sécurité : une paire plus ancienne a *moins* de risque de rug éclair
(le profil « quality » impose d'ailleurs 12 h d'âge *minimum*).

- `config.py` : `CHAIN_MAX_PAIR_AGE_HOURS = {"bsc": 96, "base": 96,
  "robinhood": 96}` + `chain_max_pair_age_hours(chain)`. Solana et les chaînes
  non listées gardent le plafond du profil. La surcharge ne peut qu'**élargir**
  (`max(override, MAX_PAIR_AGE_HOURS)`) : en quality (fenêtre 45 j) elle est
  sans effet.
- `core/security_checks.py::_market_vetoes` : le veto « paire trop ancienne »
  utilise `cfg.chain_max_pair_age_hours(chain)` au lieu de la constante globale.
- `data_sources/nansen.py::screen_tokens` : la fenêtre `token_age_days.max` du
  screener suit la même fonction — les candidats ne sont donc pas rejetés
  ensuite pour l'âge.
- `NANSEN_SCREENER_MIN_TRADERS = 8` (était 15 en dur) : Base est une chaîne
  fine, à 15 le screener y renvoyait 0. Le tri qualité reste au veto + scoring.

**Vérifié en réel** (clé du .env) : `screen_tokens` renvoie 16 BNB / 3 Base /
70 Robinhood, tous des tokens installés ($15 k–$165 k de liquidité, volume
réel) — exploitables par le moteur de veto et le scoring.

**Tests.** `tests/smoke_test.py` : 173 tests, 0 échec (169 → 173). Nouveaux :
fenêtre EVM = 96 h / Solana = 6 h, paire EVM de 50 h non écartée pour l'âge,
même paire Solana écartée, la surcharge n'abaisse jamais sous le profil.

---

## Mise à jour 15 — Liens d'affiliation Nansen + Binance Web3 dans les alertes

`core/telegram_alerts.py::_build_links` ajoutait déjà le lien Axiom en bas de
chaque alerte. Il pose maintenant, une ligne chacun :

- **Nansen Token God Mode** —
  `https://app.nansen.ai/token-god-mode?tokenAddress=<contrat>&chain=<code>&tab=transactions&ref=GloriousKing225`
  `<code>` = code de l'API Nansen (BNB Chain = `bnb`), aligné sur
  `data_sources/nansen.NANSEN_SCREENER_CHAIN_MAP`. Couvre solana / bsc / base /
  ethereum / robinhood.
- **Binance Web3** —
  `https://web3.binance.com/en/token/<chaîne>/<contrat>?ref=L3C8VW3Q`
  Couvre solana / bsc / base / ethereum. Robinhood Chain n'y est pas listée →
  le lien n'est pas ajouté plutôt que d'en produire un mort.

Chaque lien n'est posé que pour les chaînes où la plateforme sait ouvrir la
bonne page ; un contrat vide ne produit aucun lien. Les alertes SIGNAL comme
VEILLE en héritent (le placeholder `{links}` est dans les deux gabarits).

Le dashboard reçoit les deux mêmes liens dans la fiche détail
(`gui/web/index.html::explorerLinks` → `affiliateLinks`), en boutons
secondaires, à côté des liens explorateur existants.

**Tests.** `tests/smoke_test.py` : 177 tests, 0 échec (173 → 177). Nouveaux :
lien Nansen `chain=bnb` + Binance Web3 avec les refs sur BSC ; Solana cumule
Axiom + Nansen + Binance ; Robinhood a Nansen mais pas Binance ; contrat vide
=> aucun lien.

---

## Mise à jour 16 — Détection garantie dès la première minute

**Demande.** Le bot doit détecter un token dès sa première minute d'existence.

**Ce qui bloquait.** Le flux PumpPortal pousse déjà chaque création pump.fun en
quelques millisecondes, mais :
1. `_market_vetoes` écartait toute bonding curve avec moins de $1 500 de
   réserves — or à la seconde 30 la courbe n'a *aucune* réserve → « écarté
   avant vérification — Liquidité insuffisante ($0 …) ».
2. Le plafond `MAX_ENRICHMENTS_PER_CYCLE = 25` triait les candidats **par
   liquidité décroissante** : une création fraîche (liquidité ≈ 0) partait
   systématiquement en bas de pile, reportée de cycle en cycle.

**Correctif — le moteur anti-rug est intact.** Nouvelle fenêtre
« première minute » (`FIRST_MINUTE_WINDOW_MINUTES = 3` en degen, `0` en
quality) :

- `config.py` : `FIRST_MINUTE_MIN_LIQUIDITY_USD = 0`,
  `FIRST_MINUTE_ENRICHMENT_RESERVE = 12`.
- `core/security_checks.py` : `is_first_minute_candidate()` (âge < 3 min, ou
  bonding curve sans horodatage). Dans la fenêtre, le plancher de liquidité
  (bonding curve **et** pool réel) tombe à `FIRST_MINUTE_MIN_LIQUIDITY_USD`.
- `core/scoring.py` : une liquidité nulle dans la fenêtre est **exclue** du
  score (comme volume / momentum en fenêtre early), pas notée 0,25 — le token
  n'est pas pénalisé pour son âge et atteint le seuil de VEILLE sur
  sécurité + distribution.
- `core/scanner.py` : le plafond de cycle réserve jusqu'à
  `FIRST_MINUTE_ENRICHMENT_RESERVE` créneaux aux tokens de la fenêtre, **les
  plus jeunes servis d'abord** ; le reste des créneaux va aux plus liquides.

**Ce qui n'est PAS assoupli, vérifié par les tests :**
- honeypot avéré, top 10 à 95 %, dev/insiders majoritaires, pompe verticale,
  autorité de mint, métadonnées/frais modifiables → **REJET** même sur un
  token de 30 s.
- `MIN_PAIR_AGE_MINUTES` inchangé : un token de moins de 10 min ne peut
  **jamais** atteindre le palier SIGNAL (pas de plan d'entrée, pas d'achat
  auto) — il reste en VEILLE, NON VÉRIFIÉ.

**Rappel curseur.** Le bot *capture* maintenant les tokens dès la 1ʳᵉ minute
(ils ne sont plus jetés avant vérification). Pour les *voir*, régler le curseur
de score autour de 55–60 : un token de 30 s score typiquement ~58 (sécurité +
distribution seules), sous un curseur à 70 il reste hors affichage — le
curseur reste souverain, par conception.

**Tests.** `tests/smoke_test.py` : 187 tests, 0 échec (177 → 187).

---

## Mise à jour 17 — BNB Chain / Base / Robinhood traitées en priorité

**Demande.** Détecter les tokens BNB Chain, Robinhood Chain et Base en
priorité.

**Ce qui bloquait.**
1. `core/scanner.py::_scan_once` itérait `active_chains` — un `set` : l'ordre de
   traitement des chaînes était **aléatoire d'un lancement à l'autre**. Quand
   Solana passait en premier, sa découverte (300+ candidats) et ses 25
   vérifications sur RPC public throttlé consommaient le temps du cycle avant
   même que l'EVM ne soit touché.
2. `CHAIN_SCAN_MULTIPLIER["robinhood"] = 2` : Robinhood n'était scannée qu'un
   cycle sur deux.
3. Plafond `MAX_ENRICHMENTS_PER_CYCLE = 25` appliqué tel quel à toutes les
   chaînes, alors que l'enrichissement EVM est un appel GoPlus **groupé**
   (lots de 25) sans RPC par token — il peut en encaisser bien plus.

**Correctif.**

- `config.py` :
  - `CHAIN_SCAN_PRIORITY = ["bsc", "base", "robinhood", "solana", "ethereum"]`.
  - `CHAIN_SCAN_MULTIPLIER["robinhood"]` : 2 → **1** (chaque cycle).
  - `CHAIN_MAX_ENRICHMENTS_PER_CYCLE = {"bsc": 40, "base": 40, "robinhood": 60}`
    — Solana garde le plafond global de 25 (RPC public limité).
- `core/scanner.py::_scan_once` : les chaînes actives sont désormais triées
  selon `CHAIN_SCAN_PRIORITY` avant traitement → **BNB, Base, Robinhood
  découvertes et vérifiées avant Solana à chaque cycle**, de façon
  déterministe.
- `core/scanner.py::_process_chain` : le plafond de vérifications par cycle est
  lu dans `CHAIN_MAX_ENRICHMENTS_PER_CYCLE` (repli sur le global).

Aucun véto, seuil de score ou poids touché : ceci ne change que l'ordre et le
débit de traitement des chaînes.

**Tests.** `tests/smoke_test.py` : 191 tests, 0 échec (187 → 191). Nouveaux :
ordre de cycle déterministe EVM-avant-Solana, Robinhood chaque cycle, plafond
EVM > plafond Solana.

---

## Mise à jour 18 — Les tokens d'une minute vérifiés en tête de file, à chaque cycle

**Demande.** Les tokens d'environ une minute de vie doivent être détectés en
priorité.

**Ce qui manquait.** La Mise à jour 16 empêchait un token frais d'être *jeté*
avant vérification, et lui réservait des créneaux — mais **uniquement quand le
plafond de vérifications par cycle débordait**. En dessous du plafond (cas
courant sur BNB / Base / Robinhood, 7–70 candidats), la liste `fresh` restait
dans l'ordre de découverte : un token de 40 secondes pouvait être vérifié et
alerté *après* un token de trois heures.

**Correctif.** `core/scanner.py` :

- `order_for_verification(candidates, cap)` — helper qui ordonne les candidats
  d'un cycle **à chaque cycle** :
  1. fenêtre « première minute » d'abord, du plus jeune au plus ancien ;
  2. le reste par liquidité décroissante ;
  3. sous le plafond, `FIRST_MINUTE_ENRICHMENT_RESERVE` créneaux garantis à la
     fenêtre avant que les plus liquides ne prennent le reste.
- `_process_chain` appelle ce helper au lieu de ne trier qu'en cas de
  débordement.

Combiné aux Mises à jour 16 (fenêtre première minute) et 17 (chaînes EVM
traitées avant Solana), un token repéré à la seconde 40 sur n'importe quelle
chaîne est désormais dans le **premier lot vérifié de sa chaîne**, et les
chaînes EVM passent en premier.

*Portée.* La priorité « plus jeune d'abord » s'applique DANS chaque chaîne. Un
token Solana de 30 s reste traité après la découverte + vérification de
BNB / Base / Robinhood (~10 s), conformément à la priorité EVM demandée en
Mise à jour 17 ; il est de toute façon capté au cycle suivant (PumpPortal
garde le token 30 min).

**Tests.** `tests/smoke_test.py` : 194 tests, 0 échec (191 → 194).

## Mise à jour 19 — Profil DEGEN par défaut (cause racine des 0 signaux)

**Constat.** Les journaux fournis montrent `Solana [quality]` à chaque cycle :
le scanner tournait en profil **QUALITY**, alors que toutes les demandes
(détection dès la 1re minute, résultats sans plan pro, priorité BSC/Base/
Robinhood) décrivent le profil **DEGEN**. QUALITY est structurellement
incompatible :
- `MIN_PAIR_AGE_MINUTES = 720` (12 h d'âge minimum) → un token d'1 minute ne
  peut jamais être détecté ;
- `REQUIRE_SMART_MONEY = True` + découverte Nansen obligatoire → sur Robinhood
  Chain (que le smart-money Nansen n'indexe pas) le niveau SIGNAL est
  inatteignable ; d'où les « 13-15 sous le seuil » par cycle et la VEILLE
  « aucun wallet smart money positionné (profil quality) ».

**Correctif.** Le tableau de bord affichait `PROFIL: QUALITY` en dur
(`gui/web/index.html`, bouton + `profileQuality = true`) alors que la valeur
d'environnement par défaut est `degen`. Le libellé et l'état initial passent à
**DEGEN**, cohérents avec `config.SCAN_PROFILE` (resynchronisé au démarrage via
`bridge.getScanProfile`).

**Ce que DEGEN débloque, sans toucher à l'anti-rug :**
- fenêtre « 1re minute » (3 min) + `MIN_PAIR_AGE_MINUTES = 10` → détection
  précoce réelle ;
- `chain_threshold` n'assouplit que dans un sens : en degen le plancher BSC
  passe de 18 k$→8 k$ (liquidité) et 5 k$→2 k$ (volume), Base 12 k$→8 k$ /
  4 k$→2 k$ — tous les rejets `$16 997 < $18 000` / `$4 924 < $5 000` des
  journaux passent désormais ;
- plus de dépendance smart-money / Nansen pour le SIGNAL.

**Ce qui NE change PAS (vérifié) :** `SECURITY_VETOES`, `MAX_TOP10_HOLDER_PCT`
(20 dans les deux profils), `MIN_LP_LOCKED_PCT` (80), `MAX_CREATOR_PCT`,
`MAX_INSIDER_PCT`, honeypot, mint/freeze authority, `not_open_source`,
`MIN_SAFETY_FEATURE` (0.85), `REQUIRED_SECURITY_FIELDS`, `SECURITY_STRICT_MODE`
— toutes des constantes de module hors du dict `PROFILES`. Le profil ne pilote
que les seuils de marché et la découverte.

**Limite résiduelle.** `.env` : `RPC_SOLANA` est toujours vide → le flot de 429
sur `api.mainnet-beta.solana.com` continue (throttle adaptatif M12 en place,
mais un RPC privé reste nécessaire). BSC/Base/Robinhood ne sont pas concernés
(RPC BSC renseigné ; Base/Robinhood via GoPlus groupé, sans RPC par token).

194 tests, 0 échec.

## Mise à jour 20 — VEILLE garantie dès la 1re minute, avant migration

**Constat.** La fenêtre « première minute » (config.py : `FIRST_MINUTE_WINDOW_
MINUTES`, plancher de liquidité 0, créneaux de vérification réservés) promet une
« détection GARANTIE dès le repérage ». En pratique, un token PumpPortal encore
en bonding curve, âgé de quelques secondes, franchissait bien tout le pipeline
(préfiltre → enrichissement → tous les vetos de sécurité) puis était
**silencieusement jeté** dans `_handle_watch` par la condition
`score < watch_threshold()` : sans historique de marché ni distribution encore
lisible on-chain, son score est mécaniquement ~30/100, très en dessous du
curseur (60). Aucune alerte Telegram n'était donc jamais émise avant la
migration.

**Correctif.** `core/scanner.py::_handle_watch` — la condition
`score < watch_threshold()` est désormais court-circuitée pour
`is_first_minute_candidate(candidate)` (bonding curve pump.fun de moins de
`FIRST_MINUTE_WINDOW_MINUTES`). Ces tokens partent en VEILLE quel que soit le
curseur.

**Ce que ça NE fait PAS (aucun assouplissement anti-rug) :**
- l'alerte reste `tier = VEILLE`, `verified: False`, `risk_level: "Non vérifié"`,
  `entry_price/stop/tp = None` — information, pas recommandation, pas d'achat auto ;
- le token a DÉJÀ franchi tous les vetos (`compute_score` → `_process_candidate` :
  honeypot, mint/freeze authority, bundle d'insiders, concentration du top 10
  quand elle est connue, contrat non vérifié, proxy, wash trading, pompe
  verticale) — un token dont la structure prouve le rug est rejeté, pas affiché ;
- le passage en **SIGNAL** reste fermé tant que le token n'a pas migré et
  franchi `MIN_PAIR_AGE_MINUTES` + toutes les vérifications obligatoires ;
- la dérogation ne vaut QUE dans la fenêtre 1re minute : un token migré ou plus
  âgé repasse sous le curseur (test de contrôle).

**Prérequis.** Profil **DEGEN** (Mise à jour 19) : en `quality`
`FIRST_MINUTE_WINDOW_MINUTES = 0` et le flux PumpPortal est coupé — le correctif
est alors inerte, par conception.

198 tests, 0 échec (194 → 198).

## Mise à jour 21 — Dashboard vide malgré les alertes Telegram + STOP/RUN qui ne répondent pas

Trois symptômes signalés, deux causes racines.

### 1. « Un seul signal sur le dashboard, alors que Telegram reçoit tout »

Le backend émettait bien chaque VEILLE (`stats['watch']`, `send_alert` Telegram),
mais l'interface les jetait : `passesScore()` dans `gui/web/index.html` masque
toute carte dont le score est sous le curseur. Or une VEILLE « première minute »
(Mise à jour 20) a un score mécaniquement bas (~30-50) et le curseur était à 70 :
toutes ces cartes étaient invisibles, seules les rares ≥ 70 s'affichaient.

**Correctif :**
- `core/scanner.py::_handle_watch` estampille `watch_entry["first_minute"] =
  True` sur les VEILLE publiées via l'exception « 1re minute » ;
- `gui/web/index.html::passesScore()` affiche une carte si
  `sig.first_minute === true`, sans quoi il applique le curseur comme avant.

Le curseur reste un plancher strict pour tout le reste — seules les détections
« 1re minute » (déjà passées par tous les vetos de sécurité, marquées NON
VÉRIFIÉ, sans plan d'entrée) y échappent, exactement comme côté Telegram.
Limite : après un redémarrage de l'app, une VEILLE 1re minute sous le curseur ne
réapparaît pas (le drapeau n'est pas persisté en base) ; elle reste visible
toute sa durée de vie pendant la session en cours.

### 2. « STOP ne stoppe pas » et 3. « RUN ne redémarre pas »

Même cause. `solana.enrich_batch` enrichit les candidats un par un ; avec le RPC
public Solana en 429, le throttle adaptatif monte à 3 s/appel — 3 appels RPC ×
25 candidats = plusieurs minutes de boucle **sans aucun test d'arrêt**. Un clic
STOP ne prenait effet qu'à la fin de cette boucle. Et le RUN suivant refuse de
démarrer tant que le thread précédent tourne (`run()` : `join(timeout=5)` puis
« réessaie dans un instant »).

**Correctif :** `should_stop` (callable) passé de `_process_chain` à
`enrich_batch` sur toutes les chaînes :
- `chains/solana.py` : testé avant CHAQUE candidat, sort de la boucle et
  journalise `N/M vérifiés` ;
- `chains/evm_common.py` (+ wrappers bsc/base/robinhood/ethereum) : testé avant
  l'appel GoPlus groupé ;
- `core/scanner.py::_process_chain` : re-teste `_is_stopping()` juste après
  l'enrichissement, avant d'émettre quoi que ce soit.

STOP interrompt maintenant le cycle en cours en un ou deux candidats ; RUN
repart aussitôt.

### Rappels (hors code)

- **`RPC_SOLANA` toujours vide dans `.env`** — c'est l'amplificateur des deux
  problèmes (flot de 429, cycles Solana de plusieurs minutes). Un RPC privé
  (Helius/QuickNode) supprime le throttle 3 s.
- **Nansen : « Insufficient credits » (403)** en boucle. En degen la découverte
  Nansen n'est pas requise ; couper le bouton **SMART MONEY** du dashboard
  arrête ces appels (le disjoncteur ne fait que masquer le bruit).

202 tests, 0 échec (198 → 202).

## Mise à jour 22 — Le curseur SCORE MIN fait autorité, sans exception + plancher abaissé à 0.30

Les Mises à jour 20/21 laissaient les VEILLE « première minute » passer MALGRÉ
le curseur (score bas par construction). L'utilisateur ne veut pas de ce
contournement : le curseur doit décider seul, et pouvoir descendre assez bas
pour voir ces détections fraîches par choix.

**Reverts :**
- `core/scanner.py::_handle_watch` — l'exception `is_first_minute` est retirée.
  Retour à `if score < self.watch_threshold(): stats["below"] += 1; return`
  pour TOUT candidat, VEILLE 1re minute comprise. La clé `first_minute` n'est
  plus posée sur l'entrée émise.
- `gui/web/index.html::passesScore()` — retour à `score >= minScore` strict,
  plus de test `sig.first_minute`.

**Plancher du curseur abaissé de 0.60 à 0.30 :**
- `config.py` : `MIN_SCORE_RANGE = (30, 100)` (était `(60, 100)`),
  `MIN_SCORE_TO_WATCH = 30` (était `55`, aligné sur le bas du curseur) ;
- `gui/web/index.html` : `<input type="range" ... min="30">` (était `min="60"`),
  et le calcul de remplissage de la piste passe de `(x-60)/40` à `(x-30)/70`.

Conséquence : pour voir les tokens fraîchement détectés (score ~0.30–0.50),
on descend le curseur — jusqu'à 0.30. Rien sous la valeur affichée ne
s'affiche ni ne part en alerte. `set_min_score` continue de borner la valeur
à `MIN_SCORE_RANGE`.

202 tests, 0 échec.

## Mise à jour 23 — Sélection des chaînes pour les alertes (1 ou plusieurs)

Le dashboard n'offrait qu'un bouton MULTICHAIN (tout / Solana seul) et un menu
CHAÎNE qui ne filtrait QUE l'affichage — le scanner continuait de scanner et
d'alerter toutes les chaînes. Le back-end avait pourtant déjà
`scanner.toggle_chain(chain, enabled)` (mutation de `state.active_chains`, qui
pilote `_scan_once`), simplement non exposé.

**Ajouts :**
- `gui/bridge.py` : slots `toggleChain(chain: str, enabled: bool)` et
  `getActiveChains() -> str` (liste JSON des chaînes réellement scannées).
- `core/scanner.py::toggle_chain` : valide la chaîne contre `AVAILABLE_CHAINS`,
  garde-fou « au moins une chaîne active » (impossible de tout décocher), et
  journalise l'état (`Chaînes actives : …`).
- `gui/web/index.html` :
  - le menu déroulant CHAÎNE est remplacé par 4 puces cliquables
    (SOLANA / BSC / BASE / ROBINHOOD), état `active` = sélectionnée ;
  - le bouton **MULTICHAIN est retiré** (les puces le remplacent) ;
  - `alertChains` (Set) pilote à la fois le filtre d'affichage des cartes
    (`applyFilter`) ET le back-end via `bridge.toggleChain` ;
  - au démarrage, `bridge.getActiveChains` resynchronise les puces sur l'état
    réel du scanner ;
  - garde-fou UI symétrique : la dernière puce active ne peut pas être décochée.

Effet : une chaîne décochée n'est plus scannée du tout — ni carte dashboard,
ni alerte Telegram. Ethereum reste hors sélecteur (non dans `ACTIVE_CHAINS`).

207 tests, 0 échec (202 → 207).

## Mise à jour 24 — Détection « le token a déjà plongé » / « est sur le point de plonger »

Symétrique du veto de pompe verticale (`MAX_PRICE_CHANGE_1H_PCT`), dans l'autre
sens. Nouveau helper `core/security_checks.py::_dump_vetoes(candidate)`, appelé
depuis `_market_vetoes` (donc dès le préfiltre, sans appel réseau) — sauf dans
la fenêtre « première minute » où aucune variation multi-fenêtres n'existe.

**Trois signatures, chacune journalisée avec un motif explicite :**

1. **A DÉJÀ PLONGÉ** — `price_change_24h ≤ DUMP_PRICE_CHANGE_24H_PCT` (−60 %),
   ou `price_change_6h ≤ DUMP_PRICE_CHANGE_6H_PCT` (−50 %) si la 24h est encore
   verte. → « Le token a déjà plongé (24h −90%)… ». Cas de la capture Binance
   Wallet (−99,72 % / liquidité $0.05).
2. **RETOURNEMENT AMORCÉ** — pompe `price_change_1h ≥ DUMP_ROLLOVER_PUMP_1H_PCT`
   (+40 %) **suivie** d'une chute `price_change_5m ≤ DUMP_ROLLOVER_DROP_5M_PCT`
   (−12 %). → « Retournement amorcé : +X% en 1h puis −Y% en 5 min ».
3. **DISTRIBUTION ACTIVE** — `sells_h1 ≥ DUMP_SELL_DOMINANCE_RATIO × buys_h1`
   (×2) **et** `price_change_1h < 0`. → « Distribution active : N ventes pour
   M achats sur 1h et prix en baisse ».

**Fichiers :**
- `config.py` : `DUMP_PRICE_CHANGE_24H_PCT`, `DUMP_PRICE_CHANGE_6H_PCT`,
  `DUMP_ROLLOVER_PUMP_1H_PCT`, `DUMP_ROLLOVER_DROP_5M_PCT`,
  `DUMP_SELL_DOMINANCE_RATIO`.
- `core/i18n.py` : `veto.already_dumped`, `veto.dump_rollover`,
  `veto.dump_distribution` (fr/en/zh).
- `data_sources/dexscreener.py` + `data_sources/geckoterminal.py` : nouveau
  champ `price_change_6h` (fenêtre médiane, absente jusqu'ici).
- `core/security_checks.py` : `_dump_vetoes` + branchement dans `_market_vetoes`.

Ces motifs ne touchent AUCUN veto anti-rug existant : c'est un durcissement
supplémentaire (moins de faux « bons » signaux), pas un assouplissement.

213 tests, 0 échec (207 → 213).

## Mise à jour 25 — Le seuil de score au démarrage retombait tout seul (80 → 61 → 60)

**Signalé par l'utilisateur** via les journaux de démarrage : trois lignes
« Seuil de score minimum réglé à X/100 » s'enchaînaient à chaque lancement —
80, puis 61, puis 60 — sans qu'aucun clic n'ait eu lieu. Le seuil réellement
appliqué au premier cycle de scan n'était donc pas celui affiché par
défaut (80) mais 60, une valeur plus permissive jamais choisie par
l'utilisateur.

**Cause :** `gui/web/index.html` recharge le même fichier local à chaque
lancement de l'appli. Le moteur Chromium embarqué (QtWebEngine) restaure
parfois, juste après l'exécution du script d'initialisation, la position du
curseur SCORE MIN d'une session précédente — un comportement natif de
Chromium sur un rechargement, totalement indépendant d'un geste de
l'utilisateur — et déclenchait l'écouteur `change`, qui renvoyait cette
vieille valeur au backend (`bridge.setMinScore`).

**Correctif (`gui/web/index.html`), deux niveaux :**
- l'écouteur `change` du curseur ne synchronise plus le backend que si
  l'utilisateur a réellement touché le curseur (`pointerdown`/`keydown`
  observé au préalable) — un `change` déclenché par le navigateur lui-même,
  sans interaction, est ignoré et le curseur est ramené à la valeur voulue ;
- en complément, la synchronisation initiale du curseur avec le backend est
  différée de deux frames (`requestAnimationFrame` imbriqués) et réaffirme
  explicitement la valeur par défaut avant d'appeler `bridge.setMinScore`,
  pour avoir le dernier mot sur toute restauration survenue entre-temps ;
- `autocomplete="off"` ajouté sur le curseur en défense supplémentaire.

Le curseur reste un plancher strict choisi par l'utilisateur (Mise à jour 22)
— ce correctif garantit simplement qu'il ne peut plus être abaissé en
silence par le navigateur. Aucun veto de sécurité touché.

213 tests, 0 échec (changement purement JS/HTML, non couvert par la suite
Python — vérifié en relançant `tests/smoke_test.py` pour confirmer l'absence
de régression côté backend).

## Mise à jour 26 — Réduction du volume d'appels au RPC public Solana (429)

**Signalé par l'utilisateur** : rafales continues de
`Réponse HTTP 429 pour https://api.mainnet-beta.solana.com` dans les
journaux. Cette limite est imposée par l'endpoint public gratuit lui-même
(non annoncée, partagée par tous ses utilisateurs) — aucun changement de code
ne peut faire accepter plus d'appels à un serveur tiers qui refuse d'en
recevoir davantage. Un RPC personnel (Helius, QuickNode…) renseigné dans
`RPC_SOLANA` (.env) supprime complètement cette limite et reste la seule
correction définitive. Ce qui PEUT être corrigé côté code, et l'est ici :
réduire le nombre d'appels réellement envoyés, pour que la marge déjà
disponible sur l'endpoint public serve à plus de vérifications utiles plutôt
qu'à des 429 gaspillés.

**Correctif (`data_sources/solana_rpc.py`, `chains/solana.py`) :**
- nouvelle fonction `prefetch_mint_authorities()` : lit les autorités de mint
  de PLUSIEURS tokens en un seul appel `getMultipleAccounts` (jusqu'à 100
  adresses), au lieu d'un `getAccountInfo` séparé par token. `chains/solana.py
  ::enrich_batch` l'appelle une fois pour tout le lot avant la boucle
  d'enrichissement candidat par candidat — jusqu'à 25 appels remplacés par 1
  seul, sur un cycle à 25 candidats. Les mints déjà en cache et frais (TTL 60s
  existant) ne sont jamais reversés ; un échec du lot groupé laisse
  simplement `get_mint_authorities()` retenter l'appel unitaire comme avant,
  sans jamais confondre « pas encore pré-chargé » et « compte inexistant ».
  `getTokenLargestAccounts` et `getTokenAccountsByOwner` restent unitaires,
  faute d'équivalent groupé côté RPC Solana.
- le pré-chargement est soumis à la même règle STOP que la boucle qui suit
  (aucun appel réseau si `should_stop()` est déjà vrai avant de commencer).
- plafond du throttle adaptatif (`_RPC_MAX_INTERVAL`) relevé de 3 s à 6 s :
  quand l'endpoint est déjà saturé, on espace davantage plutôt que de
  continuer à cogner au même rythme pour se faire refuser pareil.

Aucun veto de sécurité touché, aucune vérification sautée — seules les
autorités de mint, dont la lecture est strictement identique, changent de
mécanique de transport (groupée au lieu d'unitaire).

219 tests, 0 échec (213 → 219) : regroupement effectif en un seul appel,
absence de mise en cache d'un compte manquant, lecture depuis le cache sans
appel supplémentaire, `enrich_batch` déclenchant bien le pré-chargement pour
tout le lot.

## Mise à jour 27 — Ajout d'Arc Network (Circle, chain ID 5042)

**Demandé par l'utilisateur.** Arc est le layer-1 EVM lancé par Circle en
mainnet public le 16 septembre 2026 (USDC comme actif de gas natif,
validateurs fondateurs institutionnels — BlackRock, Visa, Mastercard, DTCC —
accès développeur annoncé permissionless). La chaîne a été ajoutée le
lendemain de son lancement : aucun indexeur (GoPlus, GeckoTerminal,
DexScreener, Nansen) ne la couvrait encore au moment d'écrire ce correctif, et
aucune donnée de marché réelle n'existe pour la calibrer.

**Correctif, même doctrine que Robinhood Chain (Mise à jour antérieure) :
détecter la disponibilité réelle plutôt que la supposer.**
- `chains/arc.py` (nouveau) : sonde GeckoTerminal + DexScreener pour la
  découverte, et GoPlus (chain ID `5042`) pour la sécurité, exactement comme
  `chains/robinhood.py`. Tant que la découverte n'est couverte par personne,
  la chaîne reste inactive sans consommer de créneau de scan. Dès que la
  découverte répond mais pas encore GoPlus, elle scanne en niveau VEILLE
  uniquement (jamais de SIGNAL sans sécurité vérifiable). Re-testée toutes les
  30 min, aucune intervention manuelle nécessaire quand la couverture arrive.
- **Aucune entrée Nansen ajoutée** (`NANSEN_CHAIN_MAP` / `NANSEN_SCREENER_CHAIN_MAP`)
  : aucune confirmation que Nansen couvre déjà la chaîne à son lancement, et
  supposer une couverture non confirmée aurait pu déclarer à tort le
  smart-money « disponible mais absent » au lieu de « non couvert ». Vérifié
  explicitement en négatif dans les tests.
- **Aucune surcharge `CHAIN_MARKET_OVERRIDES` / `CHAIN_MAX_PAIR_AGE_HOURS`**
  ajoutée : contrairement à BSC/Base/Robinhood (surcharges justifiées par des
  mesures réelles de marché), aucune donnée n'existe encore pour Arc. La
  chaîne utilise les seuils globaux du profil actif tels quels plutôt qu'un
  assouplissement inventé.
- `config.py` : `arc` ajouté à `ACTIVE_CHAINS`/`AVAILABLE_CHAINS`,
  `GOPLUS_CHAIN_IDS["arc"] = "5042"`, `CHAIN_SCAN_PRIORITY`,
  `CHAIN_SCAN_MULTIPLIER`, `CHAIN_MAX_ENRICHMENTS_PER_CYCLE` (plafond aligné
  sur BSC/Base en attendant un volume mesuré).
- `core/scanner.py::CHAIN_MODULES`, `data_sources/dexscreener.py`
  (`CHAIN_ID_CANDIDATES`, `_PROBE_QUERIES` — sonde sur "USDC", l'actif de gas
  natif de la chaîne, faute de wrapped natif comme WETH) et
  `data_sources/geckoterminal.py` (`NETWORK_SEARCH_HINTS`) câblés pour
  l'auto-détection, même mécanique que Robinhood Chain.
- `gui/web/index.html` : puce ARC ajoutée au sélecteur de chaînes du
  dashboard.
- `core/i18n.py` : `unavailable.arc_goplus` (fr/en/zh).

Aucun veto de sécurité touché, aucun seuil assoupli — une chaîne de plus,
soumise aux mêmes vérifications que les autres, jamais promue en SIGNAL sans
données réelles.

223 tests, 0 échec (219 → 223) : câblage vérifié (chaîne active, module,
chain ID GoPlus, absence volontaire de couverture Nansen), les trois cas de
`_probe` (aucun indexeur, découverte seule, sécurité vérifiable), et
`enrich_with_security` qui ne renvoie jamais une sécurité « disponible » sans
couverture GoPlus confirmée. Deux tests préexistants (`toggle_chain`)
corrigés : leurs attentes étaient câblées en dur sur l'ancienne liste à 4
chaînes.

## Mise à jour 28 — Ton plus humain pour le récap réseaux sociaux, virgules au lieu des tirets cadratins

**Demandé par l'utilisateur.** Le texte produit par `core/signal_recap.py`
(bouton « recap » sur un signal, prêt à coller sur les réseaux) lisait comme
un rapport technique (« Conviction : forte (score 8/10). ») plutôt qu'un post
qu'un humain écrirait. Deux changements, en fr/en/zh :

- **Accroche d'ouverture** ajoutée en tête du texte (nouvelle clé `opener`) :
  « Jetons un coup d'œil sur notre trouvaille du jour ! » (et équivalents
  anglais/chinois), pour donner le ton avant les faits.
- **Toutes les phrases reformulées** en style plus naturel (« Il a d'abord été
  repéré par... » au lieu de « Repéré par... »), et **tous les tirets
  cadratins (—) remplacés par des virgules ou des connecteurs** (« donc »,
  « mais »), sur demande explicite.

Aucune information retirée ni inventée : mêmes données, même distinction
stricte VEILLE (jamais présentée comme un signal d'achat) / SIGNAL, mêmes
clauses de prudence honnêtes en fin de texte — seule la formulation change.

225 tests, 0 échec (223 → 225) : absence de tiret cadratin dans les 3 langues
vérifiée explicitement, accroche présente en tête, et les vérifications
préexistantes (ticker, distinction VEILLE/SIGNAL, clause "not a buy signal")
toujours vraies avec la nouvelle formulation.

## Mise à jour 29 — Disclaimer « pas un conseil en investissement » dans toutes les alertes

**Demandé par l'utilisateur.** En plus de la mention « UNVERIFIED — this is NOT
a buy signal » (propre aux alertes de VEILLE), chaque alerte Telegram se
termine désormais par un avertissement, **en anglais**, comme demandé :

> ⚠️ Disclaimer: This is not financial advice. Signals are provided for
> informational purposes only. Memecoin trading is highly risky, so do your own
> research, take full responsibility for your own decisions, and only invest
> what you can afford to lose.

- Ajouté par une seule fonction (`_with_disclaimer` dans
  `core/telegram_alerts.py`) appliquée aux DEUX gabarits, signal validé et
  veille précoce, donc aucun chemin d'envoi ne peut l'oublier.
- Toujours en anglais, y compris sous le gabarit français : un texte
  d'avertissement unique et identique partout.
- Placé tout en bas, après les liens (Axiom / Nansen / Binance Web3).

Aucun filtre anti-rug, seuil ni règle de scoring touché : seul le texte de
l'alerte change.

229 tests, 0 échec (225 → 229) : présence dans la veille et le signal, position
finale après les liens, contenu couvrant les cinq points demandés (pas un
conseil, informatif, responsabilité, risque, perte acceptable), et une seule
occurrence par alerte.

## Mise à jour 30 — Même disclaimer sur le récap réseaux sociaux

**Demandé par l'utilisateur** (suite de la mise à jour 29). Le texte produit par
`core/signal_recap.py`, destiné à être publié tel quel, se termine désormais par
le même avertissement anglais que les alertes Telegram. Le texte n'est écrit
qu'à un seul endroit (`DISCLAIMER` dans `core/telegram_alerts.py`, importé par
le récap), donc les deux ne peuvent pas diverger. Il est ajouté en dernier
bloc, après les « précisions honnêtes », dans les trois langues du récap
(fr/en/zh), toujours en anglais.

Aucune donnée, aucun filtre ni seuil touché.

230 tests, 0 échec (229 → 230) : le disclaimer termine le texte, une seule fois,
en fr/en/zh.

## Mise à jour 31 — Liens Axiom + Nansen dans les alertes Telegram pour Arc Network

**Signalé par l'utilisateur** : aucun lien (Axiom, Nansen, Binance Web3) n'apparaissait
dans les alertes Telegram pour un memecoin sur Arc Network. Cause : Arc avait été
ajoutée au scanner (Mise à jour 27) sans couverture Nansen confirmée à l'époque —
`core/telegram_alerts.py` n'avait donc aucune entrée « arc » dans ses cartes de
liens, et `data_sources/nansen.py` non plus.

**Vérifié en direct avant tout changement, comme pour chaque chaîne de ce bot** :

- **Nansen** : appel réel à `/token-screener` et `/tgm/holders` sur la chaîne
  `arc` → renvoie de vrais tokens (CIRBTC, ARGUS, WETH...) et de vrais détenteurs
  Smart Money labellisés. Couverture confirmée, pas supposée. Ajouté à
  `NANSEN_CHAIN_MAP` et `NANSEN_SCREENER_CHAIN_MAP` (`data_sources/nansen.py`)
  et à `NANSEN_LINK_CHAIN_MAP` (`core/telegram_alerts.py`).
- **Axiom** : Axiom liste désormais Arc Network parmi ses chaînes supportées
  (Solana, BNB Chain, Ethereum, Base, HyperEVM, Robinhood, Ink, Arc). Ajouté à
  `AXIOM_CHAIN_MAP`.
- **Binance Web3** : PAS ajouté. Aucune source ne confirme une page token pour
  Arc Network (Circle, chain ID 5042). Ce que Binance appelle « ARC-20 » est un
  standard de tokens Bitcoin (Atomicals Protocol) totalement différent — une
  fausse piste identifiée et écartée plutôt que de produire un lien mort ou trompeur.

**Effet de bord positif, documenté explicitement pour rester transparent** : en
confirmant la couverture Nansen sur Arc, l'exigence « smart money requis »
(`REQUIRE_SMART_MONEY`, mode « quality ») s'applique désormais à Arc au même
titre que Solana/BSC/Base/Ethereum. C'est un **renforcement** de la sécurité
(un critère de vérification en plus avant un SIGNAL validé), jamais un
relâchement.

231 tests, 0 échec (230 → 231) : liens Axiom + Nansen présents pour Arc, lien
Binance Web3 absent pour Arc, couverture Nansen (screener + smart money)
confirmée dans les tests de câblage de la chaîne.

## Mise à jour 32 — Filtre d'affichage/alerte par âge du token (boutons 1/5/10/15/30 min, 1h)

**Demandé par l'utilisateur** : des boutons pour sélectionner les alertes par âge du
token, « comme un filtre ». Ajouté sur le même modèle que le curseur SCORE MIN
déjà existant, à côté du sélecteur de chaîne dans l'interface.

- **Boutons** : TOUT (défaut, aucun filtre), 1MN, 5MN, 10MN, 15MN, 30MN, 1H —
  sélection exclusive (un seul actif à la fois), contrairement aux puces de
  chaîne qui se cumulent.
- **C'est un filtre d'AFFICHAGE/ALERTE, pas un seuil de sécurité.** Un token
  plus vieux que le filtre choisi continue d'être scanné, vérifié par le moteur
  anti-rug, suivi en file de promotion, et peut toujours devenir un SIGNAL en
  base — il est seulement absent du dashboard et des alertes Telegram tant
  qu'il ne rentre pas dans la fenêtre. `MIN_PAIR_AGE_MINUTES` (qui définit ce
  qui PEUT devenir un SIGNAL vérifié) n'est pas touché : aucun assouplissement
  de sécurité, uniquement un filtre de confort.
- **Âge inconnu = laissé passer**, jamais cité comme hors fenêtre par
  supposition (même règle que le reste du moteur : absence de donnée n'est
  jamais traitée comme une donnée négative).
- **Câblage** : `core/scanner.py` (`ScannerState.max_alert_age_minutes`,
  `Scanner.set_max_alert_age`, `Scanner._passes_age_filter`, appliqué juste
  avant publication d'un SIGNAL ou d'une VEILLE) → `gui/bridge.py`
  (`setMaxAlertAge`/`getMaxAlertAge`) → `gui/web/index.html` (boutons
  `#ageToggles`, filtre réappliqué en direct aux cartes déjà affichées via
  `reconcileCardsWithFilters`, généralisation de l'ancien
  `reconcileCardsWithScore`).

244 tests, 0 échec (231 → 244) : filtre désactivé par défaut, valeurs
invalides neutralisées, âge inconnu toujours laissé passer, un token hors
filtre n'est ni affiché ni envoyé sur Telegram (VEILLE et SIGNAL) mais reste
suivi pour une promotion ultérieure, un token dans la fenêtre reste affiché
normalement.

## Mise à jour 33 — L'âge affiché était celui de notre détection, pas l'âge réel du token

**Signalé par l'utilisateur** : le dashboard et Telegram affichaient « 2 min », « 4 min »
sur des cartes dont le token avait en réalité déjà 20+ minutes sur un explorateur
(DexScreener, BscScan...). L'utilisateur a ensuite précisé : « je veux des temps
exactes, pas de décalage ».

**Cause réelle, à deux niveaux :**

1. Le badge d'âge de chaque carte (`gui/web/index.html`) était calculé depuis
   `created_at` — l'instant où NOTRE bot a enregistré ce candidat en base — et
   non depuis `pair_created_at`, l'instant réel de création de la pool/paire
   sur la chaîne. Un token qui attend son tour dans la file d'enrichissement
   (plusieurs cycles de scan) peut très bien avoir déjà 20+ minutes réelles au
   moment où le bot l'alerte enfin : l'écran affichait alors l'âge de notre
   alerte, pas celui du token.
2. **Plus grave** : la table `signals` (`storage/db.py`) ne stockait `pair_created_at`
   nulle part. Même en corrigeant l'interface, l'information réelle disparaissait
   dès qu'un signal passait par la base (rechargement de l'appli, liste au
   démarrage) — elle n'était disponible que sur une carte fraîchement reçue en
   direct, avant tout passage en base.

**Corrigé :**

- `storage/db.py` : nouvelle colonne `pair_created_at` (migration non
  destructive, s'applique automatiquement au prochain lancement), persistée
  par `insert_signal()` et relue par `get_active_signals()`/`get_signal()`.
- `gui/web/index.html` : le badge d'âge de chaque carte utilise désormais
  `pair_created_at` (âge réel) en priorité, avec l'heure exacte affichée au
  survol (infobulle), pour vérification directe contre un explorateur — c'est
  la demande explicite de temps exact, sans décalage. Quand aucune pool n'est
  encore indexée (bonding curve), le badge se distingue clairement (préfixe
  « ~ » + infobulle « âge réel inconnu ») au lieu de faire croire à un âge
  vérifié. La fiche détaillée (clic sur une carte) affiche maintenant DEUX
  lignes distinctes : « Détecté le » (notre heure, inchangée) ET « Âge réel du
  token » (heure exacte + âge, ou mention explicite si inconnu).
- Le filtre d'âge ajouté en Mise à jour 32 utilisait déjà `pair_created_at` et
  n'était donc pas affecté par ce bug d'affichage — seul le badge visuel
  l'était.

247 tests, 0 échec (244 → 247) : `pair_created_at` survit à l'aller-retour en
base (insert + relecture directe + liste `get_active_signals`), et reste
`NULL` (jamais inventé) quand la donnée n'existe pas.

## Mise à jour 34 — Le lien Binance Web3 manquait pour Arc (les 3 liens sont confirmés)

**Demandé par l'utilisateur** : avoir les 3 liens (Axiom, Nansen, Binance Web3) sur
les alertes Arc, pas seulement Nansen + Axiom comme depuis la Mise à jour 31.

À l'époque, Binance Web3 n'avait pas été ajouté par prudence : aucune preuve
que la chaîne y avait une page token, et « ARC-20 » (un standard de tokens
Bitcoin sans rapport) brouillait la recherche. **Vérifié cette fois en ouvrant
réellement une page Arc** (`web3.binance.com/en/token/arc/<contrat>`, sur le
token ARGUS) plutôt qu'en devinant : la page existe, affiche de vraies données
de marché (prix, liquidité, holders, audit) et Binance y fait même la
promotion du trading sur Arc en direct. Couverture confirmée, pas supposée.

`BINANCE_WEB3_CHAIN_MAP` (`core/telegram_alerts.py`) a donc désormais `"arc": "arc"`.
Les alertes Telegram et la fiche détail du dashboard pour Arc affichent
maintenant les 3 liens (Axiom, Nansen, Binance Web3), comme pour Solana/BSC/Base/Ethereum.

247 tests, 0 échec : le test qui vérifiait explicitement l'ABSENCE de Binance
Web3 pour Arc est remplacé par un test qui vérifie la présence des 3 liens.

## Mise à jour 35 — DEGEN affichait des tokens vieux de plusieurs jours (bug réel trouvé et corrigé)

**Demandé par l'utilisateur** : « la priorité doit être pour les tokens détectés
just now, ne mets pas des tokens qui existent depuis des jours ou des années,
je veux des signaux pour être early dès la création. »

**Vérifié en direct avant tout changement** (comme pour chaque correctif de ce
bot) : en profil DEGEN, `nansen.screen_tokens("bsc")` remontait réellement des
tokens jusqu'à 96 heures (4 jours) d'âge — par exemple GSTONK à 80h, JINWU à
95h, MUSEWORLD (Base) à 104h. Un des tokens retrouvés dans ce test,
**IMDSTRTGY**, correspond exactement à une carte vue sur le dashboard de
l'utilisateur affichant « 4 min » alors qu'il avait déjà 12,8 heures réelles —
la preuve concrète du bug.

**Cause :** `config.CHAIN_MAX_PAIR_AGE_HOURS` (BSC/Base/Robinhood → 96 h,
introduite pour que la découverte Nansen sur ces chaînes ne remonte pas 0
résultat) s'appliquait **quel que soit le profil actif**, y compris en DEGEN
dont c'est justement la promesse inverse (early uniquement). Cette surcharge
n'a de sens qu'en profil « quality », qui vise au contraire des tokens
installés depuis plusieurs jours.

**Corrigé** (`config.py::chain_max_pair_age_hours`) : la surcharge à 96 h ne
s'applique plus qu'en profil « quality ». En DEGEN, toutes les chaînes
(Solana, BSC, Base, Robinhood, Arc) partagent désormais le même plafond strict
du profil (6 h par défaut) — DEGEN redevient vraiment « early only » sur
toutes les chaînes, pas seulement Solana. Aucun veto de sécurité (honeypot,
mint/freeze, LP lock, concentration...) n'est touché : l'âge d'une paire n'a
jamais été un critère anti-rug dans ce bot.

**Priorité de traitement, également corrigée** (`core/scanner.py::order_for_verification`) :
avant ce correctif, seuls les tokens de moins de 3 minutes (fenêtre « première
minute ») passaient en tête ; tout le reste, y compris un token de 45 minutes,
était mélangé et trié par LIQUIDITÉ — un token de 5 heures plus liquide passait
donc avant un token de 45 minutes. Un palier intermédiaire est ajouté : les
tokens « early » (< 60 min, hors 1re minute) sont désormais vérifiés par ordre
de fraîcheur, avant le reste (trié par liquidité comme avant). Trois paliers au
lieu de deux : 1re minute → early (< 60 min) → reste par liquidité.

250 tests, 0 échec (247 → 250) : en degen, toutes les chaînes partagent le même
plafond d'âge strict et une paire BNB de 50 h est désormais écartée comme sur
Solana ; en quality, la surcharge EVM reste intacte (c'est sa raison d'être) ;
le palier « early » passe bien avant un token plus liquide mais plus ancien.
