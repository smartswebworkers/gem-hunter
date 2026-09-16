"""
core/config.py — DÉPRÉCIÉ, ne pas modifier.
Destination finale : gem_hunter/core/config.py

CORRECTIF : ce fichier était une COPIE indépendante de config.py, restée figée
avec d'anciennes valeurs (MIN_LIQUIDITY_USD=8000, MIN_VOLUME_24H_USD au lieu
de MIN_VOLUME_1H_USD, MAX_PAIR_AGE_HOURS=72, ACTIVE_CHAINS sans "ethereum",
DB_PATH qui aurait pointé vers core/storage/gemhunter.db au lieu de
storage/gemhunter.db si jamais importée). Aucun module ne l'importait déjà
(vérifié : tout le code utilise `from config import ...` / `import config`,
qui résout vers le config.py de la racine), donc ce n'était pas un bug actif
— mais c'était une mine posée pour la prochaine modification. Remplacé par un
simple ré-export pour qu'il ne puisse plus jamais diverger de la config réelle.
"""
from config import *  # noqa: F401,F403
