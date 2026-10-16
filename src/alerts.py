"""Alert Notification System for MTF SMC Trading Bot.
Dispatches real-time alerts to Discord and Telegram webhooks for:
- Trade entries (Direction, Entry, SL, TP, Risk, Size)
- Breakeven triggers (1:1 R:R achieved, SL moved to entry)
- Trade exits (SL hit, TP hit, with realized PnL)
- Emergency alerts (SL order failure, position rollbacks)

Uses non-blocking background threads so notification network latency
never blocks the primary trading scan loop.
"""

import json
import logging
import threading
import urllib.request
import urllib.error
from typing import Optional, Dict, Any

from config import BotConfig

logger = logging.getLogger("alerts")


class AlertManager:
    def __init__(self, config: BotConfig):
        self.config = config
        self.discord_url = config.discord_webhook_url.strip() if config.discord_webhook_url else ""
        self.telegram_token = config.telegram_bot_token.strip() if config.telegram_bot_token else ""
        self.telegram_chat_id = config.telegram_chat_id.strip() if config.telegram_chat_id else ""
        self.is_enabled = bool(self.discord_url or (self.telegram_token and self.telegram_chat_id))

    def _async_send(self, fn, *args):
        """Run alert dispatch in a daemon thread so it never blocks execution."""
        t = threading.Thread(target=fn, args=args, daemon=True)
        t.start()

    def send_trade_entry(self, setup_data: Dict[str, Any], mode: str = "PAPER"):
        """Notify on new trade entry."""
        if not self.is_enabled:
            return

        symbol = setup_data.get("symbol", "N/A")
        direction = setup_data.get("direction", "N/A")
        entry = setup_data.get("entry_price", 0.0)
        sl = setup_data.get("stop_loss", 0.0)
        tp = setup_data.get("take_profit", 0.0)
        size = setup_data.get("size", 0.0)
        risk = setup_data.get("risk_usd", 0.0)
        rr = setup_data.get("risk_reward", 2.5)

        dir_emoji = "🟢 LONG" if direction == "BULLISH" else "🔴 SHORT"
        mode_badge = f"[{mode}]"

        text = (
            f"🚀 **{mode_badge} Trade Executed: {symbol}**\n"
            f"• **Side:** {dir_emoji}\n"
            f"• **Entry Price:** ${entry:,.2f}\n"
            f"• **Stop Loss:** ${sl:,.2f}\n"
            f"• **Take Profit:** ${tp:,.2f}\n"
            f"• **Position Size:** {size} {symbol}\n"
            f"• **Risk:** ${risk:.2f} (Target R:R 1:{rr})\n"
        )
        self._async_send(self._dispatch, text, 0x00D26A if direction == "BULLISH" else 0xF8312F)

    def send_breakeven(self, symbol: str, entry_price: float, mode: str = "PAPER"):
        """Notify when trade achieves 1:1 R:R and SL is moved to entry."""
        if not self.is_enabled:
            return

        text = (
            f"🛡️ **[{mode}] Breakeven Triggered: {symbol}**\n"
            f"• 1:1 Risk-to-Reward reached!\n"
            f"• Stop Loss moved to Entry: **${entry_price:,.2f}**\n"
            f"• Trade is now risk-free."
        )
        self._async_send(self._dispatch, text, 0x3B82F6)

    def send_trade_exit(self, symbol: str, status: str, pnl: float, exit_price: float, mode: str = "PAPER"):
        """Notify on trade closure (SL, TP, or manual)."""
        if not self.is_enabled:
            return

        is_profit = pnl >= 0
        status_emoji = "🎯 TP HIT" if "TP" in status else "🛑 SL HIT" if "SL" in status else "⏹️ CLOSED"
        pnl_str = f"+${pnl:.2f}" if is_profit else f"-${abs(pnl):.2f}"

        text = (
            f"{status_emoji} **[{mode}] Position Closed: {symbol}**\n"
            f"• **Outcome:** {status}\n"
            f"• **Exit Price:** ${exit_price:,.2f}\n"
            f"• **Realized PnL:** **{pnl_str}**\n"
        )
        self._async_send(self._dispatch, text, 0x10B981 if is_profit else 0xEF4444)

    def send_emergency_alert(self, symbol: str, message: str):
        """Notify on critical execution issues (e.g. SL order placement failed)."""
        if not self.is_enabled:
            return

        text = (
            f"🚨 **CRITICAL ALERT: {symbol}**\n"
            f"• **Issue:** {message}\n"
            f"• Immediate manual inspection recommended."
        )
        self._async_send(self._dispatch, text, 0xDC2626)

    def _dispatch(self, message: str, color_hex: int = 0x00E5FF):
        """Send message to configured Discord and Telegram targets."""
        if self.discord_url:
            self._send_discord(message, color_hex)
        if self.telegram_token and self.telegram_chat_id:
            self._send_telegram(message)

    def _send_discord(self, message: str, color_hex: int):
        try:
            payload = {
                "embeds": [{
                    "description": message,
                    "color": color_hex,
                    "footer": {"text": "Hyperliquid MTF SMC Bot"}
                }]
            }
            req = urllib.request.Request(
                self.discord_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "HyperliquidSMCBot/1.0"
                }
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                pass
        except Exception as e:
            logger.warning(f"Failed to deliver Discord alert: {e}")

    def _send_telegram(self, message: str):
        try:
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            # Strip markdown formatting bold tags for telegram markdown compatibility
            payload = {
                "chat_id": self.telegram_chat_id,
                "text": message,
                "parse_mode": "Markdown"
            }
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "HyperliquidSMCBot/1.0"
                }
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                pass
        except Exception as e:
            logger.warning(f"Failed to deliver Telegram alert: {e}")
