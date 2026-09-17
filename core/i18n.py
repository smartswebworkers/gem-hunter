"""
core/i18n.py — Catalogue de messages trilingue (FR/EN/ZH) et traduction inverse
Destination finale : gem_hunter/core/i18n.py

POURQUOI CE MODULE EXISTE.

Les « raisons », les vetos et les vérifications manquantes étaient écrits en
français directement dans core/security_checks.py et core/scoring.py, puis
retraduits pour Telegram par une liste de motifs tenue à la main dans
core/telegram_alerts.py. Cette liste était forcément en retard : chaque fois
qu'un message était ajouté ou reformulé côté moteur, l'alerte anglaise se
retrouvait à recracher la phrase française telle quelle. C'est exactement ce
qui se passait après la réécriture du moteur de sécurité — les alertes se
présentaient en anglais mais listaient des motifs en français.

Le remède n'est pas d'allonger la liste de motifs, c'est de supprimer la
duplication. Chaque message est déclaré ICI, une seule fois, dans les trois
langues, avec ses paramètres nommés. Le moteur produit ses messages via t(),
donc aucune langue ne peut plus être oubliée : elle est écrite au même
endroit et au même moment que les autres.

AJOUT — CHINOIS (ZH). Le dashboard (gui/web/index.html) propose désormais un
sélecteur de langue FR/EN/中文 : les raisons et vérifications manquantes
affichées dans les cartes de signal doivent donc pouvoir être rendues dans les
trois langues, pas seulement FR/EN comme au moment de la création de ce
module.

Pour les textes qui traversent la base de données (un signal enregistré hier,
relu aujourd'hui pour être renvoyé), on garde une traduction inverse : les
expressions régulières servant à retrouver le code d'un message français sont
DÉRIVÉES AUTOMATIQUEMENT des gabarits déclarés ci-dessous. Il n'y a donc plus
de seconde liste à maintenir, et un message ajouté au catalogue est traduisible
dans toutes les langues dès sa déclaration.
"""
import re

DEFAULT_LANG = "fr"
SUPPORTED_LANGS = ("fr", "en", "zh")

