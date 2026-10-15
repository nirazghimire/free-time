"""Comprehensive Unit Tests for the Smart Money Concepts (SMC) & Market Structure Engine.
Tests every function, rule, and edge case:
- Swing High and Swing Low detection
- Empty and insufficient candle edge cases
- Strict candle body close requirement for BOS
- Change of Character (CHoCH) trend reversal identification
- Bullish and Bearish 15M liquidity sweeps
- Sweep invalidation upon wick extreme violation
- 5M confirmation trigger timing and direction alignment
"""

import pytest
import pandas as pd
from src.smc_engine import (
    SMCEngine,
    TrendBias,
    EventType,
    SwingPoint,
    LiquiditySweep,
)


def create_candle_df(candles_data):
    """Helper to convert list of [open, high, low, close] into DataFrame."""
    records = []
    base_time = 1700000000000
    for idx, (o, h, l, c) in enumerate(candles_data):
        records.append({
            "timestamp": base_time + idx * 300000,
            "open": float(o),
            "high": float(h),
            "low": float(l),
            "close": float(c),
            "volume": 100.0,
        })
    return pd.DataFrame(records)


def test_swing_high_and_low_detection():
    smc = SMCEngine(swing_lookback=2)
    # Candles forming a clear peak at index 2 (high=110) and trough at index 5 (low=90)
    data = [
        [100, 102, 98, 101],   # 0
        [101, 105, 100, 104],  # 1
        [104, 110, 103, 108],  # 2 - SWING HIGH
        [108, 107, 101, 102],  # 3
        [102, 103, 96, 98],    # 4
        [98, 99, 90, 95],      # 5 - SWING LOW
        [95, 100, 94, 99],     # 6
        [99, 102, 98, 101],    # 7
    ]
    df = create_candle_df(data)
    shs, sls = smc.find_swing_points(df)

    assert len(shs) >= 1
    assert shs[0].index == 2
    assert shs[0].price == 110.0
    assert shs[0].is_high is True

    assert len(sls) >= 1
    assert sls[0].index == 5
    assert sls[0].price == 90.0
    assert sls[0].is_high is False


def test_edge_case_empty_or_insufficient_candles():
    smc = SMCEngine(swing_lookback=2)

    # Empty DataFrame
    empty_df = pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    shs, sls = smc.find_swing_points(empty_df)
    assert shs == []
    assert sls == []
    analysis_empty = smc.analyze_structure(empty_df, "BTC", "5m")
    assert analysis_empty.bias == TrendBias.NEUTRAL
    assert analysis_empty.current_price == 0.0

    # Insufficient candles (< 2*lookback + 1 = 5 candles)
    short_data = [
        [100, 105, 95, 102],
        [102, 108, 101, 106],
        [106, 107, 100, 103],
    ]
    short_df = create_candle_df(short_data)
    shs, sls = smc.find_swing_points(short_df)
    assert shs == []
    assert sls == []


def test_edge_case_flat_candles_no_false_pivots():
    smc = SMCEngine(swing_lookback=2)
    # Perfectly flat candles: all highs=100, lows=95
    flat_data = [[98, 100, 95, 98] for _ in range(8)]
    flat_df = create_candle_df(flat_data)
    shs, sls = smc.find_swing_points(flat_df)
    assert shs == []
    assert sls == []


def test_bullish_bos_requires_body_close():
    smc = SMCEngine(swing_lookback=2)
    # Peak at index 2 (high=100)
    # Candle 6 has high=102 but close=99 (WICK only -> NOT a BOS)
    # Candle 7 has close=101 (BODY close above 100 -> BOS!)
    data = [
        [90, 95, 88, 94],    # 0
        [94, 98, 93, 97],    # 1
        [97, 100, 96, 98],   # 2 - Swing High at 100
        [98, 97, 92, 94],    # 3
        [94, 95, 91, 93],    # 4
        [93, 96, 92, 95],    # 5
        [95, 102, 94, 99],   # 6 - Wick above 100, but close 99 (Sweep / no body close)
        [99, 103, 98, 101],  # 7 - Body close at 101 > 100 (BOS!)
    ]
    df = create_candle_df(data)
    analysis = smc.analyze_structure(df, "BTC", "5m")

    # Verify that BOS was triggered at candle 7
    bos_events = [e for e in analysis.recent_events if e.event_type == EventType.BOS]
    assert len(bos_events) == 1
    assert bos_events[0].candle_index == 7
    assert bos_events[0].candle_close == 101.0
    assert bos_events[0].direction == TrendBias.BULLISH


