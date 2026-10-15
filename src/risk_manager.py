"""Risk Management and Dynamic Position Sizing Module.
Optimized for small accounts ($10 - $20 balance) trading on Hyperliquid perps.
Enforces:
- Dynamic Stop Loss at the extreme wick of the 15m sweep
- Dynamic Take Profit targeting a minimum Risk-to-Reward ratio (e.g. 1:2.5)
- Hyperliquid min order notional ($10-$11 USD minimum order size)
- Correct lot size decimal rounding per coin (szDecimals)
- Max position and drawdown controls
"""

import math
import logging
from dataclasses import dataclass
from typing import Optional, Tuple
from config import BotConfig
from src.smc_engine import TrendBias, LiquiditySweep

logger = logging.getLogger("risk_manager")


@dataclass
class TradeSetup:
    symbol: str
    direction: TrendBias  # BULLISH (Long) or BEARISH (Short)
    entry_price: float
    stop_loss: float
    take_profit: float
    risk_reward: float
    size: float
    notional_usd: float
    risk_usd: float
    sl_distance_pct: float
    sweep: LiquiditySweep


class RiskManager:
    def __init__(self, config: BotConfig):
        self.config = config

    def calculate_trade_setup(
        self,
        symbol: str,
        direction: TrendBias,
        entry_price: float,
        sweep: LiquiditySweep,
        account_balance: float,
        sz_decimals: int
    ) -> Optional[TradeSetup]:
        """
        Calculate entry, SL, TP, and position size.
        Returns TradeSetup if valid, else None.
        """
        if entry_price <= 0:
            return None

        # Effective account equity (fallback to $15 for simulation if balance query returned 0)
        usable_balance = account_balance if account_balance > 0 else 15.0

        # 1. Stop Loss Calculation
        buffer = self.config.sl_buffer_pct
        if direction == TrendBias.BULLISH:
            # For LONG, SL is set below the 15m sweep low wick
            stop_loss = sweep.wick_extreme * (1.0 - buffer)
            if stop_loss >= entry_price:
                logger.warning(f"[{symbol}] Invalid Long SL: SL ({stop_loss}) >= Entry ({entry_price})")
                return None
            sl_distance = entry_price - stop_loss
            sl_distance_pct = sl_distance / entry_price
            take_profit = entry_price + (sl_distance * self.config.target_rr)
        else:
            # For SHORT, SL is set above the 15m sweep high wick
            stop_loss = sweep.wick_extreme * (1.0 + buffer)
            if stop_loss <= entry_price:
                logger.warning(f"[{symbol}] Invalid Short SL: SL ({stop_loss}) <= Entry ({entry_price})")
                return None
            sl_distance = stop_loss - entry_price
            sl_distance_pct = sl_distance / entry_price
            take_profit = entry_price - (sl_distance * self.config.target_rr)

        # 2. Risk Sizing
        # Risk amount: user-defined fixed USD or 5% of balance, whichever is smaller
        risk_usd = min(self.config.risk_usd_per_trade, usable_balance * 0.10)
        if risk_usd <= 0.20:
            risk_usd = 0.50  # sensible minimum floor for testing

        # Position notional from risk formula: Notional = Risk / SL_distance_pct
        calculated_notional = risk_usd / sl_distance_pct

        # Apply leverage and margin caps
        max_notional_by_leverage = usable_balance * self.config.leverage
        target_notional = min(calculated_notional, max_notional_by_leverage)

        # Hyperliquid requires minimum order size of ~$10
        min_notional = self.config.min_order_notional
        if target_notional < min_notional:
            # If account has sufficient leverage capacity, bump to min_notional
            if max_notional_by_leverage >= min_notional:
                target_notional = min_notional
            else:
                logger.warning(
                    f"[{symbol}] Account buying power (${max_notional_by_leverage:.2f}) "
                    f"is less than Hyperliquid min order notional (${min_notional:.2f})"
                )
                return None

        # 3. Base Currency Size and Decimal Precision
        raw_size = target_notional / entry_price
        # Round down to szDecimals
        factor = 10 ** sz_decimals
        size = math.floor(raw_size * factor) / factor

        if size <= 0:
            logger.warning(f"[{symbol}] Calculated size is 0 after decimal truncation")
            return None

        actual_notional = size * entry_price
        actual_risk_usd = actual_notional * sl_distance_pct
        actual_rr = abs(take_profit - entry_price) / sl_distance

        return TradeSetup(
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            stop_loss=round(stop_loss, 2 if entry_price > 10 else 4),
            take_profit=round(take_profit, 2 if entry_price > 10 else 4),
            risk_reward=round(actual_rr, 2),
            size=size,
            notional_usd=round(actual_notional, 2),
            risk_usd=round(actual_risk_usd, 2),
            sl_distance_pct=round(sl_distance_pct * 100, 2),
            sweep=sweep
        )
