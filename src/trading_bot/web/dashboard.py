"""Web dashboard — live view of the bot in a browser.

Served by aiohttp (already a core dependency, zero new packages). Two ways
to run it:

  - Attached: the trading engine starts it automatically when
    ``dashboard.enabled`` is true; it shows live equity, open positions,
    risk state, and the trade journal.
  - Standalone: ``python main.py dashboard`` serves a journal-only view
    (readable while a bot writes to the same SQLite file thanks to WAL).

Endpoints:
  GET /              HTML single-page dashboard
  GET /api/status    bot mode/running/equity/positions/risk (or journal summary)
  GET /api/equity    equity-curve points from the journal
  GET /api/trades    recent closed trades
  GET /api/events    recent bot events
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from aiohttp import web

from ..persistence.journal import TradeJournal

if TYPE_CHECKING:  # pragma: no cover - import cycle guard, typing only
    from ..core import TradingBot

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


def parse_limit(request: web.Request, default: int, lo: int = 1, hi: int = 5000) -> int:
    """Validate the ?limit= query param: bad input gets a clean 400, not a
    500; valid input is clamped to [lo, hi]."""
    raw = request.query.get("limit")
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise web.HTTPBadRequest(
            text='{"error": "limit must be an integer"}',
            content_type="application/json",
        ) from None
    return max(lo, min(hi, value))


class Dashboard:
    def __init__(
        self,
        journal: TradeJournal,
        bot: Optional["TradingBot"] = None,
        host: str = "127.0.0.1",
        port: int = 8899,
    ):
        self.journal = journal
        self.bot = bot
        self.host = host
        self.port = int(port)
        self._runner: Optional[web.AppRunner] = None

    # ------------------------------------------------------------------ #
    # Handlers
    # ------------------------------------------------------------------ #

    async def index(self, request: web.Request) -> web.Response:
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        return web.Response(text=html, content_type="text/html")

    async def api_status(self, request: web.Request) -> web.Response:
        if self.bot is not None:
            try:
                status = await self.bot.status()
                status["source"] = "live"
                return web.json_response(status)
            except Exception as exc:  # noqa: BLE001 - dashboard must not crash the bot
                logger.warning("Dashboard status error: %s", exc)
        summary = self.journal.performance_summary()
        return web.json_response(
            {
                "source": "journal",
                "mode": "offline",
                "running": False,
                "portfolio": summary,
            }
        )

    async def api_equity(self, request: web.Request) -> web.Response:
        rows = self.journal.equity_history(parse_limit(request, 500))
        return web.json_response(rows)

    async def api_trades(self, request: web.Request) -> web.Response:
        return web.json_response(
            self.journal.recent_trades(parse_limit(request, 100))
        )

    async def api_events(self, request: web.Request) -> web.Response:
        return web.json_response(
            self.journal.recent_events(parse_limit(request, 50))
        )

    async def api_market(self, request: web.Request) -> web.Response:
        """Live market prices straight from the exchange (live mode only)."""
        if self.bot is None:
            return web.json_response([])
        try:
            return web.json_response(await self.bot.market_snapshot())
        except Exception as exc:  # noqa: BLE001
            logger.warning("Dashboard market error: %s", exc)
            return web.json_response([])

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def _build_app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/", self.index)
        app.router.add_get("/api/status", self.api_status)
        app.router.add_get("/api/equity", self.api_equity)
        app.router.add_get("/api/trades", self.api_trades)
        app.router.add_get("/api/events", self.api_events)
        app.router.add_get("/api/market", self.api_market)
        return app

    async def start(self) -> str:
        self._runner = web.AppRunner(self._build_app(), access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()
        url = f"http://{self.host}:{self.port}"
        logger.info("Dashboard running at %s", url)
        return url

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
