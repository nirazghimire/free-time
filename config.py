"""Configuration module for Hyperliquid MTF SMC Trading Bot."""

import os
from dataclasses import dataclass, field
from typing import List
from dotenv import load_dotenv

load_dotenv()


@dataclass
class BotConfig:
    # Environment
    env: str = os.getenv("HYPERLIQUID_ENV", "testnet").lower()
    private_key: str = os.getenv("HYPERLIQUID_PRIVATE_KEY", "")
    account_address: str = os.getenv("HYPERLIQUID_ACCOUNT_ADDRESS", "")
    
    # Operation Mode
    dry_run: bool = os.getenv("DRY_RUN", "true").lower() in ("true", "1", "yes")
    auto_trade_enabled: bool = os.getenv("AUTO_TRADE_ENABLED", "true").lower() in ("true", "1", "yes")
    
    # Symbols
    symbols: List[str] = field(default_factory=lambda: [
        s.strip().upper() for s in os.getenv("TRADING_SYMBOLS", "BTC,ETH,SOL").split(",") if s.strip()
    ])
    
    # Timeframes
    timeframes: List[str] = field(default_factory=lambda: ["1w", "1d", "4h", "1h", "15m", "5m"])
    
    # Risk parameters
    risk_usd_per_trade: float = float(os.getenv("RISK_USD_PER_TRADE", "1.00"))
    min_order_notional: float = float(os.getenv("MIN_ORDER_NOTIONAL", "11.0"))
    leverage: int = int(os.getenv("LEVERAGE", "3"))
    target_rr: float = float(os.getenv("TARGET_RR", "2.5"))
    use_structural_tp: bool = os.getenv("USE_STRUCTURAL_TP", "true").lower() in ("true", "1", "yes")
    min_target_rr: float = float(os.getenv("MIN_TARGET_RR", "1.5"))
    sl_buffer_pct: float = float(os.getenv("SL_BUFFER_PCT", "0.001"))
    max_open_positions: int = int(os.getenv("MAX_OPEN_POSITIONS", "1"))  # 1 position max for $10-$20 accounts
    
    # SMC Lookbacks
    swing_lookback: int = 2  # candles to left and right to define a pivot high/low
    sweep_max_candles_age: int = 12  # how many 15m candles back a sweep remains valid
    trigger_max_5m_candles: int = 3  # tightened to 3 candles (15 mins) to prevent price chasing

    # Strategy Configuration
    strategy_mode: str = os.getenv("STRATEGY_MODE", "DEMAND_FVG_CONFLUENCE").upper()  # "DEMAND_FVG_CONFLUENCE" or "SWEEP_BOS"
    confirmation_timeframe: str = os.getenv("CONFIRMATION_TIMEFRAME", "15m").lower()  # "15m" or "5m"
    rejection_wick_ratio: float = float(os.getenv("REJECTION_WICK_RATIO", "0.30"))  # Min lower/upper wick ratio for reaction confirmation
    rejection_wick_ratio_1h: float = float(os.getenv("REJECTION_WICK_RATIO_1H", "0.70"))  # 1H wick ratio >= 70% of body
    demand_zone_max_age_candles: int = int(os.getenv("DEMAND_ZONE_MAX_AGE_CANDLES", "50"))  # Max age of active demand/supply zone
    discount_premium_filter_enabled: bool = os.getenv("DISCOUNT_PREMIUM_FILTER_ENABLED", "true").lower() in ("true", "1", "yes")
    max_equilibrium_pct: float = float(os.getenv("MAX_EQUILIBRIUM_PCT", "0.50"))  # Longs <= 50% (Discount), Shorts >= 50% (Premium)

    # Trailing Stop / Breakeven
    enable_breakeven_at_1r: bool = os.getenv("ENABLE_BREAKEVEN_AT_1R", "true").lower() in ("true", "1", "yes")

    # Notifications (Discord & Telegram Webhooks)
    discord_webhook_url: str = os.getenv("DISCORD_WEBHOOK_URL", "")
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")
    
    # Dashboard
    dashboard_port: int = int(os.getenv("DASHBOARD_PORT", "8000"))
    
    @property
    def is_testnet(self) -> bool:
        return self.env == "testnet"

    @property
    def api_url(self) -> str:
        from hyperliquid.utils import constants
        return constants.TESTNET_API_URL if self.is_testnet else constants.MAINNET_API_URL


# Default global instance
config = BotConfig()
