"""
storage/db.py — Persistance SQLite
Destination finale : gem_hunter/storage/db.py

AJOUTS DE CETTE RÉVISION :
  - colonnes `tier` (veille / signal) et `verified`, pour que l'interface et
    Telegram ne puissent jamais présenter une alerte précoce non vérifiée
    comme un signal validé,
  - colonne `vetoes`, qui conserve la trace des rejets pour audit,
  - get_rug_stats(), qui expose le taux de pertes totales — la métrique que le
    bot était structurellement incapable de mesurer auparavant,
  - migration automatique et non destructive des bases existantes (l'ancienne
    table est conservée telle quelle, les colonnes manquantes sont ajoutées).
"""
import sqlite3
import json
import time
from contextlib import contextmanager

from config import DB_PATH


SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chain TEXT NOT NULL,
    contract TEXT NOT NULL,
    ticker TEXT,
    name TEXT,
    market_cap REAL,
    liquidity REAL,
    holders INTEGER,
    score REAL,
    reasons TEXT,          -- JSON list
    features TEXT,         -- JSON dict des features de scoring (auto-correction)
    risk_level TEXT,
    entry_price REAL,
    stop_loss REAL,
    tp1 REAL,
    tp2 REAL,
    tp3 REAL,
    status TEXT DEFAULT 'active',   -- active | dismissed | bought | expired
    created_at REAL,
    UNIQUE(chain, contract, created_at)
);

CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS performance_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id INTEGER NOT NULL,
    horizon TEXT NOT NULL,          -- '1h' | '6h' | '24h'
    entry_price REAL,
    price_at_check REAL,
    return_pct REAL,
    checked_at REAL,
    UNIQUE(signal_id, horizon)
);