# =============================================================================
# Catalogue
# =============================================================================
# Les paramètres sont TOUJOURS passés déjà formatés (chaînes), jamais des
# nombres bruts : cela garde les gabarits identiques dans toutes les langues et
# rend la traduction inverse fiable.
MESSAGES: dict[str, dict[str, str]] = {
    # --- Vetos booléens sur les drapeaux de contrat -------------------------
    "veto.honeypot": {
        "fr": "Honeypot confirmé — l'achat passe, la vente est bloquée.",
        "en": "Confirmed honeypot — buying works, selling is blocked.",
        "zh": "确认为蜜罐合约——可以买入，无法卖出。",
    },
    "veto.cannot_sell_all": {
        "fr": "Vente totale impossible — piège classique qui laisse toujours une partie bloquée.",
        "en": "Cannot sell the full balance — a classic trap that always leaves part of it stuck.",
        "zh": "无法全部卖出——经典陷阱，总会卡住一部分代币。",
    },
    "veto.cannot_buy": {
        "fr": "Achat impossible sur ce contrat.",
        "en": "Buying is impossible on this contract.",
        "zh": "该合约无法买入。",
    },
    "veto.transfer_pausable": {
        "fr": "Le propriétaire peut suspendre les transferts à tout moment.",
        "en": "The owner can pause transfers at any time.",
        "zh": "所有者可随时暂停转账。",
    },
    "veto.trading_cooldown": {
        "fr": "Délai imposé entre deux transactions — empêche de sortir vite.",
        "en": "Enforced cooldown between trades — prevents a fast exit.",
        "zh": "两次交易之间存在强制冷却时间——阻止快速离场。",
    },
    "veto.is_blacklisted": {
        "fr": "Le propriétaire peut blacklister une adresse et l'empêcher de vendre.",
        "en": "The owner can blacklist an address and prevent it from selling.",
        "zh": "所有者可将某个地址列入黑名单，阻止其卖出。",
    },
    "veto.non_transferable": {
        "fr": "Token non transférable.",
        "en": "Token is non-transferable.",
        "zh": "代币不可转账。",
    },
    "veto.transfer_hook": {
        "fr": "Transfer hook actif — du code arbitraire s'exécute à chaque transfert.",
        "en": "Active transfer hook — arbitrary code runs on every transfer.",
        "zh": "转账钩子（Transfer Hook）处于激活状态——每次转账都会执行任意代码。",
    },
    "veto.transfer_hook_upgradable": {
        "fr": "Le transfer hook est modifiable après coup.",
        "en": "The transfer hook can be changed after the fact.",
        "zh": "转账钩子事后仍可被修改。",
    },
    "veto.is_proxy": {
        "fr": "Contrat proxy — la logique peut être remplacée après ton achat.",
        "en": "Proxy contract — the logic can be swapped out after you buy.",
        "zh": "代理合约——买入后其逻辑可被替换。",
    },
    "veto.hidden_owner": {
        "fr": "Propriétaire dissimulé — la renonciation affichée est trompeuse.",
        "en": "Hidden owner — the advertised renouncement is misleading.",
        "zh": "所有者被隐藏——所显示的放弃权限具有误导性。",
    },
    "veto.can_take_back_ownership": {
        "fr": "La propriété peut être reprise — la renonciation est réversible.",
        "en": "Ownership can be reclaimed — the renouncement is reversible.",
        "zh": "所有权可被重新收回——放弃权限是可逆的。",
    },
    "veto.owner_change_balance": {
        "fr": "Le propriétaire peut modifier les soldes des détenteurs.",
        "en": "The owner can modify holder balances.",
        "zh": "所有者可修改持有者的余额。",
    },
    "veto.selfdestruct": {
        "fr": "Le contrat peut s'autodétruire.",
        "en": "The contract can self-destruct.",
        "zh": "该合约可以自毁。",
    },
    "veto.slippage_modifiable": {
        "fr": "Les taxes sont modifiables — elles peuvent passer à 100% après ton entrée.",
        "en": "Taxes are modifiable — they can be raised to 100% after you enter.",
        "zh": "税率可被修改——你买入后可能被调高至100%。",
    },
    "veto.personal_slippage_modifiable": {
        "fr": "Une taxe peut être appliquée à une adresse précise.",
        "en": "A tax can be applied to one specific address.",
        "zh": "可以针对某个特定地址单独设置税率。",
    },
    "veto.anti_whale_modifiable": {
        "fr": "La limite anti-baleine est modifiable à volonté.",
        "en": "The anti-whale limit can be changed at will.",
        "zh": "防巨鲸持仓限制可被随意修改。",
    },
    "veto.external_call": {
        "fr": "Le contrat délègue sa logique à un contrat tiers modifiable.",
        "en": "The contract delegates its logic to a modifiable third-party contract.",
        "zh": "该合约将其逻辑委托给一个可被修改的第三方合约。",
    },
    "veto.gas_abuse": {
        "fr": "Le contrat consomme le gas de l'utilisateur de façon abusive.",
        "en": "The contract abusively drains the user's gas.",
        "zh": "该合约过度消耗用户的 Gas。",
    },
    "veto.transfer_fee_upgradable": {
        "fr": "Les frais de transfert sont modifiables après coup.",
        "en": "Transfer fees can be changed after the fact.",
        "zh": "转账手续费事后仍可被修改。",
    },
    "veto.balance_mutable_authority": {
        "fr": "Une autorité peut modifier les soldes.",
        "en": "An authority can modify balances.",
        "zh": "某个权限账户可以修改余额。",
    },
    "veto.closable": {
        "fr": "Le compte mint peut être fermé par son autorité.",
        "en": "The mint account can be closed by its authority.",
        "zh": "铸币账户可被其权限方关闭。",
    },
    "veto.default_account_state_upgradable": {
        "fr": "L'état par défaut des comptes est modifiable.",
        "en": "The default account state can be changed.",
        "zh": "账户的默认状态可被修改。",
    },
    "veto.metadata_mutable": {
        "fr": "Métadonnées modifiables — le token peut être renommé et recyclé après coup.",
        "en": "Mutable metadata — the token can be renamed and recycled later.",
        "zh": "元数据可被修改——代币可在事后被改名并循环利用。",
    },
    "veto.mint_authority_active": {
        "fr": "Mint Authority toujours active — l'offre peut être diluée à l'infini.",
        "en": "Mint Authority still active — the supply can be diluted without limit.",
        "zh": "铸币权限（Mint Authority）仍处于激活状态——供应量可被无限增发稀释。",
    },
    "veto.freeze_authority_active": {
        "fr": "Freeze Authority toujours active — tes tokens peuvent être gelés.",
        "en": "Freeze Authority still active — your tokens can be frozen.",
        "zh": "冻结权限（Freeze Authority）仍处于激活状态——你的代币可能被冻结。",
    },
    "veto.is_mintable": {
        "fr": "Le contrat est encore mintable.",
        "en": "The contract is still mintable.",
        "zh": "该合约仍可增发代币。",
    },
    "veto.honeypot_with_same_creator": {
        "fr": "Le créateur a déjà déployé un honeypot.",
        "en": "The creator has already deployed a honeypot.",
        "zh": "该创建者此前已部署过蜜罐合约。",
    },
    "veto.creator_known_rugger": {
        "fr": "Le wallet déployeur a déjà créé {count} contrat(s) malveillant(s) — usine à rug.",
        "en": "The deployer wallet has already created {count} malicious contract(s) — rug factory.",
        "zh": "部署钱包此前已创建 {count} 个恶意合约——典型的 rug 工厂。",
    },
    "veto.creator_wallet_flagged": {
        "fr": "Le wallet déployeur est signalé pour activité malveillante ({flags}).",
        "en": "The deployer wallet is flagged for malicious activity ({flags}).",
        "zh": "部署钱包因恶意活动被标记（{flags}）。",
    },
    "veto.fake_token": {
        "fr": "Token contrefait, imite un projet existant.",
        "en": "Counterfeit token, impersonating an existing project.",
        "zh": "仿冒代币，冒充已有项目。",
    },
    "veto.is_airdrop_scam": {
        "fr": "Signalé comme arnaque de type airdrop.",
        "en": "Flagged as an airdrop scam.",
        "zh": "已被标记为空投诈骗。",
    },
    "veto.not_open_source": {
        "fr": "Code source non vérifié — audit impossible.",
        "en": "Source code not verified — no audit possible.",
        "zh": "源代码未经验证——无法进行审计。",
    },

    # --- Taxes --------------------------------------------------------------
    "veto.buy_tax": {
        "fr": "Taxe d'achat excessive ({value}% > {max}%).",
        "en": "Excessive buy tax ({value}% > {max}%).",
        "zh": "买入税过高（{value}% > {max}%）。",
    },
    "veto.sell_tax": {
        "fr": "Taxe de vente excessive ({value}% > {max}%).",
        "en": "Excessive sell tax ({value}% > {max}%).",
        "zh": "卖出税过高（{value}% > {max}%）。",
    },
    "veto.transfer_fee": {
        "fr": "Frais de transfert excessifs ({value}%).",
        "en": "Excessive transfer fee ({value}%).",
        "zh": "转账手续费过高（{value}%）。",
    },

    # --- Concentration de l'offre ------------------------------------------
    "veto.top10": {
        "fr": "Concentration excessive — le top 10 détient {value}% (maximum toléré {max}%).",
        "en": "Excessive concentration — the top 10 hold {value}% (maximum allowed {max}%).",
        "zh": "持仓过度集中——前10名持有 {value}%（允许上限 {max}%）。",
    },
    "veto.creator_pct": {
        "fr": "Le créateur détient {value}% de l'offre (maximum {max}%).",
        "en": "The creator holds {value}% of the supply (maximum {max}%).",
        "zh": "创建者持有供应量的 {value}%（上限 {max}%）。",
    },
    "veto.owner_pct": {
        "fr": "Le propriétaire du contrat détient {value}% de l'offre.",
        "en": "The contract owner holds {value}% of the supply.",
        "zh": "合约所有者持有供应量的 {value}%。",
    },
    "veto.dev_pct": {
        "fr": "Le wallet du développeur détient encore {value}% de l'offre.",
        "en": "The developer wallet still holds {value}% of the supply.",
        "zh": "开发者钱包仍持有供应量的 {value}%。",
    },
    "veto.single_holder": {
        "fr": "Un seul wallet détient {value}% de l'offre (maximum {max}%) — il peut effondrer le prix seul.",
        "en": "A single wallet holds {value}% of the supply (maximum {max}%) — it can crash the price on its own.",
        "zh": "单一钱包持有供应量的 {value}%（上限 {max}%）——足以独自砸盘。",
    },
    "veto.insider_network": {
        "fr": "Réseau d'insiders liés au créateur détenant {value}% de l'offre (maximum {max}%) — schéma de bundle avant revente groupée.",
        "en": "Insider network tied to the creator holding {value}% of the supply (maximum {max}%) — bundle pattern ahead of a coordinated dump.",
        "zh": "与创建者关联的内部人网络持有供应量的 {value}%（上限 {max}%）——典型的集中砸盘（bundle）结构。",
    },
    "veto.holder_count": {
        "fr": "Trop peu de détenteurs ({value} < {min}).",
        "en": "Too few holders ({value} < {min}).",
        "zh": "持有人数过少（{value} < {min}）。",
    },

    # --- Liquidité ----------------------------------------------------------
    "veto.lp_lock": {
        "fr": "Liquidité insuffisamment verrouillée ({value}% verrouillée ou brûlée, minimum {min}%) — le développeur peut retirer le pool.",
        "en": "Liquidity insufficiently locked ({value}% locked or burned, minimum {min}%) — the developer can pull the pool.",
        "zh": "流动性锁定不足（已锁定或销毁 {value}%，最低要求 {min}%）——开发者可抽走资金池。",
    },

    # --- RugCheck -----------------------------------------------------------
    "veto.rugcheck_risk": {
        "fr": "RugCheck : {risk}",
        "en": "RugCheck: {risk}",
        "zh": "RugCheck：{risk}",
    },
    "veto.rugcheck_score": {
        "fr": "Score de risque RugCheck trop élevé ({value} > {max}).",
        "en": "RugCheck risk score too high ({value} > {max}).",
        "zh": "RugCheck 风险评分过高（{value} > {max}）。",
    },
    "veto.rugcheck_lp": {
        "fr": "RugCheck : liquidité verrouillée à seulement {value}%.",
        "en": "RugCheck: liquidity locked at only {value}%.",
        "zh": "RugCheck：流动性仅锁定 {value}%。",
    },

    # --- Stratégie ----------------------------------------------------------
    "veto.dex_paid": {
        "fr": "Déjà DEX Paid — hors cible, on vise l'entrée avant le paiement.",
        "en": "Already DEX Paid — out of target, we aim to enter before the payment.",
        "zh": "已经是 DEX Paid——偏离目标，策略是抢在付费推广之前进场。",
    },

    # --- Vetos de marché ----------------------------------------------------
    "veto.bonding_reserves": {
        "fr": "Réserves de bonding curve trop faibles (${value}).",
        "en": "Bonding curve reserves too small (${value}).",
        "zh": "联合曲线（bonding curve）储备过低（${value}）。",
    },
    "veto.liquidity": {
        "fr": "Liquidité insuffisante (${value} < ${min}).",
        "en": "Insufficient liquidity (${value} < ${min}).",
        "zh": "流动性不足（${value} < ${min}）。",
    },
    "veto.liq_to_mcap": {
        "fr": "Liquidité dérisoire face à la capitalisation ({value}% de ${mcap}, minimum {min}%) — le premier vendeur significatif effondre le prix.",
        "en": "Negligible liquidity against market cap ({value}% of ${mcap}, minimum {min}%) — the first meaningful seller collapses the price.",
        "zh": "流动性相对市值过低（占 ${mcap} 市值的 {value}%，最低要求 {min}%）——第一个大额卖单就会砸穿价格。",
    },
    "veto.volume_1h": {
        "fr": "Volume 1h insuffisant (${value} < ${min}).",
        "en": "Insufficient 1h volume (${value} < ${min}).",
        "zh": "1小时交易量不足（${value} < ${min}）。",
    },
    "veto.wash_trading": {
        "fr": "Volume incohérent avec la liquidité ({value}x en 1h) — signature de wash trading.",
        "en": "Volume inconsistent with liquidity ({value}x in 1h) — wash trading signature.",
        "zh": "交易量与流动性不匹配（1小时内达 {value} 倍）——刷量交易的特征。",
    },
    "veto.vertical_pump": {
        "fr": "Hausse verticale de {value}% en 1h (maximum {max}%) — entrée en haut de pompe.",
        "en": "Vertical rise of {value}% in 1h (maximum {max}%) — entering at the top of the pump.",
        "zh": "1小时内暴涨 {value}%（上限 {max}%）——此时进场等于买在拉盘顶部。",
    },
    "veto.pair_too_old": {
        "fr": "Paire trop ancienne ({value}h > {max}h).",
        "en": "Pair too old ({value}h > {max}h).",
        "zh": "交易对过旧（{value} 小时 > {max} 小时）。",
    },
    "veto.already_dumped": {
        "fr": "Le token a déjà plongé ({window} {value}%) — le mouvement est fait, la liquidité de sortie s'est vidée.",
        "en": "Token has already dumped ({window} {value}%) — the move is over, exit liquidity is gone.",
        "zh": "该代币已经跳水（{window} {value}%）——行情已走完，出货流动性已耗尽。",
    },
    "veto.dump_rollover": {
        "fr": "Retournement amorcé : +{pump}% en 1h puis {drop}% en 5 min — il est sur le point de plonger.",
        "en": "Reversal underway: +{pump}% in 1h then {drop}% in 5 min — about to dump.",
        "zh": "已开始反转：1小时 +{pump}%，随后5分钟 {drop}%——即将跳水。",
    },
    "veto.dump_distribution": {
        "fr": "Distribution active : {sells} ventes pour {buys} achats sur 1h et prix en baisse ({change}%) — sortie en cours.",
        "en": "Active distribution: {sells} sells vs {buys} buys in 1h with price falling ({change}%) — exit underway.",
        "zh": "正在派发：1小时内 {sells} 笔卖出 / {buys} 笔买入，且价格下跌（{change}%）——出货进行中。",
    },

    # --- Vérifications manquantes ------------------------------------------
    "missing.header": {
        "fr": "Non vérifiable pour l'instant : {items}.",
        "en": "Not verifiable for now: {items}.",
        "zh": "目前无法验证：{items}。",
    },
    "missing.security_data": {
        "fr": "données de sécurité indisponibles ({reason})",
        "en": "security data unavailable ({reason})",
        "zh": "安全数据不可用（{reason}）",
    },
    "missing.honeypot": {
        "fr": "simulation d'achat/vente",
        "en": "buy/sell simulation",
        "zh": "买卖模拟测试",
    },
    "missing.buy_tax": {"fr": "taxe d'achat", "en": "buy tax", "zh": "买入税"},
    "missing.sell_tax": {"fr": "taxe de vente", "en": "sell tax", "zh": "卖出税"},
    "missing.is_open_source": {
        "fr": "vérification du code source",
        "en": "source code verification",
        "zh": "源代码验证",
    },
    "missing.lp_locked_pct": {
        "fr": "verrouillage de la liquidité",
        "en": "liquidity lock",
        "zh": "流动性锁定情况",
    },
    "missing.top10_holder_pct": {
        "fr": "concentration des détenteurs",
        "en": "holder concentration",
        "zh": "持有人集中度",
    },
    "missing.holder_count": {
        "fr": "nombre de détenteurs",
        "en": "holder count",
        "zh": "持有人数量",
    },
    "missing.smart_money": {
        "fr": "aucun wallet smart money positionné (profil quality)",
        "en": "no smart money wallet positioned (quality profile)",
        "zh": "未发现聪明钱（Smart Money）钱包介入（quality 模式）",
    },
    "missing.mint_authority_active": {"fr": "mint authority", "en": "mint authority", "zh": "铸币权限"},
    "missing.freeze_authority_active": {"fr": "freeze authority", "en": "freeze authority", "zh": "冻结权限"},
    "missing.bonding_curve": {
        "fr": "token encore en bonding curve, pas de pool vérifiable",
        "en": "token still on the bonding curve, no verifiable pool",
        "zh": "代币仍处于联合曲线（bonding curve）阶段，没有可验证的资金池",
    },
    "missing.pair_too_young": {
        "fr": "paire trop jeune ({minutes} min) pour que les données soient fiables",
        "en": "pair too young ({minutes} min) for the data to be reliable",
        "zh": "交易对过于年轻（{minutes} 分钟），数据尚不可靠",
    },
    "missing.safety_feature": {
        "fr": "note de sécurité insuffisante ({value} < {min})",
        "en": "insufficient safety score ({value} < {min})",
        "zh": "安全评分不足（{value} < {min}）",
    },
    "missing.distribution_feature": {
        "fr": "répartition de l'offre trop concentrée ({value} < {min})",
        "en": "supply distribution too concentrated ({value} < {min})",
        "zh": "供应分布过度集中（{value} < {min}）",
    },

    # --- Raisons d'indisponibilité des sources ------------------------------
    "unavailable.goplus_not_indexed": {
        "fr": "GoPlus n'a pas encore indexé ce contrat",
        "en": "GoPlus has not indexed this contract yet",
        "zh": "GoPlus 尚未收录该合约",
    },
    "unavailable.goplus_empty": {
        "fr": "GoPlus n'a renvoyé aucune donnée pour ce contrat",
        "en": "GoPlus returned no data for this contract",
        "zh": "GoPlus 未返回该合约的任何数据",
    },
    "unavailable.solana_no_source": {
        "fr": "ni GoPlus ni le RPC Solana n'ont répondu",
        "en": "neither GoPlus nor the Solana RPC responded",
        "zh": "GoPlus 和 Solana RPC 均无响应",
    },
    "unavailable.robinhood_goplus": {
        "fr": "GoPlus ne couvre pas encore Robinhood Chain (chain ID 4663)",
        "en": "GoPlus does not cover Robinhood Chain yet (chain ID 4663)",
        "zh": "GoPlus 尚未覆盖 Robinhood Chain（chain ID 4663）",
    },
    "unavailable.arc_goplus": {
        "fr": "GoPlus ne couvre pas encore Arc Network (chain ID 5042)",
        "en": "GoPlus does not cover Arc Network yet (chain ID 5042)",
        "zh": "GoPlus 尚未覆盖 Arc Network（chain ID 5042）",
    },
    "unavailable.generic": {
        "fr": "source de sécurité injoignable",
        "en": "security source unreachable",
        "zh": "安全数据源无法访问",
    },

    # --- Observations de sécurité ------------------------------------------
    "reason.bonding_curve_reserves": {
        "fr": "Réserves détenues par le programme pump.fun — le dev ne peut pas retirer le pool, mais il peut vendre sa propre allocation à tout moment.",
        "en": "Reserves held by the pump.fun program — the dev cannot pull the pool, but can sell their own allocation at any time.",
        "zh": "储备由 pump.fun 程序持有——开发者无法抽走资金池，但可随时卖出自己持有的份额。",
    },
    "reason.lp_not_verifiable": {
        "fr": "Verrouillage de la liquidité non vérifiable.",
        "en": "Liquidity lock not verifiable.",
        "zh": "流动性锁定情况无法验证。",
    },
    "reason.lp_locked_burned": {
        "fr": "Liquidité verrouillée ou brûlée à {value}%.",
        "en": "Liquidity locked or burned at {value}%.",
        "zh": "流动性已锁定或销毁 {value}%。",
    },
    "reason.lp_locked": {
        "fr": "Liquidité verrouillée à {value}%.",
        "en": "Liquidity locked at {value}%.",
        "zh": "流动性已锁定 {value}%。",
    },
    "reason.mint_freeze_revoked": {
        "fr": "Mint et Freeze Authority révoquées.",
        "en": "Mint and Freeze Authority revoked.",
        "zh": "铸币权限与冻结权限均已放弃。",
    },
    "reason.mint_revoked": {
        "fr": "Mint Authority révoquée.",
        "en": "Mint Authority revoked.",
        "zh": "铸币权限已放弃。",
    },
    "reason.no_taxes": {
        "fr": "Aucune taxe d'achat ni de vente.",
        "en": "No buy or sell tax.",
        "zh": "买入和卖出均无税费。",
    },
    "reason.taxes": {
        "fr": "Taxes de {buy}% à l'achat et {sell}% à la vente.",
        "en": "Taxes of {buy}% on buy and {sell}% on sell.",
        "zh": "买入税 {buy}%，卖出税 {sell}%。",
    },
    "reason.taxes_not_verifiable": {
        "fr": "Taxes non vérifiables.",
        "en": "Taxes not verifiable.",
        "zh": "税费无法验证。",
    },
    "reason.source_verified": {
        "fr": "Code source vérifié.",
        "en": "Source code verified.",
        "zh": "源代码已验证。",
    },
    "reason.sellability_unverified": {
        "fr": "Capacité à revendre non vérifiée (second avis RugCheck indisponible).",
        "en": "Sellability not verified (RugCheck second opinion unavailable).",
        "zh": "卖出能力未经验证（RugCheck 二次核验不可用）。",
    },
    "reason.rugcheck_score": {
        "fr": "RugCheck : score de risque {value}.",
        "en": "RugCheck: risk score {value}.",
        "zh": "RugCheck：风险评分 {value}。",
    },
    "reason.not_dex_paid": {
        "fr": "Pas encore DEX Paid — entrée avant le boost de visibilité.",
        "en": "Not yet DEX Paid — entry before the visibility boost.",
        "zh": "尚未开通 DEX Paid——在曝光度提升之前进场。",
    },

    # --- Observations de distribution --------------------------------------
    "reason.holders_not_verifiable": {
        "fr": "Concentration des détenteurs non vérifiable.",
        "en": "Holder concentration not verifiable.",
        "zh": "持有人集中度无法验证。",
    },
    "reason.supply_well_spread": {
        "fr": "Offre bien répartie (top 10 = {value}%).",
        "en": "Supply well spread (top 10 = {value}%).",
        "zh": "供应分布良好（前10名持有 {value}%）。",
    },
    "reason.top10": {
        "fr": "Top 10 des détenteurs = {value}%.",
        "en": "Top 10 holders = {value}%.",
        "zh": "前10名持有人合计 {value}%。",
    },
    "reason.creator_holds_nothing": {
        "fr": "Le créateur ne détient quasiment rien.",
        "en": "The creator holds almost nothing.",
        "zh": "创建者几乎不持有任何份额。",
    },
    "reason.creator_holds": {
        "fr": "Le créateur détient {value}% de l'offre.",
        "en": "The creator holds {value}% of the supply.",
        "zh": "创建者持有供应量的 {value}%。",
    },
    "reason.holder_count": {
        "fr": "{value} détenteurs.",
        "en": "{value} holders.",
        "zh": "共 {value} 名持有人。",
    },

    # --- Observations de marché --------------------------------------------
    "reason.solid_liquidity": {
        "fr": "Liquidité solide (${value}).",
        "en": "Solid liquidity (${value}).",
        "zh": "流动性充足（${value}）。",
    },
    "reason.deep_pool": {
        "fr": "Pool profond ({value}% de la capitalisation).",
        "en": "Deep pool ({value}% of market cap).",
        "zh": "资金池深厚（占市值的 {value}%）。",
    },
    "reason.wash_trading": {
        "fr": "Volume incohérent avec la liquidité ({value}x) — probable wash trading.",
        "en": "Volume inconsistent with liquidity ({value}x) — likely wash trading.",
        "zh": "交易量与流动性不匹配（{value} 倍）——疑似刷量交易。",
    },
    "reason.no_volume": {
        "fr": "Aucun volume échangé.",
        "en": "No volume traded.",
        "zh": "没有任何成交量。",
    },
    "reason.healthy_vol_liq": {
        "fr": "Ratio volume/liquidité sain ({value}x par heure).",
        "en": "Healthy volume/liquidity ratio ({value}x per hour).",
        "zh": "成交量/流动性比例健康（每小时 {value} 倍）。",
    },
    "reason.very_low_volume": {
        "fr": "Volume très faible face à la liquidité ({value}x) — peu d'intérêt réel.",
        "en": "Very low volume against liquidity ({value}x) — little real interest.",
        "zh": "相对流动性而言成交量极低（{value} 倍）——实际关注度不高。",
    },
    "reason.vertical_1h": {
        "fr": "Hausse verticale sur 1h ({value}%) — entrée tardive, risque de sommet.",
        "en": "Vertical 1h rise ({value}%) — late entry, risk of buying the top.",
        "zh": "1小时内暴涨（{value}%）——入场偏晚，可能买在高点。",
    },
    "reason.momentum_positive_1h": {
        "fr": "Momentum positif sur 1h ({value}%).",
        "en": "Positive 1h momentum ({value}%).",
        "zh": "1小时动能为正（{value}%）。",
    },
    "reason.momentum_negative_1h": {
        "fr": "Momentum négatif sur 1h ({value}%).",
        "en": "Negative 1h momentum ({value}%).",
        "zh": "1小时动能为负（{value}%）。",
    },
    "reason.almost_no_sells": {
        "fr": "Quasi aucune vente ({value}% d'achats) — vérifier qu'il est possible de sortir.",
        "en": "Almost no sells ({value}% buys) — check that exiting is possible.",
        "zh": "几乎没有卖单（买单占 {value}%）——需确认是否能正常卖出。",
    },
    "reason.strong_buy_pressure": {
        "fr": "Pression acheteuse forte ({value}% d'achats).",
        "en": "Strong buy pressure ({value}% buys).",
        "zh": "买盘压力强劲（买单占 {value}%）。",
    },

    # --- Smart money --------------------------------------------------------
    "reason.smart_money_buying": {
        "fr": "🐋 {count} Smart Money wallet(s) en train d'acheter : {wallets}.",
        "en": "🐋 {count} Smart Money wallet(s) currently buying: {wallets}.",
        "zh": "🐋 {count} 个聪明钱（Smart Money）钱包正在买入：{wallets}。",
    },
    "reason.smart_money_holding": {
        "fr": "{count} wallet(s) Smart Money détenteur(s) ({value}% détenu), sans achat net récent.",
        "en": "{count} Smart Money wallet(s) holding ({value}% held), no recent net buying.",
        "zh": "{count} 个聪明钱（Smart Money）钱包持有（占 {value}%），但近期无净买入。",
    },

    # --- Niveaux de risque --------------------------------------------------
    "risk.low": {"fr": "Faible", "en": "Low", "zh": "低"},
    "risk.moderate": {"fr": "Modéré", "en": "Moderate", "zh": "中等"},
    "risk.high": {"fr": "Élevé", "en": "High", "zh": "高"},
    "risk.unverified": {"fr": "Non vérifié", "en": "Unverified", "zh": "未验证"},
}


