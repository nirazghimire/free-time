"""Unit tests for the Historical Backtesting Engine."""

import pytest
import pandas as pd
import numpy as np
from unittest.mock import MagicMock

from config import BotConfig
from src.backtester import BacktestEngine, BacktestResult, BacktestTrade
from src.smc_engine import TrendBias, LiquiditySweep, SwingPoint, StructureEvent, EventType
from src.risk_manager import TradeSetup


def create_candle_df(base_price: float, count: int, interval_ms: int = 300000, trend: str = "bullish") -> pd.DataFrame:
    """Generate synthetic OHLCV candles."""
    records = []
    base_t = 1700000000000
    cur_p = base_price
    for i in range(count):
        if trend == "bullish":
            cur_p += (0.1 if i % 2 == 0 else -0.05)
        elif trend == "bearish":
            cur_p -= (0.1 if i % 2 == 0 else -0.05)
        else:
            cur_p += (0.05 if i % 2 == 0 else -0.05)

        records.append({
            "timestamp": base_t + (i * interval_ms),
            "open": float(cur_p),
            "high": float(cur_p + 1.0),
            "low": float(cur_p - 1.0),
            "close": float(cur_p + 0.2),
            "volume": 10.0
        })
    return pd.DataFrame(records)


def test_backtest_engine_initialization():
    config = BotConfig(dry_run=True)
    engine = BacktestEngine(config, taker_fee_pct=0.00035, slippage_pct=0.0002)
    assert engine.taker_fee_pct == 0.00035
    assert engine.slippage_pct == 0.0002
    assert engine.strategy is not None


