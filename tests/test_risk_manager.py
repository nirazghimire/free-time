"""Comprehensive Unit Tests for Risk Management & Position Sizing.
Tests all risk scenarios, decimal rounding, and small balance edge cases.
"""

import pytest
from config import BotConfig
from src.risk_manager import RiskManager
from src.smc_engine import TrendBias, LiquiditySweep, SwingPoint


def test_calculate_trade_setup_long_success():
    config = BotConfig(
        risk_usd_per_trade=1.0,
        min_order_notional=11.0,
        leverage=3,
        target_rr=2.5,
        sl_buffer_pct=0.001
    )
    rm = RiskManager(config)

    dummy_sp = SwingPoint(index=2, timestamp=1700000000000, price=80000.0, is_high=False)
    sweep = LiquiditySweep(
        direction=TrendBias.BULLISH,
        swing_point=dummy_sp,
        sweep_candle_index=5,
        sweep_timestamp=1700001000000,
        wick_extreme=79500.0,
        candle_close=80100.0
    )

    setup = rm.calculate_trade_setup(
        symbol="BTC",
        direction=TrendBias.BULLISH,
        entry_price=80200.0,
        sweep=sweep,
        account_balance=15.0,  # $15 balance
        sz_decimals=5          # BTC szDecimals
    )

    assert setup is not None
    assert setup.direction == TrendBias.BULLISH
    assert setup.entry_price == 80200.0
    assert setup.stop_loss < 79500.0
    assert setup.take_profit > 80200.0
    assert setup.risk_reward >= 2.4
    assert setup.size > 0
    assert setup.notional_usd >= 10.5


def test_calculate_trade_setup_short_success():
    config = BotConfig(
        risk_usd_per_trade=1.0,
        min_order_notional=11.0,
        leverage=3,
        target_rr=2.5,
        sl_buffer_pct=0.001
    )
    rm = RiskManager(config)

    dummy_sp = SwingPoint(index=2, timestamp=1700000000000, price=2500.0, is_high=True)
    sweep = LiquiditySweep(
        direction=TrendBias.BEARISH,
        swing_point=dummy_sp,
        sweep_candle_index=5,
        sweep_timestamp=1700001000000,
        wick_extreme=2520.0,
        candle_close=2495.0
    )

    setup = rm.calculate_trade_setup(
        symbol="ETH",
        direction=TrendBias.BEARISH,
        entry_price=2490.0,
        sweep=sweep,
        account_balance=20.0,  # $20 balance
        sz_decimals=4          # ETH szDecimals
    )

    assert setup is not None
    assert setup.direction == TrendBias.BEARISH
    assert setup.entry_price == 2490.0
    assert setup.stop_loss > 2520.0
    assert setup.take_profit < 2490.0
    assert setup.risk_reward >= 2.4
    assert setup.size > 0
    assert setup.notional_usd >= 10.5


def test_edge_case_invalid_sl_levels():
    config = BotConfig(min_order_notional=11.0, leverage=3)
    rm = RiskManager(config)
    dummy_sp = SwingPoint(index=2, timestamp=1700000000000, price=100.0, is_high=False)

    # Long setup where sweep wick is ABOVE entry (invalid for a long SL)
    invalid_sweep_long = LiquiditySweep(
        direction=TrendBias.BULLISH,
        swing_point=dummy_sp,
        sweep_candle_index=5,
        sweep_timestamp=1700001000000,
        wick_extreme=105.0,  # > entry price 100
        candle_close=101.0
    )
    setup = rm.calculate_trade_setup(
        symbol="SOL",
        direction=TrendBias.BULLISH,
        entry_price=100.0,
        sweep=invalid_sweep_long,
        account_balance=15.0,
        sz_decimals=2
    )
    assert setup is None

    # Short setup where sweep wick is BELOW entry (invalid for a short SL)
    invalid_sweep_short = LiquiditySweep(
        direction=TrendBias.BEARISH,
        swing_point=dummy_sp,
        sweep_candle_index=5,
        sweep_timestamp=1700001000000,
        wick_extreme=95.0,  # < entry price 100
        candle_close=99.0
    )
    setup_short = rm.calculate_trade_setup(
        symbol="SOL",
        direction=TrendBias.BEARISH,
        entry_price=100.0,
        sweep=invalid_sweep_short,
        account_balance=15.0,
        sz_decimals=2
    )
    assert setup_short is None


def test_edge_case_zero_or_negative_price():
    config = BotConfig()
    rm = RiskManager(config)
    dummy_sp = SwingPoint(index=2, timestamp=1700000000000, price=100.0, is_high=False)
    sweep = LiquiditySweep(
        direction=TrendBias.BULLISH,
        swing_point=dummy_sp,
        sweep_candle_index=5,
        sweep_timestamp=1700001000000,
        wick_extreme=95.0,
        candle_close=99.0
    )
    assert rm.calculate_trade_setup("BTC", TrendBias.BULLISH, 0.0, sweep, 15.0, 5) is None
    assert rm.calculate_trade_setup("BTC", TrendBias.BULLISH, -50.0, sweep, 15.0, 5) is None


def test_edge_case_insufficient_leverage_buying_power():
    config = BotConfig(min_order_notional=11.0, leverage=1)  # 1x leverage
    rm = RiskManager(config)
    dummy_sp = SwingPoint(index=2, timestamp=1700000000000, price=100.0, is_high=False)
    sweep = LiquiditySweep(
        direction=TrendBias.BULLISH,
        swing_point=dummy_sp,
        sweep_candle_index=5,
        sweep_timestamp=1700001000000,
        wick_extreme=95.0,
        candle_close=99.0
    )
    # Account balance $5.0 with 1x leverage = max $5.0 buying power < $11.0 min notional
    setup = rm.calculate_trade_setup("SOL", TrendBias.BULLISH, 100.0, sweep, 5.0, 2)
    assert setup is None


def test_sol_lot_size_decimal_precision():
    config = BotConfig(min_order_notional=11.0, leverage=3)
    rm = RiskManager(config)
    dummy_sp = SwingPoint(index=2, timestamp=1700000000000, price=100.0, is_high=True)
    sweep = LiquiditySweep(
        direction=TrendBias.BEARISH,
        swing_point=dummy_sp,
        sweep_candle_index=5,
        sweep_timestamp=1700001000000,
        wick_extreme=108.0,
        candle_close=103.0
    )
    # SOL has 2 decimals
    setup = rm.calculate_trade_setup("SOL", TrendBias.BEARISH, 105.0, sweep, 15.0, sz_decimals=2)
    assert setup is not None
    # Check that size has at most 2 decimal places
    size_str = str(setup.size)
    if "." in size_str:
        decimals_len = len(size_str.split(".")[1])
        assert decimals_len <= 2
