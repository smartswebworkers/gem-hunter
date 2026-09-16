# AI Multichain Gem Hunter

A multichain memecoin scanner (Solana, BSC, Base, Robinhood Chain) that uses **Nansen's Smart Money data** as a core trust signal — not a cosmetic add-on — to separate real early opportunities from the hundreds of rugs launched every hour.

Built for the [Nansen Meridian Buildathon](https://nsn.ai/meridian-build).

## Why Smart Money, not just on-chain heuristics

Anti-rug heuristics (holder concentration, mint/freeze authority, LP lock, insider clustering) tell you a token *isn't an obvious trap*. They don't tell you anyone credible is actually paying attention to it. Nansen's labeled Smart Money wallets (funds, high-conviction traders with a track record) are the missing signal: a clean token nobody smart has touched is still a guess, a token clean wallets are actively buying is a thesis.

Gem Hunter treats the two as complementary, never as a shortcut around each other — Smart Money presence never overrides a failed security check, and a security-clean token without Smart Money is still shown, just flagged differently.

## How the Nansen API is used

Two endpoints, both wired into the actual decision pipeline (not just displayed):

| Endpoint | Used for | File |
|---|---|---|
| `POST /token-screener` | Multi-chain token discovery — the "quality" scan profile's primary source of new candidates, across Solana, BSC, Ethereum, Base **and Robinhood Chain** | [`data_sources/nansen.py`](data_sources/nansen.py) |
| `POST /tgm/holders` (Token God Mode) | Smart Money holder detection, filtered to `Smart Trader`, `30D/90D Smart Trader`, `Fund` labels | [`data_sources/nansen.py`](data_sources/nansen.py) |

What that Smart Money signal actually drives:

- **Scoring** — smart money presence is one of the weighted components of every candidate's score ([`core/scoring.py`](core/scoring.py)).
- **A hard gate in "quality" mode** — `REQUIRE_SMART_MONEY` blocks a candidate from reaching a verified SIGNAL if no Smart Money wallet is positioned, on every chain Nansen actually covers for that endpoint ([`config.py`](config.py)).
- **Graceful degradation, not a false negative** — on a chain Nansen's holder endpoint doesn't index (Robinhood Chain today), the requirement isn't silently bypassed as "smart money = yes"; it's explicitly not evaluated, and the token is still discoverable via the Screener.
- **Request-budget discipline** — every result is short-TTL cached per (chain, contract): the scanner loops every ~45s, and re-billing Nansen credits for a candidate that hasn't changed since the last cycle would be pure waste, not signal.

## The rest of the pipeline (context, not the point of this submission)

Nansen's data sits on top of a full multichain scanner: DexScreener/GeckoTerminal/PumpPortal for market discovery, GoPlus + RugCheck + direct Solana RPC reads (mint/freeze authority, real holder concentration, creator holdings) for security, a two-tier output (unverified early **WATCH** vs. fully-verified **SIGNAL**), and Telegram alerting. See [`CORRECTIFS.md`](CORRECTIFS.md) for the detailed build log.

## Running it

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in your own keys — see below
python main.py
```

Required in `.env`:

| Variable | Needed for |
|---|---|
| `NANSEN_API_KEY` | Smart Money signal + Token Screener discovery (this submission's core dependency) |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | Alerting |
| `RPC_SOLANA` | On-chain security reads — the public endpoint works but is heavily rate-limited; a free Helius/QuickNode key is recommended |
| `GOPLUS_APP_KEY` / `GOPLUS_APP_SECRET`, `BIRDEYE_API_KEY` | Optional, improve security-check reliability |

No key is required to run the app, but without `NANSEN_API_KEY` the Smart Money signal — this project's actual submission — is inert.

## Stack

Python, PyQt6 + QWebEngineView (HTML/JS dashboard rendered natively, no browser/server involved), SQLite for local history.

---

Built for the Nansen Meridian Buildathon (10,000 USDC). [@nansen_ai](https://x.com/nansen_ai)