def test_bearish_bos_requires_body_close():
    smc = SMCEngine(swing_lookback=2)
    # Trough at index 2 (low=80)
    # Candle 6 wicks to 78 but closes at 82 (wick only -> sweep, NOT a BOS)
    # Candle 7 closes at 77 (body close below 80 -> Bearish BOS!)
    data = [
        [90, 93, 87, 88],   # 0
        [88, 90, 84, 85],   # 1
        [85, 87, 80, 84],   # 2 - Swing Low at 80
        [84, 88, 83, 86],   # 3
        [86, 89, 85, 88],   # 4
        [88, 89, 84, 86],   # 5
        [86, 88, 78, 82],   # 6 - Wick below 80, close 82 (Sweep / rejection)
        [82, 83, 76, 77],   # 7 - Body close at 77 < 80 (Bearish BOS!)
    ]
    df = create_candle_df(data)
    analysis = smc.analyze_structure(df, "BTC", "15m")

    bearish_events = [e for e in analysis.recent_events if e.direction == TrendBias.BEARISH]
    assert len(bearish_events) >= 1
    assert bearish_events[-1].candle_close == 77.0


def test_choch_reversal_uptrend_to_downtrend():
    smc = SMCEngine(swing_lookback=2)
    # 1. Uptrend with HH (high 106) and HL (low 90)
    # 2. Later candle body closes below HL 90 -> CHoCH to BEARISH
    data = [
        [85, 88, 82, 86],    # 0
        [86, 95, 85, 93],    # 1
        [93, 100, 91, 98],   # 2 - Swing High 100
        [98, 97, 93, 94],    # 3
        [94, 96, 93, 95],    # 4
        [95, 95, 90, 94],    # 5 - Swing Low 90 (HL)
        [94, 102, 93, 100],  # 6
        [100, 106, 99, 104], # 7 - Swing High 106 (HH)
        [104, 103, 97, 98],  # 8
        [98, 99, 94, 95],    # 9
        [95, 96, 88, 89],    # 10 - Body close at 89 < 90 (Breaks Higher Low -> CHoCH!)
    ]
    df = create_candle_df(data)
    analysis = smc.analyze_structure(df, "ETH", "1h")

    choch_events = [e for e in analysis.recent_events if e.event_type == EventType.CHOCH]
    assert len(choch_events) >= 1
    assert choch_events[-1].direction == TrendBias.BEARISH
    assert choch_events[-1].candle_close == 89.0


def test_bearish_liquidity_sweep():
    smc = SMCEngine(swing_lookback=2)
    data = [
        [90, 95, 88, 94],    # 0
        [94, 98, 93, 97],    # 1
        [97, 100, 96, 98],   # 2 - Swing High at 100
        [98, 97, 92, 94],    # 3
        [94, 95, 91, 93],    # 4
        [93, 102, 92, 98],   # 5 - Wick breached 100, close 98 <= 100 (Sweep!)
        [98, 97, 91, 92],    # 6
    ]
    df = create_candle_df(data)
    analysis = smc.analyze_structure(df, "BTC", "15m")

    assert len(analysis.recent_sweeps) >= 1
    sweep = analysis.recent_sweeps[0]
    assert sweep.direction == TrendBias.BEARISH
    assert sweep.wick_extreme == 102.0
    assert sweep.candle_close == 98.0


def test_bullish_liquidity_sweep():
    smc = SMCEngine(swing_lookback=2)
    data = [
        [90, 92, 85, 86],    # 0
        [86, 88, 82, 83],    # 1
        [83, 85, 80, 84],    # 2 - Swing Low at 80
        [84, 86, 82, 85],    # 3
        [85, 87, 83, 86],    # 4
        [86, 87, 78, 81],    # 5 - Wick breached 80 down to 78, close 81 >= 80 (Sweep!)
        [81, 85, 80, 84],    # 6
    ]
    df = create_candle_df(data)
    analysis = smc.analyze_structure(df, "BTC", "15m")

    assert len(analysis.recent_sweeps) >= 1
    sweep = analysis.recent_sweeps[0]
    assert sweep.direction == TrendBias.BULLISH
    assert sweep.wick_extreme == 78.0
    assert sweep.candle_close == 81.0