CREATE INDEX IF NOT EXISTS idx_signals_lookup ON signals(chain, contract, created_at);
CREATE INDEX IF NOT EXISTS idx_perf_horizon ON performance_checks(horizon, checked_at);
"""

# Colonnes ajoutées après coup, appliquées par migration sur les bases existantes.
MIGRATIONS = [
    ("signals", "tier", "TEXT DEFAULT 'signal'"),
    ("signals", "verified", "INTEGER DEFAULT 1"),
    ("signals", "vetoes", "TEXT"),
    ("signals", "missing_checks", "TEXT"),
    # Suivi du pic pour le recap narratif (core/signal_recap.py) : la meilleure
    # capitalisation atteinte depuis la détection, l'instant où elle l'a été, et
    # le rendement correspondant. Alimentées par performance_tracker.run_peak_cycle().
    ("signals", "peak_market_cap", "REAL"),
    ("signals", "peak_price", "REAL"),
    ("signals", "peak_return_pct", "REAL"),
    ("signals", "peak_at", "REAL"),
    ("signals", "peak_checks", "INTEGER DEFAULT 0"),
    # CORRECTIF — l'âge affiché (carte + fiche détail) confondait deux notions :
    # `created_at` (quand NOUS avons détecté/enregistré ce candidat) et l'âge
    # RÉEL du token/de la pool sur la chaîne. La table ne stockait que la
    # première ; un token détecté avec du retard (backlog de scan, plusieurs
    # cycles avant qu'il n'atteigne le sommet de la file d'enrichissement)
    # affichait donc « 2 min » alors qu'il avait déjà 20+ minutes sur un
    # explorateur. `pair_created_at` (epoch MILLISECONDES, même convention que
    # partout ailleurs dans le code — voir core/scanner._passes_age_filter)
    # est désormais persisté pour que l'interface affiche l'âge réel, pas
    # celui de notre propre détection.
    ("signals", "pair_created_at", "REAL"),
]


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    # Le scanner (thread daemon) et l'interface Qt ouvrent chacun leurs propres
    # connexions vers le même fichier. Le mode WAL autorise des lectures
    # concurrentes pendant une écriture ; busy_timeout donne une marge.
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=10000;")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        _apply_migrations(conn)


def _apply_migrations(conn):
    """
    Ajoute les colonnes manquantes sans jamais toucher aux données existantes.
    SQLite n'a pas d'ADD COLUMN IF NOT EXISTS : on lit d'abord le schéma réel.
    """
    for table, column, definition in MIGRATIONS:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def insert_signal(signal: dict) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT OR IGNORE INTO signals
            (chain, contract, ticker, name, market_cap, liquidity, holders,
             score, reasons, features, risk_level, entry_price, stop_loss, tp1, tp2, tp3,
             status, created_at, tier, verified, vetoes, missing_checks, pair_created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                signal["chain"], signal["contract"], signal.get("ticker"),
                signal.get("name"), signal.get("market_cap"), signal.get("liquidity"),
                signal.get("holders"), signal.get("score"),
                json.dumps(signal.get("reasons", []), ensure_ascii=False),
                json.dumps(signal.get("features", {}), ensure_ascii=False),
                signal.get("risk_level"), signal.get("entry_price"),
                signal.get("stop_loss"), signal.get("tp1"), signal.get("tp2"),
                signal.get("tp3"), signal.get("status", "active"), time.time(),
                signal.get("tier", "signal"),
                1 if signal.get("verified", True) else 0,
                json.dumps(signal.get("vetoes", []), ensure_ascii=False),
                json.dumps(signal.get("missing_checks", []), ensure_ascii=False),
                signal.get("pair_created_at"),
            ),
        )
        return cur.lastrowid


def has_recent_signal(chain: str, contract: str, within_seconds: float = 21600,
                      tier: str | None = None) -> bool:
    """
    Vrai s'il existe déjà une entrée pour ce couple chaîne/contrat dans les
    `within_seconds` dernières secondes (défaut 6h).

    Le paramètre `tier` est important : un token déjà signalé en VEILLE doit
    pouvoir être re-signalé s'il est PROMU en SIGNAL, sinon la promotion —
    c'est-à-dire précisément le moment où le token devient intéressant et
    vérifié — serait avalée par le dédoublonnage.
    """
    cutoff = time.time() - within_seconds
    query = "SELECT 1 FROM signals WHERE chain = ? AND contract = ? AND created_at >= ?"
    params: list = [chain, contract, cutoff]
    if tier is not None:
        query += " AND tier = ?"
        params.append(tier)
    query += " LIMIT 1"

    with get_conn() as conn:
        return conn.execute(query, params).fetchone() is not None


def _row_to_dict(row) -> dict:
    d = dict(row)
    for key in ("reasons", "features", "vetoes", "missing_checks"):
        if key in d:
            try:
                d[key] = json.loads(d[key]) if d.get(key) else ([] if key != "features" else {})
            except (TypeError, ValueError):
                d[key] = [] if key != "features" else {}
    if "verified" in d:
        d["verified"] = bool(d["verified"])
    return d


def get_active_signals(tier: str | None = None):
    query = "SELECT * FROM signals WHERE status = 'active'"
    params: list = []
    if tier is not None:
        query += " AND tier = ?"
        params.append(tier)
    query += " ORDER BY created_at DESC"

    with get_conn() as conn:
        return [_row_to_dict(r) for r in conn.execute(query, params).fetchall()]


def promote_signal_to_verified(signal_id: int):
    """Marque une entrée de VEILLE comme promue, pour ne pas la ré-alerter."""
    with get_conn() as conn:
        conn.execute("UPDATE signals SET status = 'promoted' WHERE id = ?", (signal_id,))


def dismiss_signal(signal_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE signals SET status = 'dismissed' WHERE id = ?", (signal_id,))


def delete_signal_permanently(signal_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM signals WHERE id = ?", (signal_id,))


def set_state(key: str, value):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO app_state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )


def get_state(key: str, default=None):
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default


def get_signals_pending_check(horizon: str, min_age_seconds: float) -> list[dict]:
    """
    Signaux assez anciens pour cet horizon et pas encore vérifiés pour lui.
    Inclut les entrées dismissed : la performance se suit même si l'utilisateur
    a retiré la carte de l'écran, sinon l'échantillon d'apprentissage serait à
    nouveau filtré par un biais, cette fois celui de l'attention.
    """
    cutoff = time.time() - min_age_seconds
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT s.* FROM signals s
               WHERE s.created_at <= ?
                 AND s.entry_price IS NOT NULL
                 AND NOT EXISTS (
                     SELECT 1 FROM performance_checks p
                     WHERE p.signal_id = s.id AND p.horizon = ?
                 )
               ORDER BY s.created_at ASC
               LIMIT 200""",
            (cutoff, horizon),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def record_performance_check(signal_id: int, horizon: str, entry_price: float,
                             price_at_check: float, return_pct: float):
    with get_conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO performance_checks
               (signal_id, horizon, entry_price, price_at_check, return_pct, checked_at)
               VALUES (?,?,?,?,?,?)""",
            (signal_id, horizon, entry_price, price_at_check, return_pct, time.time()),
        )


def get_performance_stats(horizon: str, lookback_seconds: float | None = None) -> dict:
    """{count, win_rate_pct, avg_return_pct, rug_count} pour un horizon donné."""
    query = "SELECT return_pct FROM performance_checks WHERE horizon = ?"
    params: list = [horizon]
    if lookback_seconds is not None:
        query += " AND checked_at >= ?"
        params.append(time.time() - lookback_seconds)

    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()

    returns = [r["return_pct"] for r in rows if r["return_pct"] is not None]
    count = len(returns)
    if count == 0:
        return {"count": 0, "win_rate_pct": None, "avg_return_pct": None, "rug_count": 0}

    wins = sum(1 for r in returns if r > 0)
    rugs = sum(1 for r in returns if r <= -99)
    return {
        "count": count,
        "win_rate_pct": round((wins / count) * 100, 1),
        "avg_return_pct": round(sum(returns) / count, 2),
        "rug_count": rugs,
        "rug_rate_pct": round((rugs / count) * 100, 1),
    }


def get_rug_stats() -> dict:
    """
    Taux de pertes totales, tous horizons confondus. C'est la métrique de
    qualité la plus honnête du bot : un taux de réussite flatteur accompagné
    d'un taux de rug élevé signale un filtre qui laisse passer des pièges.
    """
    with get_conn() as conn:
        rows = conn.execute("SELECT return_pct FROM performance_checks").fetchall()
    returns = [r["return_pct"] for r in rows if r["return_pct"] is not None]
    if not returns:
        return {"checked": 0, "rug_count": 0, "rug_rate_pct": None}
    rugs = sum(1 for r in returns if r <= -99)
    return {
        "checked": len(returns),
        "rug_count": rugs,
        "rug_rate_pct": round((rugs / len(returns)) * 100, 1),
    }


def get_signal(signal_id: int) -> dict | None:
    """Une entrée signal/veille par son id, ou None si elle n'existe plus."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM signals WHERE id = ?", (signal_id,)).fetchone()
    return _row_to_dict(row) if row else None


