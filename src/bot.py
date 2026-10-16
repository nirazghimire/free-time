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
from src.alerts import AlertManager

logger = logging.getLogger("smc_bot")


class SMCBot:
    def __init__(self, config: BotConfig):
        self.config = config
        self.market_data = MarketDataClient(config)
        self.strategy = MTFStrategy(config, self.market_data)
        self.executor = OrderExecutor(config)
        self.alerts = AlertManager(config)

        self.is_running = False
        self.last_evaluations: Dict[str, SymbolEvaluation] = {}
        self.account_state: Dict[str, Any] = {}
        self.mid_prices: Dict[str, float] = {}
        self.event_log: List[Dict[str, Any]] = []
        self._listeners: List[Callable[[Dict[str, Any]], None]] = []
        # Deduplication: tracks sweep timestamps already traded to prevent re-entry on same event
        self._executed_sweep_keys: set = set()

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

        # Reconcile on-chain positions from Hyperliquid account state (adopt new, detect closed)
        exchange_events = self.executor.reconcile_exchange_positions(
            self.account_state.get("positions", []),
            self.mid_prices
        )
        for ev in exchange_events:
            self.broadcast_event(ev["event"], ev)
            if ev["event"] == "POSITION_CLOSED_ON_EXCHANGE":
                self.alerts.send_trade_exit(
                    symbol=ev["symbol"],
                    status=ev.get("status", "CLOSED_ON_EXCHANGE"),
                    pnl=ev.get("pnl", 0.0),
                    exit_price=ev.get("exit_price", 0.0),
                    mode=ev.get("mode", "LIVE")
                )

        # Update positions check against current prices (breakeven at 1R & SL/TP triggers)
        pos_events = self.executor.check_open_positions_update(self.mid_prices)
        for ev in pos_events:
            self.broadcast_event(ev["event"], ev)
            if ev["event"] == "BREAKEVEN_TRIGGERED":
                self.alerts.send_breakeven(
                    symbol=ev["symbol"],
                    entry_price=ev["entry_price"],
                    mode=ev.get("mode", "PAPER")
                )
            elif ev["event"] == "POSITION_CLOSED":
                self.alerts.send_trade_exit(
                    symbol=ev["symbol"],
                    status=ev["status"],
                    pnl=ev.get("pnl", 0.0),
                    exit_price=ev.get("exit_price", 0.0),
                    mode=ev.get("mode", "PAPER")
                )

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
                    sweep = eval_res.active_sweep_15m
                    # Build a unique key per symbol + sweep candle timestamp
                    sweep_key = f"{symbol}_{sweep.sweep_timestamp}" if sweep else None

                    # Deduplication guard: never re-enter on the same 15M sweep event
                    if sweep_key and sweep_key in self._executed_sweep_keys:
                        logger.debug(
                            f"[{symbol}] Sweep @ ts={sweep.sweep_timestamp} already traded "
                            f"— cooldown active. Skipping."
                        )
                    else:
                        logger.info(
                            f"\U0001f680 Triggering automated execution for {symbol} "
                            f"{eval_res.trade_setup.direction.value}"
                        )
                        exec_res = self.executor.execute_trade(eval_res.trade_setup)

                        # Record sweep key on any real attempt (success or already-in-pos skip)
                        if sweep_key and exec_res.get("status") in ("success", "skipped"):
                            self._executed_sweep_keys.add(sweep_key)
                            # Prune oldest keys so memory stays bounded
                            if len(self._executed_sweep_keys) > 200:
                                for k in sorted(self._executed_sweep_keys)[:50]:
                                    self._executed_sweep_keys.discard(k)

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

                        # Dispatch alert notification
                        mode_str = "PAPER" if self.config.dry_run else "LIVE"
                        if exec_res.get("status") == "success":
                            self.alerts.send_trade_entry({
                                "symbol": symbol,
                                "direction": eval_res.trade_setup.direction.value,
                                "entry_price": eval_res.trade_setup.entry_price,
                                "stop_loss": eval_res.trade_setup.stop_loss,
                                "take_profit": eval_res.trade_setup.take_profit,
                                "size": eval_res.trade_setup.size,
                                "notional_usd": eval_res.trade_setup.notional_usd,
                                "risk_usd": eval_res.trade_setup.risk_usd,
                                "risk_reward": eval_res.trade_setup.risk_reward,
                            }, mode=mode_str)
                        elif exec_res.get("status") == "error":
                            self.alerts.send_emergency_alert(
                                symbol=symbol,
                                message=str(exec_res.get("error", "Execution failure"))
                            )

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
                    "active_fvgs_count": len(ta.active_fvgs),
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