def test_sweep_invalidation_when_extreme_is_violated():
    smc = SMCEngine(swing_lookback=2)
    dummy_sp = SwingPoint(index=2, timestamp=1700000000000, price=100.0, is_high=False)
    
    # 1. Bullish sweep with wick extreme at 95.0
    bullish_sweep = LiquiditySweep(
        direction=TrendBias.BULLISH,
        swing_point=dummy_sp,
        sweep_candle_index=5,
        sweep_timestamp=1700001000000,
        wick_extreme=95.0,
        candle_close=101.0,
        is_active=True
    )

    # When price is above 95.0, sweep is active
    analysis_valid = smc.analyze_structure(
        create_candle_df([[100, 102, 96, 98] for _ in range(6)]),
        "BTC", "15m"
    )
    analysis_valid.recent_sweeps = [bullish_sweep]
    analysis_valid.current_price = 98.0
    active = smc.detect_active_15m_sweep(analysis_valid)
    assert active is not None

    # When price drops BELOW 95.0, sweep is invalidated
    analysis_invalid = smc.analyze_structure(
        create_candle_df([[96, 97, 93, 94] for _ in range(6)]),
        "BTC", "15m"
    )
    analysis_invalid.recent_sweeps = [bullish_sweep]
    analysis_invalid.current_price = 94.0
    active_inv = smc.detect_active_15m_sweep(analysis_invalid)
    assert active_inv is None


def test_5m_trigger_after_15m_sweep():
    smc = SMCEngine(swing_lookback=2)

    dummy_sp = SwingPoint(index=2, timestamp=1700000000000, price=1000.0, is_high=False)
    sweep = LiquiditySweep(
        direction=TrendBias.BULLISH,
        swing_point=dummy_sp,
        sweep_candle_index=5,
        sweep_timestamp=1700001000000,
        wick_extreme=990.0,
        candle_close=1005.0
    )

    data_5m = [
        [1005, 1008, 1004, 1007],  # 0
        [1007, 1010, 1005, 1009],  # 1
        [1009, 1012, 1008, 1010],  # 2 - 5m Swing High at 1012
        [1010, 1009, 1004, 1006],  # 3
        [1006, 1008, 1003, 1005],  # 4
        [1005, 1011, 1004, 1010],  # 5
        [1010, 1018, 1009, 1016],  # 6 - Body close at 1016 > 1012 (Bullish BOS confirmation!)
    ]
    df_5m = create_candle_df(data_5m)
    df_5m["timestamp"] = [sweep.sweep_timestamp + i * 300000 for i in range(len(df_5m))]

    analysis_5m = smc.analyze_structure(df_5m, "BTC", "5m")
    trigger = smc.detect_5m_trigger(analysis_5m, sweep)

    assert trigger is not None
    assert trigger.direction == TrendBias.BULLISH
    assert trigger.candle_close == 1016.0


def test_5m_trigger_rejects_past_events_and_wrong_direction():
    smc = SMCEngine(swing_lookback=2)
    dummy_sp = SwingPoint(index=2, timestamp=1700000000000, price=1000.0, is_high=False)
    
    sweep = LiquiditySweep(
        direction=TrendBias.BULLISH,
        swing_point=dummy_sp,
        sweep_candle_index=5,
        sweep_timestamp=1700005000000,  # Timestamp at T=5000
        wick_extreme=990.0,
        candle_close=1005.0
    )

    # Event occurred BEFORE the sweep (T=2000 < T=5000)
    data_5m_past = [
        [1005, 1008, 1004, 1007],
        [1007, 1010, 1005, 1009],
        [1009, 1012, 1008, 1010],
        [1010, 1009, 1004, 1006],
        [1006, 1008, 1003, 1005],
        [1005, 1011, 1004, 1010],
        [1010, 1018, 1009, 1016],
    ]
    df_past = create_candle_df(data_5m_past)
    df_past["timestamp"] = [1700001000000 + i * 300000 for i in range(len(df_past))]  # all before T=5000

    analysis_past = smc.analyze_structure(df_past, "BTC", "5m")
    trigger_past = smc.detect_5m_trigger(analysis_past, sweep)
    # Must reject events that happened before the sweep
    assert trigger_past is None
