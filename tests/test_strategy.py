"""Unit tests for the Multi-Timeframe Strategy Coordinator."""

import pytest
import pandas as pd
from config import BotConfig
from src.strategy import MTFStrategy, SymbolEvaluation
from src.smc_engine import TrendBias
from src.market_data import MarketDataClient


def create_bullish_wave_candles(base_price=100.0):
    """Generate realistic candle waves that produce HH and HL structure."""
    wave_data = [
        [base_price + 0, base_price + 2, base_price - 2, base_price + 1],
        [base_price + 1, base_price + 6, base_price + 0, base_price + 5],  # peak 1
        [base_price + 5, base_price + 4, base_price + 1, base_price + 2],
        [base_price + 2, base_price + 3, base_price + 0, base_price + 1],  # trough 1
        [base_price + 1, base_price + 7, base_price + 1, base_price + 6],
        [base_price + 6, base_price + 12, base_price + 5, base_price + 11], # peak 2 (HH)
        [base_price + 11, base_price + 10, base_price + 5, base_price + 6],
        [base_price + 6, base_price + 7, base_price + 4, base_price + 5],   # trough 2 (HL)
        [base_price + 5, base_price + 13, base_price + 5, base_price + 12],
        [base_price + 12, base_price + 18, base_price + 11, base_price + 17], # peak 3 (HH)
        [base_price + 17, base_price + 16, base_price + 13, base_price + 14],
        [base_price + 14, base_price + 15, base_price + 12, base_price + 13], # trough 3 (HL)
        [base_price + 13, base_price + 22, base_price + 13, base_price + 21], # BOS body close
        [base_price + 21, base_price + 25, base_price + 20, base_price + 24],
    ]
    records = []
    base_t = 1700000000000
    for idx, (o, h, l, c) in enumerate(wave_data):
        records.append({
            "timestamp": base_t + idx * 300000,
            "open": float(o),
            "high": float(h),
            "low": float(l),
            "close": float(c),
            "volume": 100.0
        })
    return pd.DataFrame(records)


def test_strategy_mtf_evaluation_with_cached_data():
    config = BotConfig(dry_run=True)
    md = MarketDataClient(config)
    strategy = MTFStrategy(config, md)

    # Provide bullish structure candles for all timeframes
    mock_data = {
        "1w": create_bullish_wave_candles(base_price=100),
        "1d": create_bullish_wave_candles(base_price=120),
        "4h": create_bullish_wave_candles(base_price=130),
        "1h": create_bullish_wave_candles(base_price=135),
        "15m": create_bullish_wave_candles(base_price=140),
        "5m": create_bullish_wave_candles(base_price=142),
    }

    eval_res = strategy.evaluate_symbol("BTC", account_balance=15.0, cached_data=mock_data)
    assert isinstance(eval_res, SymbolEvaluation)
    assert eval_res.symbol == "BTC"
    assert eval_res.macro_bias == TrendBias.BULLISH
    assert eval_res.intermediate_trend == TrendBias.BULLISH
    assert eval_res.is_aligned is True
    assert "Macro: BULLISH" in eval_res.summary_message


def test_strategy_price_chase_rejection():
    """Verify that if current 5M price is >0.75% above trigger close, trade setup is discarded."""
    from unittest.mock import MagicMock
    from src.smc_engine import LiquiditySweep, SwingPoint, StructureEvent, EventType
    config = BotConfig(dry_run=True, strategy_mode="SWEEP_BOS")
    md = MarketDataClient(config)
    strategy = MTFStrategy(config, md)

    # Setup a mock sweep and mock 5m trigger with close at 80000
    sp = SwingPoint(index=2, timestamp=1700000000000, price=79500.0, is_high=False)
    sweep = LiquiditySweep(
        direction=TrendBias.BULLISH,
        swing_point=sp,
        sweep_candle_index=5,
        sweep_timestamp=1700001000000,
        wick_extreme=79400.0,
        candle_close=79800.0
    )
    trigger = StructureEvent(
        event_type=EventType.BOS,
        direction=TrendBias.BULLISH,
        candle_index=6,
        timestamp=1700001300000,
        trigger_price=79900.0,
        broken_swing=sp,
        candle_close=80000.0
    )

    strategy.smc.detect_active_15m_sweep = MagicMock(return_value=sweep)
    strategy.smc.detect_5m_trigger = MagicMock(return_value=trigger)

    # 1. Price is 80100 (< 0.75% chase) -> Trade setup accepted
    df_normal = pd.DataFrame([{"timestamp": 1700001300000, "open": 80000.0, "high": 80200.0, "low": 79900.0, "close": 80100.0, "volume": 10.0}])
    mock_data_normal = {tf: create_bullish_wave_candles() for tf in ["1w", "1d", "4h", "1h", "15m"]}
    mock_data_normal["5m"] = df_normal
    res_normal = strategy.evaluate_symbol("BTC", account_balance=15.0, cached_data=mock_data_normal)
    assert res_normal.trigger_5m is not None
    assert res_normal.trade_setup is not None

    # 2. Price chased to 81000 (> 1.2% chase from 80000) -> Trade setup discarded
    df_chased = pd.DataFrame([{"timestamp": 1700001300000, "open": 80000.0, "high": 81200.0, "low": 79900.0, "close": 81000.0, "volume": 10.0}])
    mock_data_chased = {tf: create_bullish_wave_candles() for tf in ["1w", "1d", "4h", "1h", "15m"]}
    mock_data_chased["5m"] = df_chased
    res_chased = strategy.evaluate_symbol("BTC", account_balance=15.0, cached_data=mock_data_chased)
    assert res_chased.trigger_5m is None
    assert res_chased.trade_setup is None
    assert "price chased too far" in res_chased.summary_message


