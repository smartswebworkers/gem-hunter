"""
core/solana.py — DÉPRÉCIÉ, ne pas modifier.
Destination finale : gem_hunter/core/solana.py

CORRECTIF : ce fichier était une copie intégrale de chains/solana.py, restée
figée après le déplacement de la logique de découverte vers chains/. Aucun
module ne l'importait (vérifié : core/scanner.py importe `from chains import
solana, ...`), donc ce n'était pas un bug actif, mais deux implémentations
identiques qui auraient inévitablement divergé à la prochaine correction
appliquée à une seule des deux copies (c'était d'ailleurs déjà en train de se
produire : ce fichier n'avait pas reçu le correctif "volume_24h" appliqué à
chains/solana.py). Remplacé par un simple ré-export.
"""
from chains.solana import *  # noqa: F401,F403
from chains.solana import discover_candidates, enrich_with_security  # noqa: F401
