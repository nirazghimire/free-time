"""Market Data Client for Hyperliquid.
Fetches multi-timeframe OHLCV candles, mid prices, metadata, and user account state.
"""

import time
import logging
from typing import Dict, List, Optional, Any
import pandas as pd
from hyperliquid.info import Info
from hyperliquid.utils import constants

from config import BotConfig

logger = logging.getLogger("market_data")

# Milliseconds mapping for candle lookback history
LOOKBACK_MS = {
    "1w": 365 * 24 * 3600 * 1000,   # 1 year (~52 candles)
    "1d": 120 * 24 * 3600 * 1000,   # 120 days
    "4h": 30 * 24 * 3600 * 1000,    # 30 days (~180 candles)
    "1h": 14 * 24 * 3600 * 1000,    # 14 days (~336 candles)
    "15m": 4 * 24 * 3600 * 1000,    # 4 days (~384 candles)
    "5m": 2 * 24 * 3600 * 1000,     # 2 days (~576 candles)
}


class MarketDataClient:
    def __init__(self, config: BotConfig):
        self.config = config
        self.base_url = config.api_url
        self.info = Info(self.base_url, skip_ws=True)
        self._meta_cache: Optional[Dict[str, Any]] = None
        self._asset_decimals: Dict[str, int] = {}
        self._cache_candles: Dict[str, Dict[str, Any]] = {}

    def get_meta(self) -> Dict[str, Any]:
        """Fetch universe metadata including szDecimals."""
        if not self._meta_cache:
            try:
                self._meta_cache = self.info.meta()
                for asset in self._meta_cache.get("universe", []):
                    self._asset_decimals[asset["name"]] = asset.get("szDecimals", 3)
            except Exception as e:
                logger.error(f"Failed to fetch meta: {e}")
                return {"universe": []}
        return self._meta_cache

    def get_sz_decimals(self, symbol: str) -> int:
        """Get lot size decimal precision for an asset."""
        if not self._asset_decimals:
            self.get_meta()
        return self._asset_decimals.get(symbol.upper(), 3)

    def get_mid_prices(self) -> Dict[str, float]:
        """Fetch all current mid prices from Hyperliquid."""
        try:
            mids = self.info.all_mids()
            return {k: float(v) for k, v in mids.items()}
        except Exception as e:
            logger.error(f"Failed to fetch mid prices: {e}")
            return {}

    def get_current_price(self, symbol: str) -> Optional[float]:
        """Get current mid price for a specific symbol."""
        mids = self.get_mid_prices()
        return mids.get(symbol.upper())

    def fetch_candles(self, symbol: str, interval: str, limit_candles: int = 250) -> pd.DataFrame:
        """
        Fetch OHLCV candle data for a symbol and interval.
        Returns a sorted pandas DataFrame with columns: ['timestamp', 'open', 'high', 'low', 'close', 'volume']
        """
        now_ms = int(time.time() * 1000)
        lookback = LOOKBACK_MS.get(interval, 48 * 3600 * 1000)
        start_ms = now_ms - lookback

        try:
            raw_candles = self.info.candles_snapshot(symbol.upper(), interval, start_ms, now_ms)
            if not raw_candles:
                return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

            records = []
            for c in raw_candles:
                records.append({
                    "timestamp": c["t"],
                    "open": float(c["o"]),
                    "high": float(c["h"]),
                    "low": float(c["l"]),
                    "close": float(c["c"]),
                    "volume": float(c.get("v", 0.0)),
                })

            df = pd.DataFrame(records)
            df = df.sort_values("timestamp").reset_index(drop=True)
            if len(df) > limit_candles:
                df = df.iloc[-limit_candles:].reset_index(drop=True)
            return df

        except Exception as e:
            logger.error(f"Error fetching {interval} candles for {symbol}: {e}")
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    def fetch_all_timeframes(self, symbol: str) -> Dict[str, pd.DataFrame]:
        """Fetch all configured timeframes for a symbol."""
        result = {}
        for tf in self.config.timeframes:
            result[tf] = self.fetch_candles(symbol, tf)
        return result

    def get_account_state(self, user_address: Optional[str] = None) -> Dict[str, Any]:
        """
        Fetch user margin summary, equity, and positions.
        """
        target_addr = user_address or self.config.account_address
        if not target_addr:
            return {
                "account_value": 0.0,
                "total_margin_used": 0.0,
                "withdrawable": 0.0,
                "positions": [],
            }

        try:
            state = self.info.user_state(target_addr)
            margin_summary = state.get("marginSummary", {})
            positions_raw = state.get("assetPositions", [])
            
            positions = []
            for p in positions_raw:
                pos = p.get("position", {})
                size = float(pos.get("szi", 0.0))
                if abs(size) > 0:
                    positions.append({
                        "coin": pos.get("coin"),
                        "size": size,
                        "entry_px": float(pos.get("entryPx", 0.0)),
                        "liquidation_px": float(pos.get("liquidationPx", 0.0)) if pos.get("liquidationPx") else None,
                        "unrealized_pnl": float(pos.get("unrealizedPnl", 0.0)),
                        "leverage": pos.get("leverage"),
                        "margin_used": float(pos.get("marginUsed", 0.0)),
                    })

            return {
                "account_value": float(margin_summary.get("accountValue", 0.0)),
                "total_margin_used": float(margin_summary.get("totalMarginUsed", 0.0)),
                "withdrawable": float(state.get("withdrawable", 0.0)),
                "positions": positions,
            }
        except Exception as e:
            logger.error(f"Failed to fetch account state for {target_addr}: {e}")
            return {
                "account_value": 0.0,
                "total_margin_used": 0.0,
                "withdrawable": 0.0,
                "positions": [],
            }
