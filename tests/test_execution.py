"""Unit tests for the Order Execution module.
Tests paper trading lifecycle, position tracking, duplicate order blocking,
PnL updates, SL/TP triggers, and emergency position closure.
"""

import pytest
from config import BotConfig
from src.execution import OrderExecutor
from src.risk_manager import TradeSetup
from src.smc_engine import TrendBias, LiquiditySweep, SwingPoint


def create_mock_setup(symbol="BTC", direction=TrendBias.BULLISH, entry=80000.0, sl=79500.0, tp=81250.0, size=0.0002):
    dummy_sp = SwingPoint(index=2, timestamp=1700000000000, price=sl, is_high=(direction == TrendBias.BEARISH))
    sweep = LiquiditySweep(
        direction=direction,
        swing_point=dummy_sp,
        sweep_candle_index=5,
        sweep_timestamp=1700001000000,
        wick_extreme=sl,
        candle_close=entry
    )
    return TradeSetup(
        symbol=symbol,
        direction=direction,
        entry_price=entry,
        stop_loss=sl,
        take_profit=tp,
        risk_reward=2.5,
        size=size,
        notional_usd=round(size * entry, 2),
        risk_usd=1.0,
        sl_distance_pct=0.62,
        sweep=sweep
    )


def test_paper_execution_and_duplicate_block():
    config = BotConfig(dry_run=True)
    executor = OrderExecutor(config)

    setup = create_mock_setup(symbol="BTC", direction=TrendBias.BULLISH)
    res = executor.execute_trade(setup)
    assert res["status"] == "success"
    assert res["mode"] == "paper"
    assert "BTC" in executor.paper_positions

    # Attempting to execute duplicate trade for BTC must be skipped
    dup_res = executor.execute_trade(setup)
    assert dup_res["status"] == "skipped"
    assert dup_res["reason"] == "Already in position"


def test_pnl_updates_and_sl_hit():
    config = BotConfig(dry_run=True)
    executor = OrderExecutor(config)

    # Long setup: entry 80000, SL 79500, TP 81250
    setup = create_mock_setup(symbol="BTC", direction=TrendBias.BULLISH, entry=80000.0, sl=79500.0, tp=81250.0, size=0.001)
    executor.execute_trade(setup)

    # 1. Price increases to 80500 -> PnL should be +0.50
    executor.check_open_positions_update({"BTC": 80500.0})
    pos = executor.paper_positions.get("BTC")
    assert pos is not None
    assert round(pos["pnl"], 2) == 0.50

    # 2. Price crashes to 79400 (below SL 79500) -> SL should trigger and close position
    executor.check_open_positions_update({"BTC": 79400.0})
    assert "BTC" not in executor.paper_positions
    # Check history contains closed SL record
    closed_trade = executor.trade_history[-1]
    assert closed_trade["status"] == "CLOSED_SL"
    assert closed_trade["pnl"] < 0


def test_tp_hit_for_short():
    config = BotConfig(dry_run=True)
    executor = OrderExecutor(config)

    # Short setup: entry 100, SL 105, TP 90
    setup = create_mock_setup(symbol="SOL", direction=TrendBias.BEARISH, entry=100.0, sl=105.0, tp=90.0, size=1.0)
    executor.execute_trade(setup)

    # Price drops to 89 (below TP 90) -> TP should trigger
    executor.check_open_positions_update({"SOL": 89.0})
    assert "SOL" not in executor.paper_positions
    closed_trade = executor.trade_history[-1]
    assert closed_trade["status"] == "CLOSED_TP"
    assert closed_trade["pnl"] > 0


def test_close_all_positions():
    config = BotConfig(dry_run=True)
    executor = OrderExecutor(config)

    setup_btc = create_mock_setup(symbol="BTC")
    setup_eth = create_mock_setup(symbol="ETH", entry=2500.0, sl=2480.0, tp=2550.0, size=0.01)

    executor.execute_trade(setup_btc)
    executor.execute_trade(setup_eth)
    assert len(executor.paper_positions) == 2

    closed_res = executor.close_all_positions()
    assert len(closed_res) == 2
    assert len(executor.paper_positions) == 0
