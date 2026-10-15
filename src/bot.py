"""Main SMC Trading Bot Orchestrator.
Manages the scanning loop, market data refreshes, strategy signals,
automated trade executions, and event broadcasting to the web dashboard.
"""

import time
import asyncio
import logging
from typing import Dict, Any, List, Optional, Callable
from config import BotConfig
from src.market_data import MarketDataClient
from src.strategy import MTFStrategy, SymbolEvaluation
from src.execution import OrderExecutor

logger = logging.getLogger("smc_bot")


class SMCBot:
    def __init__(self, config: BotConfig):
        self.config = config
        self.market_data = MarketDataClient(config)
        self.strategy = MTFStrategy(config, self.market_data)
        self.executor = OrderExecutor(config)

        self.is_running = False
        self.last_evaluations: Dict[str, SymbolEvaluation] = {}
        self.account_state: Dict[str, Any] = {}
        self.mid_prices: Dict[str, float] = {}
        self.event_log: List[Dict[str, Any]] = []
        self._listeners: List[Callable[[Dict[str, Any]], None]] = []

    def add_listener(self, callback: Callable[[Dict[str, Any]], None]):
        """Subscribe to real-time events for web dashboard."""
        self._listeners.append(callback)

    def broadcast_event(self, event_type: str, data: Any):
        """Emit events to subscribed listeners."""
        payload = {
            "type": event_type,
            "timestamp": int(time.time() * 1000),
            "data": data
        }
        self.event_log.append(payload)
        if len(self.event_log) > 200:
            self.event_log = self.event_log[-200:]

        for listener in self._listeners:
            try:
                listener(payload)
            except Exception as e:
                logger.error(f"Error in event listener: {e}")

    def scan_once(self) -> Dict[str, SymbolEvaluation]:
        """Run a complete multi-timeframe scan across all configured symbols."""
        logger.info("--- Starting Multi-Timeframe SMC Market Scan ---")

        # 1. Update prices & account state
        self.mid_prices = self.market_data.get_mid_prices()
        self.account_state = self.market_data.get_account_state()

        # Update paper positions check against current prices
        self.executor.check_open_positions_update(self.mid_prices)

        # Usable balance
        balance = self.account_state.get("account_value", 0.0)
        if balance <= 0:
            balance = 15.0  # safe default for testnet / dry-run sizing calculation

        evaluations = {}
        for symbol in self.config.symbols:
            try:
                eval_res = self.strategy.evaluate_symbol(symbol, account_balance=balance)
                evaluations[symbol] = eval_res
                self.last_evaluations[symbol] = eval_res

                logger.info(f"[{symbol}] {eval_res.summary_message}")

                # Check for trade execution
                if eval_res.trade_setup and self.config.auto_trade_enabled:
                    logger.info(f"🚀 Triggering automated execution for {symbol} {eval_res.trade_setup.direction.value}")
                    exec_res = self.executor.execute_trade(eval_res.trade_setup)
                    self.broadcast_event("TRADE_EXECUTION", {
                        "symbol": symbol,
                        "setup": {
                            "direction": eval_res.trade_setup.direction.value,
                            "entry_price": eval_res.trade_setup.entry_price,
                            "stop_loss": eval_res.trade_setup.stop_loss,
                            "take_profit": eval_res.trade_setup.take_profit,
                            "size": eval_res.trade_setup.size,
                            "notional_usd": eval_res.trade_setup.notional_usd,
                            "risk_usd": eval_res.trade_setup.risk_usd,
                        },
                        "execution_result": exec_res
                    })

            except Exception as e:
                logger.error(f"Error evaluating {symbol}: {e}", exc_info=True)

        # Broadcast state snapshot
        self.broadcast_event("SCAN_COMPLETED", self.get_dashboard_snapshot())
        return evaluations

    def get_dashboard_snapshot(self) -> Dict[str, Any]:
        """Generate a serializable snapshot of the system state for dashboard rendering."""
        symbols_data = {}
        for sym, ev in self.last_evaluations.items():
            tf_data = {}
            for tf, ta in ev.timeframe_analyses.items():
                tf_data[tf] = {
                    "bias": ta.bias.value,
                    "price": ta.current_price,
                    "last_sh": ta.last_swing_high.price if ta.last_swing_high else None,
                    "last_sl": ta.last_swing_low.price if ta.last_swing_low else None,
                    "has_sweep": len(ta.recent_sweeps) > 0,
                    "has_bos": any(e.event_type.value == "BOS" for e in ta.recent_events),
                    "has_choch": any(e.event_type.value == "CHOCH" for e in ta.recent_events),
                }

            symbols_data[sym] = {
                "symbol": sym,
                "current_price": self.mid_prices.get(sym, 0.0),
                "macro_bias": ev.macro_bias.value,
                "intermediate_trend": ev.intermediate_trend.value,
                "is_aligned": ev.is_aligned,
                "summary": ev.summary_message,
                "has_active_sweep": ev.active_sweep_15m is not None,
                "sweep_details": {
                    "direction": ev.active_sweep_15m.direction.value,
                    "extreme": ev.active_sweep_15m.wick_extreme,
                    "candle_close": ev.active_sweep_15m.candle_close
                } if ev.active_sweep_15m else None,
                "has_5m_trigger": ev.trigger_5m is not None,
                "trigger_details": {
                    "type": ev.trigger_5m.event_type.value,
                    "close": ev.trigger_5m.candle_close,
                    "direction": ev.trigger_5m.direction.value
                } if ev.trigger_5m else None,
                "trade_setup": {
                    "direction": ev.trade_setup.direction.value,
                    "entry": ev.trade_setup.entry_price,
                    "sl": ev.trade_setup.stop_loss,
                    "tp": ev.trade_setup.take_profit,
                    "size": ev.trade_setup.size,
                    "notional": ev.trade_setup.notional_usd,
                    "risk_usd": ev.trade_setup.risk_usd,
                    "rr": ev.trade_setup.risk_reward
                } if ev.trade_setup else None,
                "timeframes": tf_data
            }

        return {
            "env": self.config.env,
            "mode": "DRY_RUN / PAPER" if self.config.dry_run else "LIVE_HYPERLIQUID",
            "auto_trade_enabled": self.config.auto_trade_enabled,
            "account": {
                "address": self.executor.account_address or self.config.account_address or "Not Connected",
                "equity": self.account_state.get("account_value", 0.0),
                "margin_used": self.account_state.get("total_margin_used", 0.0),
                "withdrawable": self.account_state.get("withdrawable", 0.0),
            },
            "open_positions": list(self.executor.paper_positions.values()),
            "trade_history": self.executor.trade_history[-20:],
            "symbols": symbols_data,
            "recent_events": self.event_log[-30:]
        }

    async def run_loop(self, interval_seconds: int = 20):
        """Asynchronous execution loop that runs periodic scans."""
        self.is_running = True
        logger.info(f"SMC Trading Bot loop started. Scanning every {interval_seconds}s...")
        while self.is_running:
            try:
                # Run synchronous scan in executor to keep event loop free
                await asyncio.to_thread(self.scan_once)
            except Exception as e:
                logger.error(f"Error in scan loop: {e}", exc_info=True)
            await asyncio.sleep(interval_seconds)

    def stop(self):
        """Stop the bot loop."""
        self.is_running = False
        logger.info("SMC Trading Bot stopped.")
