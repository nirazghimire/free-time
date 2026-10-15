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

    def check_open_positions_update(self, current_prices: Dict[str, float]):
        """
        Update unrealized PnL and simulate SL/TP triggers for paper positions.
        """
        for symbol, pos in list(self.paper_positions.items()):
            cur_px = current_prices.get(symbol)
            if not cur_px:
                continue

            entry = pos["entry_price"]
            size = pos["size"]
            direction = pos["direction"]
            sl = pos["stop_loss"]
            tp = pos["take_profit"]

            if direction == "BULLISH":
                pnl = (cur_px - entry) * size
                # Check hit SL
                if cur_px <= sl:
                    pos["status"] = "CLOSED_SL"
                    pos["pnl"] = (sl - entry) * size
                    pos["exit_price"] = sl
                    pos["exit_time"] = int(time.time() * 1000)
                    logger.info(f"🔴 [PAPER SL HIT] {symbol} hit Stop Loss @ ${sl:,.2f} | PnL: ${pos['pnl']:.2f}")
                    del self.paper_positions[symbol]
                # Check hit TP
                elif cur_px >= tp:
                    pos["status"] = "CLOSED_TP"
                    pos["pnl"] = (tp - entry) * size
                    pos["exit_price"] = tp
                    pos["exit_time"] = int(time.time() * 1000)
                    logger.info(f"🟢 [PAPER TP HIT] {symbol} hit Take Profit @ ${tp:,.2f} | PnL: +${pos['pnl']:.2f}")
                    del self.paper_positions[symbol]
                else:
                    pos["pnl"] = pnl
            else:
                pnl = (entry - cur_px) * size
                # Check hit SL
                if cur_px >= sl:
                    pos["status"] = "CLOSED_SL"
                    pos["pnl"] = (entry - sl) * size
                    pos["exit_price"] = sl
                    pos["exit_time"] = int(time.time() * 1000)
                    logger.info(f"🔴 [PAPER SL HIT] {symbol} hit Stop Loss @ ${sl:,.2f} | PnL: ${pos['pnl']:.2f}")
                    del self.paper_positions[symbol]
                # Check hit TP
                elif cur_px <= tp:
                    pos["status"] = "CLOSED_TP"
                    pos["pnl"] = (entry - tp) * size
                    pos["exit_price"] = tp
                    pos["exit_time"] = int(time.time() * 1000)
                    logger.info(f"🟢 [PAPER TP HIT] {symbol} hit Take Profit @ ${tp:,.2f} | PnL: +${pos['pnl']:.2f}")
                    del self.paper_positions[symbol]
                else:
                    pos["pnl"] = pnl

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
                    self.paper_positions.pop(symbol, None)
                    results.append({"symbol": symbol, "status": "closed_live", "response": res})
                except Exception as e:
                    results.append({"symbol": symbol, "status": "error", "error": str(e)})
        return results