def update_peak(signal_id: int, price: float | None, market_cap: float | None,
                return_pct: float | None):
    """
    Met à jour les colonnes de pic si `return_pct` dépasse le maximum déjà
    enregistré (ou s'il n'y en avait pas). Incrémente toujours peak_checks pour
    que le recap sache combien de relevés ont été faits.

    Le pic est indexé sur le rendement, pas sur la capitalisation brute : c'est
    la seule grandeur comparable d'un token à l'autre, et c'est elle qui donne
    le « +Y% » du recap. market_cap et price sont conservés pour l'affichage.
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT peak_return_pct FROM signals WHERE id = ?", (signal_id,)
        ).fetchone()
        if row is None:
            return
        current_peak = row["peak_return_pct"]
        is_new_peak = return_pct is not None and (
            current_peak is None or return_pct > current_peak
        )
        if is_new_peak:
            conn.execute(
                """UPDATE signals SET
                   peak_return_pct = ?, peak_market_cap = ?, peak_price = ?,
                   peak_at = ?, peak_checks = COALESCE(peak_checks, 0) + 1
                   WHERE id = ?""",
                (return_pct, market_cap, price, time.time(), signal_id),
            )
        else:
            conn.execute(
                "UPDATE signals SET peak_checks = COALESCE(peak_checks, 0) + 1 WHERE id = ?",
                (signal_id,),
            )


def bump_cycle_counter(key: str = "recap_cycle_count", by: int = 1) -> int:
    """
    Compteur cumulatif stocké dans app_state. Sert au recap pour exprimer la
    rareté d'un signal (« 1 signal validé sur N cycles de scan »).
    """
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
        current = 0
        if row:
            try:
                current = int(json.loads(row["value"]))
            except (TypeError, ValueError):
                current = 0
        new_value = current + by
        conn.execute(
            "INSERT INTO app_state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(new_value)),
        )
        return new_value


def get_cycle_stats() -> dict:
    """{scan_cycles, signals_emitted, watch_emitted} — corpus pour la rareté."""
    def _read(key: str) -> int:
        with get_conn() as conn:
            row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
        if not row:
            return 0
        try:
            return int(json.loads(row["value"]))
        except (TypeError, ValueError):
            return 0

    return {
        "scan_cycles": _read("recap_cycle_count"),
        "signals_emitted": _read("recap_signals_emitted"),
        "watch_emitted": _read("recap_watch_emitted"),
    }


def get_cohort_stats(tier: str | None = None, risk_level: str | None = None) -> dict:
    """
    Moyenne et meilleur pic de rendement des signaux comparables (même niveau,
    même risque). Le recap s'en sert pour situer un token dans son cohorte :
    « le pic moyen d'un signal de ce type est +X% ».

    Ne compte que les entrées ayant au moins un relevé de pic (peak_checks > 0),
    pour ne pas diluer la moyenne avec des signaux trop récents pour être jugés.
    """
    query = (
        "SELECT peak_return_pct FROM signals "
        "WHERE peak_checks > 0 AND peak_return_pct IS NOT NULL"
    )
    params: list = []
    if tier is not None:
        query += " AND tier = ?"
        params.append(tier)
    if risk_level is not None:
        query += " AND risk_level = ?"
        params.append(risk_level)

    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()

    peaks = [r["peak_return_pct"] for r in rows if r["peak_return_pct"] is not None]
    if not peaks:
        return {"count": 0, "avg_peak_return_pct": None, "best_peak_return_pct": None}
    return {
        "count": len(peaks),
        "avg_peak_return_pct": round(sum(peaks) / len(peaks), 1),
        "best_peak_return_pct": round(max(peaks), 1),
    }


def get_signals_for_peak_tracking(max_age_seconds: float) -> list[dict]:
    """
    Signaux encore « vivants » à suivre pour le pic : statut actif ou promu,
    avec un prix d'entrée (donc niveau SIGNAL), détectés il y a moins de
    `max_age_seconds`.
    """
    cutoff = time.time() - max_age_seconds
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM signals
               WHERE entry_price IS NOT NULL AND entry_price > 0
                 AND created_at >= ?
                 AND status IN ('active', 'promoted', 'bought')
               ORDER BY created_at DESC
               LIMIT 200""",
            (cutoff,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_training_data(horizon: str = "6h", limit: int = 500) -> list[tuple[dict, float]]:
    """
    (features, rendement réel) pour tous les signaux mesurés à l'horizon donné —
    la matière première de core/self_tuning.py.
    """
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT s.features AS features, p.return_pct AS return_pct
               FROM signals s
               JOIN performance_checks p ON p.signal_id = s.id
               WHERE p.horizon = ? AND s.features IS NOT NULL AND s.features != '{}'
               ORDER BY p.checked_at DESC
               LIMIT ?""",
            (horizon, limit),
        ).fetchall()

    results = []
    for r in rows:
        try:
            features = json.loads(r["features"])
        except (TypeError, ValueError):
            continue
        if features and r["return_pct"] is not None:
            results.append((features, r["return_pct"]))
    return results
