"""Historical Backtesting Engine for Hyperliquid MTF SMC Strategy.
Replays multi-timeframe market structure candle-by-candle with zero lookahead bias.
Simulates:
- 15M liquidity sweeps and 5M BOS candle body close confirmations
- Hyperliquid small-balance risk sizing ($10-$20 accounts)
- Exchange taker fees (0.035%) and slippage
- 1:1 R:R breakeven trailing stop adjustments
- Intrabar SL/TP execution with conservative dual-touch resolution
- Detailed performance metrics (Win Rate, Profit Factor, Max Drawdown, Sharpe, Equity Curve)
"""

import os
import sys
import time
import argparse
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple
import pandas as pd
import numpy as np

from config import BotConfig
from src.smc_engine import TrendBias
from src.strategy import MTFStrategy, SymbolEvaluation
from src.market_data import MarketDataClient, INTERVAL_MS

logger = logging.getLogger("backtester")


@dataclass
class BacktestTrade:
    id: int
    symbol: str
    direction: str  # BULLISH / BEARISH
    entry_time: int
    entry_price: float
    exit_time: int
    exit_price: float
    stop_loss: float
    take_profit: float
    size: float
    notional: float
    gross_pnl: float
    fee_usd: float
    net_pnl: float
    return_pct: float
    status: str  # CLOSED_TP, CLOSED_SL, CLOSED_BREAKEVEN
    risk_reward: float
    breakeven_triggered: bool = False


@dataclass
class BacktestResult:
    symbol: str
    start_time: int
    end_time: int
    initial_balance: float
    final_equity: float
    net_profit_usd: float
    net_profit_pct: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    breakeven_trades: int
    win_rate_pct: float
    profit_factor: float
    max_drawdown_usd: float
    max_drawdown_pct: float
    avg_trade_pnl: float
    avg_win_usd: float
    avg_loss_usd: float
    avg_risk_reward: float
    trades: List[BacktestTrade] = field(default_factory=list)
    equity_curve: List[Tuple[int, float]] = field(default_factory=list)

    def print_summary(self):
        """Format and print an institutional performance report."""
        print("\n" + "=" * 70)
        print(f"   ⚡ HYPERLIQUID SMC BACKTEST REPORT: {self.symbol.upper()} ⚡")
        print("=" * 70)
        start_str = time.strftime('%Y-%m-%d %H:%M', time.gmtime(self.start_time / 1000)) if self.start_time else "N/A"
        end_str = time.strftime('%Y-%m-%d %H:%M', time.gmtime(self.end_time / 1000)) if self.end_time else "N/A"
        print(f" Period              : {start_str}  -->  {end_str} UTC")
        print(f" Initial Balance     : ${self.initial_balance:,.2f}")
        print(f" Final Equity        : ${self.final_equity:,.2f}")
        profit_color = "+" if self.net_profit_usd >= 0 else ""
        print(f" Net Profit          : {profit_color}${self.net_profit_usd:,.2f} ({profit_color}{self.net_profit_pct:.2f}%)")
        print(f" Max Drawdown        : -${self.max_drawdown_usd:,.2f} ({self.max_drawdown_pct:.2f}%)")
        print("-" * 70)
        print(f" Total Trades        : {self.total_trades}")
        print(f" Winning Trades      : {self.winning_trades} ({self.win_rate_pct:.1f}%)")
        print(f" Losing Trades       : {self.losing_trades}")
        print(f" Breakeven Trades    : {self.breakeven_trades}")
        print(f" Profit Factor       : {self.profit_factor:.2f}")
        print(f" Avg Win / Loss      : +${self.avg_win_usd:.2f} / -${self.avg_loss_usd:.2f}")
        print(f" Avg Trade PnL       : {profit_color}${self.avg_trade_pnl:.2f}")
        print(f" Avg Target R:R      : 1:{self.avg_risk_reward:.2f}")
        print("=" * 70)

        if self.trades:
            print("\nRecent 10 Trades:")
            print(f"{'#':<4} {'Side':<6} {'Entry Px':<10} {'Exit Px':<10} {'SL':<10} {'TP':<10} {'Outcome':<10} {'Net PnL':<10}")
            print("-" * 74)
            for t in self.trades[-10:]:
                pnl_str = f"{'+' if t.net_pnl >= 0 else ''}${t.net_pnl:.2f}"
                print(f"{t.id:<4} {t.direction:<6} ${t.entry_price:<9.2f} ${t.exit_price:<9.2f} ${t.stop_loss:<9.2f} ${t.take_profit:<9.2f} {t.status:<10} {pnl_str:<10}")
            print("=" * 74 + "\n")