def test_backtest_run_simulation_long_win():
    """Simulate a winning long trade: triggers, reaches TP, records win metrics."""
    config = BotConfig(dry_run=True)
    engine = BacktestEngine(config)

    # 200 5m candles
    df_5m = create_candle_df(base_price=100.0, count=200, interval_ms=300000)
    mock_candles = {
        "1w": create_candle_df(100.0, 50, 7 * 24 * 3600 * 1000),
        "1d": create_candle_df(100.0, 60, 24 * 3600 * 1000),
        "4h": create_candle_df(100.0, 80, 4 * 3600 * 1000),
        "1h": create_candle_df(100.0, 100, 3600 * 1000),
        "15m": create_candle_df(100.0, 150, 15 * 60 * 1000),
        "5m": df_5m,
    }

    # Mock evaluate_symbol to trigger trade setup at bar 155
    sp = SwingPoint(index=2, timestamp=1700000000000, price=95.0, is_high=False)
    sweep = LiquiditySweep(
        direction=TrendBias.BULLISH,
        swing_point=sp,
        sweep_candle_index=5,
        sweep_timestamp=1700001000000,
        wick_extreme=95.0,
        candle_close=98.0
    )
    setup = TradeSetup(
        symbol="SOL",
        direction=TrendBias.BULLISH,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=112.5,
        risk_reward=2.5,
        size=0.1,
        notional_usd=10.0,
        risk_usd=0.5,
        sl_distance_pct=5.0,
        sweep=sweep,
        sz_decimals=2
    )

    eval_triggered = MagicMock()
    eval_triggered.trade_setup = setup
    eval_triggered.active_sweep_15m = sweep
    eval_empty = MagicMock()
    eval_empty.trade_setup = None
    eval_empty.active_sweep_15m = None

    call_count = [0]
    def mock_eval(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 5:  # trigger on 5th bar after warmup
            return eval_triggered
        return eval_empty

    engine.strategy.evaluate_symbol = MagicMock(side_effect=mock_eval)

    # In bar 160, inject a candle high that hits TP 112.5
    df_5m.loc[160, "high"] = 115.0

    result = engine.run_backtest("SOL", mock_candles, initial_balance=20.0, warmup_5m_candles=150)
    assert result.total_trades == 1
    assert result.winning_trades == 1
    assert result.win_rate_pct == 100.0
    assert result.net_profit_usd > 0
    assert result.trades[0].status == "CLOSED_TP"
    assert result.trades[0].direction == "BULLISH"


def test_backtest_run_simulation_stop_loss():
    """Simulate a losing trade that hits Stop Loss."""
    config = BotConfig(dry_run=True)
    engine = BacktestEngine(config)

    df_5m = create_candle_df(base_price=100.0, count=200, interval_ms=300000)
    mock_candles = {
        "1w": create_candle_df(100.0, 50, 7 * 24 * 3600 * 1000),
        "1d": create_candle_df(100.0, 60, 24 * 3600 * 1000),
        "4h": create_candle_df(100.0, 80, 4 * 3600 * 1000),
        "1h": create_candle_df(100.0, 100, 3600 * 1000),
        "15m": create_candle_df(100.0, 150, 15 * 60 * 1000),
        "5m": df_5m,
    }

    sp = SwingPoint(index=2, timestamp=1700000000000, price=95.0, is_high=False)
    sweep = LiquiditySweep(
        direction=TrendBias.BULLISH,
        swing_point=sp,
        sweep_candle_index=5,
        sweep_timestamp=1700001000000,
        wick_extreme=95.0,
        candle_close=98.0
    )
    setup = TradeSetup(
        symbol="SOL",
        direction=TrendBias.BULLISH,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=112.5,
        risk_reward=2.5,
        size=0.1,
        notional_usd=10.0,
        risk_usd=0.5,
        sl_distance_pct=5.0,
        sweep=sweep,
        sz_decimals=2
    )

    eval_triggered = MagicMock()
    eval_triggered.trade_setup = setup
    eval_triggered.active_sweep_15m = sweep
    eval_empty = MagicMock()
    eval_empty.trade_setup = None

    call_count = [0]
    def mock_eval(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 3:
            return eval_triggered
        return eval_empty

    engine.strategy.evaluate_symbol = MagicMock(side_effect=mock_eval)

    # In bar 155, inject a candle low that crashes through SL 95.0
    df_5m.loc[155, "low"] = 93.0

    result = engine.run_backtest("SOL", mock_candles, initial_balance=20.0, warmup_5m_candles=150)
    assert result.total_trades == 1
    assert result.losing_trades == 1
    assert result.win_rate_pct == 0.0
    assert result.net_profit_usd < 0
    assert result.trades[0].status == "CLOSED_SL"


def test_backtest_breakeven_trailing_stop():
    """Verify trade that achieves 1:1 R moves SL to entry, exiting at breakeven."""
    config = BotConfig(dry_run=True, enable_breakeven_at_1r=True)
    engine = BacktestEngine(config)

    df_5m = create_candle_df(base_price=100.0, count=200, interval_ms=300000)
    mock_candles = {
        "1w": create_candle_df(100.0, 50, 7 * 24 * 3600 * 1000),
        "1d": create_candle_df(100.0, 60, 24 * 3600 * 1000),
        "4h": create_candle_df(100.0, 80, 4 * 3600 * 1000),
        "1h": create_candle_df(100.0, 100, 3600 * 1000),
        "15m": create_candle_df(100.0, 150, 15 * 60 * 1000),
        "5m": df_5m,
    }

    sp = SwingPoint(index=2, timestamp=1700000000000, price=95.0, is_high=False)
    sweep = LiquiditySweep(
        direction=TrendBias.BULLISH,
        swing_point=sp,
        sweep_candle_index=5,
        sweep_timestamp=1700001000000,
        wick_extreme=95.0,
        candle_close=98.0
    )
    # Entry 100, SL 95 -> risk = 5.0 -> 1:1 R target is 105.0
    setup = TradeSetup(
        symbol="SOL",
        direction=TrendBias.BULLISH,
        entry_price=100.0,
        stop_loss=95.0,
        take_profit=112.5,
        risk_reward=2.5,
        size=0.1,
        notional_usd=10.0,
        risk_usd=0.5,
        sl_distance_pct=5.0,
        sweep=sweep,
        sz_decimals=2
    )

    eval_triggered = MagicMock()
    eval_triggered.trade_setup = setup
    eval_triggered.active_sweep_15m = sweep
    eval_empty = MagicMock()
    eval_empty.trade_setup = None

    call_count = [0]
    def mock_eval(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 2:
            return eval_triggered
        return eval_empty

    engine.strategy.evaluate_symbol = MagicMock(side_effect=mock_eval)

    # Bar 153: Price rises to 105.5 (>= 1:1 R:R) -> triggers breakeven move
    df_5m.loc[153, "high"] = 106.0
    # Bar 154: Price dumps back down to 99.0 (below entry 100.0) -> exits at breakeven
    df_5m.loc[154, "low"] = 98.0

    result = engine.run_backtest("SOL", mock_candles, initial_balance=20.0, warmup_5m_candles=150)
    assert result.total_trades == 1
    assert result.breakeven_trades == 1
    trade = result.trades[0]
    assert trade.breakeven_triggered is True
    assert trade.status == "CLOSED_BREAKEVEN"
    # Gross PnL is 0; net PnL is only the trading fees
    assert abs(trade.gross_pnl) < 0.01
