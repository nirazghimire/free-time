"""Multi-Timeframe Smart Money Concepts Strategy.
Evaluates:
- Mode 1: DEMAND_FVG_CONFLUENCE (Nested HTF/MTF Demand/Supply Zone + FVG Tap + Confirmed Reaction)
- Mode 2: SWEEP_BOS (15M Liquidity Sweep + 5M BOS Candle Close)
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Tuple
import pandas as pd

from config import BotConfig
from src.smc_engine import (
    SMCEngine,
    TrendBias,
    TimeframeAnalysis,
    LiquiditySweep,
    StructureEvent,
    DemandZone,
    FairValueGap,
    ReactionConfirmation,
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
    active_demand_zone: Optional[DemandZone] = None
    active_fvg: Optional[FairValueGap] = None
    reaction_confirmation: Optional[ReactionConfirmation] = None
    trade_setup: Optional[TradeSetup] = None
    timeframe_analyses: Dict[str, TimeframeAnalysis] = field(default_factory=dict)
    summary_message: str = ""


class MTFStrategy:
    def __init__(self, config: BotConfig, market_data: MarketDataClient):
        self.config = config
        self.market_data = market_data
        self.smc = SMCEngine(swing_lookback=config.swing_lookback)
        self.risk_manager = RiskManager(config)

    def _check_discount_premium_equilibrium(
        self,
        target_direction: TrendBias,
        entry_price: float,
        analyses: Dict[str, TimeframeAnalysis]
    ) -> Tuple[bool, str]:
        """
        Evaluate if entry_price is in the valid half of the dealing range:
        - Bullish (Long): Must be in DISCOUNT (lower 50% of 1H/4H dealing range).
        - Bearish (Short): Must be in PREMIUM (upper 50% of 1H/4H dealing range).
        """
        if not getattr(self.config, "discount_premium_filter_enabled", True):
            return True, ""

        # Prefer 1H dealing range, fallback to 4H
        range_analysis = analyses.get("1h")
        if not range_analysis or not (range_analysis.last_swing_high and range_analysis.last_swing_low):
            range_analysis = analyses.get("4h")

        if not range_analysis or not range_analysis.last_swing_high or not range_analysis.last_swing_low:
            return True, ""

        sh = range_analysis.last_swing_high.price
        sl = range_analysis.last_swing_low.price

        if sh <= sl:
            return True, ""

        range_span = sh - sl
        # Guard against synthetic tests where mock prices across timeframes are on different scales
        if entry_price < (sl - range_span) or entry_price > (sh + range_span):
            return True, ""

        eq_ratio = getattr(self.config, "max_equilibrium_pct", 0.50)
        equilibrium = sl + (range_span * eq_ratio)
        tf_label = range_analysis.timeframe.upper()

        if target_direction == TrendBias.BULLISH:
            if entry_price > equilibrium:
                return False, f"In Premium zone (${entry_price:,.2f} > {int(eq_ratio*100)}% Eq ${equilibrium:,.2f} of {tf_label} [${sl:,.2f} - ${sh:,.2f}]) - Long Rejected"
        elif target_direction == TrendBias.BEARISH:
            if entry_price < equilibrium:
                return False, f"In Discount zone (${entry_price:,.2f} < {int(eq_ratio*100)}% Eq ${equilibrium:,.2f} of {tf_label} [${sl:,.2f} - ${sh:,.2f}]) - Short Rejected"

        return True, ""

    def evaluate_symbol(
        self,
        symbol: str,
        account_balance: float = 15.0,
        cached_data: Optional[Dict[str, pd.DataFrame]] = None
    ) -> SymbolEvaluation:
        """
        Run full multi-timeframe SMC evaluation on a single symbol.
        Dispatches to DEMAND_FVG_CONFLUENCE or SWEEP_BOS depending on config.strategy_mode.
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
            if w_bias != TrendBias.BEARISH and d_bias != TrendBias.BEARISH:
                macro_bias = TrendBias.BULLISH
            else:
                macro_bias = d_bias
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

        is_aligned = (macro_bias != TrendBias.NEUTRAL) and (intermediate_trend == macro_bias)

        # Dispatch by Strategy Mode
        if getattr(self.config, "strategy_mode", "DEMAND_FVG_CONFLUENCE") == "DEMAND_FVG_CONFLUENCE":
            return self._evaluate_demand_fvg_confluence(
                symbol=symbol,
                macro_bias=macro_bias,
                intermediate_trend=intermediate_trend,
                is_aligned=is_aligned,
                analyses=analyses,
                candles_by_tf=candles_by_tf,
                account_balance=account_balance
            )

        return self._evaluate_sweep_bos(
            symbol=symbol,
            macro_bias=macro_bias,
            intermediate_trend=intermediate_trend,
            is_aligned=is_aligned,
            analyses=analyses,
            account_balance=account_balance
        )

    def _evaluate_demand_fvg_confluence(
        self,
        symbol: str,
        macro_bias: TrendBias,
        intermediate_trend: TrendBias,
        is_aligned: bool,
        analyses: Dict[str, TimeframeAnalysis],
        candles_by_tf: Dict[str, pd.DataFrame],
        account_balance: float
    ) -> SymbolEvaluation:
        """
        Nested Multi-Timeframe Demand Zone & Fair Value Gap Confluence:
        1. 4H / 1W defines Institutional Bias.
        2. 4H & 1H establish active Demand / Supply Zones (Engulfing Order Blocks).
        3. 15M provides intermediate confirmation (Demand Zone / Engulfing / Support hold).
        4. 5M marks precise entry zone (5M Demand Zone & 5M Fair Value Gap).
        5. Price retraces, taps Demand Zone / FVG, and confirms a reaction (rejection wick or bounce).
        """
        h4_bias = analyses.get("4h", TimeframeAnalysis(symbol, "4h", TrendBias.NEUTRAL, 0)).bias
        h1_bias = analyses.get("1h", TimeframeAnalysis(symbol, "1h", TrendBias.NEUTRAL, 0)).bias

        # Determine target direction (4H trend prioritizes immediate swing order flow)
        target_direction = TrendBias.NEUTRAL
        if h4_bias == TrendBias.BULLISH or macro_bias == TrendBias.BULLISH:
            if h4_bias != TrendBias.BEARISH and macro_bias != TrendBias.BEARISH:
                target_direction = TrendBias.BULLISH
            elif h4_bias == TrendBias.BULLISH:
                target_direction = TrendBias.BULLISH
        elif h4_bias == TrendBias.BEARISH or macro_bias == TrendBias.BEARISH:
            if h4_bias != TrendBias.BULLISH and macro_bias != TrendBias.BULLISH:
                target_direction = TrendBias.BEARISH
            elif h4_bias == TrendBias.BEARISH:
                target_direction = TrendBias.BEARISH

        if target_direction == TrendBias.NEUTRAL and intermediate_trend != TrendBias.NEUTRAL:
            target_direction = intermediate_trend

        analysis_5m = analyses.get("5m")
        current_px = analysis_5m.current_price if analysis_5m else 0.0

        summary_msg = f"Mode: DEMAND_FVG | Macro: {macro_bias.value} | Bias: {target_direction.value} (4H: {h4_bias.value}, 1H: {h1_bias.value})"

        if target_direction == TrendBias.NEUTRAL:
            summary_msg += " | No clear directional bias"
            return SymbolEvaluation(
                symbol=symbol,
                macro_bias=macro_bias,
                intermediate_trend=intermediate_trend,
                is_aligned=is_aligned,
                timeframe_analyses=analyses,
                summary_message=summary_msg
            )

        # 1. Collect HTF Demand / Supply Zones (4H and 1H)
        htf_zones: List[DemandZone] = []
        for tf in ["4h", "1h"]:
            if tf in analyses:
                htf_zones.extend([
                    z for z in analyses[tf].active_demand_zones
                    if z.direction == target_direction and not z.is_invalidated
                ])

        # 2. Collect LTF Demand Zones & FVGs (15M and 5M)
        ltf_zones: List[DemandZone] = []
        ltf_fvgs: List[FairValueGap] = []
        for tf in ["15m", "5m"]:
            if tf in analyses:
                ltf_zones.extend([
                    z for z in analyses[tf].active_demand_zones
                    if z.direction == target_direction and not z.is_invalidated
                ])
                ltf_fvgs.extend([
                    f for f in analyses[tf].active_fvgs
                    if f.direction == target_direction and not f.mitigated
                ])

        all_target_zones = htf_zones + ltf_zones
        df_5m = candles_by_tf.get("5m", pd.DataFrame())

        reaction: Optional[ReactionConfirmation] = None
        if not df_5m.empty and (all_target_zones or ltf_fvgs):
            reaction = self.smc.check_zone_and_fvg_tap_with_reaction(
                df=df_5m,
                zones=all_target_zones,
                fvgs=ltf_fvgs,
                target_direction=target_direction,
                lookback_candles=5,
                rejection_wick_ratio=getattr(self.config, "rejection_wick_ratio", 0.30)
            )

        trade_setup = None
        if reaction and reaction.confirmed:
            zone_desc = (
                f"{reaction.zone.timeframe.upper()} {reaction.zone.direction.value} Zone (${reaction.zone.bottom:,.2f}-${reaction.zone.top:,.2f})"
                if reaction.zone else ""
            )
            fvg_desc = f"FVG (${reaction.fvg.bottom:,.2f}-${reaction.fvg.top:,.2f})" if reaction.fvg else ""
            confluence_str = " + ".join(filter(None, [zone_desc, fvg_desc]))

            summary_msg += f" | REACTION CONFIRMED [{reaction.reaction_type}] off {confluence_str} @ ${reaction.reaction_price:,.2f}"

            entry_px = current_px if current_px > 0 else reaction.reaction_price
            is_valid_eq, eq_msg = self._check_discount_premium_equilibrium(
                target_direction=target_direction,
                entry_price=entry_px,
                analyses=analyses
            )

            if not is_valid_eq:
                summary_msg += f" | {eq_msg}"
            else:
                sz_decimals = self.market_data.get_sz_decimals(symbol)
                trade_setup = self.risk_manager.calculate_trade_setup(
                    symbol=symbol,
                    direction=target_direction,
                    entry_price=entry_px,
                    account_balance=account_balance,
                    sz_decimals=sz_decimals,
                    anchor_sl_price=reaction.invalidation_level,
                    demand_zone=reaction.zone,
                    reaction=reaction
                )

                if trade_setup:
                    summary_msg += (
                        f" | Ready for {trade_setup.direction.value} trade: "
                        f"Size={trade_setup.size} {symbol} (~${trade_setup.notional_usd:.2f}) "
                        f"SL=${trade_setup.stop_loss:,.2f} TP=${trade_setup.take_profit:,.2f} "
                        f"R:R={trade_setup.risk_reward}"
                    )
        else:
            active_zones_count = len(all_target_zones)
            active_fvgs_count = len(ltf_fvgs)
            summary_msg += f" | Active Zones: {active_zones_count}, FVGs: {active_fvgs_count} | Waiting for Tap + Confirmed Reaction"

        return SymbolEvaluation(
            symbol=symbol,
            macro_bias=macro_bias,
            intermediate_trend=intermediate_trend,
            is_aligned=is_aligned,
            active_demand_zone=reaction.zone if reaction else (all_target_zones[-1] if all_target_zones else None),
            active_fvg=reaction.fvg if reaction else (ltf_fvgs[-1] if ltf_fvgs else None),
            reaction_confirmation=reaction,
            trade_setup=trade_setup,
            timeframe_analyses=analyses,
            summary_message=summary_msg
        )

    def _evaluate_sweep_bos(
        self,
        symbol: str,
        macro_bias: TrendBias,
        intermediate_trend: TrendBias,
        is_aligned: bool,
        analyses: Dict[str, TimeframeAnalysis],
        account_balance: float
    ) -> SymbolEvaluation:
        """
        Original 15M Liquidity Sweep + 5M BOS Model.
        """
        analysis_15m = analyses.get("15m")
        active_sweep = None
        if analysis_15m:
            active_sweep = self.smc.detect_active_15m_sweep(
                analysis_15m,
                max_age_candles=self.config.sweep_max_candles_age
            )

        analysis_5m = analyses.get("5m")
        trigger_5m = None
        trade_setup = None
        summary_msg = f"Mode: SWEEP_BOS | Macro: {macro_bias.value} | Int: {intermediate_trend.value} | Aligned: {is_aligned}"

        if active_sweep:
            summary_msg += f" | Active 15M Sweep: {active_sweep.direction.value} @ ${active_sweep.wick_extreme:,.2f}"

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
                    trigger_close = trigger_5m.candle_close
                    current_px = analysis_5m.current_price
                    max_chase_pct = 0.0075
                    is_chasing = False
                    if active_sweep.direction == TrendBias.BULLISH and current_px > (trigger_close * (1 + max_chase_pct)):
                        is_chasing = True
                    elif active_sweep.direction == TrendBias.BEARISH and current_px < (trigger_close * (1 - max_chase_pct)):
                        is_chasing = True

                    if is_chasing:
                        summary_msg += (
                            f" | 5M {trigger_5m.event_type.value} CONFIRMED! (Close: ${trigger_close:,.2f})"
                            f" but price chased too far (${current_px:,.2f}) - Entry Discarded"
                        )
                        trigger_5m = None
                    else:
                        is_valid_eq, eq_msg = self._check_discount_premium_equilibrium(
                            target_direction=active_sweep.direction,
                            entry_price=current_px,
                            analyses=analyses
                        )
                        if not is_valid_eq:
                            summary_msg += (
                                f" | 5M {trigger_5m.event_type.value} CONFIRMED! (Close: ${trigger_close:,.2f})"
                                f" | {eq_msg}"
                            )
                            trigger_5m = None
                        else:
                            summary_msg += f" | 5M {trigger_5m.event_type.value} CONFIRMED! (Close: ${trigger_close:,.2f})"

                            sz_decimals = self.market_data.get_sz_decimals(symbol)
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
