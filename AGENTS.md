# Agent Guidelines for Hyperliquid Multi-Timeframe SMC Trading Bot

**Last Updated:** 2026-10-15  
**Context File:** See [PROJECT_PLAN.md](file:///Users/niraj/my_work/PROJECT_PLAN.md) for complete project architecture, strategy specifications, and roadmap.

---

## 🛡️ Critical Security Rules (Non-Negotiable)
1. **Never commit secrets:** Never commit `.env`, private keys, API keys, or wallet seed phrases to git. Ensure `.gitignore` remains intact.
2. **Never log raw private keys:** Private keys must only be used internally by `eth_account.Account` for signing transactions. Never output private keys to logs, console, or web API payloads.
3. **Safe defaults:** By default, keep `DRY_RUN=true` and `HYPERLIQUID_ENV=testnet` unless the user explicitly asks to trade live on Mainnet.

---

## 🧠 Core Strategy Reference
1. **Timeframes:** 1W (Weekly), 1D (Daily), 4H (4-Hour), 1H (1-Hour), 15M (15-Minute), 5M (5-Minute).
2. **Macro Bias (1W / 1D):** Higher highs / higher lows = Bullish; Lower highs / lower lows = Bearish.
3. **BOS / CHoCH:** Must require **candle body close** strictly beyond swing points. Wicks alone do not qualify as BOS.
4. **15M Sweep:** Intrabar wick breaches 15M swing high/low but candle closes back within range. Sweep wick extreme anchors Stop Loss.
5. **5M Trigger:** 5M candle body close BOS in direction of the 15M sweep.
6. **Risk Management:** Engineered for $10 - $20 accounts. Minimum order notional is $11.00 USD. Format sizes using asset `szDecimals`. Default target R:R is 1:2.5.

---

## 🧪 Testing Requirement
Whenever modifying strategy logic or risk management code, run the unit test suite:
```bash
PYTHONPATH=. ./venv/bin/pytest tests/ -v
```
All tests must pass before completing tasks.
