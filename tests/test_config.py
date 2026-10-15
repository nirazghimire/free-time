"""Unit tests for configuration loading and environment validation."""

import pytest
from config import BotConfig


def test_config_defaults_and_properties():
    cfg = BotConfig()
    assert cfg.env in ("testnet", "mainnet")
    assert "BTC" in cfg.symbols
    assert "ETH" in cfg.symbols
    assert "SOL" in cfg.symbols
    assert cfg.timeframes == ["1w", "1d", "4h", "1h", "15m", "5m"]
    assert cfg.min_order_notional >= 10.0
    assert cfg.leverage >= 1

    # Testnet vs Mainnet URL check
    testnet_cfg = BotConfig(env="testnet")
    assert testnet_cfg.is_testnet is True
    assert "testnet" in testnet_cfg.api_url

    mainnet_cfg = BotConfig(env="mainnet")
    assert mainnet_cfg.is_testnet is False
    assert "api.hyperliquid.xyz" in mainnet_cfg.api_url


def test_symbol_custom_parsing():
    cfg = BotConfig()
    cfg.symbols = ["BTC", "eth", "sol ", "ARB"]
    # Check normalization
    cleaned = [s.strip().upper() for s in cfg.symbols]
    assert cleaned == ["BTC", "ETH", "SOL", "ARB"]
