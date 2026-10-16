"""Order Execution Module for Hyperliquid.
Supports:
- Live on-chain / exchange execution via official hyperliquid-python-sdk
- EIP-712 transaction signing via local private key
- Automatic leverage configuration
- Native Hyperliquid Stop Loss ('sl') and Take Profit ('tp') trigger orders
- Safe Paper / Dry-run execution mode with simulated order fills and PnL tracking
"""

import time
import logging
from typing import Dict, Any, Optional, List
from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants

from config import BotConfig
from src.risk_manager import TradeSetup
from src.smc_engine import TrendBias

logger = logging.getLogger("execution")


class OrderExecutor:
    def __init__(self, config: BotConfig):
        self.config = config
        self.is_dry_run = config.dry_run
        self.exchange: Optional[Exchange] = None
        self.account_address: str = config.account_address
        self.paper_positions: Dict[str, Dict[str, Any]] = {}
        self.trade_history: List[Dict[str, Any]] = []
        # Shared dedup set — bot.py writes to this; executor checks existing-position guard
        self.executed_sweep_keys: set = set()

        self._init_exchange()

    def _init_exchange(self):
        """Initialize Hyperliquid Exchange client if private key is provided."""
        if self.is_dry_run or not self.config.private_key:
            logger.info("Operating in DRY RUN / PAPER mode. No real exchange orders will be submitted.")
            return

        try:
            # Clean private key format
            pk = self.config.private_key.strip()
            if not pk.startswith("0x"):
                pk = "0x" + pk

            wallet = Account.from_key(pk)
            self.account_address = wallet.address
            base_url = self.config.api_url

            self.exchange = Exchange(
                wallet=wallet,
                base_url=base_url,
                account_address=self.config.account_address or self.account_address
            )
            logger.info(
                f"Initialized Hyperliquid Exchange client on {self.config.env.upper()} "
                f"for wallet: {self.account_address}"
            )
        except Exception as e:
            logger.error(f"Failed to initialize Hyperliquid Exchange: {e}")
            logger.warning("Falling back to DRY RUN / PAPER mode for safety.")
            self.is_dry_run = True

    def execute_trade(self, setup: TradeSetup) -> Dict[str, Any]:
        """
        Execute trade entry + set Stop Loss and Take Profit orders.
        """
        symbol = setup.symbol.upper()
        is_buy = (setup.direction == TrendBias.BULLISH)

        # Check if already in position for this symbol
        if symbol in self.paper_positions:
            logger.info(f"[{symbol}] Already in an open position. Skipping new entry.")
            return {"status": "skipped", "reason": "Already in position"}

        if self.is_dry_run or not self.exchange:
            return self._execute_paper_trade(setup)
        else:
            return self._execute_live_trade(setup)

    def _execute_paper_trade(self, setup: TradeSetup) -> Dict[str, Any]:
        """Simulate trade execution for testing and safety."""
        trade_id = f"paper_{int(time.time()*1000)}"
        position_record = {
            "id": trade_id,
            "symbol": setup.symbol,
            "direction": setup.direction.value,
            "size": setup.size,
            "entry_price": setup.entry_price,
            "stop_loss": setup.stop_loss,
            "take_profit": setup.take_profit,
            "notional_usd": setup.notional_usd,
            "risk_usd": setup.risk_usd,
            "risk_reward": setup.risk_reward,
            "initial_sl": setup.stop_loss,
            "breakeven_triggered": False,
            "sz_decimals": getattr(setup, "sz_decimals", 3),
            "entry_time": int(time.time() * 1000),
            "status": "OPEN",
            "pnl": 0.0,
            "mode": "PAPER"
        }
        self.paper_positions[setup.symbol] = position_record
        self.trade_history.append(position_record)

        logger.info(
            f"🛒 [PAPER TRADE EXECUTED] {setup.direction.value} {setup.size} {setup.symbol} "
            f"@ ${setup.entry_price:,.2f} | SL: ${setup.stop_loss:,.2f} | TP: ${setup.take_profit:,.2f} "
            f"| Risk: ${setup.risk_usd:.2f} (R:R {setup.risk_reward})"
        )
        return {"status": "success", "mode": "paper", "trade": position_record}

    def _execute_live_trade(self, setup: TradeSetup) -> Dict[str, Any]:
        """Submit live market entry and trigger orders to Hyperliquid.
        If SL placement fails after retries, immediately market-closes the position
        to avoid leaving a naked leveraged trade with no stop protection.
        """
        symbol = setup.symbol.upper()
        is_buy = (setup.direction == TrendBias.BULLISH)
        size = setup.size
        sl_price = setup.stop_loss
        tp_price = setup.take_profit

        try:
            # 1. Update leverage
            logger.info(f"Setting leverage for {symbol} to {self.config.leverage}x...")
            self.exchange.update_leverage(self.config.leverage, symbol, is_cross=True)

            # 2. Market Open Order
            logger.info(f"Opening market order: {'BUY' if is_buy else 'SELL'} {size} {symbol}...")
            open_result = self.exchange.market_open(
                name=symbol,
                is_buy=is_buy,
                sz=size,
                slippage=0.01  # 1% max slippage
            )
            logger.info(f"Market open response: {open_result}")

            # Verify fill before placing SL/TP
            open_status = open_result.get("status") if isinstance(open_result, dict) else None
            if open_status == "err":
                err_msg = open_result.get("response", "Unknown error")
                logger.error(f"[{symbol}] Market open REJECTED: {err_msg}")
                return {"status": "error", "error": f"Market open rejected: {err_msg}"}

            # Check if matching engine rejected or failed the IoC market order
            resp_data = open_result.get("response", {}) if isinstance(open_result, dict) else {}
            if isinstance(resp_data, dict):
                inner_data = resp_data.get("data", {})
                if isinstance(inner_data, dict):
                    statuses = inner_data.get("statuses", [])
                    if statuses and isinstance(statuses[0], dict) and "error" in statuses[0]:
                        err_msg = statuses[0]["error"]
                        logger.error(f"[{symbol}] Market open rejected by matching engine: {err_msg}")
                        return {"status": "error", "error": f"Matching engine error: {err_msg}"}

            # 3. Stop Loss Order with retry + rollback
            sl_is_buy = not is_buy
            sl_result = None
            for attempt in range(1, 3):  # up to 2 attempts
                try:
                    logger.info(f"Placing Stop Loss trigger order @ ${sl_price:,.2f} (attempt {attempt})...")
                    sl_result = self.exchange.order(
                        name=symbol,
                        is_buy=sl_is_buy,
                        sz=size,
                        limit_px=sl_price,
                        order_type={
                            "trigger": {
                                "isMarket": True,
                                "triggerPx": sl_price,
                                "tpsl": "sl",
                            }
                        },
                        reduce_only=True
                    )
                    logger.info(f"Stop Loss order placed: {sl_result}")
                    # Check for rejection
                    if isinstance(sl_result, dict) and sl_result.get("status") == "err":
                        raise RuntimeError(f"SL rejected: {sl_result.get('response')}")
                    break  # success
                except Exception as sl_err:
                    logger.error(f"[{symbol}] SL placement attempt {attempt} failed: {sl_err}")
                    if attempt >= 2:
                        # CRITICAL: Emergency close to avoid naked position
                        logger.critical(
                            f"[{symbol}] Stop Loss could not be placed after {attempt} attempts. "
                            f"Emergency closing position to prevent unprotected liquidation."
                        )
                        try:
                            self.exchange.market_close(symbol)
                        except Exception as close_err:
                            logger.critical(f"[{symbol}] Emergency close also FAILED: {close_err}")
                        return {
                            "status": "error",
                            "error": f"SL placement failed; position emergency-closed: {sl_err}"
                        }
                    time.sleep(1)

            # 4. Take Profit Order
            tp_result = None
            try:
                logger.info(f"Placing Take Profit trigger order @ ${tp_price:,.2f}...")
                tp_result = self.exchange.order(
                    name=symbol,
                    is_buy=sl_is_buy,
                    sz=size,
                    limit_px=tp_price,
                    order_type={
                        "trigger": {
                            "isMarket": True,
                            "triggerPx": tp_price,
                            "tpsl": "tp",
                        }
                    },
                    reduce_only=True
                )
                logger.info(f"Take Profit order placed: {tp_result}")
                if isinstance(tp_result, dict) and tp_result.get("status") == "err":
                    logger.warning(
                        f"[{symbol}] TP placement failed (SL is in place — position partially protected): "
                        f"{tp_result.get('response')}"
                    )
            except Exception as tp_err:
                # TP failure is not critical — SL already protects the position
                logger.warning(f"[{symbol}] TP placement failed (SL is active): {tp_err}")

            # Extract order IDs for clean lifecycle management
            sl_oid = None
            if isinstance(sl_result, dict):
                st = sl_result.get("response", {}).get("data", {}).get("statuses", [])
                if st and isinstance(st[0], dict):
                    sl_oid = st[0].get("resting", {}).get("oid")

            tp_oid = None
            if isinstance(tp_result, dict):
                st = tp_result.get("response", {}).get("data", {}).get("statuses", [])
                if st and isinstance(st[0], dict):
                    tp_oid = st[0].get("resting", {}).get("oid")

            record = {
                "id": f"live_{int(time.time()*1000)}",
                "symbol": symbol,
                "direction": setup.direction.value,
                "size": size,
                "entry_price": setup.entry_price,
                "stop_loss": sl_price,
                "take_profit": tp_price,
                "notional_usd": setup.notional_usd,
                "risk_usd": setup.risk_usd,
                "risk_reward": setup.risk_reward,
                "initial_sl": sl_price,
                "sl_oid": sl_oid,
                "tp_oid": tp_oid,
                "sz_decimals": getattr(setup, "sz_decimals", 3),
                "breakeven_triggered": False,
                "entry_time": int(time.time() * 1000),
                "status": "OPEN",
                "pnl": 0.0,
                "mode": "LIVE"
            }
            self.paper_positions[symbol] = record
            self.trade_history.append(record)

            return {
                "status": "success",
                "mode": "live",
                "open_result": open_result,
                "sl_result": sl_result,
                "tp_result": tp_result
            }

        except Exception as e:
            logger.error(f"Failed to execute live trade on Hyperliquid: {e}")
            return {"status": "error", "error": str(e)}

    def check_open_positions_update(self, current_prices: Dict[str, float]) -> List[Dict[str, Any]]:
        """
        Update unrealized PnL, trigger 1:1 R:R breakeven stop loss adjustments,
        and simulate SL/TP triggers for paper positions. Returns list of triggered events.
        """
        events: List[Dict[str, Any]] = []

        for symbol, pos in list(self.paper_positions.items()):
            cur_px = current_prices.get(symbol)
            if not cur_px:
                continue

            entry = pos["entry_price"]
            size = pos["size"]
            direction = pos["direction"]
            sl = pos["stop_loss"]
            tp = pos["take_profit"]
            initial_sl = pos.get("initial_sl", sl)

            # --- Breakeven Trailing Stop at 1:1 R:R ---
            if getattr(self.config, "enable_breakeven_at_1r", True) and not pos.get("breakeven_triggered"):
                if direction == "BULLISH":
                    risk_dist = entry - initial_sl
                    if risk_dist > 0 and cur_px >= (entry + risk_dist):
                        pos["breakeven_triggered"] = True
                        pos["stop_loss"] = entry
                        logger.info(
                            f"🛡️ [BREAKEVEN TRIGGERED] {symbol} reached 1:1 R:R! "
                            f"Stop Loss moved to Entry @ ${entry:,.2f}"
                        )
                        events.append({
                            "event": "BREAKEVEN_TRIGGERED",
                            "symbol": symbol,
                            "entry_price": entry,
                            "mode": pos.get("mode", "PAPER"),
                            "time": int(time.time() * 1000)
                        })
                        if pos.get("mode") == "LIVE" and self.exchange:
                            res = self._update_live_stop_loss(
                                symbol,
                                is_buy=False,
                                size=size,
                                new_sl_px=entry,
                                sz_decimals=pos.get("sz_decimals", 3),
                                old_sl_oid=pos.get("sl_oid")
                            )
                            if isinstance(res, dict):
                                st = res.get("response", {}).get("data", {}).get("statuses", [])
                                if st and isinstance(st[0], dict):
                                    pos["sl_oid"] = st[0].get("resting", {}).get("oid")
                elif direction == "BEARISH":
                    risk_dist = initial_sl - entry
                    if risk_dist > 0 and cur_px <= (entry - risk_dist):
                        pos["breakeven_triggered"] = True
                        pos["stop_loss"] = entry
                        logger.info(
                            f"🛡️ [BREAKEVEN TRIGGERED] {symbol} reached 1:1 R:R! "
                            f"Stop Loss moved to Entry @ ${entry:,.2f}"
                        )
                        events.append({
                            "event": "BREAKEVEN_TRIGGERED",
                            "symbol": symbol,
                            "entry_price": entry,
                            "mode": pos.get("mode", "PAPER"),
                            "time": int(time.time() * 1000)
                        })
                        if pos.get("mode") == "LIVE" and self.exchange:
                            res = self._update_live_stop_loss(
                                symbol,
                                is_buy=True,
                                size=size,
                                new_sl_px=entry,
                                sz_decimals=pos.get("sz_decimals", 3),
                                old_sl_oid=pos.get("sl_oid")
                            )
                            if isinstance(res, dict):
                                st = res.get("response", {}).get("data", {}).get("statuses", [])
                                if st and isinstance(st[0], dict):
                                    pos["sl_oid"] = st[0].get("resting", {}).get("oid")

            # --- Check SL / TP Trigger ---
            current_sl = pos["stop_loss"]
            if direction == "BULLISH":
                pnl = (cur_px - entry) * size
                # Check hit SL
                if cur_px <= current_sl:
                    pos["status"] = "CLOSED_SL"
                    pos["pnl"] = (current_sl - entry) * size
                    pos["exit_price"] = current_sl
                    pos["exit_time"] = int(time.time() * 1000)
                    logger.info(f"🔴 [PAPER SL HIT] {symbol} hit Stop Loss @ ${current_sl:,.2f} | PnL: ${pos['pnl']:.2f}")
                    events.append({
                        "event": "POSITION_CLOSED",
                        "symbol": symbol,
                        "status": "CLOSED_SL",
                        "pnl": pos["pnl"],
                        "exit_price": current_sl,
                        "mode": pos.get("mode", "PAPER")
                    })
                    if pos.get("mode") == "LIVE":
                        self.cancel_open_orders_for_symbol(symbol)
                    del self.paper_positions[symbol]
                # Check hit TP
                elif cur_px >= tp:
                    pos["status"] = "CLOSED_TP"
                    pos["pnl"] = (tp - entry) * size
                    pos["exit_price"] = tp
                    pos["exit_time"] = int(time.time() * 1000)
                    logger.info(f"🟢 [PAPER TP HIT] {symbol} hit Take Profit @ ${tp:,.2f} | PnL: +${pos['pnl']:.2f}")
                    events.append({
                        "event": "POSITION_CLOSED",
                        "symbol": symbol,
                        "status": "CLOSED_TP",
                        "pnl": pos["pnl"],
                        "exit_price": tp,
                        "mode": pos.get("mode", "PAPER")
                    })
                    if pos.get("mode") == "LIVE":
                        self.cancel_open_orders_for_symbol(symbol)
                    del self.paper_positions[symbol]
                else:
                    pos["pnl"] = pnl
            else:
                pnl = (entry - cur_px) * size
                # Check hit SL
                if cur_px >= current_sl:
                    pos["status"] = "CLOSED_SL"
                    pos["pnl"] = (entry - current_sl) * size
                    pos["exit_price"] = current_sl
                    pos["exit_time"] = int(time.time() * 1000)
                    logger.info(f"🔴 [PAPER SL HIT] {symbol} hit Stop Loss @ ${current_sl:,.2f} | PnL: ${pos['pnl']:.2f}")
                    events.append({
                        "event": "POSITION_CLOSED",
                        "symbol": symbol,
                        "status": "CLOSED_SL",
                        "pnl": pos["pnl"],
                        "exit_price": current_sl,
                        "mode": pos.get("mode", "PAPER")
                    })
                    if pos.get("mode") == "LIVE":
                        self.cancel_open_orders_for_symbol(symbol)
                    del self.paper_positions[symbol]
                # Check hit TP
                elif cur_px <= tp:
                    pos["status"] = "CLOSED_TP"
                    pos["pnl"] = (entry - tp) * size
                    pos["exit_price"] = tp
                    pos["exit_time"] = int(time.time() * 1000)
                    logger.info(f"🟢 [PAPER TP HIT] {symbol} hit Take Profit @ ${tp:,.2f} | PnL: +${pos['pnl']:.2f}")
                    events.append({
                        "event": "POSITION_CLOSED",
                        "symbol": symbol,
                        "status": "CLOSED_TP",
                        "pnl": pos["pnl"],
                        "exit_price": tp,
                        "mode": pos.get("mode", "PAPER")
                    })
                    if pos.get("mode") == "LIVE":
                        self.cancel_open_orders_for_symbol(symbol)
                    del self.paper_positions[symbol]
                else:
                    pos["pnl"] = pnl

        return events

    def _update_live_stop_loss(
        self,
        symbol: str,
        is_buy: bool,
        size: float,
        new_sl_px: float,
        sz_decimals: int = 3,
        old_sl_oid: Optional[int] = None
    ):
        """Update Stop Loss trigger order on Hyperliquid to breakeven."""
        if not self.exchange:
            return None
        try:
            from src.risk_manager import format_hyperliquid_price
            formatted_sl = format_hyperliquid_price(new_sl_px, sz_decimals=sz_decimals)

            # Cancel previous SL trigger order if known to prevent duplicate resting stops
            if old_sl_oid:
                try:
                    self.exchange.cancel(symbol, old_sl_oid)
                    logger.info(f"[{symbol}] Cancelled initial SL order {old_sl_oid} before placing breakeven SL")
                except Exception as c_err:
                    logger.warning(f"[{symbol}] Note: could not cancel initial SL {old_sl_oid}: {c_err}")

            logger.info(f"[{symbol}] Updating live SL trigger order to breakeven @ ${formatted_sl:,.2f}...")
            return self.exchange.order(
                name=symbol,
                is_buy=is_buy,
                sz=size,
                limit_px=formatted_sl,
                order_type={
                    "trigger": {
                        "isMarket": True,
                        "triggerPx": formatted_sl,
                        "tpsl": "sl",
                    }
                },
                reduce_only=True
            )
        except Exception as e:
            logger.warning(f"[{symbol}] Failed to update live SL to breakeven: {e}")
            return None

    def cancel_open_orders_for_symbol(self, symbol: str):
        """Cancel all resting and trigger orders for a coin on Hyperliquid when position closes."""
        if self.is_dry_run or not self.exchange:
            return
        try:
            target_addr = self.account_address or self.config.account_address
            if not target_addr:
                return
            from hyperliquid.info import Info
            info = Info(self.config.api_url, skip_ws=True)
            orders = info.frontend_open_orders(target_addr)
            for o in orders:
                if o.get("coin", "").upper() == symbol.upper():
                    oid = o.get("oid")
                    if oid:
                        self.exchange.cancel(symbol.upper(), oid)
                        logger.info(f"[{symbol}] Cancelled orphaned resting/trigger order {oid}")
        except Exception as e:
            logger.warning(f"[{symbol}] Error cancelling trigger orders on close: {e}")

    def reconcile_exchange_positions(
        self,
        live_positions: List[Dict[str, Any]],
        current_prices: Dict[str, float]
    ) -> List[Dict[str, Any]]:
        """
        Reconcile tracked live positions with actual on-chain Hyperliquid account state.
        1. If a position was active in self.paper_positions (mode=LIVE) but is no longer in live_positions,
           it has been closed on Hyperliquid (by SL, TP, or liquidation).
        2. If a position is active on Hyperliquid but missing from self.paper_positions,
           adopt it so we don't double-enter.
        """
        if self.is_dry_run or not self.exchange:
            return []

        events: List[Dict[str, Any]] = []
        live_coin_map = {
            p["coin"].upper(): p for p in live_positions if abs(float(p.get("size", 0.0))) > 0
        }

        # Check existing tracked live positions
        for symbol, pos in list(self.paper_positions.items()):
            if pos.get("mode") != "LIVE":
                continue

            if symbol not in live_coin_map:
                # Closed on exchange!
                cur_px = current_prices.get(symbol, pos.get("entry_price", 0.0))
                entry = pos["entry_price"]
                sl = pos["stop_loss"]
                tp = pos["take_profit"]
                direction = pos["direction"]
                size = pos["size"]

                if direction == "BULLISH":
                    if sl > 0 and cur_px <= sl:
                        status = "CLOSED_SL"
                        pnl = (sl - entry) * size
                    elif tp > 0 and cur_px >= tp:
                        status = "CLOSED_TP"
                        pnl = (tp - entry) * size
                    else:
                        status = "CLOSED_ON_EXCHANGE"
                        pnl = (cur_px - entry) * size
                else:
                    if sl > 0 and cur_px >= sl:
                        status = "CLOSED_SL"
                        pnl = (entry - sl) * size
                    elif tp > 0 and cur_px <= tp:
                        status = "CLOSED_TP"
                        pnl = (entry - tp) * size
                    else:
                        status = "CLOSED_ON_EXCHANGE"
                        pnl = (entry - cur_px) * size

                pos["status"] = status
                pos["pnl"] = pnl
                pos["exit_price"] = cur_px
                pos["exit_time"] = int(time.time() * 1000)
                logger.info(
                    f"⚡ [EXCHANGE SYNC] {symbol} position closed on Hyperliquid ({status}) | "
                    f"Est. PnL: ${pnl:.2f}"
                )
                self.cancel_open_orders_for_symbol(symbol)
                del self.paper_positions[symbol]
                events.append({
                    "event": "POSITION_CLOSED_ON_EXCHANGE",
                    "symbol": symbol,
                    "status": status,
                    "pnl": pnl,
                    "exit_price": cur_px,
                    "mode": "LIVE"
                })
            else:
                # Still open, update unrealized PnL and size
                ex_pos = live_coin_map[symbol]
                pos["pnl"] = float(ex_pos.get("unrealized_pnl", pos.get("pnl", 0.0)))
                pos["size"] = abs(float(ex_pos.get("size", pos["size"])))

        # Adopt any open positions on exchange that aren't tracked
        for coin, ex_pos in live_coin_map.items():
            if coin not in self.paper_positions:
                size_signed = float(ex_pos.get("size", 0.0))
                size = abs(size_signed)
                direction = "BULLISH" if size_signed > 0 else "BEARISH"
                entry_px = float(ex_pos.get("entry_px", 0.0))
                pnl = float(ex_pos.get("unrealized_pnl", 0.0))

                record = {
                    "id": f"live_adopted_{int(time.time()*1000)}",
                    "symbol": coin,
                    "direction": direction,
                    "size": size,
                    "entry_price": entry_px,
                    "stop_loss": 0.0,
                    "initial_sl": 0.0,
                    "take_profit": 0.0,
                    "notional_usd": round(size * entry_px, 2),
                    "risk_usd": 0.0,
                    "risk_reward": 0.0,
                    "entry_time": int(time.time() * 1000),
                    "status": "OPEN",
                    "pnl": pnl,
                    "mode": "LIVE",
                    "adopted_from_exchange": True,
                    "breakeven_triggered": False
                }
                self.paper_positions[coin] = record
                self.trade_history.append(record)
                logger.info(
                    f"🔄 [EXCHANGE SYNC] Discovered existing position on Hyperliquid: "
                    f"{direction} {size} {coin} @ ${entry_px:,.2f}. Position adopted into tracker."
                )
                events.append({
                    "event": "POSITION_ADOPTED",
                    "symbol": coin,
                    "direction": direction,
                    "size": size,
                    "entry_price": entry_px
                })

        return events

    def close_all_positions(self) -> List[Dict[str, Any]]:
        """Emergency close all positions."""
        results = []
        for symbol in list(self.paper_positions.keys()):
            if self.is_dry_run or not self.exchange:
                pos = self.paper_positions.pop(symbol, None)
                if pos:
                    pos["status"] = "MANUALLY_CLOSED"
                    results.append({"symbol": symbol, "status": "closed_paper"})
            else:
                try:
                    res = self.exchange.market_close(symbol)
                    self.cancel_open_orders_for_symbol(symbol)
                    self.paper_positions.pop(symbol, None)
                    results.append({"symbol": symbol, "status": "closed_live", "response": res})
                except Exception as e:
                    results.append({"symbol": symbol, "status": "error", "error": str(e)})
        return results
