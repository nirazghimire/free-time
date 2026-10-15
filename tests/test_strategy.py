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
