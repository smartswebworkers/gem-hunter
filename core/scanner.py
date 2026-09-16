"""
core/scanner.py — Orchestrateur central du scan multichain
Destination finale : gem_hunter/core/scanner.py

AJOUT PRINCIPAL — DEUX NIVEAUX D'INFORMATION AU LIEU D'UN.

L'ancien scanner n'avait qu'une sortie : SIGNAL. Tout ce qui n'était pas
rejeté et dépassait le seuil devenait un signal avec plan d'entrée, stop loss
et objectifs, même quand aucune de ses données de sécurité n'avait pu être
vérifiée. C'est cette sortie unique qui obligeait le moteur de sécurité à être
laxiste : refuser les tokens non vérifiables aurait vidé l'écran, alors le
choix avait été fait de les accepter. Le mauvais choix, résolu ici par une
deuxième sortie.

  VEILLE : le token est repéré tôt, affiché et alerté, marqué NON VÉRIFIÉ,
           sans plan d'entrée ni achat automatique. C'est de l'information.
  SIGNAL : toutes les vérifications obligatoires ont abouti avec de vraies
           données. Seul niveau qui produit un plan de risque.

Un candidat en VEILLE reste suivi (file de promotion ci-dessous) et est
re-testé à chaque cycle. Dès que ses données deviennent disponibles et
propres, il est promu en SIGNAL, avec le plan d'entrée. On voit donc les
tokens aussi tôt qu'avant, mais on n'engage du capital que sur ce qui a été
vérifié.
"""
import threading
import time
import logging

from chains import solana, bsc, base, ethereum, robinhood
from core.scoring import compute_score
from core.security_checks import (
    evaluate_market_prefilter, creator_reputation_veto, is_first_minute_candidate,
)
from core.risk_management import compute_risk_plan
from core.telegram_alerts import send_alert
from core import performance_tracker, self_tuning, self_upgrade
from data_sources import dexscreener, nansen, goplus
from storage import db
import config as cfg

logger = logging.getLogger("gem_hunter.scanner")

CHAIN_MODULES = {
    "solana": solana,
    "bsc": bsc,
    "base": base,
    "ethereum": ethereum,
    "robinhood": robinhood,
}


def order_for_verification(candidates: list[dict], cap: int) -> tuple[list[dict], int]:
    """
    Ordonne les candidats préfiltrés d'un cycle pour la vérification, et
    applique le plafond `cap`. Règle, À CHAQUE CYCLE (pas seulement en cas de
    débordement) :

      1. les tokens de la fenêtre « première minute » d'abord, du plus jeune au
         plus ancien — un token nouveau-né passe AVANT un token de plusieurs
         heures ;
      2. le reste ensuite, par liquidité décroissante.

    Sous le plafond, `FIRST_MINUTE_ENRICHMENT_RESERVE` créneaux sont garantis à
    la fenêtre première minute avant que les plus liquides ne prennent le
    reste ; s'il reste de la place, on complète avec d'autres tokens frais.

    Renvoie (liste_ordonnée_et_plafonnée, nombre_reporté).
    """
    first_min = [c for c in candidates if is_first_minute_candidate(c)]
    others = [c for c in candidates if not is_first_minute_candidate(c)]
    first_min.sort(key=lambda c: c.get("pair_created_at") or 0, reverse=True)
    others.sort(key=lambda c: c.get("liquidity") or 0, reverse=True)

    total = len(first_min) + len(others)
    if total <= cap:
        return first_min + others, 0

    reserve = min(len(first_min),
                  getattr(cfg, "FIRST_MINUTE_ENRICHMENT_RESERVE", 0), cap)
    kept = first_min[:reserve] + others[:cap - reserve]
    if len(kept) < cap:
        kept += first_min[reserve:reserve + (cap - len(kept))]
    return kept, total - len(kept)


class ScannerState:
    """État partagé et thread-safe du scanner, piloté depuis l'interface."""

    def __init__(self):
        self._lock = threading.Lock()
        self.running = False
        self.paused = False
        self.auto_buy = False
        self.telegram_enabled = True
        self.mode = "safe"  # "safe" | "aggressive"
        self.min_score = cfg.MIN_SCORE_TO_BUY
        self.watch_enabled = True   # alertes précoces non vérifiées
        self.active_chains = set(cfg.ACTIVE_CHAINS)
        self.on_new_signal = None   # callback(dict) fourni par l'interface
        self.on_stats_update = None

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "running": self.running,
                "paused": self.paused,
                "auto_buy": self.auto_buy,
                "telegram_enabled": self.telegram_enabled,
                "watch_enabled": self.watch_enabled,
                "mode": self.mode,
                "min_score": self.min_score,
                "active_chains": sorted(self.active_chains),
            }


