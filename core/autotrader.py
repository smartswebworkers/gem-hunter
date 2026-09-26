"""
core/autotrader.py — AUTO BUY en SIMULATION (Solana / pump.fun)
Destination finale : gem_hunter/core/autotrader.py

Ce que fait le bouton AUTO BUY quand il est ON :
  1. dès qu'un token Solana passe TOUT le pipeline d'alerte (vetos de sécurité, porte
     d'importance) et déclenche une alerte, une position de `montant` dollars est
     « achetée » au prix courant de sa bonding curve ;
  2. une surveillance relit le prix des positions ouvertes toutes les
     AUTOBUY_MONITOR_SECONDS et « revend » 100 % dès que la valeur NETTE de frais
     atteint AUTOBUY_TAKE_PROFIT_X (x2) fois la mise.

C'est de la SIMULATION : aucune transaction n'est signée ni envoyée, aucune clé de
wallet n'est lue. Les positions et leur résultat sont enregistrés (table
`autobuy_positions`) et journalisés, ce qui permet de mesurer ce que la stratégie
aurait donné avant d'y engager un centime. L'exécution réelle n'est PAS implémentée :
elle engage de l'argent réel et reste une décision de l'utilisateur (voir
CORRECTIFS.md, Mise à jour 40).

Les prix viennent de l'état on-chain de la bonding curve (SOL virtuel / jetons
virtuels) pour un token encore en curve, de DexScreener après migration.
"""
import logging
import math
import queue
import threading
import time

import config as cfg
from data_sources import dexscreener, solana_rpc
from storage import db

logger = logging.getLogger("gem_hunter.autotrader")

_AMOUNT_STATE_KEY = "autobuy_amount_usd"
MODE = "simulation"