def test_bot_max_open_positions_guard():
    """Verify SMCBot will not enter a new trade when max_open_positions limit is reached."""
    from unittest.mock import MagicMock
    from src.bot import SMCBot
    config = BotConfig(dry_run=True, max_open_positions=1)
    bot = SMCBot(config)
    bot.executor.paper_positions = {
        "ETH": {
            "symbol": "ETH",
            "status": "OPEN",
            "entry_price": 2500.0,
            "size": 0.01,
            "direction": "BULLISH",
            "stop_loss": 2450.0,
            "take_profit": 2600.0
        }
    }

    mock_setup = MagicMock()
    mock_setup.direction = TrendBias.BULLISH
    eval_res = MagicMock()
    eval_res.trade_setup = mock_setup
    eval_res.active_sweep_15m = MagicMock()
    eval_res.summary_message = "Ready"

    bot.strategy.evaluate_symbol = MagicMock(return_value=eval_res)
    bot.market_data.get_mid_prices = MagicMock(return_value={"BTC": 80000.0, "ETH": 2500.0})
    bot.market_data.get_account_state = MagicMock(return_value={"account_value": 15.0, "positions": []})
    bot.executor.execute_trade = MagicMock()

    bot.scan_once()
    # execute_trade should NOT be called because max_open_positions (1) is reached
    bot.executor.execute_trade.assert_not_called()


def test_strategy_demand_fvg_confluence_trade_trigger():
    """Verify that a confirmed 5M reaction off a Demand Zone generates a valid TradeSetup in DEMAND_FVG_CONFLUENCE mode."""
    from unittest.mock import MagicMock
    from src.smc_engine import DemandZone, ReactionConfirmation

    config = BotConfig(dry_run=True, strategy_mode="DEMAND_FVG_CONFLUENCE")
    md = MarketDataClient(config)
    strategy = MTFStrategy(config, md)

    # Mock candles for 1w, 1d, 4h, 1h, 15m
    mock_data = {tf: create_bullish_wave_candles(base_price=100) for tf in ["1w", "1d", "4h", "1h", "15m"]}

    # Create 5M candles where candle dips into demand zone with a rejection wick
    # Base: 98, Zone: [95, 100]
    df_5m = pd.DataFrame([
        {"timestamp": 1700000000000, "open": 102.0, "high": 104.0, "low": 101.0, "close": 103.0, "volume": 100.0},
        {"timestamp": 1700000300000, "open": 103.0, "high": 103.5, "low": 96.0, "close": 102.5, "volume": 150.0}, # Taps 96 with 96% lower wick
    ])
    mock_data["5m"] = df_5m

    # Create active demand zone on 1H
    dz = DemandZone(
        direction=TrendBias.BULLISH,
        top=100.0,
        bottom=95.0,
        candle_index=1,
        timestamp=1700000000000,
        timeframe="1h"
    )

    # Let 1H have this active demand zone
    strategy.smc.detect_demand_zones = MagicMock(return_value=[dz])

    res = strategy.evaluate_symbol("BTC", account_balance=15.0, cached_data=mock_data)
    assert res.reaction_confirmation is not None
    assert res.reaction_confirmation.confirmed is True
    assert res.reaction_confirmation.reaction_type == "REJECTION_WICK"
    assert res.trade_setup is not None
    assert res.trade_setup.direction == TrendBias.BULLISH
    assert res.trade_setup.stop_loss < 96.0
    assert "REACTION CONFIRMED" in res.summary_message