class Scanner:
    def __init__(self, state: ScannerState):
        self.state = state
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._cycle_count = 0
        self._last_signal_time = time.time()
        self._best_candidate_window: dict | None = None
        # File de promotion : contrat -> {chain, first_seen, watch_signal_id}
        self._watchlist: dict[str, dict] = {}
        self._watchlist_lock = threading.Lock()

    # --- Contrôles exposés à l'interface ---
    def _is_stopping(self) -> bool:
        """
        Vrai dès qu'un STOP a été demandé. Testé en de nombreux points du cycle
        de scan pour qu'un STOP interrompe le cycle EN COURS — et surtout coupe
        toute alerte restante — au lieu d'attendre qu'il se termine. Sans ces
        tests, cliquer STOP au milieu d'un cycle laissait le bot finir
        d'enrichir et d'alerter tous les candidats déjà découverts, soit
        plusieurs dizaines de secondes d'alertes après le clic.
        """
        return self._stop_event.is_set() or not self.state.running

    def run(self):
        if self._thread and self._thread.is_alive():
            if not self._stop_event.is_set():
                logger.info("Scanner déjà en cours.")
                return
            # Un thread issu d'un STOP précédent est encore en train de terminer
            # son cycle. On lui laisse un court instant pour s'arrêter avant
            # d'en démarrer un neuf : deux boucles de scan en parallèle
            # doubleraient les alertes.
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                logger.warning(
                    "Le scan précédent ne s'est pas encore arrêté — réessaie dans un instant."
                )
                return
        self_tuning.apply_weights_to_config(self_tuning.load_weights())
        self.state.running = True
        self.state.paused = False
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        logger.info("Scanner démarré (RUN).")

    def pause(self):
        self.state.paused = True
        logger.info("Scanner en PAUSE — achats, ventes et alertes suspendus, journaux conservés.")

    def resume(self):
        self.state.paused = False
        logger.info("Scanner relancé depuis PAUSE.")

    def stop(self):
        self.state.running = False
        self._stop_event.set()
        logger.info("Scanner arrêté (STOP).")

    def set_auto_buy(self, enabled: bool):
        self.state.auto_buy = enabled

    def set_telegram(self, enabled: bool):
        self.state.telegram_enabled = enabled

    def set_watch_enabled(self, enabled: bool):
        """Active ou coupe les alertes de VEILLE (détection précoce non vérifiée)."""
        self.state.watch_enabled = enabled
        logger.info(f"Alertes de veille précoce {'activées' if enabled else 'désactivées'}.")

    def set_mode(self, mode: str):
        if mode in cfg.MODES:
            self.state.mode = mode

    def set_scan_profile(self, name: str):
        """
        Bascule le profil de scan (« quality » / « degen »). Les constantes de
        config.py sont réécrites à chaud ; le scanner les relit à chaque cycle.
        """
        applied = cfg.apply_profile(name)
        logger.info(
            f"Profil de scan : {applied.upper()} — "
            f"{'flux pump.fun coupé, découverte par traction, seul le SIGNAL vérifié est publié' if applied == 'quality' else 'flux pump.fun actif, VEILLE précoce affichée'}."
        )

    def set_min_score(self, value: int):
        low, high = cfg.MIN_SCORE_RANGE
        clamped = max(low, min(high, value))
        self.state.min_score = clamped
        logger.info(
            f"Seuil de score minimum réglé à {clamped}/100 "
            f"(veille comprise : rien en dessous de {self.watch_threshold()}/100 ne sera affiché)."
        )

    def watch_threshold(self) -> int:
        """
        Seuil effectif du niveau VEILLE.

        Par défaut (cfg.WATCH_SCORE_MARGIN == 0) il est IDENTIQUE au curseur de
        l'interface : ce que l'utilisateur règle est un plancher strict, rien en
        dessous ne s'affiche ni ne part en alerte, VEILLE comprise. Une marge
        non nulle autorise la VEILLE à descendre sous le curseur, jamais sous
        cfg.MIN_SCORE_TO_WATCH.
        """
        margin = max(0, getattr(cfg, "WATCH_SCORE_MARGIN", 0))
        if margin == 0:
            return self.state.min_score
        return max(cfg.MIN_SCORE_TO_WATCH, self.state.min_score - margin)

    def set_multichain(self, enabled: bool):
        self.state.active_chains = set(cfg.ACTIVE_CHAINS) if enabled else {"solana"}

    def set_nansen_enabled(self, enabled: bool):
        nansen.set_enabled(enabled)
        logger.info(f"Smart money Nansen {'activé' if enabled else 'désactivé'}.")

    def toggle_chain(self, chain: str, enabled: bool):
        """Active/désactive une chaîne. Une chaîne retirée de active_chains
        n'est plus ni découverte, ni vérifiée, ni alertée. On garde toujours au
        moins une chaîne active (sinon le scan tourne à vide sans que ce soit
        visible)."""
        chain = (chain or "").strip().lower()
        if chain not in cfg.AVAILABLE_CHAINS:
            logger.warning(f"Chaîne inconnue ignorée : {chain!r}.")
            return
        if enabled:
            self.state.active_chains.add(chain)
        else:
            if self.state.active_chains <= {chain}:
                logger.info(
                    f"Chaîne {chain} conservée : au moins une chaîne doit rester active."
                )
                return
            self.state.active_chains.discard(chain)
        logger.info(
            f"Chaînes actives : {', '.join(sorted(self.state.active_chains)) or 'aucune'}."
        )

    def get_watchlist(self) -> list[dict]:
        with self._watchlist_lock:
            return list(self._watchlist.values())

    # --- Boucle principale ---
    def _loop(self):
        while not self._stop_event.is_set():
            if self.state.paused:
                time.sleep(1)
                continue
            try:
                self._scan_once()
            except Exception:
                logger.exception("Erreur pendant le cycle de scan.")

            if self._stop_event.is_set():
                break  # STOP demandé pendant le cycle : on sort sans rien émettre de plus

            try:
                performance_tracker.run_check_cycle()
                performance_tracker.run_peak_cycle()
                if self.state.on_stats_update:
                    self.state.on_stats_update(performance_tracker.get_all_stats())
            except Exception:
                logger.exception("Erreur pendant le cycle de suivi de performance.")

            if self._cycle_count and self._cycle_count % cfg.LEARN_CYCLE_EVERY_N_SCANS == 0:
                try:
                    self_tuning.run_learning_cycle()
                except Exception:
                    logger.exception("Erreur pendant le cycle d'auto-correction des poids.")

            if self._cycle_count and self._cycle_count % cfg.SELF_UPGRADE_CYCLE_EVERY_N_SCANS == 0:
                try:
                    self_upgrade.run_supervision_cycle()
                except Exception:
                    logger.exception("Erreur pendant le cycle d'auto-diagnostic.")

            self._prune_watchlist()
            self._log_visibility_if_stalled()
            self._stop_event.wait(cfg.SCAN_INTERVAL_SECONDS)

    def _prune_watchlist(self):
        cutoff = time.time() - cfg.WATCH_REQUEUE_TTL_SECONDS
        with self._watchlist_lock:
            expired = [k for k, v in self._watchlist.items() if v["first_seen"] < cutoff]
            for k in expired:
                del self._watchlist[k]
            if len(self._watchlist) > cfg.WATCH_MAX_TRACKED:
                oldest = sorted(self._watchlist.items(), key=lambda kv: kv[1]["first_seen"])
                for k, _ in oldest[:len(self._watchlist) - cfg.WATCH_MAX_TRACKED]:
                    del self._watchlist[k]

    def _log_visibility_if_stalled(self):
        """
        Si aucun signal validé n'est apparu depuis 10 minutes, journalise quand
        même le meilleur candidat rencontré, pour garder une visibilité
        continue. Ligne informative uniquement, jamais une alerte.
        """
        if time.time() - self._last_signal_time < 600:
            return
        if self._best_candidate_window:
            b = self._best_candidate_window
            logger.info(
                f"Aucun signal validé depuis 10 min — meilleur candidat : "
                f"[{b['chain']}] {b['ticker']} — score {b['score']}/100 "
                f"(seuil {self.state.min_score}/100, niveau {b['tier']})."
            )
        else:
            logger.info(
                "Aucun signal validé depuis 10 min, et aucun candidat n'a passé "
                "les vérifications de sécurité sur cette période. En mode strict, "
                "c'est un résultat normal : la plupart des tokens récents sont "
                "soit dangereux, soit encore invérifiables."
            )
        self._last_signal_time = time.time()
        self._best_candidate_window = None

    # --- Cycle de scan ---
    def _scan_once(self):
        self._cycle_count += 1
        try:
            db.bump_cycle_counter("recap_cycle_count")
        except Exception:
            logger.debug("Compteur de cycles (recap) non incrémenté.", exc_info=True)
        # Ordre de priorité : les chaînes EVM ciblées (BNB Chain, Base,
        # Robinhood) sont traitées AVANT Solana — cf. cfg.CHAIN_SCAN_PRIORITY.
        # active_chains est un set : sans ce tri, l'ordre serait aléatoire d'un
        # lancement à l'autre.
        _prio = getattr(cfg, "CHAIN_SCAN_PRIORITY", [])
        _ordered = sorted(
            self.state.active_chains,
            key=lambda c: (_prio.index(c) if c in _prio else len(_prio), c),
        )
        for chain in _ordered:
            if self._is_stopping():
                return
            multiplier = cfg.CHAIN_SCAN_MULTIPLIER.get(chain, 1)
            if self._cycle_count % multiplier != 0:
                continue

            module = CHAIN_MODULES.get(chain)
            if module is None:
                continue
            try:
                candidates = module.discover_candidates()
            except Exception:
                logger.exception(f"Erreur de découverte sur {chain}.")
                continue

            self_upgrade.record_cycle_candidates(chain, len(candidates))
            self._process_chain(chain, module, candidates)

    def _process_chain(self, chain: str, module, candidates: list[dict]):
        stats = {"seen": 0, "prefiltered": 0, "rejected": 0, "below": 0, "watch": 0, "signal": 0}

        fresh: list[dict] = []
        for candidate in candidates:
            stats["seen"] += 1
            candidate["chain"] = chain
            contract = candidate.get("contract")
            if not contract:
                continue
            # Un token déjà publié en SIGNAL n'est pas republié. Un token déjà
            # publié en VEILLE, lui, reste candidat à la promotion : c'est tout
            # l'intérêt de la file de suivi.
            if db.has_recent_signal(chain, contract, tier=cfg.TIER_SIGNAL):
                continue

            # --- Préfiltre marché, sans aucun appel réseau ---
            # Un cycle Solana peut remonter 200 candidats. Les enrichir tous
            # coûterait un appel GoPlus, plusieurs appels RPC et un appel
            # RugCheck chacun, soit plus d'un millier de requêtes pour un cycle
            # de 45 secondes — ce qui faisait répondre 429 au RPC public. Or la
            # plupart de ces candidats sont éliminables immédiatement sur des
            # données déjà en main : liquidité, volume, âge, capitalisation.
            market_vetoes = evaluate_market_prefilter(candidate)
            if market_vetoes:
                stats["prefiltered"] += 1
                logger.info(
                    f"[{chain}] {candidate.get('ticker', '?')} écarté avant vérification — "
                    f"{market_vetoes[0]}"
                )
                continue

            fresh.append(candidate)

        if not fresh:
            if stats["seen"]:
                logger.info(
                    f"[{chain}] bilan du cycle : {stats['seen']} candidat(s), "
                    f"{stats['prefiltered']} écarté(s) sur critères de marché, "
                    f"aucun à vérifier."
                )
            return

        # Ordre de vérification + plafond de cycle (voir order_for_verification).
        cap = cfg.CHAIN_MAX_ENRICHMENTS_PER_CYCLE.get(chain, cfg.MAX_ENRICHMENTS_PER_CYCLE)
        fresh, dropped = order_for_verification(fresh, cap)
        if dropped:
            logger.info(
                f"[{chain}] {dropped} candidat(s) reporté(s) au prochain cycle "
                f"(plafond de {cap} vérifications par cycle ; les tokens de moins de "
                f"{getattr(cfg, 'FIRST_MINUTE_WINDOW_MINUTES', 0)} min passent en tête)."
            )

        if self._is_stopping():
            return

        try:
            # should_stop est passé à l'enrichissement pour qu'un STOP interrompe
            # la boucle de vérification EN COURS. Sans lui, un cycle Solana avec
            # le RPC public en 429 (throttle jusqu'à 3 s/appel × 25 candidats)
            # bloquait plusieurs minutes après le clic STOP — et le RUN suivant
            # refusait de démarrer tant que ce thread n'avait pas fini.
            fresh = module.enrich_batch(fresh, should_stop=self._is_stopping) if hasattr(module, "enrich_batch") else [
                module.enrich_with_security(c) for c in fresh
            ]
        except Exception:
            logger.exception(f"Erreur d'enrichissement sur {chain}.")
            return

        if self._is_stopping():
            return  # STOP demandé pendant l'enrichissement : on n'émet plus rien

        for candidate in fresh:
            if self._is_stopping():
                return  # STOP demandé : on n'émet plus aucune alerte
            try:
                self._process_candidate(chain, candidate, stats)
            except Exception:
                logger.exception(f"Erreur de traitement d'un candidat sur {chain}.")

        if stats["seen"]:
            logger.info(
                f"[{chain}] bilan du cycle : {stats['seen']} candidat(s), "
                f"{stats['prefiltered']} écarté(s) sur critères de marché, "
                f"{stats['rejected']} rejeté(s) sécurité, {stats['below']} sous le seuil, "
                f"{stats['watch']} en veille, {stats['signal']} signal(aux) validé(s)."
            )

    def _process_candidate(self, chain: str, candidate: dict, stats: dict):
        ticker = candidate.get("ticker", "?")
        contract = candidate.get("contract")
        scored = compute_score(candidate)

        # --- 1. Veto de sécurité : définitif ---
        if scored["rejected"]:
            stats["rejected"] += 1
            with self._watchlist_lock:
                self._watchlist.pop(contract, None)
            logger.info(
                f"[{chain}] {ticker} REJETÉ — {'; '.join(scored['reasons'][:3]) or 'raison inconnue'}"
            )
            return

        score = scored["score"]
        tier = scored["tier"]

        if not self._best_candidate_window or score > self._best_candidate_window["score"]:
            self._best_candidate_window = {"ticker": ticker, "chain": chain, "score": score, "tier": tier}

        # --- 2. Niveau VEILLE : information précoce, sans engagement ---
        if tier == cfg.TIER_WATCH:
            self._handle_watch(chain, candidate, scored, stats)
            return

        # --- 3. Niveau SIGNAL : entièrement vérifié ---
        if score < self.state.min_score:
            stats["below"] += 1
            logger.info(f"[{chain}] {ticker} vérifié mais sous le seuil — {score}/100 < {self.state.min_score}/100")
            return

        # Vérification « DEX Paid », faite seulement maintenant : elle coûte un
        # appel réseau par token et n'a d'intérêt que sur les candidats qui ont
        # déjà tout passé. La faire à l'enrichissement revenait à dépenser
        # jusqu'à 40 requêtes par cycle et par chaîne pour rien.
        if candidate.get("dex_paid") is None:
            candidate["dex_paid"] = dexscreener.check_dex_paid(chain, contract)
            if candidate["dex_paid"] is True:
                stats["rejected"] += 1
                logger.info(f"[{chain}] {ticker} REJETÉ — déjà DEX Paid, hors cible.")
                return

        # Réputation du wallet déployeur (BSC/EVM) : un appel réseau par token,
        # donc réservé aux candidats qui ont déjà tout passé. À 10 minutes de
        # vie, c'est le seul signal qui distingue un runner d'une usine à rug.
        if cfg.CHECK_CREATOR_REPUTATION and chain != "solana":
            sec = candidate.get("security") or {}
            creator = sec.get("creator_address") or sec.get("owner_address")
            if creator and candidate.get("creator_reputation") is None:
                candidate["creator_reputation"] = goplus.normalize_address_reputation(
                    goplus.check_evm_address_security(chain, creator)
                )
            veto = creator_reputation_veto(candidate)
            if veto:
                stats["rejected"] += 1
                with self._watchlist_lock:
                    self._watchlist.pop(contract, None)
                logger.info(f"[{chain}] {ticker} REJETÉ — {veto}")
                return

        risk_plan = compute_risk_plan(scored, mode=self.state.mode)
        if "error" in risk_plan:
            logger.info(f"[{chain}] {ticker} ignoré — {risk_plan['error']}")
            return

        if self._is_stopping():
            return  # STOP demandé entre-temps : ni enregistrement ni alerte

        promoted_from_watch = False
        with self._watchlist_lock:
            entry = self._watchlist.pop(contract, None)
        if entry and entry.get("watch_signal_id"):
            db.promote_signal_to_verified(entry["watch_signal_id"])
            promoted_from_watch = True

        signal = {
            **scored,
            **risk_plan,
            "risk_level": self._risk_level(score),
            "tier": cfg.TIER_SIGNAL,
            "verified": True,
            "promoted_from_watch": promoted_from_watch,
            "created_at": time.time(),
        }
        signal["id"] = db.insert_signal(signal)
        try:
            db.bump_cycle_counter("recap_signals_emitted")
        except Exception:
            logger.debug("Compteur de signaux (recap) non incrémenté.", exc_info=True)

        stats["signal"] += 1
        prefix = "PROMU EN SIGNAL" if promoted_from_watch else "SIGNAL VÉRIFIÉ"
        logger.info(f"[{chain}] ✔ {prefix} {ticker} — score {score}/100")
        self._last_signal_time = time.time()
        self._best_candidate_window = None

        if self.state.on_new_signal and not self._is_stopping():
            self.state.on_new_signal(signal)

        if self.state.telegram_enabled and not self.state.paused and not self._is_stopping():
            for err in send_alert(signal):
                logger.warning(err)

        if self.state.auto_buy and not self.state.paused and not self._is_stopping():
            logger.info(
                f"AUTO BUY activé mais exécution d'ordre non implémentée par sécurité — "
                f"signal {ticker} journalisé pour confirmation manuelle."
            )

    def _handle_watch(self, chain: str, candidate: dict, scored: dict, stats: dict):
        """
        Publie une alerte précoce NON VÉRIFIÉE et met le token en file de
        promotion. Aucun plan de risque n'est calculé : ce niveau ne recommande
        rien, il informe.
        """
        contract = candidate.get("contract")
        ticker = candidate.get("ticker", "?")
        score = scored["score"]

        with self._watchlist_lock:
            already_tracked = contract in self._watchlist

        # Le seuil de veille SUIT le curseur de l'interface, sans exception : ce
        # que l'utilisateur règle est un plancher strict. Rien sous le curseur
        # ne s'affiche ni ne part en alerte, VEILLE comprise — y compris les
        # détections « première minute », dont le score est bas par nature.
        # Pour les voir, on baisse le curseur (il descend jusqu'à 30/100), on ne
        # laisse pas le bot le contourner en douce.
        if score < self.watch_threshold():
            stats["below"] += 1
            logger.debug(
                f"[{chain}] {ticker} en veille mais sous le seuil — "
                f"{score}/100 < {self.watch_threshold()}/100"
            )
            return

        if not self.state.watch_enabled or not getattr(cfg, "PUBLISH_WATCH_ALERTS", True):
            # Alertes de veille coupées (bouton VEILLE de l'UI, ou profil
            # « quality » qui ne montre QUE du SIGNAL vérifié). Le suivi
            # continue : le token pourra quand même être promu en SIGNAL.
            with self._watchlist_lock:
                self._watchlist.setdefault(contract, {
                    "chain": chain, "ticker": ticker,
                    "first_seen": time.time(), "watch_signal_id": None,
                })
            return

        if already_tracked or db.has_recent_signal(chain, contract, tier=cfg.TIER_WATCH):
            return  # déjà annoncé en veille, on n'en refait pas une alerte

        if self._is_stopping():
            return  # STOP demandé : pas de nouvelle alerte de veille

        watch_entry = {
            **scored,
            "tier": cfg.TIER_WATCH,
            "verified": False,
            "risk_level": "Non vérifié",
            "entry_price": None,   # aucun plan d'entrée : rien n'est validé
            "stop_loss": None,
            "tp1": None, "tp2": None, "tp3": None,
            "created_at": time.time(),
        }
        watch_entry["id"] = db.insert_signal(watch_entry)
        try:
            db.bump_cycle_counter("recap_watch_emitted")
        except Exception:
            logger.debug("Compteur de veilles (recap) non incrémenté.", exc_info=True)

        with self._watchlist_lock:
            self._watchlist[contract] = {
                "chain": chain, "ticker": ticker,
                "first_seen": time.time(), "watch_signal_id": watch_entry["id"],
            }

        stats["watch"] += 1
        missing = "; ".join(scored.get("missing_checks", [])[:2]) or "vérifications incomplètes"
        logger.info(f"[{chain}] 👁 VEILLE {ticker} — score {score}/100, NON VÉRIFIÉ ({missing})")

        if self.state.on_new_signal and not self._is_stopping():
            self.state.on_new_signal(watch_entry)

        if self.state.telegram_enabled and not self.state.paused and not self._is_stopping():
            for err in send_alert(watch_entry):
                logger.warning(err)

    @staticmethod
    def _risk_level(score: float) -> str:
        if score >= 90:
            return "Faible"
        if score >= 75:
            return "Modéré"
        return "Élevé"
