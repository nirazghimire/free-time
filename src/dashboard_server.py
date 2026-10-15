"""Async HTTP and WebSocket Dashboard Server.
Serves the web UI and provides real-time API endpoints to monitor and control
the Hyperliquid Multi-Timeframe SMC Trading Bot.
"""

import os
import json
import logging
from aiohttp import web, WSMsgType
from typing import Set

from config import BotConfig
from src.bot import SMCBot

logger = logging.getLogger("dashboard_server")


class DashboardServer:
    def __init__(self, bot: SMCBot, config: BotConfig):
        self.bot = bot
        self.config = config
        self.app = web.Application()
        self.websockets: Set[web.WebSocketResponse] = set()
        self.static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")

        self.loop = None
        self._setup_routes()
        self.bot.add_listener(self._on_bot_event)

    def _setup_routes(self):
        # API Endpoints
        self.app.router.add_get("/api/snapshot", self.handle_snapshot)
        self.app.router.add_post("/api/scan", self.handle_force_scan)
        self.app.router.add_post("/api/toggle_autotrade", self.handle_toggle_autotrade)
        self.app.router.add_post("/api/toggle_dryrun", self.handle_toggle_dryrun)
        self.app.router.add_post("/api/close_all", self.handle_close_all)
        self.app.router.add_get("/ws", self.handle_websocket)

        # Static files
        self.app.router.add_static("/static", self.static_dir)
        self.app.router.add_get("/", self.handle_index)

    async def handle_index(self, request: web.Request) -> web.Response:
        index_path = os.path.join(self.static_dir, "index.html")
        if os.path.exists(index_path):
            with open(index_path, "r", encoding="utf-8") as f:
                return web.Response(text=f.read(), content_type="text/html")
        return web.Response(text="Web directory not initialized yet.", status=404)

    async def handle_snapshot(self, request: web.Request) -> web.Response:
        snapshot = self.bot.get_dashboard_snapshot()
        return web.json_response(snapshot)

    async def handle_force_scan(self, request: web.Request) -> web.Response:
        # Run scan in thread
        import asyncio
        await asyncio.to_thread(self.bot.scan_once)
        snapshot = self.bot.get_dashboard_snapshot()
        return web.json_response({"status": "success", "snapshot": snapshot})

    async def handle_toggle_autotrade(self, request: web.Request) -> web.Response:
        self.bot.config.auto_trade_enabled = not self.bot.config.auto_trade_enabled
        state = self.bot.config.auto_trade_enabled
        self.bot.broadcast_event("CONFIG_CHANGED", {"auto_trade_enabled": state})
        return web.json_response({"auto_trade_enabled": state})

    async def handle_toggle_dryrun(self, request: web.Request) -> web.Response:
        self.bot.config.dry_run = not self.bot.config.dry_run
        self.bot.executor.is_dry_run = self.bot.config.dry_run
        # Re-initialize exchange client when switching TO live mode so real orders can be submitted
        if not self.bot.config.dry_run and self.bot.executor.exchange is None:
            self.bot.executor._init_exchange()
        mode = "PAPER" if self.bot.config.dry_run else "LIVE"
        self.bot.broadcast_event("CONFIG_CHANGED", {"mode": mode})
        return web.json_response({"dry_run": self.bot.config.dry_run, "mode": mode})

    async def handle_close_all(self, request: web.Request) -> web.Response:
        res = self.bot.executor.close_all_positions()
        self.bot.broadcast_event("POSITIONS_CLOSED", res)
        return web.json_response({"status": "success", "results": res})

    async def handle_websocket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.websockets.add(ws)
        logger.info(f"WebSocket client connected. Total clients: {len(self.websockets)}")

        # Send initial snapshot immediately
        await ws.send_str(json.dumps({
            "type": "INITIAL_STATE",
            "data": self.bot.get_dashboard_snapshot()
        }))

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    # Echo or client ping handling
                    pass
                elif msg.type == WSMsgType.ERROR:
                    logger.warning(f"WebSocket connection closed with exception {ws.exception()}")
        finally:
            self.websockets.discard(ws)
            logger.info("WebSocket client disconnected")

        return ws

    def _on_bot_event(self, event_data: dict):
        """Called whenever the bot emits an event; pushes to all WebSocket clients."""
        if not self.websockets or not self.loop:
            return
        msg = json.dumps(event_data)
        import asyncio
        for ws in list(self.websockets):
            if not ws.closed:
                try:
                    asyncio.run_coroutine_threadsafe(ws.send_str(msg), self.loop)
                except Exception as e:
                    logger.debug(f"Could not dispatch WS message: {e}")

    async def start(self):
        """Start the aiohttp web server."""
        import asyncio
        self.loop = asyncio.get_running_loop()
        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", self.config.dashboard_port)
        await site.start()
        logger.info(f"Dashboard running at http://localhost:{self.config.dashboard_port}")
