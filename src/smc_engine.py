"""Smart Money Concepts (SMC) & Market Structure Engine.
Implements:
- Swing High & Swing Low detection (Pivot fractals)
- Break of Structure (BOS) with candle body close confirmation
- Change of Character (CHoCH) with candle body close confirmation
- 15-Minute Liquidity Sweeps (wick breaches swing point, candle closes inside)
- 5-Minute Entry Trigger (BOS/CHoCH confirmation following a liquidity sweep)
- Multi-Timeframe Market Structure analysis (1W, 1D, 4H, 1H, 15M, 5M)
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple, Dict
import pandas as pd
import numpy as np


class TrendBias(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class EventType(str, Enum):
    BOS = "BOS"          # Break of Structure (trend continuation)
    CHOCH = "CHOCH"      # Change of Character (trend reversal)
    SWEEP = "SWEEP"      # Liquidity Sweep (fakeout / stop hunt)


@dataclass
class SwingPoint:
    index: int
    timestamp: int
    price: float
    is_high: bool  # True for Swing High, False for Swing Low
    label: str = ""  # HH, LH, HL, LL
    broken: bool = False  # True once price closes beyond this swing point


@dataclass
class StructureEvent:
    event_type: EventType
    direction: TrendBias  # BULLISH or BEARISH
    candle_index: int
    timestamp: int
    trigger_price: float
    broken_swing: SwingPoint
    candle_close: float


@dataclass
class LiquiditySweep:
    direction: TrendBias  # BULLISH (swept sell-side) or BEARISH (swept buy-side)
    swing_point: SwingPoint
    sweep_candle_index: int
    sweep_timestamp: int
    wick_extreme: float  # Low of the wick for bullish sweep; High of the wick for bearish sweep
    candle_close: float
    is_active: bool = True


@dataclass
class FairValueGap:
    direction: TrendBias  # BULLISH or BEARISH
    top: float
    bottom: float
    candle_index: int
    timestamp: int
    mitigated: bool = False
    size_pct: float = 0.0


@dataclass
class TimeframeAnalysis:
    symbol: str
    timeframe: str
    bias: TrendBias
    current_price: float
    last_swing_high: Optional[SwingPoint] = None
    last_swing_low: Optional[SwingPoint] = None
    recent_events: List[StructureEvent] = field(default_factory=list)
    recent_sweeps: List[LiquiditySweep] = field(default_factory=list)
    swing_highs: List[SwingPoint] = field(default_factory=list)
    swing_lows: List[SwingPoint] = field(default_factory=list)
    active_fvgs: List[FairValueGap] = field(default_factory=list)
    total_candles: int = 0
    latest_timestamp: int = 0


class SMCEngine:
    def __init__(self, swing_lookback: int = 2):
        """
        :param swing_lookback: Number of bars on left and right required to qualify a swing pivot.
        """
        self.swing_lookback = swing_lookback

    def find_swing_points(self, df: pd.DataFrame) -> Tuple[List[SwingPoint], List[SwingPoint]]:
        """
        Identify swing highs and swing lows using fractal lookback.
        A candle i is a Swing High if high[i] > high[i-k] and high[i] > high[i+k] for k in 1..lookback.
        A candle i is a Swing Low if low[i] < low[i-k] and low[i] < low[i+k] for k in 1..lookback.
        """
        if len(df) < (self.swing_lookback * 2 + 1):
            return [], []

        highs = df["high"].values
        lows = df["low"].values
        timestamps = df["timestamp"].values
        n = len(df)
        k = self.swing_lookback

        swing_highs: List[SwingPoint] = []
        swing_lows: List[SwingPoint] = []

        # Find raw pivots
        for i in range(k, n - k):
            current_h = highs[i]
            is_sh = True
            for j in range(1, k + 1):
                if highs[i - j] >= current_h or highs[i + j] >= current_h:
                    is_sh = False
                    break
            if is_sh:
                # Classify label relative to previous swing high
                label = "SH"
                if swing_highs:
                    label = "HH" if current_h > swing_highs[-1].price else "LH"
                sp = SwingPoint(
                    index=i,
                    timestamp=int(timestamps[i]),
                    price=float(current_h),
                    is_high=True,
                    label=label
                )
                swing_highs.append(sp)

            current_l = lows[i]
            is_sl = True
            for j in range(1, k + 1):
                if lows[i - j] <= current_l or lows[i + j] <= current_l:
                    is_sl = False
                    break
            if is_sl:
                # Classify label relative to previous swing low
                label = "SL"
                if swing_lows:
                    label = "HL" if current_l > swing_lows[-1].price else "LL"
                sp = SwingPoint(
                    index=i,
                    timestamp=int(timestamps[i]),
                    price=float(current_l),
                    is_high=False,
                    label=label
                )
                swing_lows.append(sp)

        return swing_highs, swing_lows

    def analyze_structure(self, df: pd.DataFrame, symbol: str, timeframe: str) -> TimeframeAnalysis:
        """
        Analyze market structure:
        - Detect Swing Highs & Lows
        - Track active unbroken swing levels
        - Check for Break of Structure (BOS) on candle body close
        - Check for Change of Character (CHoCH) on candle body close
        - Detect Liquidity Sweeps (wick breaks swing, body closes inside)
        - Determine overall Timeframe Bias (BULLISH, BEARISH, NEUTRAL)
        """
        if df.empty:
            return TimeframeAnalysis(
                symbol=symbol,
                timeframe=timeframe,
                bias=TrendBias.NEUTRAL,
                current_price=0.0
            )

        current_price = float(df["close"].iloc[-1])
        swing_highs, swing_lows = self.find_swing_points(df)

        if not swing_highs and not swing_lows:
            all_fvgs = self.detect_fair_value_gaps(df)
            active_fvgs = [f for f in all_fvgs if not f.mitigated]
            return TimeframeAnalysis(
                symbol=symbol,
                timeframe=timeframe,
                bias=TrendBias.NEUTRAL,
                current_price=current_price,
                active_fvgs=active_fvgs[-10:],
                total_candles=len(df),
                latest_timestamp=int(df["timestamp"].iloc[-1]) if ("timestamp" in df.columns and len(df) > 0) else 0
            )

        # Merge swings chronologically
        all_swings = sorted(swing_highs + swing_lows, key=lambda s: s.index)

        closes = df["close"].values
        highs = df["high"].values
        lows = df["low"].values
        timestamps = df["timestamp"].values

        events: List[StructureEvent] = []
        sweeps: List[LiquiditySweep] = []
        
        # Initial trend heuristic from earliest swings
        current_trend = TrendBias.NEUTRAL
        if len(swing_highs) >= 2 and len(swing_lows) >= 2:
            if swing_highs[-1].price > swing_highs[-2].price and swing_lows[-1].price > swing_lows[-2].price:
                current_trend = TrendBias.BULLISH
            elif swing_highs[-1].price < swing_highs[-2].price and swing_lows[-1].price < swing_lows[-2].price:
                current_trend = TrendBias.BEARISH

        # Iterate through candles after the first confirmed swing
        start_candle = all_swings[0].index + self.swing_lookback + 1 if all_swings else 0

        active_sh: Optional[SwingPoint] = None
        active_sl: Optional[SwingPoint] = None

        # Build list of active (unbroken) swings as we walk forward
        for i in range(start_candle, len(df)):
            c_close = closes[i]
            c_high = highs[i]
            c_low = lows[i]
            c_time = int(timestamps[i])

            # Update known confirmed swings up to this point
            confirmed_swings = [s for s in all_swings if (s.index + self.swing_lookback) <= i and not s.broken]
            recent_sh = [s for s in confirmed_swings if s.is_high]
            recent_sl = [s for s in confirmed_swings if not s.is_high]

            target_sh = recent_sh[-1] if recent_sh else None
            target_sl = recent_sl[-1] if recent_sl else None

            # 1. Check for Bullish Break (above Swing High)
            if target_sh:
                # Body close strictly above swing high -> Structure Break
                if c_close > target_sh.price:
                    # If current trend is BEARISH, breaking the high is a CHoCH (reversal)
                    # If current trend is BULLISH, breaking the high is a BOS (continuation)
                    is_reversal = (current_trend == TrendBias.BEARISH)
                    event_type = EventType.CHOCH if is_reversal else EventType.BOS
                    current_trend = TrendBias.BULLISH
                    target_sh.broken = True

                    events.append(StructureEvent(
                        event_type=event_type,
                        direction=TrendBias.BULLISH,
                        candle_index=i,
                        timestamp=c_time,
                        trigger_price=target_sh.price,
                        broken_swing=target_sh,
                        candle_close=c_close
                    ))
                # Wick breached swing high, but body closed BELOW -> Bearish Liquidity Sweep
                elif c_high > target_sh.price and c_close <= target_sh.price:
                    if sweeps and sweeps[-1].swing_point == target_sh and sweeps[-1].direction == TrendBias.BEARISH:
                        if c_high > sweeps[-1].wick_extreme:
                            sweeps[-1].wick_extreme = c_high
                            sweeps[-1].sweep_candle_index = i
                            sweeps[-1].sweep_timestamp = c_time
                            sweeps[-1].candle_close = c_close
                    else:
                        sweeps.append(LiquiditySweep(
                            direction=TrendBias.BEARISH,  # Bearish rejection after sweeping buy-side
                            swing_point=target_sh,
                            sweep_candle_index=i,
                            sweep_timestamp=c_time,
                            wick_extreme=c_high,
                            candle_close=c_close,
                            is_active=True
                        ))

            # 2. Check for Bearish Break (below Swing Low)
            if target_sl:
                # Body close strictly below swing low -> Structure Break
                if c_close < target_sl.price:
                    # If current trend is BULLISH, breaking the low is a CHoCH (reversal)
                    # If current trend is BEARISH, breaking the low is a BOS (continuation)
                    is_reversal = (current_trend == TrendBias.BULLISH)
                    event_type = EventType.CHOCH if is_reversal else EventType.BOS
                    current_trend = TrendBias.BEARISH
                    target_sl.broken = True

                    events.append(StructureEvent(
                        event_type=event_type,
                        direction=TrendBias.BEARISH,
                        candle_index=i,
                        timestamp=c_time,
                        trigger_price=target_sl.price,
                        broken_swing=target_sl,
                        candle_close=c_close
                    ))
                # Wick breached swing low, but body closed ABOVE -> Bullish Liquidity Sweep
                elif c_low < target_sl.price and c_close >= target_sl.price:
                    if sweeps and sweeps[-1].swing_point == target_sl and sweeps[-1].direction == TrendBias.BULLISH:
                        if c_low < sweeps[-1].wick_extreme:
                            sweeps[-1].wick_extreme = c_low
                            sweeps[-1].sweep_candle_index = i
                            sweeps[-1].sweep_timestamp = c_time
                            sweeps[-1].candle_close = c_close
                    else:
                        sweeps.append(LiquiditySweep(
                            direction=TrendBias.BULLISH,  # Bullish rejection after sweeping sell-side
                            swing_point=target_sl,
                            sweep_candle_index=i,
                            sweep_timestamp=c_time,
                            wick_extreme=c_low,
                            candle_close=c_close,
                            is_active=True
                        ))

        # Check latest active unbroken swing high & low
        unbroken_sh = [s for s in swing_highs if not s.broken]
        unbroken_sl = [s for s in swing_lows if not s.broken]

        last_sh = unbroken_sh[-1] if unbroken_sh else (swing_highs[-1] if swing_highs else None)
        last_sl = unbroken_sl[-1] if unbroken_sl else (swing_lows[-1] if swing_lows else None)

        # Final bias determination based on most recent structural events or swings
        final_bias = current_trend
        if final_bias == TrendBias.NEUTRAL and events:
            final_bias = events[-1].direction

        # Detect unmitigated Fair Value Gaps
        all_fvgs = self.detect_fair_value_gaps(df)
        active_fvgs = [f for f in all_fvgs if not f.mitigated]

        return TimeframeAnalysis(
            symbol=symbol,
            timeframe=timeframe,
            bias=final_bias,
            current_price=current_price,
            last_swing_high=last_sh,
            last_swing_low=last_sl,
            recent_events=events[-10:],
            recent_sweeps=sweeps[-10:],
            swing_highs=swing_highs[-15:],
            swing_lows=swing_lows[-15:],
            active_fvgs=active_fvgs[-10:],
            total_candles=len(df),
            latest_timestamp=int(timestamps[-1]) if len(timestamps) > 0 else 0
        )

    def detect_active_15m_sweep(
        self,
        analysis_15m: TimeframeAnalysis,
        max_age_candles: int = 12
    ) -> Optional[LiquiditySweep]:
        """
        Check if there is a recent active 15m liquidity sweep that hasn't been invalidated.
        - Must be within max_age_candles.
        - A bullish sweep is invalidated if price later closes below the wick extreme.
        - A bearish sweep is invalidated if price later closes above the wick extreme.
        """
        if not analysis_15m.recent_sweeps:
            return None

        # Check most recent sweeps in reverse
        for sweep in reversed(analysis_15m.recent_sweeps):
            # Check age: sweep cannot be older than max_age_candles
            if analysis_15m.total_candles > 0:
                candles_ago = (analysis_15m.total_candles - 1) - sweep.sweep_candle_index
                if candles_ago > max_age_candles:
                    sweep.is_active = False
                    continue
            elif analysis_15m.latest_timestamp > 0:
                max_age_ms = max_age_candles * 15 * 60 * 1000
                if (analysis_15m.latest_timestamp - sweep.sweep_timestamp) > max_age_ms:
                    sweep.is_active = False
                    continue

            # If current price violates the wick extreme, it's invalidated
            if sweep.direction == TrendBias.BULLISH:
                if analysis_15m.current_price < sweep.wick_extreme:
                    sweep.is_active = False
                    continue
            elif sweep.direction == TrendBias.BEARISH:
                if analysis_15m.current_price > sweep.wick_extreme:
                    sweep.is_active = False
                    continue

            if sweep.is_active:
                return sweep

        return None

    def detect_5m_trigger(
        self,
        analysis_5m: TimeframeAnalysis,
        active_sweep: LiquiditySweep,
        max_lookback_candles: int = 8
    ) -> Optional[StructureEvent]:
        """
        Check if a 5m Break of Structure (BOS) or CHoCH occurred in the direction of the 15m sweep,
        strictly after the sweep occurred and within max_lookback_candles.
        - For Bullish Sweep: Look for a 5m candle body close above a 5m swing high (Bullish BOS/CHoCH).
        - For Bearish Sweep: Look for a 5m candle body close below a 5m swing low (Bearish BOS/CHoCH).
        """
        if not analysis_5m.recent_events:
            return None

        for event in reversed(analysis_5m.recent_events):
            # Event must occur at or after the sweep timestamp
            if event.timestamp < active_sweep.sweep_timestamp:
                continue

            # Check max lookback age
            if analysis_5m.total_candles > 0:
                candles_ago = (analysis_5m.total_candles - 1) - event.candle_index
                if candles_ago > max_lookback_candles:
                    continue
            elif analysis_5m.latest_timestamp > 0:
                max_age_ms = max_lookback_candles * 5 * 60 * 1000
                if (analysis_5m.latest_timestamp - event.timestamp) > max_age_ms:
                    continue

            # Must match the sweep direction
            if event.direction == active_sweep.direction:
                return event

        return None

    def detect_fair_value_gaps(
        self,
        df: pd.DataFrame,
        min_gap_pct: float = 0.01
    ) -> List[FairValueGap]:
        """
        Detect 3-candle Fair Value Gaps (FVG) and track unmitigated imbalances.
        - Bullish FVG: Low[i] > High[i-2]. Imbalance zone: [High[i-2], Low[i]].
        - Bearish FVG: High[i] < Low[i-2]. Imbalance zone: [High[i], Low[i-2]].
        A gap is marked mitigated if any subsequent candle wick pierces through the zone.
        """
        if len(df) < 3:
            return []

        highs = df["high"].values
        lows = df["low"].values
        timestamps = df["timestamp"].values
        n = len(df)
        fvgs: List[FairValueGap] = []

        for i in range(2, n):
            # Bullish FVG: candle i low > candle i-2 high
            if lows[i] > highs[i - 2]:
                bottom = float(highs[i - 2])
                top = float(lows[i])
                gap_size = top - bottom
                pct = (gap_size / bottom) * 100 if bottom > 0 else 0.0
                if pct >= min_gap_pct:
                    mitigated = False
                    for j in range(i + 1, n):
                        if lows[j] <= bottom:  # fully penetrated / filled
                            mitigated = True
                            break
                    fvgs.append(FairValueGap(
                        direction=TrendBias.BULLISH,
                        top=top,
                        bottom=bottom,
                        candle_index=i - 1,
                        timestamp=int(timestamps[i - 1]),
                        mitigated=mitigated,
                        size_pct=round(pct, 3)
                    ))

            # Bearish FVG: candle i high < candle i-2 low
            elif highs[i] < lows[i - 2]:
                top = float(lows[i - 2])
                bottom = float(highs[i])
                gap_size = top - bottom
                pct = (gap_size / top) * 100 if top > 0 else 0.0
                if pct >= min_gap_pct:
                    mitigated = False
                    for j in range(i + 1, n):
                        if highs[j] >= top:  # fully penetrated / filled
                            mitigated = True
                            break
                    fvgs.append(FairValueGap(
                        direction=TrendBias.BEARISH,
                        top=top,
                        bottom=bottom,
                        candle_index=i - 1,
                        timestamp=int(timestamps[i - 1]),
                        mitigated=mitigated,
                        size_pct=round(pct, 3)
                    ))

        return fvgs