# =============================================================================
# Rendu
# =============================================================================
def t(code: str, lang: str = DEFAULT_LANG, **params) -> str:
    """
    Rend le message `code` dans la langue demandée.
    Un code inconnu renvoie le code lui-même : visible en test, jamais une
    exception qui ferait tomber un cycle de scan en production.
    """
    entry = MESSAGES.get(code)
    if not entry:
        return code
    template = entry.get(lang) or entry.get(DEFAULT_LANG) or code
    try:
        return template.format(**params)
    except (KeyError, IndexError):
        return template


# =============================================================================
# Traduction inverse (pour les textes déjà stockés en base)
# =============================================================================
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


def _compile_pattern(template: str) -> re.Pattern:
    """Transforme un gabarit « Taxe d'achat de {value}% » en expression régulière."""
    parts = _PLACEHOLDER_RE.split(template)
    # split() alterne littéral, nom de paramètre, littéral, ...
    regex = ["^"]
    for index, part in enumerate(parts):
        if index % 2 == 0:
            regex.append(re.escape(part))
        else:
            regex.append(f"(?P<{part}>.+?)")
    regex.append("$")
    return re.compile("".join(regex), re.DOTALL)


def _build_reverse_index() -> list[tuple[re.Pattern, str]]:
    """
    Index de traduction inverse, dérivé du catalogue, TOUJOURS à partir du
    gabarit français : c'est la langue dans laquelle le moteur (security_checks,
    scoring) produit les raisons au moment du scoring, quelle que soit la
    langue d'affichage choisie ensuite dans le dashboard ou pour Telegram.
    Les gabarits les plus « littéraux » (le plus de texte fixe) sont testés en
    premier : entre deux gabarits susceptibles de correspondre, le plus
    spécifique gagne.
    """
    index = []
    for code, entry in MESSAGES.items():
        fr = entry.get("fr")
        if not fr:
            continue
        literal_length = len(_PLACEHOLDER_RE.sub("", fr))
        index.append((literal_length, _compile_pattern(fr), code))
    index.sort(key=lambda item: item[0], reverse=True)
    return [(pattern, code) for _, pattern, code in index]


