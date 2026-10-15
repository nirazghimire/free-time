"""Multi-Timeframe Smart Money Concepts Strategy.
Evaluates:
- Macro Tier (Weekly + Daily): Defines overall directional bias
- Intermediate Tier (4H + 1H): Confirms current order flow
- Lower Tier (15M Sweep + 5M BOS): Precise liquidity sweep entry trigger
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, Optional, List
import pandas as pd

from config import BotConfig
from src.smc_engine import (
    SMCEngine,
    TrendBias,
    TimeframeAnalysis,
    LiquiditySweep,
    StructureEvent,
)
from src.risk_manager import RiskManager, TradeSetup
from src.market_data import MarketDataClient

logger = logging.getLogger("strategy")


@dataclass
class SymbolEvaluation:
    symbol: str
    macro_bias: TrendBias
    intermediate_trend: TrendBias
    is_aligned: bool
    active_sweep_15m: Optional[LiquiditySweep] = None
    trigger_5m: Optional[StructureEvent] = None
    trade_setup: Optional[TradeSetup] = None
    timeframe_analyses: Dict[str, TimeframeAnalysis] = field(default_factory=dict)
    summary_message: str = ""


class MTFStrategy:
    def __init__(self, config: BotConfig, market_data: MarketDataClient):
        self.config = config
        self.market_data = market_data
        self.smc = SMCEngine(swing_lookback=config.swing_lookback)
        self.risk_manager = RiskManager(config)

    def evaluate_symbol(
        self,
        symbol: str,
        account_balance: float = 15.0,
        cached_data: Optional[Dict[str, pd.DataFrame]] = None
    ) -> SymbolEvaluation:
        """
        Run full multi-timeframe SMC evaluation on a single symbol.
        """
        symbol = symbol.upper()
        candles_by_tf = cached_data or self.market_data.fetch_all_timeframes(symbol)

        # Analyze each timeframe
        analyses: Dict[str, TimeframeAnalysis] = {}
        for tf in self.config.timeframes:
            df = candles_by_tf.get(tf, pd.DataFrame())
            analyses[tf] = self.smc.analyze_structure(df, symbol, tf)

        # 1. Macro Bias (Weekly + Daily)
        w_bias = analyses.get("1w", TimeframeAnalysis(symbol, "1w", TrendBias.NEUTRAL, 0)).bias
        d_bias = analyses.get("1d", TimeframeAnalysis(symbol, "1d", TrendBias.NEUTRAL, 0)).bias

        if w_bias == TrendBias.BULLISH or d_bias == TrendBias.BULLISH:
            # If one is bullish and other is not bearish, macro is bullish
            if w_bias != TrendBias.BEARISH and d_bias != TrendBias.BEARISH:
                macro_bias = TrendBias.BULLISH
            else:
                macro_bias = d_bias  # Daily takes priority for immediate macro
        elif w_bias == TrendBias.BEARISH or d_bias == TrendBias.BEARISH:
            if w_bias != TrendBias.BULLISH and d_bias != TrendBias.BULLISH:
                macro_bias = TrendBias.BEARISH
            else:
                macro_bias = d_bias
        else:
            macro_bias = TrendBias.NEUTRAL

        # 2. Intermediate Trend (4H + 1H)
        h4_bias = analyses.get("4h", TimeframeAnalysis(symbol, "4h", TrendBias.NEUTRAL, 0)).bias
        h1_bias = analyses.get("1h", TimeframeAnalysis(symbol, "1h", TrendBias.NEUTRAL, 0)).bias

        if h4_bias == macro_bias or h1_bias == macro_bias:
            intermediate_trend = macro_bias
        elif h4_bias == h1_bias:
            intermediate_trend = h4_bias
        else:
            intermediate_trend = TrendBias.NEUTRAL

        # Is macro and intermediate aligned?
        is_aligned = (macro_bias != TrendBias.NEUTRAL) and (intermediate_trend == macro_bias)

        # 3. Check for 15M Liquidity Sweep
        analysis_15m = analyses.get("15m")
        active_sweep = None
        if analysis_15m:
            active_sweep = self.smc.detect_active_15m_sweep(
                analysis_15m,
                max_age_candles=self.config.sweep_max_candles_age
            )

        # 4. Check for 5M Trigger (BOS in direction of sweep)
        analysis_5m = analyses.get("5m")
        trigger_5m = None
        trade_setup = None
        summary_msg = f"Macro: {macro_bias.value} | Int: {intermediate_trend.value} | Aligned: {is_aligned}"

        if active_sweep:
            summary_msg += f" | Active 15M Sweep: {active_sweep.direction.value} @ ${active_sweep.wick_extreme:,.2f}"

            # ENFORCE: Only seek entry trigger if intermediate trend aligns with macro bias AND sweep matches macro bias
            # (or if macro is neutral but intermediate strongly aligned)
            sweep_matches_trend = (
                is_aligned and (active_sweep.direction == macro_bias)
            ) or (
                macro_bias == TrendBias.NEUTRAL and intermediate_trend != TrendBias.NEUTRAL and (active_sweep.direction == intermediate_trend)
            )

            if not sweep_matches_trend:
                if not is_aligned and macro_bias != TrendBias.NEUTRAL:
                    summary_msg += f" | Intermediate trend ({intermediate_trend.value}) opposes Macro bias ({macro_bias.value}) - Discarded"
                else:
                    summary_msg += f" | Sweep ({active_sweep.direction.value}) counter to HTF bias ({macro_bias.value}) - Ignored"
            elif analysis_5m:
                trigger_5m = self.smc.detect_5m_trigger(
                    analysis_5m,
                    active_sweep,
                    max_lookback_candles=self.config.trigger_max_5m_candles
                )

                if trigger_5m:
                    summary_msg += f" | 5M {trigger_5m.event_type.value} CONFIRMED! (Close: ${trigger_5m.candle_close:,.2f})"
                    
                    # Compute trade setup and risk parameters
                    sz_decimals = self.market_data.get_sz_decimals(symbol)
                    current_px = analysis_5m.current_price
                    trade_setup = self.risk_manager.calculate_trade_setup(
                        symbol=symbol,
                        direction=active_sweep.direction,
                        entry_price=current_px,
                        sweep=active_sweep,
                        account_balance=account_balance,
                        sz_decimals=sz_decimals
                    )
                    if trade_setup:
                        summary_msg += (
                            f" | Ready for {trade_setup.direction.value} trade: "
                            f"Size={trade_setup.size} {symbol} (~${trade_setup.notional_usd:.2f}) "
                            f"SL=${trade_setup.stop_loss:,.2f} TP=${trade_setup.take_profit:,.2f} "
                            f"R:R={trade_setup.risk_reward}"
                        )
                else:
                    summary_msg += " | Waiting for 5M BOS confirmation candle close"

        return SymbolEvaluation(
            symbol=symbol,
            macro_bias=macro_bias,
            intermediate_trend=intermediate_trend,
            is_aligned=is_aligned,
            active_sweep_15m=active_sweep,
            trigger_5m=trigger_5m,
            trade_setup=trade_setup,
            timeframe_analyses=analyses,
            summary_message=summary_msg
        )
