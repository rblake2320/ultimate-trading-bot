"""Notifications: Telegram, Discord webhook, and log fallback.

Uses aiohttp directly (already a ccxt dependency) — no heavyweight bot
frameworks. All sends are fire-and-forget with a short timeout so a slow
notification can never stall the trading loop.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class NotificationManager:
    def __init__(self, config: Optional[Dict] = None):
        cfg = dict(config or {})
        tg = cfg.get("telegram", {}) or {}
        self.telegram_token = tg.get("bot_token") if tg.get("enabled") else None
        self.telegram_chat_id = tg.get("chat_id")
        if self.telegram_token and "YOUR_" in str(self.telegram_token):
            self.telegram_token = None
        dc = cfg.get("discord", {}) or {}
        self.discord_webhook = dc.get("webhook_url") if dc.get("enabled") else None
        if self.discord_webhook and "YOUR_" in str(self.discord_webhook):
            self.discord_webhook = None
        self.timeout = float(cfg.get("timeout_seconds", 10.0))

    @property
    def channels(self) -> list:
        active = ["log"]
        if self.telegram_token and self.telegram_chat_id:
            active.append("telegram")
        if self.discord_webhook:
            active.append("discord")
        return active

    async def send(self, message: str, level: str = "info") -> None:
        log_fn = getattr(logger, level, logger.info)
        log_fn("NOTIFY: %s", message)
        tasks = []
        if self.telegram_token and self.telegram_chat_id:
            tasks.append(self._send_telegram(message))
        if self.discord_webhook:
            tasks.append(self._send_discord(message))
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    logger.warning(
                        "Notification channel failed: %s", self._redact(str(result))
                    )

    def _redact(self, text: str) -> str:
        """Exception text can embed the request URL, which carries the
        Telegram token and the Discord webhook secret."""
        for secret in (self.telegram_token, self.discord_webhook):
            if secret:
                text = text.replace(str(secret), "***")
        return text

    async def trade(self, message: str) -> None:
        await self.send(f"[trade] {message}")

    async def alert(self, message: str) -> None:
        await self.send(f"[ALERT] {message}", level="warning")

    async def emergency(self, message: str) -> None:
        await self.send(f"[EMERGENCY] {message}", level="critical")

    async def _send_telegram(self, message: str) -> None:
        import aiohttp

        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        payload = {"chat_id": self.telegram_chat_id, "text": message[:4000]}
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise RuntimeError(f"Telegram API {resp.status}: {body[:200]}")

    async def _send_discord(self, message: str) -> None:
        import aiohttp

        payload = {"content": message[:1900]}
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(self.discord_webhook, json=payload) as resp:
                if resp.status not in (200, 204):
                    body = await resp.text()
                    raise RuntimeError(f"Discord webhook {resp.status}: {body[:200]}")
