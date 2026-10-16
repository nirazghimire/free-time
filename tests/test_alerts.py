"""Unit tests for AlertManager notification system."""

import pytest
from unittest.mock import patch, MagicMock
from config import BotConfig
from src.alerts import AlertManager


def test_alert_manager_disabled_when_no_credentials():
    config = BotConfig(discord_webhook_url="", telegram_bot_token="", telegram_chat_id="")
    manager = AlertManager(config)
    assert manager.is_enabled is False

    # Should safely no-op without exceptions
    manager.send_trade_entry({"symbol": "BTC", "direction": "BULLISH"})
    manager.send_breakeven("BTC", 80000.0)
    manager.send_trade_exit("BTC", "CLOSED_TP", 10.0, 81000.0)
    manager.send_emergency_alert("BTC", "Test error")


def test_alert_manager_enabled_and_dispatch():
    config = BotConfig(
        discord_webhook_url="https://discord.com/api/webhooks/test",
        telegram_bot_token="123456:ABC-DEF",
        telegram_chat_id="987654321"
    )
    manager = AlertManager(config)
    assert manager.is_enabled is True

    with patch.object(manager, "_send_discord") as mock_discord, \
         patch.object(manager, "_send_telegram") as mock_telegram:
        
        manager._dispatch("Test notification message", 0x00FF00)
        mock_discord.assert_called_once()
        mock_telegram.assert_called_once()
