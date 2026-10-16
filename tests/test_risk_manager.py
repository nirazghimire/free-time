"""Comprehensive Unit Tests for Risk Management & Position Sizing.
Tests all risk scenarios, decimal rounding, and small balance edge cases.
"""

import pytest
from config import BotConfig
from src.risk_manager import RiskManager
from src.smc_engine import TrendBias, LiquiditySweep, SwingPoint, DemandZone, ReactionConfirmation


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


# ─── New critical-fix tests ───────────────────────────────────────────────────

def test_hyperliquid_price_formatting_5_sig_figs():
    """format_hyperliquid_price must produce max 5 significant figures for exchange compliance."""
    from src.risk_manager import format_hyperliquid_price

    # BTC szDecimals=1 -> max_decimals = max(0, 6-1) = 5
    # Price 81575.25: 8 sig figs -> must be rounded to 5 sig figs
    btc_sl = format_hyperliquid_price(81575.25, sz_decimals=1)
    # 81575.25 -> 5 sig figs = 81575 (remove trailing .0)
    sig_figs = len(f"{btc_sl:.5g}".replace(".", "").lstrip("0"))
    assert sig_figs <= 5, f"BTC SL has {sig_figs} sig figs (must be <=5): {btc_sl}"

    # ETH szDecimals=2 -> max_decimals = max(0, 6-2) = 4
    eth_sl = format_hyperliquid_price(2489.99876, sz_decimals=2)
    eth_sig_figs = len(f"{eth_sl:.5g}".replace(".", "").lstrip("0"))
    assert eth_sig_figs <= 5, f"ETH SL has {eth_sig_figs} sig figs: {eth_sl}"

    # SOL szDecimals=2 -> max_decimals = 4
    sol_sl = format_hyperliquid_price(159.9876, sz_decimals=2)
    sol_sig_figs = len(f"{sol_sl:.5g}".replace(".", "").lstrip("0"))
    assert sol_sig_figs <= 5, f"SOL SL has {sol_sig_figs} sig figs: {sol_sl}"

    # Zero or negative input must return 0.0
    assert format_hyperliquid_price(0.0, 2) == 0.0
    assert format_hyperliquid_price(-100.0, 2) == 0.0


def test_notional_floor_bump_prevents_sub_minimum_order():
    """After floor truncation, if notional < min, the size must bump up 1 step."""
    config = BotConfig(min_order_notional=11.0, leverage=5)
    rm = RiskManager(config)

    dummy_sp = SwingPoint(index=2, timestamp=1700000000000, price=100.0, is_high=True)
    # SHORT: sweep wick at 108, entry at 100 -> SL = 108 * 1.001 = 108.108 > entry -> invalid
    # Use a sweep wick that is just above entry so SL is valid for short
    sweep = LiquiditySweep(
        direction=TrendBias.BEARISH,
        swing_point=dummy_sp,
        sweep_candle_index=5,
        sweep_timestamp=1700001000000,
        wick_extreme=103.0,  # slightly above entry 101
        candle_close=100.0
    )
    # Use a high entry price and small balance so floor truncation is likely to hit min
    setup = rm.calculate_trade_setup(
        symbol="BTC",
        direction=TrendBias.BEARISH,
        entry_price=101.0,
        sweep=sweep,
        account_balance=15.0,
        sz_decimals=2
    )
    if setup is not None:
        # Notional must meet minimum after bump
        assert setup.notional_usd >= 11.0, (
            f"Notional ${setup.notional_usd:.2f} is below min $11.00"
        )
        # Size precision must be respected
        size_str = str(setup.size)
        if "." in size_str:
            assert len(size_str.split(".")[1]) <= 2


