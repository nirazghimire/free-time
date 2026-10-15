# ⚡ Hyperliquid Multi-Timeframe SMC Trading Bot — Master Project Plan

**Document Version:** 1.0.0  
**Last Updated:** 2026-10-15 (2026-10-15T18:52:00-04:00)  
**Author:** Pair Programming Assistant & Niraj  
**Project Status:** Core Engine Complete, Unit Tested, Dashboard Operational, Git Protected  

---

## 🎯 1. Project Mission & Objectives

The goal of this project is to build an automated algorithmic trading system for **Hyperliquid Perpetual DEX** using **Smart Money Concepts (SMC)** across multiple timeframes.

The system is engineered to:
1. Monitor high-liquidity crypto assets (`BTC`, `ETH`, `SOL`).
2. Establish higher-timeframe directional bias using **Weekly (1W)** and **Daily (1D)** market structure.
3. Validate intraday order flow with **4-Hour (4H)** and **1-Hour (1H)** structure.
4. Detect high-probability **15-Minute (15M) Liquidity Sweeps** (trapping counter-trend traders).
5. Trigger automated execution upon a **5-Minute (5M) Break of Structure (BOS)** confirmed by a strict **candle body close**.
6. Enforce strict risk management tailored for small balances (**$10 – $20 USD**), including minimum order notional handling ($11.00 USD), leverage settings, and minimum 1:2.5 Risk-to-Reward ratio.
7. Provide a visual, real-time **Web Dashboard** (`http://localhost:8000`) for monitoring market structure, active setups, open positions, and terminal logs.

---

## 🧠 2. Core SMC Trading Rules & Strategy Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. MACRO TIER (1W & 1D)                                         │
│    • Tracks Swing Highs (HH, LH) & Swing Lows (HL, LL)          │
│    • Confirms Bullish or Bearish Institutional Bias             │
└───────────────────────────────┬─────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────┐
│ 2. INTERMEDIATE TIER (4H & 1H)                                  │
│    • Confirms Order Flow direction aligns with Macro Bias       │
│    • Discards any lower timeframe setup counter to this trend   │
└───────────────────────────────┬─────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────┐
│ 3. 15-MINUTE LIQUIDITY SWEEP (Liquidity Grab)                   │
│    • Bullish Sweep: Price wicks below 15M Swing Low, closes IN  │
│    • Bearish Sweep: Price wicks above 15M Swing High, closes IN │
│    • Sweep extreme wick defines the Stop Loss level             │
└───────────────────────────────┬─────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────┐
│ 4. 5-MINUTE BOS ENTRY TRIGGER                                   │
│    • 5M Candle BODY CLOSE strictly beyond 5M structure          │
│    • Triggers Market Order entry on Hyperliquid                 │
│    • Places SL trigger order at 15M sweep wick                  │
│    • Places TP trigger order at target R:R (1:2.5)              │
└─────────────────────────────────────────────────────────────────┘
```

### Detailed Structural Definitions:
- **Swing Points**: Formed when candle $i$ has higher high (or lower low) than $k$ candles to both left and right (default $k=2$).
- **BOS (Break of Structure)**: Continuation pattern. Requires a candle **body close** beyond the previous major swing point.
- **CHoCH (Change of Character)**: Reversal pattern. Occurs when price closes beyond the counter-trend pivot (e.g. closing below higher low in uptrend).
- **Liquidity Sweep**: Intrabar penetration of a key swing level followed by a body close back inside the range.

---

## 🔒 3. Security & GitHub Readiness Guidelines

**Zero Secrets in Public Repositories:**
- All sensitive credentials (private keys, API agent keys, wallet addresses) are strictly contained in `.env`.
- `.env` is completely excluded from git via `.gitignore`.
- Template settings are documented in `.env.example` with empty secret fields.
- Private keys are never logged in console outputs, never returned in API payloads, and never passed to the web dashboard.
- Only truncated public wallet addresses (e.g., `0x1a...4b`) are displayed.

---

## 📁 4. Architecture & Component Blueprint

```
/Users/niraj/my_work/
├── .env.example             # Template for user environment variables
├── .env                     # Private credentials & runtime config (GIT IGNORED)
├── .gitignore               # Strict exclusion of .env, keys, venv, caches, logs
├── requirements.txt         # Pinned python dependencies
├── config.py                # Strongly typed BotConfig dataclass
├── main.py                  # Main entrypoint: orchestrates bot loop & dashboard
├── PROJECT_PLAN.md          # Master blueprint & context file for AI agents
├── AGENTS.md                # System instructions & auto-loaded agent guidelines
├── README.md                # User-facing documentation & quickstart
├── src/
│   ├── market_data.py       # Hyperliquid API client for OHLCV candles & user state
│   ├── smc_engine.py        # Core SMC math: Swings, BOS, CHoCH, Sweeps, 5M trigger
│   ├── risk_manager.py      # Small-balance sizing, decimal truncation, SL/TP math
│   ├── strategy.py          # Multi-Timeframe coordinator & signal generator
│   ├── execution.py         # Live Hyperliquid exchange orders & paper trading
│   ├── bot.py               # Periodic scanning loop, position updates, event bus
│   └── dashboard_server.py  # aiohttp async HTTP & WebSocket web dashboard server
├── web/
│   ├── index.html           # Modern glassmorphic terminal interface
│   ├── styles.css           # Custom dark theme styling, badges, animations
│   └── app.js               # WebSocket client, reactive UI, trade controls
└── tests/
    ├── test_smc_engine.py   # Unit tests for swings, body close BOS/CHoCH, sweeps
    └── test_risk_manager.py # Unit tests for sizing, decimal precision, SL/TP
```

---

## 📅 5. Project Roadmap & Next Milestones

### Phase 1: Core Engine & Multi-Timeframe Foundation ✅ (Completed 2026-10-15)
- [x] Hyperliquid SDK integration with Testnet and Mainnet support.
- [x] Multi-timeframe candle fetching for 1W, 1D, 4H, 1H, 15M, 5M.
- [x] Swing points, body close BOS, and CHoCH detection engine.
- [x] 15M liquidity sweep detector with extreme wick invalidation levels.
- [x] 5M confirmation trigger logic.
- [x] Small-balance risk manager ($10-$20 accounts) with minimum notional support.
- [x] Real-time glassmorphic Web Dashboard (`http://localhost:8000`).
- [x] Thread-safe WebSocket streaming of scans and executions.
- [x] Comprehensive unit testing suite.
- [x] Git repository initialization and `.gitignore` security shielding.

### Phase 2: Enhanced Features (Upcoming)
- [ ] Telegram / Discord webhook alert notifications on confirmed signals.
- [ ] Backtesting engine to evaluate historical win rate and R:R over 6–12 months of data.
- [ ] Fair Value Gap (FVG) and Order Block (OB) confluence filtering.
- [ ] Multi-asset scanner expansion beyond BTC, ETH, SOL to all top-volume Hyperliquid perps.
- [ ] Trailing Stop Loss feature (moving SL to Breakeven upon 1:1 R:R reached).

---

## 💡 6. Quick Reference Commands

- **Run Bot & Dashboard:**
  ```bash
  PYTHONPATH=. ./venv/bin/python3 main.py
  ```
- **Run Unit Tests:**
  ```bash
  PYTHONPATH=. ./venv/bin/pytest tests/ -v
  ```
- **Check Git Tracking Status:**
  ```bash
  git status
  ```
