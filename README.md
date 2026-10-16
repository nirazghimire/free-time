# ⚡ Hyperliquid Multi-Timeframe SMC Trading Bot & Terminal

An institutional-grade **Smart Money Concepts (SMC)** automated trading system built for **Hyperliquid Perpetual DEX**. The bot performs multi-timeframe structural analysis, detects 15-minute liquidity sweeps and 5-minute break of structure (BOS) candle body close triggers, and automatically executes and manages trades with dynamic risk controls.

---

## 🏛️ Strategy Architecture & Trading Rules

The system implements the exact multi-timeframe strategy outlined in your requirements:

```
┌────────────────────────────────────────────────────────┐
│  MACRO TIER (1W & 1D)                                  │
│  • Swing Highs & Lows (Higher Highs / Lower Lows)      │
│  • Candle Body Close BOS / CHoCH                       │
│  • Determines overall Institutional Bias (BULL / BEAR) │
└───────────────────────────┬────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────┐
│  INTERMEDIATE TIER (4H & 1H)                           │
│  • Validates prevailing order flow                     │
│  • Must align with Macro Bias before lower TF entries  │
└───────────────────────────┬────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────┐
│  15-MINUTE LIQUIDITY SWEEP                             │
│  • Long: Wicks below 15M Swing Low, closes back inside │
│  • Short: Wicks above 15M Swing High, closes inside    │
│  • Traps counter-trend liquidity & identifies SL level │
└───────────────────────────┬────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────┐
│  5-MINUTE BOS ENTRY TRIGGER                            │
│  • Strict Candle Body Close breaking 5M structure      │
│  • Confirms reversal in direction of 15M sweep         │
│  • Enters trade with dynamic Stop Loss & 1:2.5 R:R TP  │
└────────────────────────────────────────────────────────┘
```

### 1. Market Structure Core Logic
- **Swing Points (Pivot Highs / Lows)**: Identified using fractal lookback windows.
- **Break of Structure (BOS)**: Continuation of trend confirmed only when the **candle body closes** beyond the previous major swing high or swing low.
- **Change of Character (CHoCH)**: Reversal of trend confirmed when candle body closes beyond the most recent higher low (in an uptrend) or lower high (in a downtrend).
- **Liquidity Sweep (15-Minute)**:
  - **Bullish (Sell-Side Sweep)**: Price wicks below a recent 15M Swing Low (hunting stop orders), but the candle **closes back above** the swing low level.
  - **Bearish (Buy-Side Sweep)**: Price wicks above a recent 15M Swing High (hunting buy stops), but the candle **closes back below** the swing high level.
  - The extreme wick of the sweep forms the invalidation price.
- **Trigger Execution (5-Minute)**:
  - After a 15M sweep occurs in the direction of the Macro/Intermediate trend, the bot watches the 5M timeframe.
  - A 5M **candle body close** breaking 5M structure activates the market order.

---

## 💰 Risk Management (Tailored for $10 – $20 Accounts)

Trading with smaller balances ($10 to $20) requires precise position sizing on Hyperliquid:
- **Min Order Notional**: Hyperliquid enforces a ~$10 minimum order notional. The bot defaults to `$11.00` to prevent order rejections.
- **Risk Sizing**: Calculates risk per trade (e.g. `$1.00` fixed risk or 5% of balance).
- **Leverage**: Configurable cross leverage (default `3x`, scalable up to `10x`).
- **Precision**: Dynamically queries Hyperliquid's `szDecimals` (e.g., 5 decimals for BTC, 4 for ETH, 2 for SOL) to format order sizes.
- **Stop Loss**: Placed at `sweep.wick_extreme * (1 ± buffer)`.
- **Take Profit**: Placed dynamically at a minimum `1:2.5` Risk-to-Reward ratio (or opposite swing liquidity).
- **Max Positions Guard**: Configurable maximum concurrent positions (`MAX_OPEN_POSITIONS=1`) to prevent small balances from becoming overleveraged across multiple pairs.
- **Anti-Chase Protection**: Discards 5M entry triggers if price has drifted more than 0.75% beyond the trigger candle close.

---

## 🚀 Quick Start Guide

### 1. Installation
The virtual environment is pre-configured with all required packages:
```bash
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Environment Configuration (`.env`)
Copy `.env.example` to `.env`:
```bash
# Hyperliquid Environment: "testnet" or "mainnet"
HYPERLIQUID_ENV=testnet

# Ethereum Wallet Private Key or Hyperliquid API Agent Key
# Leave blank to run in safe Paper Trading mode
HYPERLIQUID_PRIVATE_KEY=

# Mode
DRY_RUN=true
AUTO_TRADE_ENABLED=true

# Monitored Symbols
TRADING_SYMBOLS=BTC,ETH,SOL

# Risk Parameters
RISK_USD_PER_TRADE=1.00
MIN_ORDER_NOTIONAL=11.0
LEVERAGE=3
TARGET_RR=2.5
MAX_OPEN_POSITIONS=1
```

> **Security Tip**: On Hyperliquid, navigate to **Account > API Agents** to generate an API Agent wallet. This lets you trade programmatically without exposing your main wallet private key!

### 3. Run the Bot & Terminal
```bash
PYTHONPATH=. ./venv/bin/python3 main.py
```

Open your browser to:
**[http://localhost:8000](http://localhost:8000)**

---

## 🖥️ Web Dashboard Features

- **Live Multi-Timeframe Matrix**: Color-coded view of BTC, ETH, and SOL across `1W`, `1D`, `4H`, `1H`, `15M`, and `5M` with badges for detected Sweeps, BOS, and CHoCH.
- **Real-time Account Ribbon**: Live equity, margin usage, active positions count, and total unrealized PnL.
- **Active Setups & Positions**: Shows current open positions with entry, current mark price, SL, TP, and live PnL.
- **SMC Activity Terminal**: Live WebSocket event feed of market scans, sweep detections, and order fills.
- **Interactive Controls**:
  - **Auto-Trade Toggle**: Enable or pause automated entry.
  - **Paper / Live Toggle**: Switch between test simulation and exchange orders.
  - **Scan Now**: Trigger an instant on-demand scan.
  - **Emergency Close**: 1-click market close for all open positions.

---

## 🧪 Unit Tests

Run the full pytest suite to verify SMC detection rules and risk math:
```bash
PYTHONPATH=. ./venv/bin/pytest tests/
```
Output:
```
tests/test_risk_manager.py ..                                            [ 28%]
tests/test_smc_engine.py .....                                           [100%]
============================== 7 passed in 0.18s ===============================
```