_REVERSE_INDEX = _build_reverse_index()

# Messages dont un paramètre contient lui-même une liste d'autres messages :
# {code: (nom_du_paramètre, séparateur)}. Sans ce traitement, l'en-tête
# « Non vérifiable pour l'instant : … » se traduisait mais recrachait sa liste
# de vérifications en français.
_CONTAINER_MESSAGES = {
    "missing.header": ("items", ", "),
    "missing.security_data": ("reason", None),
}


def translate(text: str, lang: str = "en") -> str:
    """
    Traduit un message français déjà rendu vers `lang` (« en » ou « zh »).

    Sert aux textes qui ont transité par la base de données ou par un module
    tiers. Le texte est rendu tel quel si aucun gabarit ne correspond, ce qui
    est le comportement le moins destructeur : mieux vaut une ligne en français
    qu'une ligne perdue.
    """
    if lang == "fr" or not text:
        return text
    for pattern, code in _REVERSE_INDEX:
        match = pattern.match(text)
        if match:
            params = match.groupdict()
            container = _CONTAINER_MESSAGES.get(code)
            if container:
                param_name, separator = container
                value = params.get(param_name, "")
                if separator:
                    params[param_name] = separator.join(
                        translate(part, lang) for part in value.split(separator)
                    )
                else:
                    params[param_name] = translate(value, lang)
            return t(code, lang, **params)
    return text


def translate_all(items, lang: str = "en") -> list[str]:
    return [translate(item, lang) for item in (items or [])]


def translate_joined(text: str, lang: str = "en", separators: tuple = (", ", " ; ", "; ")) -> str:
    """
    Traduit un texte composé de plusieurs messages assemblés par un séparateur
    (c'est le cas de « Non vérifiable pour l'instant : a, b, c. »).
    """
    if lang == "fr" or not text:
        return text
    direct = translate(text, lang)
    if direct != text:
        # Le gabarit global a été reconnu ; ses paramètres peuvent eux-mêmes
        # contenir une liste de messages, retraduite ici.
        return direct
    for sep in separators:
        if sep in text:
            return sep.join(translate(part, lang) for part in text.split(sep))
    return text
