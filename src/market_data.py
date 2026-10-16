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
    "12h": 60 * 24 * 3600 * 1000,   # 60 days (~120 candles)
    "4h": 30 * 24 * 3600 * 1000,    # 30 days (~180 candles)
    "1h": 14 * 24 * 3600 * 1000,    # 14 days (~336 candles)
    "15m": 4 * 24 * 3600 * 1000,    # 4 days (~384 candles)
    "5m": 2 * 24 * 3600 * 1000,     # 2 days (~576 candles)
}

# Milliseconds duration for single candle intervals
INTERVAL_MS = {
    "1w": 7 * 24 * 3600 * 1000,
    "1d": 24 * 3600 * 1000,
    "12h": 12 * 3600 * 1000,
    "4h": 4 * 3600 * 1000,
    "1h": 3600 * 1000,
    "15m": 15 * 60 * 1000,
    "5m": 5 * 60 * 1000,
}

# TTL cache in seconds: Higher timeframes don't change every 20 seconds
CANDLE_CACHE_TTL = {
    "1w": 3600,   # 1 hour
    "1d": 1800,   # 30 mins
    "12h": 1800,  # 30 mins
    "4h": 600,    # 10 mins
    "1h": 300,    # 5 mins
    "15m": 0,     # Fresh every scan
    "5m": 0,      # Fresh every scan
}


class MarketDataClient:
    def __init__(self, config: BotConfig):
        self.config = config
        self.base_url = config.api_url
        try:
            self.info = Info(self.base_url, skip_ws=True)
        except Exception as e:
            logger.warning(f"Could not initialize Hyperliquid Info client: {e}")
            self.info = None
        self._meta_cache: Optional[Dict[str, Any]] = None
        self._asset_decimals: Dict[str, int] = {}
        self._cache_candles: Dict[str, Dict[str, Any]] = {}
        self._candle_cache: Dict[str, tuple] = {}  # key: (symbol, interval) -> (cached_at_sec, df)

    def _ensure_info(self) -> Optional[Info]:
        if self.info is None:
            try:
                self.info = Info(self.base_url, skip_ws=True)
            except Exception:
                return None
        return self.info

    def get_meta(self) -> Dict[str, Any]:
        """Fetch universe metadata including szDecimals."""
        if not self._meta_cache:
            info = self._ensure_info()
            if not info:
                return {"universe": []}
            try:
                self._meta_cache = info.meta()
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
        fallback_decimals = {"BTC": 5, "ETH": 4, "SOL": 2}
        return self._asset_decimals.get(symbol.upper(), fallback_decimals.get(symbol.upper(), 3))

    def get_mid_prices(self) -> Dict[str, float]:
        """Fetch all current mid prices from Hyperliquid."""
        info = self._ensure_info()
        if not info:
            return {}
        try:
            mids = info.all_mids()
            return {k: float(v) for k, v in mids.items()}
        except Exception as e:
            logger.error(f"Failed to fetch mid prices: {e}")
            return {}

    def get_current_price(self, symbol: str) -> Optional[float]:
        """Get current mid price for a specific symbol."""
        mids = self.get_mid_prices()
        return mids.get(symbol.upper())

    def fetch_candles(
        self,
        symbol: str,
        interval: str,
        limit_candles: int = 250,
        only_closed: bool = True
    ) -> pd.DataFrame:
        """
        Fetch OHLCV candle data for a symbol and interval.
        - only_closed=True filters out the in-progress (active) candle to ensure SMC
          swings, BOS, and sweeps only execute on confirmed candle body closes.
        - Respects TTL caching for higher timeframes to prevent rate-limiting.
        """
        sym_key = f"{symbol.upper()}_{interval.lower()}"
        now_sec = time.time()
        ttl = CANDLE_CACHE_TTL.get(interval.lower(), 0)

        # Check TTL cache
        if ttl > 0 and sym_key in self._candle_cache:
            cached_at, cached_df = self._candle_cache[sym_key]
            if (now_sec - cached_at) < ttl and not cached_df.empty:
                return cached_df.copy()

        now_ms = int(now_sec * 1000)
        lookback = LOOKBACK_MS.get(interval, 48 * 3600 * 1000)
        start_ms = now_ms - lookback

        try:
            raw_candles = self.info.candles_snapshot(symbol.upper(), interval, start_ms, now_ms)
            if not raw_candles:
                return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

            interval_dur_ms = INTERVAL_MS.get(interval.lower(), 0)
            records = []
            for c in raw_candles:
                c_open_time = int(c["t"])
                c_close_time = int(c.get("T", c_open_time + interval_dur_ms))

                # Filter out in-progress candle if only_closed is True
                if only_closed:
                    if c_close_time > now_ms:
                        continue

                records.append({
                    "timestamp": c_open_time,
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

            # Store in TTL cache if applicable
            if ttl > 0 and not df.empty:
                self._candle_cache[sym_key] = (now_sec, df.copy())

            return df

        except Exception as e:
            logger.error(f"Error fetching {interval} candles for {symbol}: {e}")
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    def fetch_all_timeframes(self, symbol: str) -> Dict[str, pd.DataFrame]:
        """Fetch all configured timeframes for a symbol with confirmed closed candles."""
        result = {}
        for tf in self.config.timeframes:
            result[tf] = self.fetch_candles(symbol, tf, only_closed=True)
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
