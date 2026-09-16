"""
chains/bsc.py — Découverte de tokens sur BNB Smart Chain
Destination finale : gem_hunter/chains/bsc.py

La logique est mutualisée dans chains/evm_common.py. Ce module ne fait plus que
fixer l'identifiant de chaîne, ce qui garantit que BNB Chain, Robinhood Chain
et Ethereum reçoivent toujours exactement les mêmes correctifs.

Rappel du bug majeur corrigé au passage (détail dans
data_sources/geckoterminal.py) : la source primaire de cette chaîne envoyait à
GoPlus l'adresse du POOL au lieu de celle du TOKEN. Aucune donnée de sécurité
ne revenait jamais, et tous les candidats BNB Chain étaient rejetés pour
« données indisponibles ». BNB Chain n'était pas scannée, elle était rejetée
en boucle.
"""
from chains import evm_common

CHAIN = "bsc"


def discover_candidates() -> list[dict]:
    return evm_common.discover_candidates(CHAIN)


def enrich_batch(candidates: list[dict], should_stop=None) -> list[dict]:
    return evm_common.enrich_batch(CHAIN, candidates, should_stop)


def enrich_with_security(candidate: dict) -> dict:
    return evm_common.enrich_with_security(CHAIN, candidate)
