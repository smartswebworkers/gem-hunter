"""
chains/base.py — Découverte de tokens sur Base (Coinbase L2, chain ID 8453)
Destination finale : gem_hunter/chains/base.py

La logique est mutualisée dans chains/evm_common.py : ce module ne fait que
fixer l'identifiant de chaîne, exactement comme chains/bsc.py. Base était déjà
câblée partout ailleurs (CHAIN_ID_BY_NAME, GOPLUS_CHAIN_IDS, NANSEN_CHAIN_MAP,
NANSEN_SCREENER_CHAIN_MAP) mais absente de ACTIVE_CHAINS et de CHAIN_MODULES :
elle n'était donc jamais scannée. C'est réparé.

Base est la mieux couverte des trois chaînes EVM ciblées : contrats presque
toujours vérifiés sur Basescan (pas de rejet de masse « code source non
vérifié » comme sur Robinhood), couverture GoPlus complète, smart-money Nansen
disponible, et Base est indexée par GeckoTerminal comme par DexScreener.
"""
from chains import evm_common

CHAIN = "base"


def discover_candidates() -> list[dict]:
    return evm_common.discover_candidates(CHAIN)


def enrich_batch(candidates: list[dict], should_stop=None) -> list[dict]:
    return evm_common.enrich_batch(CHAIN, candidates, should_stop)


def enrich_with_security(candidate: dict) -> dict:
    return evm_common.enrich_with_security(CHAIN, candidate)