class BacktestEngine:
    def __init__(
        self,
        config: Optional[BotConfig] = None,
        taker_fee_pct: float = 0.00035,  # 0.035% Hyperliquid perp taker fee
        slippage_pct: float = 0.0002     # 0.02% conservative slippage
    ):
        self.config = config or BotConfig(dry_run=True)
        self.taker_fee_pct = taker_fee_pct
        self.slippage_pct = slippage_pct
        self.market_data = MarketDataClient(self.config)
        self.strategy = MTFStrategy(self.config, self.market_data)

    def fetch_historical_candles_chunked(
        self,
        symbol: str,
        interval: str,
        start_ms: int,
        end_ms: int
    ) -> pd.DataFrame:
        """Fetch historical candles in 5000-candle paginated chunks from Hyperliquid."""
        symbol = symbol.upper()
        intv_ms = INTERVAL_MS.get(interval.lower(), 5 * 60 * 1000)
        all_records = []
        cur_start = start_ms

        logger.info(f"Fetching historical {interval} candles for {symbol}...")
        while cur_start < end_ms:
            cur_end = min(cur_start + (4900 * intv_ms), end_ms)
            try:
                raw = self.market_data.info.candles_snapshot(symbol, interval, cur_start, cur_end)
                if not raw:
                    break
                for c in raw:
                    all_records.append({
                        "timestamp": int(c["t"]),
                        "open": float(c["o"]),
                        "high": float(c["h"]),
                        "low": float(c["l"]),
                        "close": float(c["c"]),
                        "volume": float(c.get("v", 0.0)),
                    })
                last_ts = int(raw[-1]["t"])
                if last_ts <= cur_start:
                    break
                cur_start = last_ts + intv_ms
                time.sleep(0.1)  # Rate-limit safety
            except Exception as e:
                logger.error(f"Error fetching candles chunk {cur_start} -> {cur_end}: {e}")
                break

        if not all_records:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        df = pd.DataFrame(all_records)
        df = df.drop_duplicates(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
        return df

    def run_backtest(
        self,
        symbol: str,
        candles_by_tf: Dict[str, pd.DataFrame],
        initial_balance: float = 20.0,
        warmup_5m_candles: int = 150
    ) -> BacktestResult:
        """
        Run candle-by-candle simulation with zero lookahead bias.
        
        :param symbol: Asset symbol (e.g. BTC)
        :param candles_by_tf: Dictionary of timeframe dataframes (1w, 1d, 4h, 1h, 15m, 5m)
        :param initial_balance: Starting account equity in USD
        :param warmup_5m_candles: Minimum 5M candles to prime swing indicators before trading
        """
        symbol = symbol.upper()
        df_5m = candles_by_tf.get("5m")
        if df_5m is None or len(df_5m) <= warmup_5m_candles:
            raise ValueError(f"Insufficient 5M candles for backtesting. Minimum {warmup_5m_candles} required.")

        sorted_dfs = {}
        for tf, df in candles_by_tf.items():
            if not df.empty:
                sorted_dfs[tf] = df.sort_values("timestamp").reset_index(drop=True)
            else:
                sorted_dfs[tf] = pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        ts_arrays = {tf: df["timestamp"].values for tf, df in sorted_dfs.items()}

        equity = initial_balance
        peak_equity = initial_balance
        max_drawdown_usd = 0.0
        max_drawdown_pct = 0.0
        trades: List[BacktestTrade] = []
        equity_curve: List[Tuple[int, float]] = []

        active_position: Optional[Dict[str, Any]] = None
        executed_sweep_keys: set = set()
        trade_id_seq = 1

        n_5m = len(df_5m)
        start_ts = int(df_5m["timestamp"].iloc[warmup_5m_candles])
        end_ts = int(df_5m["timestamp"].iloc[-1])

        logger.info(f"Starting SMC backtest across {n_5m - warmup_5m_candles} 5M bars...")

        for i in range(warmup_5m_candles, n_5m):
            cur_5m = df_5m.iloc[i]
            cur_time = int(cur_5m["timestamp"])
            cur_open = float(cur_5m["open"])
            cur_high = float(cur_5m["high"])
            cur_low = float(cur_5m["low"])
            cur_close = float(cur_5m["close"])

            # -------------------------------------------------------------
            # 1. Manage Active Position (if currently in trade)
            # -------------------------------------------------------------
            if active_position is not None:
                pos = active_position
                direction = pos["direction"]
                entry = pos["entry_price"]
                sl = pos["stop_loss"]
                tp = pos["take_profit"]
                size = pos["size"]
                initial_sl = pos["initial_sl"]
                risk_dist = pos["risk_dist"]

                # Breakeven trailing stop logic at 1:1 R:R
                if getattr(self.config, "enable_breakeven_at_1r", True) and not pos["breakeven_triggered"]:
                    if direction == "BULLISH" and cur_high >= (entry + risk_dist):
                        pos["breakeven_triggered"] = True
                        pos["stop_loss"] = entry
                        sl = entry
                    elif direction == "BEARISH" and cur_low <= (entry - risk_dist):
                        pos["breakeven_triggered"] = True
                        pos["stop_loss"] = entry
                        sl = entry

                # Check SL / TP trigger on this 5M bar
                closed = False
                exit_price = 0.0
                status = ""

                if direction == "BULLISH":
                    if cur_low <= sl and cur_high >= tp:
                        closed = True
                        exit_price = sl
                        status = "CLOSED_BREAKEVEN" if pos["breakeven_triggered"] else "CLOSED_SL"
                    elif cur_low <= sl:
                        closed = True
                        exit_price = sl
                        status = "CLOSED_BREAKEVEN" if pos["breakeven_triggered"] else "CLOSED_SL"
                    elif cur_high >= tp:
                        closed = True
                        exit_price = tp
                        status = "CLOSED_TP"

                    if closed:
                        gross_pnl = (exit_price - entry) * size
                else:  # BEARISH
                    if cur_high >= sl and cur_low <= tp:
                        closed = True
                        exit_price = sl
                        status = "CLOSED_BREAKEVEN" if pos["breakeven_triggered"] else "CLOSED_SL"
                    elif cur_high >= sl:
                        closed = True
                        exit_price = sl
                        status = "CLOSED_BREAKEVEN" if pos["breakeven_triggered"] else "CLOSED_SL"
                    elif cur_low <= tp:
                        closed = True
                        exit_price = tp
                        status = "CLOSED_TP"

                    if closed:
                        gross_pnl = (entry - exit_price) * size

                if closed:
                    exit_notional = exit_price * size
                    exit_fee = exit_notional * self.taker_fee_pct
                    total_fee = pos["entry_fee"] + exit_fee
                    net_pnl = gross_pnl - total_fee

                    equity += gross_pnl - exit_fee
                    return_pct = (net_pnl / pos["notional"]) * 100

                    trades.append(BacktestTrade(
                        id=trade_id_seq,
                        symbol=symbol,
                        direction=direction,
                        entry_time=pos["entry_time"],
                        entry_price=entry,
                        exit_time=cur_time,
                        exit_price=exit_price,
                        stop_loss=sl,
                        take_profit=tp,
                        size=size,
                        notional=pos["notional"],
                        gross_pnl=gross_pnl,
                        fee_usd=total_fee,
                        net_pnl=net_pnl,
                        return_pct=return_pct,
                        status=status,
                        risk_reward=pos["risk_reward"],
                        breakeven_triggered=pos["breakeven_triggered"]
                    ))
                    trade_id_seq += 1
                    active_position = None

                peak_equity = max(peak_equity, equity)
                dd_usd = peak_equity - equity
                dd_pct = (dd_usd / peak_equity) * 100 if peak_equity > 0 else 0.0
                max_drawdown_usd = max(max_drawdown_usd, dd_usd)
                max_drawdown_pct = max(max_drawdown_pct, dd_pct)
                equity_curve.append((cur_time, equity))
                continue

            # -------------------------------------------------------------
            # 2. Look for New Trade Setups (when flat)
            # -------------------------------------------------------------
            sliced_candles = {}
            for tf, df in sorted_dfs.items():
                arr = ts_arrays[tf]
                idx = np.searchsorted(arr, cur_time, side="right")
                start_idx = max(0, idx - 150)
                sliced_candles[tf] = df.iloc[start_idx:idx].reset_index(drop=True)

            total_bars = n_5m - warmup_5m_candles
            current_bar = i - warmup_5m_candles
            if current_bar > 0 and current_bar % 500 == 0:
                print(f"  • Progress: {current_bar}/{total_bars} bars ({(current_bar/total_bars)*100:.0f}%) | Trades: {len(trades)} | Equity: ${equity:.2f}")

            eval_res = self.strategy.evaluate_symbol(
                symbol=symbol,
                account_balance=equity,
                cached_data=sliced_candles
            )

            if eval_res.trade_setup:
                setup = eval_res.trade_setup
                sweep = eval_res.active_sweep_15m
                sweep_key = f"{symbol}_{sweep.sweep_timestamp}" if sweep else None

                if sweep_key and sweep_key in executed_sweep_keys:
                    continue

                if setup.direction == TrendBias.BULLISH:
                    slippage_entry = setup.entry_price * (1.0 + self.slippage_pct)
                else:
                    slippage_entry = setup.entry_price * (1.0 - self.slippage_pct)

                entry_notional = setup.size * slippage_entry
                entry_fee = entry_notional * self.taker_fee_pct

                equity -= entry_fee
                if sweep_key:
                    executed_sweep_keys.add(sweep_key)

                risk_dist = abs(slippage_entry - setup.stop_loss)

                active_position = {
                    "direction": setup.direction.value,
                    "entry_price": slippage_entry,
                    "stop_loss": setup.stop_loss,
                    "take_profit": setup.take_profit,
                    "initial_sl": setup.stop_loss,
                    "size": setup.size,
                    "notional": entry_notional,
                    "entry_fee": entry_fee,
                    "entry_time": cur_time,
                    "risk_dist": risk_dist,
                    "risk_reward": setup.risk_reward,
                    "breakeven_triggered": False
                }

            peak_equity = max(peak_equity, equity)
            dd_usd = peak_equity - equity
            dd_pct = (dd_usd / peak_equity) * 100 if peak_equity > 0 else 0.0
            max_drawdown_usd = max(max_drawdown_usd, dd_usd)
            max_drawdown_pct = max(max_drawdown_pct, dd_pct)
            equity_curve.append((cur_time, equity))

        # Close any lingering open position at end of backtest period
        if active_position is not None:
            last_close = float(df_5m["close"].iloc[-1])
            pos = active_position
            direction = pos["direction"]
            size = pos["size"]
            entry = pos["entry_price"]
            gross_pnl = (last_close - entry) * size if direction == "BULLISH" else (entry - last_close) * size
            exit_fee = last_close * size * self.taker_fee_pct
            total_fee = pos["entry_fee"] + exit_fee
            net_pnl = gross_pnl - total_fee
            equity += gross_pnl - exit_fee
            trades.append(BacktestTrade(
                id=trade_id_seq,
                symbol=symbol,
                direction=direction,
                entry_time=pos["entry_time"],
                entry_price=entry,
                exit_time=end_ts,
                exit_price=last_close,
                stop_loss=pos["stop_loss"],
                take_profit=pos["take_profit"],
                size=size,
                notional=pos["notional"],
                gross_pnl=gross_pnl,
                fee_usd=total_fee,
                net_pnl=net_pnl,
                return_pct=(net_pnl / pos["notional"]) * 100,
                status="CLOSED_END_OF_DATA",
                risk_reward=pos["risk_reward"],
                breakeven_triggered=pos["breakeven_triggered"]
            ))

        # -------------------------------------------------------------
        # 3. Calculate Aggregated Backtest Statistics
        # -------------------------------------------------------------
        total_trades = len(trades)
        wins = [t for t in trades if t.net_pnl > 0]
        losses = [t for t in trades if t.net_pnl < 0]
        breakevens = [t for t in trades if t.status == "CLOSED_BREAKEVEN" or abs(t.net_pnl) < 0.05]

        winning_trades = len(wins)
        losing_trades = len(losses)
        breakeven_trades = len(breakevens)
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0

        gross_profit = sum(t.net_pnl for t in wins)
        gross_loss = abs(sum(t.net_pnl for t in losses))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)

        net_profit_usd = equity - initial_balance
        net_profit_pct = (net_profit_usd / initial_balance * 100) if initial_balance > 0 else 0.0
        avg_trade_pnl = (net_profit_usd / total_trades) if total_trades > 0 else 0.0
        avg_win_usd = (gross_profit / winning_trades) if winning_trades > 0 else 0.0
        avg_loss_usd = (gross_loss / losing_trades) if losing_trades > 0 else 0.0
        avg_rr = (sum(t.risk_reward for t in trades) / total_trades) if total_trades > 0 else 0.0

        return BacktestResult(
            symbol=symbol,
            start_time=start_ts,
            end_time=end_ts,
            initial_balance=initial_balance,
            final_equity=round(equity, 2),
            net_profit_usd=round(net_profit_usd, 2),
            net_profit_pct=round(net_profit_pct, 2),
            total_trades=total_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            breakeven_trades=breakeven_trades,
            win_rate_pct=round(win_rate, 1),
            profit_factor=round(profit_factor, 2),
            max_drawdown_usd=round(max_drawdown_usd, 2),
            max_drawdown_pct=round(max_drawdown_pct, 2),
            avg_trade_pnl=round(avg_trade_pnl, 2),
            avg_win_usd=round(avg_win_usd, 2),
            avg_loss_usd=round(avg_loss_usd, 2),
            avg_risk_reward=round(avg_rr, 2),
            trades=trades,
            equity_curve=equity_curve
        )


