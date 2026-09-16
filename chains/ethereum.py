"""
chains/ethereum.py — Découverte de tokens sur Ethereum
Destination finale : gem_hunter/chains/ethereum.py

Même logique que BNB Chain, mutualisée dans chains/evm_common.py.
Ethereum n'est plus scannée par défaut (voir config.ACTIVE_CHAINS) : la
priorité va à BNB Chain, Solana et Robinhood Chain. Le module reste
fonctionnel et la chaîne réactivable depuis l'interface.
"""
from chains import evm_common

CHAIN = "ethereum"


def discover_candidates() -> list[dict]:
    return evm_common.discover_candidates(CHAIN)


def enrich_batch(candidates: list[dict], should_stop=None) -> list[dict]:
    return evm_common.enrich_batch(CHAIN, candidates, should_stop)


def enrich_with_security(candidate: dict) -> dict:
    return evm_common.enrich_with_security(CHAIN, candidate)