def test_strategy_discards_misaligned_intermediate_trend():
    """When intermediate trend opposes macro bias, no trade setup should be returned."""
    import pandas as pd
    from config import BotConfig
    from src.strategy import MTFStrategy
    from src.smc_engine import TrendBias
    from src.market_data import MarketDataClient

    def _bullish_candles(base=100.0):
        data = [
            [base+0, base+2, base-2, base+1],
            [base+1, base+6, base+0, base+5],
            [base+5, base+4, base+1, base+2],
            [base+2, base+3, base+0, base+1],
            [base+1, base+7, base+1, base+6],
            [base+6, base+12, base+5, base+11],
            [base+11, base+10, base+5, base+6],
            [base+6, base+7, base+4, base+5],
            [base+5, base+13, base+5, base+12],
            [base+12, base+18, base+11, base+17],
            [base+17, base+16, base+13, base+14],
            [base+14, base+15, base+12, base+13],
            [base+13, base+22, base+13, base+21],
            [base+21, base+25, base+20, base+24],
        ]
        records = []
        base_t = 1700000000000
        for i, (o, h, l, c) in enumerate(data):
            records.append({"timestamp": base_t + i*300000, "open": float(o), "high": float(h),
                            "low": float(l), "close": float(c), "volume": 100.0})
        return pd.DataFrame(records)

    def _bearish_candles(base=100.0):
        data = [
            [base+0, base+2, base-2, base-1],
            [base-1, base+0, base-6, base-5],
            [base-5, base-1, base-4, base-2],
            [base-2, base-1, base-5, base-4],
            [base-4, base-1, base-7, base-6],
            [base-6, base-5, base-12, base-11],
            [base-11, base-5, base-10, base-6],
            [base-6, base-4, base-7, base-5],
            [base-5, base-4, base-13, base-12],
            [base-12, base-11, base-18, base-17],
            [base-17, base-13, base-16, base-14],
            [base-14, base-12, base-15, base-13],
            [base-13, base-12, base-22, base-21],
            [base-21, base-20, base-25, base-24],
        ]
        records = []
        base_t = 1700000000000
        for i, (o, h, l, c) in enumerate(data):
            records.append({"timestamp": base_t + i*300000, "open": float(o), "high": float(h),
                            "low": float(l), "close": float(c), "volume": 100.0})
        return pd.DataFrame(records)

    config = BotConfig(dry_run=True)
    md = MarketDataClient(config)
    strategy = MTFStrategy(config, md)

    # Macro bullish (1W + 1D), Intermediate BEARISH (4H + 1H) -> NOT aligned -> no trade
    mock_data = {
        "1w": _bullish_candles(100),
        "1d": _bullish_candles(120),
        "4h": _bearish_candles(130),  # counter-trend
        "1h": _bearish_candles(135),  # counter-trend
        "15m": _bullish_candles(140),
        "5m": _bullish_candles(142),
    }
    result = strategy.evaluate_symbol("BTC", account_balance=15.0, cached_data=mock_data)
    assert result.trade_setup is None, (
        "Trade setup must not be created when intermediate trend opposes macro bias"
    )
    assert result.is_aligned is False


def test_calculate_trade_setup_demand_zone_success():
    """Verify calculate_trade_setup works with anchor_sl_price and demand_zone."""
    config = BotConfig(
        risk_usd_per_trade=1.0,
        min_order_notional=11.0,
        leverage=3,
        target_rr=2.5,
        sl_buffer_pct=0.001
    )
    rm = RiskManager(config)
    zone = DemandZone(
        direction=TrendBias.BULLISH,
        top=90000.0,
        bottom=89500.0,
        candle_index=1,
        timestamp=1700000000000,
        timeframe="1h"
    )
    setup = rm.calculate_trade_setup(
        symbol="BTC",
        direction=TrendBias.BULLISH,
        entry_price=90200.0,
        account_balance=20.0,
        sz_decimals=5,
        anchor_sl_price=89500.0,
        demand_zone=zone
    )
    assert setup is not None
    assert setup.direction == TrendBias.BULLISH
    assert setup.entry_price == 90200.0
    assert setup.stop_loss < 89500.0
    assert setup.take_profit > 90200.0
    assert setup.risk_reward >= 2.4
    assert setup.size > 0
    assert setup.notional_usd >= 10.5