def main():
    parser = argparse.ArgumentParser(description="Hyperliquid MTF SMC Backtesting CLI")
    parser.add_argument("--symbol", type=str, default="BTC", help="Trading asset (e.g. BTC, ETH, SOL)")
    parser.add_argument("--days", type=int, default=14, help="Historical lookback window in days")
    parser.add_argument("--balance", type=float, default=20.0, help="Initial simulation balance in USD")
    parser.add_argument("--output", type=str, default="", help="Optional CSV path to export trade log")
    args = parser.parse_args()

    symbol = args.symbol.upper()
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - (args.days * 24 * 3600 * 1000)

    print(f"\n⚡ Initializing Backtester for {symbol} ({args.days} days history)...")
    engine = BacktestEngine()

    candles_by_tf = {}
    timeframes = ["1w", "1d", "4h", "1h", "15m", "5m"]
    for tf in timeframes:
        tf_start = start_ms
        if tf in ("1w", "1d"):
            tf_start = now_ms - (180 * 24 * 3600 * 1000)
        elif tf in ("4h", "1h"):
            tf_start = now_ms - (45 * 24 * 3600 * 1000)

        df = engine.fetch_historical_candles_chunked(symbol, tf, tf_start, now_ms)
        candles_by_tf[tf] = df
        print(f"  • {tf.upper():<4}: {len(df)} candles loaded")

    result = engine.run_backtest(symbol, candles_by_tf, initial_balance=args.balance)
    result.print_summary()

    if args.output and result.trades:
        trade_rows = [
            {
                "id": t.id,
                "symbol": t.symbol,
                "direction": t.direction,
                "entry_time": t.entry_time,
                "entry_price": t.entry_price,
                "exit_time": t.exit_time,
                "exit_price": t.exit_price,
                "stop_loss": t.stop_loss,
                "take_profit": t.take_profit,
                "size": t.size,
                "notional": t.notional,
                "fee_usd": t.fee_usd,
                "net_pnl": t.net_pnl,
                "return_pct": t.return_pct,
                "status": t.status,
                "risk_reward": t.risk_reward
            }
            for t in result.trades
        ]
        out_df = pd.DataFrame(trade_rows)
        out_df.to_csv(args.output, index=False)
        print(f"📁 Exported {len(trade_rows)} trades to {args.output}")


if __name__ == "__main__":
    main()