class AutoTrader:
    def __init__(self):
        self._enabled = False
        self._queue: queue.Queue = queue.Queue()
        self._threads_started = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ réglages
    def mode(self) -> str:
        return MODE

    def is_enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> str:
        """Active/coupe l'auto buy. Renvoie le mode (« simulation »)."""
        self._enabled = bool(enabled)
        if self._enabled:
            stale = db.mark_stale_buying()
            if stale:
                logger.warning(f"AUTO BUY : {stale} achat(s) interrompu(s) marqué(s) « unknown ».")
            self._ensure_threads()
            logger.info(
                f"AUTO BUY ACTIVÉ en SIMULATION (aucune transaction réelle) — "
                f"{self.get_amount_usd():.2f} $ par token, revente automatique à "
                f"x{cfg.AUTOBUY_TAKE_PROFIT_X:g}."
            )
        else:
            logger.info("AUTO BUY coupé : plus d'achat ni de revente automatique "
                        "(les positions ouvertes sont conservées).")
        return MODE

    def get_amount_usd(self) -> float:
        raw = db.get_state(_AMOUNT_STATE_KEY, None)
        try:
            return self._clamp_amount(float(raw)) if raw is not None else self._clamp_amount(cfg.AUTOBUY_AMOUNT_USD)
        except (TypeError, ValueError):
            return self._clamp_amount(cfg.AUTOBUY_AMOUNT_USD)

    def set_amount_usd(self, value) -> float:
        """
        Capital par achat, choisi à la main. Borné entre AUTOBUY_MIN_USD (1 $) et
        AUTOBUY_MAX_USD ; une valeur invalide est ignorée. Conservé entre les
        lancements. Renvoie la valeur réellement retenue.
        """
        try:
            v = float(value)
            if not math.isfinite(v):
                raise ValueError
        except (TypeError, ValueError):
            return self.get_amount_usd()
        effective = self._clamp_amount(v)
        db.set_state(_AMOUNT_STATE_KEY, effective)
        logger.info(f"AUTO BUY : capital par achat réglé à {effective:.2f} $.")
        return effective

    @staticmethod
    def _clamp_amount(v: float) -> float:
        return max(cfg.AUTOBUY_MIN_USD, min(cfg.AUTOBUY_MAX_USD, round(float(v), 2)))

    # ------------------------------------------------------------------ décision d'achat
    def consider(self, chain: str, candidate: dict, tier: str) -> str:
        """
        Appelé par le scanner APRÈS l'émission d'une alerte. Renvoie la raison de la
        décision (pour les tests et les journaux) : « queued » si un achat est planifié.
        Un token n'arrive ici que s'il a passé tous les vetos et la porte d'importance.
        """
        if not self._enabled:
            return "off"
        if chain != "solana":
            return "chain"
        mint = candidate.get("contract")
        if not mint:
            return "no_mint"
        # VEILLE : uniquement les créations fraîches PumpPortal (« acheté dès sa
        # création »). Un SIGNAL vérifié, lui, est toujours éligible.
        if tier == cfg.TIER_WATCH and candidate.get("source") != "pumpportal":
            return "not_fresh"

        if len(db.get_positions(status="open", simulated=True)) >= cfg.AUTOBUY_MAX_OPEN:
            return "max_open"
        amount = self.get_amount_usd()
        spent = db.spent_usd_since(time.time() - 86400, simulated=True)
        if spent + amount > cfg.AUTOBUY_MAX_SPENT_USD_PER_DAY + 1e-9:
            return "daily_cap"

        self._ensure_threads()
        self._queue.put({"chain": chain, "candidate": dict(candidate), "amount_usd": amount})
        return "queued"

    # ------------------------------------------------------------------ prix
    def _sol_price(self) -> float | None:
        return dexscreener.get_sol_price_usd()

    def _curve_states(self, curve_keys: list[str]) -> dict:
        try:
            return solana_rpc.probe_curves(curve_keys)
        except Exception:
            logger.exception("AUTO BUY : lecture des curves impossible.")
            return {}

    def _dex_price_usd(self, mint: str) -> float | None:
        try:
            pair = dexscreener.get_best_pair("solana", mint)
        except Exception:
            return None
        return (pair or {}).get("price_usd")

    # ------------------------------------------------------------------ achat simulé
    def _execute_buy(self, job: dict) -> str:
        cand = job["candidate"]
        mint = cand["contract"]
        ticker = cand.get("ticker") or "?"
        curve_key = cand.get("bonding_curve")
        amount_usd = job["amount_usd"]

        position_id = db.create_position("solana", mint, ticker, curve_key, simulated=True,
                                         amount_usd=amount_usd)
        if position_id is None:
            return "duplicate"   # déjà acheté (ou tenté) : jamais deux fois

        sol_price = self._sol_price()
        if not sol_price:
            db.update_position(position_id, status="failed", note="prix SOL/USD indisponible")
            logger.warning(f"AUTO BUY : {ticker} non acheté (prix SOL/USD indisponible).")
            return "no_sol_price"

        fee = cfg.AUTOBUY_FEE_PCT / 100.0
        amount_sol = amount_usd / sol_price
        spot_sol = None
        if curve_key:
            spot_sol = solana_rpc.spot_price_sol(self._curve_states([curve_key]).get(curve_key))
        if spot_sol:
            tokens = amount_sol * (1 - fee) / spot_sol
            entry_price_sol = amount_sol / tokens
            entry_price_usd = entry_price_sol * sol_price
        else:
            price_usd = self._dex_price_usd(mint)
            if not price_usd:
                db.update_position(position_id, status="failed", note="prix du token illisible")
                logger.warning(f"AUTO BUY : {ticker} non acheté (prix illisible).")
                return "no_price"
            tokens = amount_usd * (1 - fee) / price_usd
            entry_price_usd = amount_usd / tokens
            entry_price_sol = entry_price_usd / sol_price

        db.update_position(
            position_id, status="open", sol_price_usd=sol_price, cost_sol=amount_sol, tokens=tokens,
            entry_price_sol=entry_price_sol, entry_price_usd=entry_price_usd,
            buy_sig=f"SIM-BUY-{position_id}",
        )
        logger.info(f"AUTO BUY (simulation) : achat {ticker} — {amount_usd:.2f} $ "
                    f"({amount_sol:.5f} SOL), cible x{cfg.AUTOBUY_TAKE_PROFIT_X:g}.")
        return "bought"

    # ------------------------------------------------------------------ surveillance et revente
    def _multiple(self, pos: dict, curve_states: dict, sol_price: float | None) -> tuple[float | None, float | None]:
        """
        (multiple net de frais, valeur actuelle en SOL) d'une position, ou (None, None)
        si le prix est illisible. Token en curve : prix spot de la curve. Migré ou curve
        illisible : DexScreener.
        """
        fee = cfg.AUTOBUY_FEE_PCT / 100.0
        tokens = pos.get("tokens") or 0
        cost_sol = pos.get("cost_sol") or 0
        if not tokens or not cost_sol:
            return None, None
        state = curve_states.get(pos.get("curve_key") or "")
        if state and not state.get("complete"):
            spot = solana_rpc.spot_price_sol(state)
            if spot:
                value_sol = tokens * spot * (1 - fee)
                return value_sol / cost_sol, value_sol
        price_usd = self._dex_price_usd(pos["mint"])
        entry_usd = pos.get("entry_price_usd")
        if price_usd and entry_usd and sol_price:
            value_sol = tokens * price_usd * (1 - fee) / sol_price
            return value_sol / cost_sol, value_sol
        return None, None

    def monitor_once(self) -> list[dict]:
        """
        Relit le prix des positions ouvertes (un appel RPC groupé) et revend celles qui
        ont atteint x2. Ne fait rien si le bouton est OFF. Renvoie les positions vendues.
        """
        if not self._enabled:
            return []
        open_positions = db.get_positions(status="open", simulated=True)
        if not open_positions:
            return []
        keys = [p["curve_key"] for p in open_positions if p.get("curve_key")]
        states = self._curve_states(keys) if keys else {}
        sol_price = self._sol_price()

        sold = []
        for pos in open_positions:
            multiple, value_sol = self._multiple(pos, states, sol_price)
            if multiple is None or multiple < cfg.AUTOBUY_TAKE_PROFIT_X:
                continue
            db.update_position(
                pos["id"], status="closed", closed_at=time.time(), proceeds_sol=value_sol,
                sell_sig=f"SIM-SELL-{pos['id']}", note=f"revente auto à x{multiple:.2f}",
            )
            logger.info(
                f"AUTO BUY (simulation) : VENTE {pos.get('ticker') or '?'} à x{multiple:.2f} — "
                f"{pos['amount_usd']:.2f} $ -> ~{pos['amount_usd'] * multiple:.2f} $."
            )
            sold.append({**pos, "multiple": multiple})
        return sold

    # ------------------------------------------------------------------ threads
    def _ensure_threads(self):
        with self._lock:
            if self._threads_started:
                return
            self._threads_started = True
        threading.Thread(target=self._worker_loop, daemon=True, name="autobuy-worker").start()
        threading.Thread(target=self._monitor_loop, daemon=True, name="autobuy-monitor").start()

    def _worker_loop(self):
        while True:
            job = self._queue.get()
            try:
                if self._enabled:
                    self._execute_buy(job)
            except Exception:
                logger.exception("AUTO BUY : erreur pendant l'achat.")

    def _monitor_loop(self):
        while True:
            time.sleep(max(0.5, float(cfg.AUTOBUY_MONITOR_SECONDS)))
            try:
                self.monitor_once()
            except Exception:
                logger.exception("AUTO BUY : erreur pendant la surveillance.")

    # ------------------------------------------------------------------ état pour l'interface
    def summary(self) -> dict:
        positions = db.get_positions(simulated=True)
        closed = [p for p in positions if p["status"] == "closed"]
        return {
            "mode": MODE,
            "enabled": self._enabled,
            "amount_usd": self.get_amount_usd(),
            "open": sum(1 for p in positions if p["status"] == "open"),
            "closed": len(closed),
        }
